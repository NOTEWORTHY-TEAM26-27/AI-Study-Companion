import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from noteworthy_app.attempts import AttemptStore
from noteworthy_app.database import Database, PostgresAttemptStore
from tests.postgres import PostgresTestCase, needs_postgres
from tests.test_grading import questions

LEARNER = "a" * 32
OTHER = "b" * 32


class AttemptStoreContract:
    """Checks every attempt store must pass; each subclass says how to make one."""

    # Where the store reads the time and logs warnings.
    CLOCK = "noteworthy_app.attempts.datetime"
    LOGGER = "noteworthy_app.attempts"

    def make_store(self):
        """Return a new store on the same saved data."""
        raise NotImplementedError

    def save_raw_attempt(self, learner_id, quiz_id, record):
        """Save record as that attempt without any checks, replacing a saved one."""
        raise NotImplementedError

    def save_raw_quiz(self, quiz_id, saved):
        """Save a quiz for doc1 whose questions are saved, which can be any JSON value."""
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.store = self.make_store()
        self.quiz = self.store.issue("doc1", questions())

    def submit(self, learner=LEARNER, answers=None):
        return self.store.submit(
            self.quiz["quiz_id"],
            learner,
            answers if answers is not None else ["B", "True", None, None],
        )

    def test_issued_quiz_and_attempt_survive_a_new_store(self):
        self.store = self.make_store()
        result = self.submit()
        self.assertEqual((result["correct"], result["total"]), (2, 4))
        self.assertEqual(result["document_id"], "doc1")
        self.assertTrue(result["created_at"].endswith("+00:00"))
        progress = self.make_store().progress(LEARNER)
        self.assertEqual(set(progress), {"topic_scores"})
        self.assertEqual(
            self.make_store().submit(self.quiz["quiz_id"], LEARNER, [None] * 4), result
        )
        self.assertEqual(progress["topic_scores"]["Numbers"], {"correct": 2, "total": 2})

    def test_browser_and_document_progress_are_separate(self):
        self.submit()
        self.assertEqual(self.store.progress(OTHER), {"topic_scores": {}})
        second = self.store.issue("doc2", questions())
        self.store.submit(second["quiz_id"], LEARNER, [None, None, None, "Photosynthesis"])
        self.assertEqual(
            self.store.progress(LEARNER)["topic_scores"]["Biology"], {"correct": 1, "total": 4}
        )
        selected = self.store.progress(LEARNER, "doc2")
        self.assertEqual(set(selected), {"topic_scores"})
        self.assertEqual(selected["topic_scores"]["Biology"], {"correct": 1, "total": 2})
        self.assertEqual(self.store.progress(LEARNER, "doc3"), {"topic_scores": {}})

    def test_progress_adds_up_every_attempt(self):
        second = self.store.issue("doc1", questions())
        third = self.store.issue("doc2", questions())
        self.submit()
        self.store.submit(second["quiz_id"], LEARNER, ["B", "False", "red blood cells", None])
        self.store.submit(third["quiz_id"], LEARNER, ["A", "True", None, "photosynthesis"])
        self.store.submit(third["quiz_id"], OTHER, ["B", "True", "Red blood cells", None])
        self.assertEqual(
            self.store.progress(LEARNER),
            {
                "topic_scores": {
                    "Numbers": {"correct": 4, "total": 6},
                    "Biology": {"correct": 2, "total": 6},
                }
            },
        )
        self.assertEqual(
            self.store.progress(LEARNER, "doc1")["topic_scores"],
            {"Numbers": {"correct": 3, "total": 4}, "Biology": {"correct": 1, "total": 4}},
        )
        self.assertEqual(
            self.store.progress(OTHER)["topic_scores"],
            {"Numbers": {"correct": 2, "total": 2}, "Biology": {"correct": 1, "total": 2}},
        )

    def test_duplicate_and_concurrent_submit_return_one_saved_attempt(self):
        # Different answers, so a second saved attempt would show.
        answers = [["B", "True", None, None], [None] * 4]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(
                pool.map(lambda index: self.submit(answers=answers[index % 2]), range(12))
            )
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.submit(answers=["B", "True", "Red blood cells", None]), results[0])
        self.assertEqual(self.store.progress(LEARNER)["topic_scores"]["Numbers"]["total"], 2)
        self.assertEqual(len(self.store.history(LEARNER)), 1)

    def test_malformed_submission_does_not_save_an_attempt(self):
        with self.assertRaises(ValueError):
            self.submit(answers=["B"])
        self.assertEqual(self.store.progress(LEARNER), {"topic_scores": {}})
        with self.assertRaises(LookupError):
            self.store.attempt(self.quiz["quiz_id"], LEARNER)

    def test_issued_quiz_can_be_read_again_with_its_keys(self):
        self.assertEqual(self.make_store().quiz(self.quiz["quiz_id"]), self.quiz)
        self.assertNotIn("owner", self.quiz)
        with self.assertRaises(LookupError):
            self.store.quiz("f" * 32)
        for name, saved in [("c" * 32, "not a list"), ("d" * 32, [1, 2])]:
            self.save_raw_quiz(name, saved)
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.store.quiz(name)
        # A saved quiz that cannot be read is reported, not graded as a bad submission.
        with self.assertRaises(RuntimeError):
            self.store.submit("c" * 32, LEARNER, [None] * 4)

    def test_quizzes_are_found_only_by_their_owner(self):
        owned = self.store.issue("doc1", questions(), owner=LEARNER)
        self.assertEqual(owned["owner"], LEARNER)
        self.assertEqual(self.make_store().quiz(owned["quiz_id"], LEARNER), owned)
        # Another student's quiz, and a quiz from before accounts, are not found.
        for quiz_id, owner in [
            (owned["quiz_id"], None),
            (owned["quiz_id"], OTHER),
            (self.quiz["quiz_id"], LEARNER),
        ]:
            with self.subTest(quiz_id=quiz_id, owner=owner):
                with self.assertRaises(LookupError):
                    self.store.quiz(quiz_id, owner)
                with self.assertRaises(LookupError):
                    self.store.submit(quiz_id, owner or OTHER, [None] * 4, owner)
        result = self.store.submit(owned["quiz_id"], LEARNER, ["B", "True", None, None], LEARNER)
        self.assertEqual((result["learner_id"], result["correct"]), (LEARNER, 2))
        self.assertEqual(self.store.history(OTHER), [])
        self.assertEqual(self.store.progress(OTHER), {"topic_scores": {}})

    def test_saved_owned_attempt_needs_its_quiz_owner(self):
        owned = self.store.issue("private", questions(), owner=LEARNER)
        result = self.store.submit(owned["quiz_id"], LEARNER, [None] * 4, LEARNER)
        for owner in [None, OTHER]:
            with self.subTest(owner=owner):
                with self.assertRaises(LookupError):
                    self.store.attempt(owned["quiz_id"], LEARNER, owner=owner)
                # A retry must check ownership even when an attempt is already saved.
                with self.assertRaises(LookupError):
                    self.store.submit(owned["quiz_id"], LEARNER, [], owner=owner)
        self.assertEqual(self.make_store().attempt(owned["quiz_id"], LEARNER, LEARNER), result)
        self.assertEqual(self.store.submit(owned["quiz_id"], LEARNER, [], LEARNER), result)

    def test_history_and_progress_only_include_matching_quiz_owner(self):
        self.submit()
        owned = self.store.issue("private", questions(), owner=LEARNER)
        self.store.submit(owned["quiz_id"], LEARNER, [None] * 4, LEARNER)
        for owner, quiz_id, correct in [
            (None, self.quiz["quiz_id"], 2),
            (LEARNER, owned["quiz_id"], 0),
        ]:
            with self.subTest(owner=owner):
                history = self.make_store().history(LEARNER, owner=owner)
                self.assertEqual([attempt["quiz_id"] for attempt in history], [quiz_id])
                self.assertEqual(
                    self.store.progress(LEARNER, owner=owner)["topic_scores"]["Numbers"],
                    {"correct": correct, "total": 2},
                )
        self.assertEqual(self.store.history(LEARNER, owner=OTHER), [])
        self.assertEqual(self.store.progress(LEARNER, owner=OTHER), {"topic_scores": {}})

    def test_saved_attempt_is_returned_as_submitted(self):
        result = self.submit()
        self.assertEqual(self.make_store().attempt(self.quiz["quiz_id"], LEARNER), result)
        self.assertEqual(
            list(self.store.attempt(self.quiz["quiz_id"], LEARNER)["topic_scores"]),
            ["Numbers", "Biology"],
        )
        with self.assertRaises(LookupError):
            self.store.attempt(self.quiz["quiz_id"], OTHER)
        with self.assertRaises(LookupError):
            self.store.attempt("f" * 32, LEARNER)
        self.save_raw_attempt(LEARNER, self.quiz["quiz_id"], {**result, "score": "100"})
        with self.assertRaises(RuntimeError):
            self.store.attempt(self.quiz["quiz_id"], LEARNER)
        with self.assertRaises(RuntimeError):
            self.submit()

    def test_history_lists_attempts_newest_first(self):
        second = self.store.issue("doc2", questions())
        third, fourth = sorted(
            (self.store.issue("doc2", questions()) for _ in range(2)),
            key=lambda quiz: quiz["quiz_id"],
        )
        with mock.patch(self.CLOCK) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)
            self.store.submit(second["quiz_id"], LEARNER, [None, None, None, "Photosynthesis"])
            clock.now.return_value = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)
            self.submit()
            # Attempts saved at the same time are listed by quiz ID, highest first.
            clock.now.return_value = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
            for quiz in (third, fourth):
                self.store.submit(quiz["quiz_id"], LEARNER, [None] * 4)
        history = self.store.history(LEARNER)
        self.assertEqual(
            history[:2],
            [
                {
                    "quiz_id": second["quiz_id"],
                    "document_id": "doc2",
                    "correct": 1,
                    "total": 4,
                    "score": 25.0,
                    "created_at": "2026-11-02T09:30:00+00:00",
                },
                {
                    "quiz_id": self.quiz["quiz_id"],
                    "document_id": "doc1",
                    "correct": 2,
                    "total": 4,
                    "score": 50.0,
                    "created_at": "2026-11-01T09:30:00+00:00",
                },
            ],
        )
        self.assertEqual(
            [item["quiz_id"] for item in history[2:]], [fourth["quiz_id"], third["quiz_id"]]
        )
        self.assertEqual(self.store.history(OTHER), [])

    def test_unreadable_attempts_are_skipped_without_hiding_valid_ones(self):
        result = self.submit()
        for name, record in [
            ("c" * 32, {"correct": "bad"}),
            ("d" * 32, [result]),
            ("e" * 32, {**result, "quiz_id": "e" * 32, "score": None}),
            # Saved under one quiz ID while naming another.
            ("f" * 32, result),
        ]:
            self.save_raw_attempt(LEARNER, name, record)
        with self.assertLogs(self.LOGGER, level="WARNING") as logs:
            history = self.store.history(LEARNER)
        self.assertEqual(len(logs.output), 4)
        self.assertEqual([item["quiz_id"] for item in history], [self.quiz["quiz_id"]])
        with self.assertLogs(self.LOGGER, level="WARNING"):
            progress = self.store.progress(LEARNER)
        self.assertEqual(progress["topic_scores"]["Numbers"], {"correct": 2, "total": 2})
        with self.assertRaisesRegex(RuntimeError, "^Saved attempt could not be read"):
            self.store.attempt("c" * 32, LEARNER)

    def test_untrusted_identifiers_cannot_select_storage_paths(self):
        for identifier in ["../outside", "", "A" * 32, None, "a" * 33]:
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    self.store.submit(identifier, LEARNER, [None] * 4)
                with self.assertRaises(ValueError):
                    self.store.progress(identifier)
                with self.assertRaises(ValueError):
                    self.store.quiz(identifier)
                with self.assertRaises(ValueError):
                    self.store.attempt(identifier, LEARNER)
                with self.assertRaises(ValueError):
                    self.store.attempt(self.quiz["quiz_id"], identifier)
                with self.assertRaises(ValueError):
                    self.store.history(identifier)
        with self.assertRaises(LookupError):
            self.store.submit("f" * 32, LEARNER, [None] * 4)


class FileAttemptTests(AttemptStoreContract, unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        super().setUp()

    def make_store(self):
        return AttemptStore(self.directory)

    def save_raw_attempt(self, learner_id, quiz_id, record):
        if not (self.directory / "quizzes" / f"{quiz_id}.json").exists():
            self.save_raw_quiz(quiz_id, questions())
        folder = self.directory / "attempts" / learner_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{quiz_id}.json").write_text(json.dumps(record))

    def save_raw_quiz(self, quiz_id, saved):
        record = {"quiz_id": quiz_id, "document_id": "doc1", "questions": saved}
        (self.directory / "quizzes" / f"{quiz_id}.json").write_text(json.dumps(record))

    def test_failed_attempt_write_can_be_retried(self):
        (self.directory / "attempts").write_text("not a directory")
        with self.assertRaises(OSError):
            self.submit()
        (self.directory / "attempts").unlink()
        self.assertEqual(self.store.progress(LEARNER), {"topic_scores": {}})
        self.assertEqual(self.submit()["correct"], 2)

    def test_files_that_are_not_json_are_skipped(self):
        self.submit()
        (self.directory / "attempts" / LEARNER / f"{'c' * 32}.json").write_text("{")
        (self.directory / "quizzes" / f"{'c' * 32}.json").write_text("{")
        with self.assertLogs("noteworthy_app.attempts", level="WARNING"):
            result = self.store.progress(LEARNER)
        self.assertEqual(result["topic_scores"]["Numbers"], {"correct": 2, "total": 2})
        with self.assertLogs("noteworthy_app.attempts", level="WARNING"):
            self.assertEqual(len(self.store.history(LEARNER)), 1)
        with self.assertRaises(RuntimeError):
            self.store.quiz("c" * 32)

    def test_slow_progress_read_does_not_block_a_new_submission(self):
        self.submit()
        second = self.store.issue("doc1", questions())
        reading, release = threading.Event(), threading.Event()
        read_attempt = self.store._read_attempt

        def slow_read(path, learner_id):
            record = read_attempt(path, learner_id)
            reading.set()
            if not release.wait(timeout=3):
                raise TimeoutError("Progress reader was not released.")
            return record

        with mock.patch.object(self.store, "_read_attempt", side_effect=slow_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                progress = pool.submit(self.store.progress, LEARNER)
                try:
                    self.assertTrue(reading.wait(timeout=2))
                    submitted = pool.submit(
                        self.store.submit, second["quiz_id"], LEARNER, [None] * 4
                    )
                    self.assertEqual(submitted.result(timeout=1)["correct"], 0)
                finally:
                    release.set()
                self.assertEqual(set(progress.result(timeout=2)), {"topic_scores"})

    def test_deeply_nested_saved_files_are_unreadable_records(self):
        # json.loads can raise RecursionError here, which is not a ValueError.
        self.submit()
        nested = "[" * 100000 + "]" * 100000
        (self.directory / "attempts" / LEARNER / f"{'c' * 32}.json").write_text(nested)
        (self.directory / "quizzes" / f"{'c' * 32}.json").write_text(nested)
        with self.assertLogs("noteworthy_app.attempts", level="WARNING") as logs:
            history = self.store.history(LEARNER)
            progress = self.store.progress(LEARNER)
        self.assertEqual(len(logs.output), 2)
        self.assertEqual([item["quiz_id"] for item in history], [self.quiz["quiz_id"]])
        self.assertEqual(progress["topic_scores"]["Numbers"], {"correct": 2, "total": 2})
        with self.assertRaisesRegex(RuntimeError, "^Saved attempt could not be read"):
            self.store.attempt("c" * 32, LEARNER)
        with self.assertRaisesRegex(RuntimeError, "^Saved quiz could not be read"):
            self.store.quiz("c" * 32)
        with self.assertRaisesRegex(RuntimeError, "^Saved quiz could not be read"):
            self.store.submit("c" * 32, OTHER, [None] * 4)


@needs_postgres
class PostgresAttemptTests(AttemptStoreContract, PostgresTestCase):
    CLOCK = "noteworthy_app.database.datetime"
    LOGGER = "noteworthy_app.database"

    def make_store(self):
        return PostgresAttemptStore(self.database)

    def save_raw_attempt(self, learner_id, quiz_id, record):
        self.execute(
            "INSERT INTO quizzes (quiz_id, document_id, questions) VALUES (%s, 'doc1', %s::json)"
            " ON CONFLICT (quiz_id) DO NOTHING",
            (quiz_id, json.dumps(questions())),
        )
        self.execute(
            "INSERT INTO attempts (learner_id, quiz_id, document_id, created_at, result)"
            " VALUES (%s, %s, 'doc1', now(), %s::json) ON CONFLICT (learner_id, quiz_id)"
            " DO UPDATE SET result = EXCLUDED.result",
            (learner_id, quiz_id, json.dumps(record)),
        )

    def save_raw_quiz(self, quiz_id, saved):
        self.execute(
            "INSERT INTO quizzes (quiz_id, document_id, questions) VALUES (%s, 'doc1', %s::json)",
            (quiz_id, json.dumps(saved)),
        )

    def test_submits_from_separate_app_processes_save_one_attempt(self):
        # Each store has its own Database, as separate app processes would.
        stores = [PostgresAttemptStore(Database(self.database_url)) for _ in range(3)]
        answers = [["B", "True", None, None], [None] * 4]
        barrier = threading.Barrier(6)

        def submit(index):
            barrier.wait(timeout=30)
            store = stores[index % len(stores)]
            return store.submit(self.quiz["quiz_id"], LEARNER, answers[index % 2])

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(submit, range(6)))
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.execute("SELECT count(*) FROM attempts"), [(1,)])

    def test_text_postgres_cannot_hold_is_kept_as_json(self):
        quiz = self.store.issue("doc1", [*questions()[:3], {**questions()[3], "answer": "a\x00b"}])
        answers = [None, None, "\ud800 lone half", "a\x00b"]
        result = self.store.submit(quiz["quiz_id"], LEARNER, answers)
        self.assertEqual([item["answer"] for item in result["results"]], answers)
        self.assertEqual(self.store.attempt(quiz["quiz_id"], LEARNER), result)
        self.assertEqual(self.store.quiz(quiz["quiz_id"]), quiz)
        # Such a document ID matches no attempt.
        self.assertEqual(self.store.progress(LEARNER, "doc\x001"), {"topic_scores": {}})

    def test_database_errors_are_server_errors(self):
        self.execute("DROP TABLE attempts")
        self.addCleanup(Database, self.database_url)
        with (
            self.assertLogs("noteworthy_app.database", level="ERROR"),
            self.assertRaisesRegex(RuntimeError, "^The database is not available"),
        ):
            self.store.history(LEARNER)


if __name__ == "__main__":
    unittest.main()
