"""File-backed quizzes and anonymous progress for the single-process app."""

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .grading import grade_quiz
from .storage import _atomic_write

LOGGER = logging.getLogger(__name__)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("quiz_id and learner_id must be 32 lowercase hexadecimal characters.")
    return value


class AttemptStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()

    @staticmethod
    def _save(path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(record).encode())

    def issue(self, document_id: str, questions: list[dict]) -> dict:
        quiz_id = uuid4().hex
        quiz = {"quiz_id": quiz_id, "document_id": document_id, "questions": questions}
        self._save(self.directory / "quizzes" / f"{quiz_id}.json", quiz)
        return quiz

    @staticmethod
    def _read_attempt(path: Path, learner_id: str) -> dict:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            record["learner_id"] != learner_id
            or record["quiz_id"] != path.stem
            or not isinstance(record["document_id"], str)
            or not isinstance(record["results"], list)
            or not isinstance(record["created_at"], str)
            or type(record["correct"]) is not int
            or type(record["total"]) is not int
            or not 0 <= record["correct"] <= record["total"]
            or record["total"] < 1
            or not isinstance(record["topic_scores"], dict)
        ):
            raise ValueError("Invalid saved attempt.")
        for topic, totals in record["topic_scores"].items():
            if (
                not isinstance(topic, str)
                or type(totals["correct"]) is not int
                or type(totals["total"]) is not int
                or not 0 <= totals["correct"] <= totals["total"]
            ):
                raise ValueError("Invalid topic score.")
        if sum(t["total"] for t in record["topic_scores"].values()) != record["total"]:
            raise ValueError("Invalid topic total.")
        return record

    def submit(self, quiz_id: str, learner_id: str, answers: list) -> dict:
        quiz_id, learner_id = _identifier(quiz_id), _identifier(learner_id)
        path = self.directory / "attempts" / learner_id / f"{quiz_id}.json"
        with self.lock:
            if path.exists():
                try:
                    return self._read_attempt(path, learner_id)
                except (ValueError, TypeError, KeyError) as exc:
                    raise RuntimeError("Saved attempt could not be read.") from exc
            try:
                quiz = json.loads(
                    (self.directory / "quizzes" / f"{quiz_id}.json").read_text(encoding="utf-8")
                )
            except FileNotFoundError as exc:
                raise LookupError("Quiz not found. Generate a new quiz.") from exc
            result = {
                **grade_quiz(quiz["questions"], answers),
                "quiz_id": quiz_id,
                "learner_id": learner_id,
                "document_id": quiz["document_id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(path, result)
            return result

    def progress(self, learner_id: str, document_id: str | None = None) -> dict:
        learner_id = _identifier(learner_id)
        topics = {}
        # Published attempts are immutable and atomically replaced. Readers need not
        # hold the submit lock; a concurrent new attempt appears on the next read.
        for path in (self.directory / "attempts" / learner_id).glob("*.json"):
            try:
                attempt = self._read_attempt(path, learner_id)
            except (OSError, ValueError, TypeError, KeyError):
                LOGGER.warning("Skipping unreadable quiz attempt %s", path.name)
                continue
            if document_id is not None and attempt["document_id"] != document_id:
                continue
            for topic, values in attempt["topic_scores"].items():
                totals = topics.setdefault(topic, {"correct": 0, "total": 0})
                totals["correct"] += values["correct"]
                totals["total"] += values["total"]
        return {"topic_scores": topics}
