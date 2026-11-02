import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from noteworthy_app.attempts import AttemptStore
from tests.test_grading import questions

LEARNER = "a" * 32
OTHER = "b" * 32


class AttemptTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(directory)
        self.store = AttemptStore(self.directory)
        self.quiz = self.store.issue("doc1", questions())

    def submit(self, learner=LEARNER, answers=None):
        return self.store.submit(
            self.quiz["quiz_id"],
            learner,
            answers if answers is not None else ["B", "True", None, None],
        )

    def test_issued_quiz_and_attempt_survive_a_new_store(self):
        self.store = AttemptStore(self.directory)
        result = self.submit()
        self.assertEqual((result["correct"], result["total"]), (2, 4))
        self.assertEqual(result["document_id"], "doc1")
        self.assertTrue(result["created_at"].endswith("+00:00"))
        progress = AttemptStore(self.directory).progress(LEARNER)
        self.assertEqual(progress["attempts"], [result])
        self.assertEqual(progress["topic_scores"]["Numbers"], {"correct": 2, "total": 2})

    def test_browser_and_document_progress_are_separate(self):
        self.submit()
        self.assertEqual(self.store.progress(OTHER), {"attempts": [], "topic_scores": {}})
        second = self.store.issue("doc2", questions())
        self.store.submit(second["quiz_id"], LEARNER, [None, None, None, "Photosynthesis"])
        self.assertEqual(
            self.store.progress(LEARNER)["topic_scores"]["Biology"], {"correct": 1, "total": 4}
        )
        selected = self.store.progress(LEARNER, "doc2")
        self.assertEqual(len(selected["attempts"]), 1)
        self.assertEqual(selected["topic_scores"]["Biology"], {"correct": 1, "total": 2})

    def test_duplicate_and_concurrent_submit_return_one_saved_attempt(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.submit(), range(12)))
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.submit(answers=[None] * 4), results[0])
        self.assertEqual(len(self.store.progress(LEARNER)["attempts"]), 1)
        self.assertEqual(self.store.progress(LEARNER)["topic_scores"]["Numbers"]["total"], 2)

    def test_failed_attempt_write_can_be_retried(self):
        (self.directory / "attempts").write_text("not a directory")
        with self.assertRaises(OSError):
            self.submit()
        (self.directory / "attempts").unlink()
        self.assertEqual(self.store.progress(LEARNER)["attempts"], [])
        self.assertEqual(self.submit()["correct"], 2)

    def test_corrupt_records_are_skipped_without_hiding_valid_progress(self):
        self.submit()
        folder = self.directory / "attempts" / LEARNER
        for name, content in [("c" * 32, "{"), ("d" * 32, json.dumps({"correct": "bad"}))]:
            (folder / f"{name}.json").write_text(content)
        with self.assertLogs("noteworthy_app.attempts", level="WARNING"):
            result = self.store.progress(LEARNER)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["topic_scores"]["Numbers"], {"correct": 2, "total": 2})

    def test_malformed_submission_does_not_save_an_attempt(self):
        with self.assertRaises(ValueError):
            self.submit(answers=["B"])
        self.assertEqual(self.store.progress(LEARNER)["attempts"], [])

    def test_untrusted_identifiers_cannot_select_storage_paths(self):
        for identifier in ["../outside", "", "A" * 32, None, "a" * 33]:
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    self.store.submit(identifier, LEARNER, [None] * 4)
                with self.assertRaises(ValueError):
                    self.store.progress(identifier)
        with self.assertRaises(LookupError):
            self.store.submit("f" * 32, LEARNER, [None] * 4)
