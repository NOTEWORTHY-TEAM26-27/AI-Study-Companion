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
        self.request_paths = []
        self.authorization_headers = []
        test = self

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                test.request_paths.append(self.path)
                test.authorization_headers.append(self.headers.get("Authorization"))
                test.requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(test.response_status)

                if test.response_content_length is not None:
                    self.send_header(
                        "Content-Length",
                        str(test.response_content_length),
                    )

                self.end_headers()
                self.wfile.write(test.response_body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01),
            daemon=True,
        )
        self.thread.start()

        endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"

        self.environment = patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "ollama",
                "OLLAMA_URL": endpoint,
                "OLLAMA_MODEL": "test-model",
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    # Original Ollama tests

    def test_ollama_request_and_answer(self):
        self.assertEqual(
            generate_answer("Use these passages."),
            "A cited answer [p1-1].",
        )
        self.assertEqual(
            self.requests[0]["model"],
            "test-model",
        )
        self.assertEqual(
            self.requests[0]["prompt"],
            "Use these passages.",
        )
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

            with self.subTest(body=body):
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    generate_answer("question")

    def test_provider_http_failure_is_unavailable(self):
        self.response_status = 503

        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", ResourceWarning)

            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                generate_answer("question")

            gc.collect()

        self.assertEqual(
            [item for item in recorded if item.category is ResourceWarning],
            [],
        )

    def test_truncated_provider_response_is_unavailable(self):
        self.response_content_length = 100

        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            generate_answer("question")

    # New Sprint 2 Task 8-5 tests

    def test_default_provider_is_ollama(self):
        # Remove AI_PROVIDER temporarily to verify default.
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(
                os.environ,
                {
                    "OLLAMA_URL": (f"http://127.0.0.1:{self.server.server_port}/api/generate"),
                    "OLLAMA_MODEL": "test-model",
                },
            ):
                answer = generate_answer("Test question")

        self.assertEqual(
            answer,
            "A cited answer [p1-1].",
        )
        self.assertEqual(
            self.requests[0]["model"],
            "test-model",
        )
        self.assertEqual(
            self.request_paths[0],
            "/api/generate",
        )

    def test_digitalocean_request_and_answer(self):
        self.response_body = json.dumps(
            {"choices": [{"message": {"content": " Hosted answer. "}}]}
        ).encode()

        endpoint = f"http://127.0.0.1:{self.server.server_port}/v1"

        with patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "digitalocean",
                "DIGITALOCEAN_API_KEY": "fake-test-key",
                "DIGITALOCEAN_BASE_URL": endpoint,
                "DIGITALOCEAN_MODEL": "llama-4-maverick",
            },
        ):
            answer = generate_answer("Explain Scrum.")

        self.assertEqual(answer, "Hosted answer.")
        self.assertEqual(
            self.requests[0]["model"],
            "llama-4-maverick",
        )
        self.assertEqual(
            self.requests[0]["messages"],
            [
                {
                    "role": "user",
                    "content": "Explain Scrum.",
                }
            ],
        )
        self.assertFalse(self.requests[0]["stream"])
        self.assertEqual(
            self.request_paths[0],
            "/v1/chat/completions",
        )
        self.assertEqual(
            self.authorization_headers[0],
            "Bearer fake-test-key",
        )

    def test_digitalocean_requires_api_key(self):
        with patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "digitalocean",
                "DIGITALOCEAN_API_KEY": "",
            },
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "DIGITALOCEAN_API_KEY",
            ):
                generate_answer("Test question")

        self.assertEqual(self.requests, [])

    def test_invalid_provider_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "invalid-provider",
            },
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Unsupported AI_PROVIDER",
            ):
                generate_answer("Test question")

        self.assertEqual(self.requests, [])
