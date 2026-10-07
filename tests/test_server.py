import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from noteworthy_app.server import MAX_PDF_BYTES, create_server
from tests.fixtures import study_pdf


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.prompts = []
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.data_dir = Path(self.storage.name) / "documents"

        def answer(prompt):
            self.prompts.append(prompt)
            return "Photosynthesis uses sunlight [p1-1]."

        self.server = create_server("127.0.0.1", 0, answer_generator=answer, data_dir=self.data_dir)
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

    def test_documents_are_scoped_to_storage_directory(self):
        self.upload()
        other = create_server("127.0.0.1", 0, data_dir=Path(self.storage.name) / "other")
        self.addCleanup(other.server_close)
        self.assertEqual(other.documents, {})

    def test_upload_saves_original_pdf_and_extracted_passages(self):
        document_id = self.upload()
        self.assertEqual((self.data_dir / f"{document_id}.pdf").read_bytes(), study_pdf())
        metadata = json.loads((self.data_dir / f"{document_id}.json").read_text())
        self.assertEqual(metadata["name"], "biology.pdf")
        self.assertIn("Photosynthesis", metadata["passages"][0]["text"])
        self.assertEqual(metadata["passages"][0]["page"], 1)

    def test_documents_can_be_listed_queried_and_quizzed_after_restart(self):
        document_id = self.upload()
        answer_generator = self.server.answer_generator
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            "127.0.0.1", 0, answer_generator=answer_generator, data_dir=self.data_dir
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        status, content = self.request("/api/documents")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["documents"][0]["id"], document_id)
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "Photosynthesis sunlight"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["sources"])
        status, result = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["questions"]), 5)

    def test_storage_failure_returns_error_and_does_not_publish_document(self):
        self.data_dir.rmdir()
        self.data_dir.write_text("a file blocks the document directory")
        status, result = self.post(
            "/api/upload", {"name": "biology.pdf", "data": base64.b64encode(study_pdf()).decode()}
        )
        self.assertEqual(status, 500)
        self.assertIn("save", result["error"])
        self.assertEqual(self.server.documents, {})

    def test_reupload_has_same_id_and_no_duplicate_document(self):
        document_id = self.upload()
        self.assertEqual(self.upload(), document_id)
        self.assertEqual(len(self.server.documents), 1)
        self.assertEqual(len(list(self.data_dir.glob("*.pdf"))), 1)
        self.assertEqual(len(list(self.data_dir.glob("*.json"))), 1)

    def test_untrusted_filename_does_not_control_storage_path(self):
        status, result = self.post(
            "/api/upload",
            {"name": "../outside.pdf", "data": base64.b64encode(study_pdf()).decode()},
        )
        self.assertEqual(status, 200)
        self.assertTrue((self.data_dir / f"{result['id']}.pdf").is_file())
        self.assertFalse((self.data_dir.parent / "outside.pdf").exists())

    def test_empty_and_invalid_pdfs_do_not_create_records(self):
        for content in [b"", b"not a PDF"]:
            with self.subTest(content=content):
                if content:
                    with self.assertLogs("pypdf", level="WARNING"):
                        status, _ = self.post(
                            "/api/upload",
                            {"name": "bad.pdf", "data": base64.b64encode(content).decode()},
                        )
                else:
                    status, _ = self.post("/api/upload", {"name": "bad.pdf", "data": ""})
                self.assertEqual(status, 400)
        self.assertEqual(list(self.data_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
