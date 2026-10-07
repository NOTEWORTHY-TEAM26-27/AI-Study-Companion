import base64
import http.client
import json
import threading
import unittest

from noteworthy_app.server import MAX_PDF_BYTES, create_server
from tests.fixtures import study_pdf


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.prompts = []

        def answer(prompt):
            self.prompts.append(prompt)
            return "Photosynthesis uses sunlight [p1-1]."

        self.server = create_server("127.0.0.1", 0, answer_generator=answer)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, body=None, headers=None, method=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(connection.close)
        connection.request(method or ("GET" if body is None else "POST"), path, body, headers or {})
        response = connection.getresponse()
        content = response.read()
        return response.status, content

    def post(self, path, payload):
        status, content = self.request(
            path, json.dumps(payload), {"Content-Type": "application/json"}
        )
        return status, json.loads(content)

    def upload(self):
        status, payload = self.post(
            "/api/upload",
            {
                "name": "biology.pdf",
                "data": base64.b64encode(study_pdf()).decode(),
            },
        )
        self.assertEqual(status, 200)
        return payload["id"]

    def test_health_does_not_call_model(self):
        status, content = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {"status": "ok"})
        self.assertEqual(self.prompts, [])

    def test_ui_is_served(self):
        status, content = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Ask your notes.", content)

    def test_upload_ask_quiz_flow(self):
        document_id = self.upload()
        status, content = self.request("/api/documents")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["documents"][0]["id"], document_id)
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "Photosynthesis sunlight"}
        )
        self.assertEqual(status, 200)
        self.assertIn("[p1-1]", result["answer"])
        self.assertTrue(result["sources"])
        self.assertEqual(len(self.prompts), 1)
        status, quiz = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200)
        self.assertEqual(len(quiz["questions"]), 5)

    def test_unrelated_question_skips_model(self):
        status, result = self.post(
            "/api/ask", {"document_id": self.upload(), "question": "quantum gravity"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result, {"answer": "The PDF does not say.", "sources": []})
        self.assertEqual(self.prompts, [])

    def test_unknown_document_and_route(self):
        status, _ = self.post("/api/quiz", {"document_id": "missing"})
        self.assertEqual(status, 404)
        status, content = self.request("/missing")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(content))
        status, _ = self.post("/missing", {})
        self.assertEqual(status, 404)

    def test_bad_json_and_non_object_payloads(self):
        for body in ["{", "[]", "null", "1"]:
            with self.subTest(body=body):
                status, content = self.request("/api/upload", body)
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(content))

    def test_invalid_length_returns_json_error(self):
        status, content = self.request("/api/upload", "{}", {"Content-Length": "invalid"})
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(content))

    def test_oversized_request_is_rejected_before_reading(self):
        status, content = self.request(
            "/api/upload", "{}", {"Content-Length": str(MAX_PDF_BYTES * 2 + 1)}
        )
        self.assertEqual(status, 413)
        self.assertIn("error", json.loads(content))

    def test_invalid_upload_fields(self):
        for payload in [
            {},
            {"name": None, "data": "AA=="},
            {"name": "notes.txt", "data": "AA=="},
            {"name": "notes.pdf", "data": "%%%"},
            {"name": "notes.pdf", "data": 10},
        ]:
            with self.subTest(payload=payload):
                status, _ = self.post("/api/upload", payload)
                self.assertEqual(status, 400)

    def test_empty_or_non_string_question_is_rejected(self):
        document_id = self.upload()
        for question in [" ", None, 10]:
            status, _ = self.post("/api/ask", {"document_id": document_id, "question": question})
            self.assertEqual(status, 400)
        self.assertEqual(self.prompts, [])

    def test_model_unavailable_returns_503(self):
        def unavailable(prompt):
            raise RuntimeError("Local answer model is unavailable.")

        self.server.answer_generator = unavailable
        status, result = self.post(
            "/api/ask", {"document_id": self.upload(), "question": "Photosynthesis"}
        )
        self.assertEqual(status, 503)
        self.assertIn("unavailable", result["error"])

    def test_documents_are_scoped_to_server_instance(self):
        self.upload()
        other = create_server("127.0.0.1", 0)
        self.addCleanup(other.server_close)
        self.assertEqual(other.documents, {})


if __name__ == "__main__":
    unittest.main()
