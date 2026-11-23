"""File-backed quizzes and learner progress for the single-process app."""

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


def _check_quiz(quiz, quiz_id: str) -> dict:
    """Return a saved quiz if it has the shape issue() returns; raise RuntimeError otherwise."""
    if (
        not isinstance(quiz, dict)
        or quiz.get("quiz_id") != quiz_id
        or not isinstance(quiz.get("document_id"), str)
        or not isinstance(quiz.get("questions"), list)
        or not all(isinstance(question, dict) for question in quiz["questions"])
        or not isinstance(quiz.get("owner"), str | None)
    ):
        raise RuntimeError("Saved quiz could not be read.")
    return quiz


def _check_attempt(record, learner_id: str, quiz_id: str) -> dict:
    """Return a saved attempt if it has the shape submit() returns.

    Raise ValueError, TypeError or KeyError otherwise. The PostgreSQL store checks its
    records the same way.
    """
    if (
        record["learner_id"] != learner_id
        or record["quiz_id"] != quiz_id
        or not isinstance(record["document_id"], str)
        or not isinstance(record["results"], list)
        or not isinstance(record["created_at"], str)
        or type(record["correct"]) is not int
        or type(record["total"]) is not int
        or not 0 <= record["correct"] <= record["total"]
        or record["total"] < 1
        or type(record["score"]) not in {int, float}
        or not 0 <= record["score"] <= 100
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


class AttemptStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()

    @staticmethod
    def _save(path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(record).encode())

    def issue(self, document_id: str, questions: list[dict], owner: str | None = None) -> dict:
        quiz_id = uuid4().hex
        quiz = {"quiz_id": quiz_id, "document_id": document_id, "questions": questions}
        if owner is not None:
            quiz["owner"] = owner
        self._save(self.directory / "quizzes" / f"{quiz_id}.json", quiz)
        return quiz

    def quiz(self, quiz_id: str, owner: str | None = None) -> dict:
        """Return an issued quiz with its keys; the server filters what it sends."""
        quiz_id = _identifier(quiz_id)
        try:
            quiz = json.loads(
                (self.directory / "quizzes" / f"{quiz_id}.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError as exc:
            raise LookupError("Quiz not found. Generate a new quiz.") from exc
        except (ValueError, RecursionError) as exc:
            raise RuntimeError("Saved quiz could not be read.") from exc
        _check_quiz(quiz, quiz_id)
        # Owner is an account ID, or None without accounts. Another student's quiz is not found.
        if quiz.get("owner") != owner:
            raise LookupError("Quiz not found. Generate a new quiz.")
        return quiz

    @staticmethod
    def _read_attempt(path: Path, learner_id: str) -> dict:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except RecursionError as exc:
            # Deeply nested JSON is just another unreadable record, not a server error.
            raise ValueError("Invalid saved attempt.") from exc
        return _check_attempt(record, learner_id, path.stem)

    def submit(
        self, quiz_id: str, learner_id: str, answers: list, owner: str | None = None
    ) -> dict:
        quiz_id, learner_id = _identifier(quiz_id), _identifier(learner_id)
        path = self.directory / "attempts" / learner_id / f"{quiz_id}.json"
        with self.lock:
            # Check the quiz owner before returning an already saved attempt, too.
            quiz = self.quiz(quiz_id, owner)
            if path.exists():
                try:
                    return self._read_attempt(path, learner_id)
                except (ValueError, TypeError, KeyError) as exc:
                    raise RuntimeError("Saved attempt could not be read.") from exc
            result = {
                **grade_quiz(quiz["questions"], answers),
                "quiz_id": quiz_id,
                "learner_id": learner_id,
                "document_id": quiz["document_id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(path, result)
            return result

    def _visible_attempt(self, path: Path, learner_id: str, owner: str | None) -> dict:
        record = self._read_attempt(path, learner_id)
        # Legacy attempts have no owner field; their issued quiz is the authority.
        self.quiz(path.stem, owner)
        return record

    def attempt(self, quiz_id: str, learner_id: str, owner: str | None = None) -> dict:
        """Return a saved attempt exactly as submit() returned it."""
        quiz_id, learner_id = _identifier(quiz_id), _identifier(learner_id)
        try:
            return self._visible_attempt(
                self.directory / "attempts" / learner_id / f"{quiz_id}.json", learner_id, owner
            )
        except FileNotFoundError as exc:
            raise LookupError("Quiz attempt not found.") from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError("Saved attempt could not be read.") from exc

    def history(self, learner_id: str, owner: str | None = None) -> list[dict]:
        """Summarize a learner's saved attempts, newest first."""
        learner_id = _identifier(learner_id)
        attempts = []
        for path in (self.directory / "attempts" / learner_id).glob("*.json"):
            try:
                attempt = self._visible_attempt(path, learner_id, owner)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                LOGGER.warning("Skipping unreadable quiz attempt %s", path.name)
                continue
            except LookupError:
                continue
            attempts.append(
                {
                    key: attempt[key]
                    for key in ("quiz_id", "document_id", "correct", "total", "score", "created_at")
                }
            )
        # Saved times are all UTC ISO 8601 strings, so they sort as text.
        return sorted(
            attempts, key=lambda item: (item["created_at"], item["quiz_id"]), reverse=True
        )

    def progress(
        self, learner_id: str, document_id: str | None = None, owner: str | None = None
    ) -> dict:
        learner_id = _identifier(learner_id)
        topics = {}
        # Published attempts are immutable and atomically replaced. Readers need not
        # hold the submit lock; a concurrent new attempt appears on the next read.
        for path in (self.directory / "attempts" / learner_id).glob("*.json"):
            try:
                attempt = self._visible_attempt(path, learner_id, owner)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                LOGGER.warning("Skipping unreadable quiz attempt %s", path.name)
                continue
            except LookupError:
                continue
            if document_id is not None and attempt["document_id"] != document_id:
                continue
            for topic, values in attempt["topic_scores"].items():
                totals = topics.setdefault(topic, {"correct": 0, "total": 0})
                totals["correct"] += values["correct"]
                totals["total"] += values["total"]
        return {"topic_scores": topics}
