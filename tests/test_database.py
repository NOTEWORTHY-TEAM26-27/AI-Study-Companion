"""Choosing the stores from DATABASE_URL, and the app on PostgreSQL from end to end."""

import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from noteworthy_app.accounts import FileAccountStore
from noteworthy_app.attempts import AttemptStore
from noteworthy_app.database import PostgresAccountStore, PostgresAttemptStore
from noteworthy_app.server import create_server
from tests.fixtures import study_pdf
from tests.postgres import HAVE_PSYCOPG, PostgresTestCase, needs_postgres

CLASS_CODE = "cse4316-class-code"
PASSWORD = "correct horse battery"


class StoreChoiceTests(unittest.TestCase):
    def setUp(self):
        settings = {"RETRIEVAL": "keyword", "CLASS_CODE": CLASS_CODE}
        self.enterContext(mock.patch.dict(os.environ, settings))
        self.data_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "documents"

    def test_files_are_used_without_a_database_url(self):
        for value in [None, "", "  "]:
            with self.subTest(value=value), mock.patch.dict(os.environ):
                os.environ.pop("DATABASE_URL", None)
                if value is not None:
                    os.environ["DATABASE_URL"] = value
                server = create_server("127.0.0.1", 0, data_dir=self.data_dir)
                server.server_close()
                self.assertIsInstance(server.attempts, AttemptStore)
                self.assertIsInstance(server.accounts.store, FileAccountStore)

    def test_database_url_needs_psycopg(self):
        with (
            mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://noteworthy@db/noteworthy"}),
            mock.patch.dict(sys.modules, {"psycopg": None}),
            self.assertRaisesRegex(RuntimeError, "psycopg is not installed"),
        ):
            create_server("127.0.0.1", 0, data_dir=self.data_dir)

    @unittest.skipUnless(HAVE_PSYCOPG, "psycopg is not installed.")
    def test_unreachable_database_stops_startup(self):
        # Nothing listens on port 1, so the connection is refused.
        url = "postgresql://noteworthy@127.0.0.1:1/noteworthy"
        with (
            mock.patch.dict(os.environ, {"DATABASE_URL": url}),
            self.assertLogs("noteworthy_app.database", level="ERROR"),
            self.assertRaisesRegex(RuntimeError, "^The database is not available"),
        ):
            create_server("127.0.0.1", 0, data_dir=self.data_dir)


@needs_postgres
class PostgresServerTests(PostgresTestCase):
    """The app with DATABASE_URL set, across a restart like a redeploy."""

    def setUp(self):
        super().setUp()
        settings = {
            "RETRIEVAL": "keyword",
            "CLASS_CODE": CLASS_CODE,
            "DATABASE_URL": self.database_url,
        }
        self.enterContext(mock.patch.dict(os.environ, settings))
        os.environ.pop("COOKIE_SECURE", None)
        os.environ.pop("SESSION_DAYS", None)
        # Faster hashes; PasswordTests use the real setting.
        self.enterContext(mock.patch("noteworthy_app.accounts.SCRYPT_N", 2**10))
        # The fake model's reply is not JSON, so quizzes use cloze questions.
        self.enterContext(mock.patch("noteworthy_app.question_generator.LOGGER"))
        self.data_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "documents"
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        self.server = create_server(
            "127.0.0.1",
            0,
            answer_generator=lambda prompt: "Photosynthesis uses sunlight [p1-1].",
            data_dir=self.data_dir,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, payload=None, token=None):
        headers = {} if payload is None else {"Content-Type": "application/json"}
        if token is not None:
            headers["Cookie"] = f"noteworthy_session={token}"
        # A remote test database can take a second for each connection.
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=60)
        self.addCleanup(connection.close)
        body = None if payload is None else json.dumps(payload)
        connection.request("GET" if payload is None else "POST", path, body, headers)
        response = connection.getresponse()
        return response.status, response.headers, json.loads(response.read())

    def test_accounts_quizzes_and_attempts_survive_a_redeploy(self):
        self.assertIsInstance(self.server.attempts, PostgresAttemptStore)
        self.assertIsInstance(self.server.accounts.store, PostgresAccountStore)
        account = {"email": "a@uta.edu", "password": PASSWORD, "class_code": CLASS_CODE}
        status, headers, body = self.request("/api/signup", account)
        self.assertEqual((status, body), (201, {"email": "a@uta.edu"}))
        token = headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        pdf = {"name": "biology.pdf", "data": base64.b64encode(study_pdf()).decode()}
        status, _, document = self.request("/api/upload", pdf, token)
        self.assertEqual(status, 200)
        status, _, quiz = self.request("/api/quiz", {"document_id": document["id"]}, token)
        self.assertEqual(status, 200)
        answers = {"quiz_id": quiz["quiz_id"], "answers": [None] * len(quiz["questions"])}
        status, _, result = self.request("/api/quiz/submit", answers, token)
        self.assertEqual(status, 200)

        # A redeploy starts a new app on the same database and documents volume.
        self.stop()
        self.start()
        status, _, body = self.request("/api/me", token=token)
        self.assertEqual((status, body), (200, {"email": "a@uta.edu"}))
        status, _, listed = self.request("/api/attempts", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([item["quiz_id"] for item in listed["attempts"]], [quiz["quiz_id"]])
        self.assertEqual(listed["attempts"][0]["document_name"], "biology.pdf")
        status, _, saved = self.request(f"/api/attempts/{quiz['quiz_id']}", token=token)
        self.assertEqual((status, saved), (200, result))
        status, _, again = self.request(f"/api/quiz/{quiz['quiz_id']}", token=token)
        self.assertEqual((status, again), (200, quiz))
        status, _, body = self.request("/api/login", {"email": " A@uta.edu", "password": PASSWORD})
        self.assertEqual((status, body), (200, {"email": "a@uta.edu"}))
        status, _, _ = self.request("/api/signup", account)
        self.assertEqual(status, 409)

        # Only the uploaded PDF and its passages are files.
        files = sorted(path.name for path in self.data_dir.iterdir())
        self.assertEqual(files, [f"{document['id']}.json", f"{document['id']}.pdf"])
        self.assertEqual(self.execute("SELECT count(*) FROM attempts"), [(1,)])
        self.assertEqual(self.execute("SELECT count(*) FROM sessions"), [(2,)])


if __name__ == "__main__":
    unittest.main()
