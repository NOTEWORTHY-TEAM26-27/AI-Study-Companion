import base64
import http.client
import json
import os
import tempfile
import threading
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from noteworthy_app.search import EmbeddingSearch
from noteworthy_app.server import (
    MAX_PDF_BYTES,
    PUBLIC_QUESTION_FIELDS,
    STATIC_TYPES,
    create_server,
)
from tests.fixtures import model_questions, model_reply, study_pdf


class ServerTests(unittest.TestCase):
    def setUp(self):
        # Keyword search unless a test sets one; never load the real search model.
        self.enterContext(mock.patch.dict(os.environ, {"RETRIEVAL": "keyword"}))
        # The fake model's reply is not JSON, so quizzes use cloze questions; that warning
        # is checked in test_question_generator.
        self.enterContext(mock.patch("noteworthy_app.question_generator.LOGGER"))
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
        self.assertEqual(payload["status"], "ready")
        return payload["id"]

    def test_health_does_not_call_model(self):
        status, content = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {"status": "ok"})
        self.assertEqual(self.prompts, [])

    def page(self, path):
        """GET a path and also return the response headers."""
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def static_folder(self):
        """Serve pages from an empty temporary folder instead of the package's."""
        folder = Path(self.storage.name) / "static"
        folder.mkdir()
        self.enterContext(mock.patch("noteworthy_app.server.STATIC_DIR", folder))
        return folder

    def test_ui_is_served(self):
        status, content = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Ask your notes.", content)
        for path in ["/", "/index.html", "/index.html?doc=../server.py"]:
            with self.subTest(path=path):
                status, headers, body = self.page(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(body, content)

    def test_pages_scripts_styles_and_images_are_served_with_their_types(self):
        folder = self.static_folder()
        files = {
            "library.html": (b"<p>Library</p>", "text/html; charset=utf-8"),
            "style-2.css": (b"p { color: black; }", "text/css; charset=utf-8"),
            "app.js": (b"const page = 1;", "text/javascript; charset=utf-8"),
            "logo.svg": (b"<svg></svg>", "image/svg+xml"),
        }
        for name, (content, _) in files.items():
            (folder / name).write_bytes(content)
        for name, (content, content_type) in files.items():
            with self.subTest(name=name):
                # The query string never changes which file is served.
                status, headers, body = self.page(f"/{name}?file=index.html")
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(body, content)

    def test_other_paths_are_not_served(self):
        for path in [
            "/..%2fserver.py",
            "/%2e%2e/x.html",
            "/%2e%2e%2fserver.py",
            "/../pyproject.toml",
            "/static/index.html",
            "/static",
            "/Index.html",
            "/index.HTML",
            "/index.htm",
            "/index.html/",
            "/index.html.",
            "/index%2ehtml",
            "/%69ndex.html",
            "/-index.html",
            "/.html",
            "/app.py",
            "/server.py",
            "/missing.html",
            "/missing.html?file=index.html",
        ]:
            with self.subTest(path=path):
                status, _, body = self.page(path)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body), {"error": "Not found"})

    def test_static_files_cannot_lead_out_of_the_folder(self):
        folder = self.static_folder()
        (folder / "notes.html").mkdir()
        outside = Path(self.storage.name) / "secret.html"
        outside.write_bytes(b"secret")
        status, _, _ = self.page("/notes.html")
        self.assertEqual(status, 404)
        try:
            (folder / "link.html").symlink_to(outside)
        except OSError:
            self.skipTest("This system needs extra rights to create symbolic links.")
        status, _, body = self.page("/link.html")
        self.assertEqual(status, 404)
        self.assertNotIn(b"secret", body)

    def test_package_includes_every_served_file_type(self):
        with (Path(__file__).parents[1] / "pyproject.toml").open("rb") as file:
            package_data = tomllib.load(file)["tool"]["setuptools"]["package-data"]
        self.assertEqual(
            set(package_data["noteworthy_app"]), {f"static/*.{kind}" for kind in STATIC_TYPES}
        )

    def test_documents_list_upload_time(self):
        document_id = self.upload()
        pdf = self.data_dir / f"{document_id}.pdf"
        moment = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc).timestamp()
        os.utime(pdf, (moment, moment))
        status, content = self.request("/api/documents")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(content)["documents"],
            [
                {
                    "id": document_id,
                    "name": "biology.pdf",
                    "passages": len(self.server.documents[document_id][1]),
                    "status": "ready",
                    "uploaded_at": "2026-11-01T09:30:00+00:00",
                }
            ],
        )
        # A PDF removed while the app runs is still listed, without a time.
        pdf.unlink()
        status, content = self.request("/api/documents")
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(content)["documents"][0]["uploaded_at"])

    def test_issued_quiz_can_be_loaded_again_without_keys(self):
        document_id = self.upload()
        # Cloze questions use answer_index; model questions use answer and excerpts.
        for generator in [self.server.answer_generator, lambda prompt: model_reply()]:
            self.server.answer_generator = generator
            status, issued = self.post("/api/quiz", {"document_id": document_id})
            self.assertEqual(status, 200)
            status, content = self.request(f"/api/quiz/{issued['quiz_id']}")
            self.assertEqual(status, 200)
            quiz = json.loads(content)
            self.assertEqual(quiz, issued)
            self.assertEqual(set(quiz), {"quiz_id", "document_id", "questions"})
            self.assertEqual(quiz["document_id"], document_id)
            saved = json.loads(
                (self.data_dir / "quizzes" / f"{quiz['quiz_id']}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(quiz["questions"]), len(saved["questions"]))
            for question in quiz["questions"]:
                self.assertLessEqual(set(question), PUBLIC_QUESTION_FIELDS)
                for key in ["answer", "answer_index", "source_excerpt", "source_text"]:
                    self.assertNotIn(key, question)

    def test_quiz_and_attempt_lookups_reject_bad_and_unknown_ids(self):
        learner = {"X-Learner-Id": "a" * 32}
        for path, headers, expected in [
            ("/api/quiz/abc", {}, 400),
            ("/api/quiz/" + "A" * 32, {}, 400),
            ("/api/quiz/" + "a" * 33, {}, 400),
            ("/api/quiz/%2e%2e", {}, 400),
            ("/api/quiz/" + "f" * 32, {}, 404),
            ("/api/quiz/../server.py", {}, 404),
            ("/api/attempts", {}, 400),
            ("/api/attempts", {"X-Learner-Id": "../x"}, 400),
            # The ID is read only from the header, never from the URL.
            ("/api/attempts?learner_id=" + "a" * 32, {}, 400),
            ("/api/attempts/" + "f" * 32, {}, 400),
            ("/api/attempts/abc", learner, 400),
            ("/api/attempts/" + "f" * 32, {"X-Learner-Id": "A" * 32}, 400),
            ("/api/attempts/" + "f" * 32, learner, 404),
        ]:
            with self.subTest(path=path, headers=headers):
                status, content = self.request(path, headers=headers)
                self.assertEqual(status, expected)
                self.assertIn("error", json.loads(content))

    def test_attempts_are_listed_newest_first_and_reopened(self):
        from tests.test_grading import questions

        document_id = self.upload()
        _, quiz = self.post("/api/quiz", {"document_id": document_id})
        # A quiz on a document that is no longer stored.
        removed = self.server.attempts.issue("0" * 16, questions())
        learner = {"X-Learner-Id": "a" * 32}
        with mock.patch("noteworthy_app.attempts.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)
            _, first = self.post(
                "/api/quiz/submit",
                {"quiz_id": quiz["quiz_id"], "learner_id": "a" * 32, "answers": [None] * 5},
            )
            clock.now.return_value = datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)
            _, second = self.post(
                "/api/quiz/submit",
                {
                    "quiz_id": removed["quiz_id"],
                    "learner_id": "a" * 32,
                    "answers": ["B", "True", None, None],
                },
            )
        status, content = self.request("/api/attempts", headers=learner)
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(content),
            {
                "attempts": [
                    {
                        "quiz_id": removed["quiz_id"],
                        "document_id": "0" * 16,
                        "document_name": None,
                        "correct": 2,
                        "total": 4,
                        "score": 50,
                        "created_at": "2026-11-02T09:30:00+00:00",
                    },
                    {
                        "quiz_id": quiz["quiz_id"],
                        "document_id": document_id,
                        "document_name": "biology.pdf",
                        "correct": 0,
                        "total": 5,
                        "score": 0,
                        "created_at": "2026-11-01T09:30:00+00:00",
                    },
                ]
            },
        )
        for submitted in [first, second]:
            status, content = self.request(f"/api/attempts/{submitted['quiz_id']}", headers=learner)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(content), submitted)
        other = {"X-Learner-Id": "b" * 32}
        status, content = self.request("/api/attempts", headers=other)
        self.assertEqual((status, json.loads(content)), (200, {"attempts": []}))
        status, _ = self.request(f"/api/attempts/{quiz['quiz_id']}", headers=other)
        self.assertEqual(status, 404)
        # As on submit, a saved attempt that cannot be read is a server problem.
        (self.data_dir / "attempts" / ("a" * 32) / f"{quiz['quiz_id']}.json").write_text("{")
        status, content = self.request(f"/api/attempts/{quiz['quiz_id']}", headers=learner)
        self.assertEqual(status, 503)
        self.assertIn("error", json.loads(content))
        with self.assertLogs("noteworthy_app.attempts", level="WARNING"):
            status, content = self.request("/api/attempts", headers=learner)
        self.assertEqual(status, 200)
        self.assertEqual(
            [item["quiz_id"] for item in json.loads(content)["attempts"]], [removed["quiz_id"]]
        )

    def test_upload_ask_quiz_flow(self):
        document_id = self.upload()
        status, content = self.request("/api/documents")
        self.assertEqual(status, 200)
        listed = json.loads(content)["documents"][0]
        self.assertEqual(listed["id"], document_id)
        self.assertEqual(listed["status"], "ready")
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
        # No body, because the server replies without reading one (see the invalid length test).
        status, content = self.request("/missing", method="POST")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(content))

    def test_server_grades_issued_quiz_and_ignores_forged_score_and_key(self):
        document_id = self.upload()
        status, quiz = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200)
        self.assertIn("quiz_id", quiz)
        self.assertEqual(len(self.prompts), 1)
        for question in quiz["questions"]:
            self.assertEqual(set(question), {"question", "options", "source_id", "page", "topic"})
        # Keys are random, so read them from the saved quiz. Deliberately miss the first.
        saved = json.loads(
            (self.data_dir / "quizzes" / f"{quiz['quiz_id']}.json").read_text(encoding="utf-8")
        )
        keys = [question["answer_index"] for question in saved["questions"]]
        answers = [(keys[0] + 1) % 4, *keys[1:]]
        status, result = self.post(
            "/api/quiz/submit",
            {
                "quiz_id": quiz["quiz_id"],
                "learner_id": "a" * 32,
                "answers": answers,
                "score": 100,
                "correct": 5,
                "questions": [],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual((result["correct"], result["total"], result["score"]), (4, 5, 80))
        status, content = self.request("/api/progress", headers={"X-Learner-Id": "a" * 32})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(content), {"topic_scores": {"Page 1": {"correct": 4, "total": 5}}}
        )
        status, content = self.request("/api/progress", headers={"X-Learner-Id": "b" * 32})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content), {"topic_scores": {}})
        # Only generating the quiz called the model; grading and progress did not.
        self.assertEqual(len(self.prompts), 1)

    def test_model_quiz_hides_keys_and_grades_all_four_formats(self):
        document_id = self.upload()
        self.server.answer_generator = lambda prompt: model_reply()
        status, quiz = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200)
        self.assertEqual(
            [question["type"] for question in quiz["questions"]],
            [question["type"] for question in model_questions()],
        )
        for question in quiz["questions"]:
            self.assertEqual(
                set(question),
                {"type", "question", "choices", "source_id", "page", "source_page", "topic"},
            )
        saved = json.loads(
            (self.data_dir / "quizzes" / f"{quiz['quiz_id']}.json").read_text(encoding="utf-8")
        )
        answers = [question["answer"] for question in saved["questions"]]
        answers[3] = "FALSE"
        answers[6] = " Chemical Reactions. "
        status, result = self.post(
            "/api/quiz/submit",
            {"quiz_id": quiz["quiz_id"], "learner_id": "a" * 32, "answers": answers},
        )
        self.assertEqual(status, 200)
        self.assertEqual((result["correct"], result["total"]), (8, 8))
        self.assertEqual(result["results"][5]["expected_answer"], ["Chromosomes", "nucleus"])
        self.assertEqual(
            result["results"][0]["source_text"], model_questions()[0]["source_excerpt"]
        )

    def test_shared_question_schema_keeps_keys_and_excerpts_private_until_submission(self):
        from tests.test_grading import questions

        document_id = self.upload()
        with mock.patch("noteworthy_app.server.generate_questions", return_value=questions()):
            status, quiz = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200)
        self.assertEqual(quiz["questions"][0]["choices"]["B"], "7")
        for question in quiz["questions"]:
            self.assertEqual(set(question), {"type", "question", "choices", "topic", "source_page"})
        status, result = self.post(
            "/api/quiz/submit",
            {
                "quiz_id": quiz["quiz_id"],
                "learner_id": "a" * 32,
                "answers": ["B", "True", "Red blood cells", "Photosynthesis"],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["correct"], 4)
        self.assertEqual(result["results"][0]["expected_answer"], "7")
        self.assertEqual(result["results"][0]["source_text"], "Seven is prime.")

    def test_quiz_api_reports_invalid_submission_and_unknown_quiz(self):
        for quiz_id, learner_id, expected in [
            ("../x", "a" * 32, 400),
            ("a" * 32, "../../x", 400),
            ("a" * 32, "b" * 32, 404),
        ]:
            status, result = self.post(
                "/api/quiz/submit", {"quiz_id": quiz_id, "learner_id": learner_id, "answers": []}
            )
            self.assertEqual(status, expected)
            self.assertIn("error", result)
        status, content = self.request("/api/progress")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(content))
        # The ID is read only from the header, never from the URL.
        status, content = self.request("/api/progress?learner_id=" + "a" * 32)
        self.assertEqual(status, 400)

    def test_failed_quiz_submission_reports_error_and_retry_saves_once(self):
        _, quiz = self.post("/api/quiz", {"document_id": self.upload()})
        self.assertIn("quiz_id", quiz)
        (self.data_dir / "attempts").write_text("blocked")
        body = {"quiz_id": quiz["quiz_id"], "learner_id": "a" * 32, "answers": [None] * 5}
        status, result = self.post("/api/quiz/submit", body)
        self.assertEqual(status, 500)
        self.assertIn("error", result)
        (self.data_dir / "attempts").unlink()
        status, result = self.post("/api/quiz/submit", body)
        self.assertEqual(status, 200)
        self.assertEqual(result["correct"], 0)
        self.assertEqual(self.post("/api/quiz/submit", body), (200, result))

    def test_bad_json_and_non_object_payloads(self):
        for body in ["{", "[]", "null", "1"]:
            with self.subTest(body=body):
                status, content = self.request("/api/upload", body)
                self.assertEqual(status, 400)
                self.assertIn("error", json.loads(content))

    def test_invalid_length_returns_json_error(self):
        # Send no body: the server replies without reading it, and closing a socket with
        # unread bytes makes Windows reset the connection before the client reads the reply.
        status, content = self.request(
            "/api/upload", headers={"Content-Length": "invalid"}, method="POST"
        )
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(content))

    def test_oversized_request_is_rejected_before_reading(self):
        # No body: if the server tried to read the declared length, this request would time out.
        status, content = self.request(
            "/api/upload", headers={"Content-Length": str(MAX_PDF_BYTES * 2 + 1)}, method="POST"
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

    def test_empty_upload_has_clear_message(self):
        status, payload = self.post("/api/upload", {"name": "empty.pdf", "data": ""})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "The file is empty. Choose a PDF that has content.")

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

    def test_embedding_search_answers_questions_without_shared_words(self):
        calls = []

        def embed(texts):
            calls.append(texts)
            return [
                [1.0, 0.0] if "hotosynthesis" in t or "leaves" in t else [0.0, 1.0] for t in texts
            ]

        answer_generator = self.server.answer_generator
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            "127.0.0.1",
            0,
            answer_generator=answer_generator,
            data_dir=self.data_dir,
            search=EmbeddingSearch(embed),
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        document_id = self.upload()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.upload(), document_id)
        self.assertEqual(len(calls), 1)
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "What makes green leaves work?"}
        )
        self.assertEqual(status, 200)
        self.assertEqual([source["id"] for source in result["sources"]], ["p1-1"])
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(calls[1:], [["What makes green leaves work?"]])
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "quantum gravity"}
        )
        self.assertEqual(result, {"answer": "The PDF does not say.", "sources": []})
        self.assertEqual(len(self.prompts), 1)

    def test_upload_is_saved_when_search_model_fails(self):
        def broken(texts):
            raise OSError("model files missing")

        self.server.search = EmbeddingSearch(broken)
        document_id = self.upload()
        self.assertIn(document_id, self.server.documents)
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "Photosynthesis"}
        )
        self.assertEqual(status, 503)
        self.assertIn("search model is unavailable", result["error"])
        self.assertEqual(self.prompts, [])

    def test_unknown_retrieval_setting_stops_startup(self):
        with mock.patch.dict(os.environ, {"RETRIEVAL": "vector"}):
            with self.assertRaisesRegex(ValueError, "Unsupported RETRIEVAL"):
                create_server("127.0.0.1", 0, data_dir=self.data_dir)

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
