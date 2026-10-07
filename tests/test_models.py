import gc
import json
import os
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from noteworthy_app.models import generate_answer


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.response_body = b'{"response": " A cited answer [p1-1]. "}'
        self.response_status = 200
        self.response_content_length = None
        self.requests = []
        test = self

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                test.requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(test.response_status)
                if test.response_content_length is not None:
                    self.send_header("Content-Length", str(test.response_content_length))
                self.end_headers()
                self.wfile.write(test.response_body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"
        self.environment = patch.dict(
            os.environ, {"OLLAMA_URL": endpoint, "OLLAMA_MODEL": "test-model"}
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_ollama_request_and_answer(self):
        self.assertEqual(generate_answer("Use these passages."), "A cited answer [p1-1].")
        self.assertEqual(self.requests[0]["model"], "test-model")
        self.assertEqual(self.requests[0]["prompt"], "Use these passages.")
        self.assertFalse(self.requests[0]["stream"])

    def test_malformed_model_responses_are_unavailable(self):
        for body in [
            b"not json",
            b"[]",
            b"{}",
            b'{"response":null}',
            b'{"response":3}',
            b'{"response":" "}',
        ]:
            self.response_body = body
            with self.subTest(body=body), self.assertRaisesRegex(RuntimeError, "unavailable"):
                generate_answer("question")

    def test_provider_http_failure_is_unavailable(self):
        self.response_status = 503
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", ResourceWarning)
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                generate_answer("question")
            gc.collect()
        self.assertEqual([item for item in recorded if item.category is ResourceWarning], [])

    def test_truncated_provider_response_is_unavailable(self):
        self.response_content_length = 100
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            generate_answer("question")
