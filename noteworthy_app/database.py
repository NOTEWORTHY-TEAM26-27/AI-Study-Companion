"""PostgreSQL stores for accounts, sessions, quizzes and attempts (SCRUM-225).

The app uses them when DATABASE_URL is set and the file stores otherwise. psycopg is
imported only when a Database is made, so the app and the tests run without it.
"""

import importlib
import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .accounts import SESSION_KEY, AccountError
from .attempts import _check_attempt, _check_quiz, _identifier
from .grading import grade_quiz

LOGGER = logging.getLogger(__name__)

UNAVAILABLE = "The database is not available. Try again later."
# Seconds to wait for a new connection before the request gets a 503.
CONNECT_TIMEOUT = 5
# Most connections one app process opens at once; other requests wait their turn. Staging
# allows 20 (compose.staging.yaml), which leaves room for backups and psql.
MAX_CONNECTIONS = 10
# Seconds a request waits for one of those connections before it gets a 503.
SLOT_TIMEOUT = 5
# Milliseconds a statement may wait for a lock, and may run in all, before PostgreSQL stops
# it and the request gets a 503. Without them, a lock held by a psql session left in an
# open transaction would hold every request, and then every connection, until released.
LOCK_TIMEOUT_MS = 5000
STATEMENT_TIMEOUT_MS = 10000
TABLES = (
    """CREATE TABLE IF NOT EXISTS accounts (
        id text PRIMARY KEY,
        email text NOT NULL UNIQUE,
        password_hash text NOT NULL,
        created_at timestamptz NOT NULL
    )""",
    # key is the SHA-256 of the login token; the token itself is never stored.
    """CREATE TABLE IF NOT EXISTS sessions (
        key text PRIMARY KEY,
        account_id text NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        email text NOT NULL,
        expires_at bigint NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions (expires_at)",
    # owner is the account ID, or NULL for quizzes made with accounts off.
    """CREATE TABLE IF NOT EXISTS quizzes (
        quiz_id text PRIMARY KEY,
        document_id text NOT NULL,
        owner text,
        questions json NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )""",
    # The primary key allows one attempt per learner and quiz. result is the submit
    # response; json, unlike jsonb, keeps it exactly as written, key order included.
    """CREATE TABLE IF NOT EXISTS attempts (
        learner_id text NOT NULL,
        quiz_id text NOT NULL,
        document_id text NOT NULL,
        created_at timestamptz NOT NULL,
        result json NOT NULL,
        PRIMARY KEY (learner_id, quiz_id)
    )""",
)


def _storable(text: str) -> bool:
    # PostgreSQL text is UTF-8 without NUL characters, but JSON input can hold both.
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class Database:
    """Open one short connection for each store operation.

    Each operation connects, runs in one transaction, commits and closes. On the Docker
    network a connection takes a few milliseconds, and it keeps things simple: threads of
    the threaded HTTP server never share a connection, and a database restart leaves no
    broken connection behind. A semaphore caps how many are open at once, and every wait
    has a time limit, so a stuck database gives 503s instead of hanging requests.
    """

    def __init__(self, url: str):
        try:
            self.psycopg = importlib.import_module("psycopg")
        except ImportError as exc:
            raise RuntimeError(
                "DATABASE_URL is set, but psycopg is not installed. "
                "Run: python -m pip install -r requirements.txt"
            ) from exc
        self.url = url
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        with self.connect() as conn:
            # App processes that start together take turns creating the tables; any fixed
            # number names the lock.
            conn.execute("SELECT pg_advisory_xact_lock(4316225)")
            for statement in TABLES:
                conn.execute(statement)

    @contextmanager
    def connect(self):
        """Yield a connection in a transaction that commits on success and rolls back on error.

        Driver errors, timeouts included, become RuntimeError, which the server answers
        with 503. The URL and query values are never logged.
        """
        if not self.slots.acquire(timeout=SLOT_TIMEOUT):
            LOGGER.error("Database error: no free connection after %s seconds", SLOT_TIMEOUT)
            raise RuntimeError(UNAVAILABLE)
        try:
            with self.psycopg.connect(self.url, connect_timeout=CONNECT_TIMEOUT) as conn:
                # Set for this transaction only. As connection options they would replace
                # any options in the URL, such as the tests' search_path.
                conn.execute(
                    "SELECT set_config('lock_timeout', %s, true),"
                    " set_config('statement_timeout', %s, true)",
                    (f"{LOCK_TIMEOUT_MS}ms", f"{STATEMENT_TIMEOUT_MS}ms"),
                )
                yield conn
        except self.psycopg.Error as exc:
            LOGGER.error("Database error: %s (SQLSTATE %s)", type(exc).__name__, exc.sqlstate)
            raise RuntimeError(UNAVAILABLE) from exc
        finally:
            self.slots.release()


class PostgresAccountStore:
    """Accounts and sessions in PostgreSQL (see AccountStore in accounts.py).

    The email column is unique, so each email has one account even with several app
    processes.
    """

    def __init__(self, database: Database):
        self.database = database

    def add_account(self, account: dict) -> None:
        if not _storable(account["email"]):
            raise ValueError("Enter a valid email address.")
        with self.database.connect() as conn:
            added = conn.execute(
                "INSERT INTO accounts (id, email, password_hash, created_at)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (email) DO NOTHING RETURNING id",
                (
                    account["id"],
                    account["email"],
                    account["password_hash"],
                    datetime.fromisoformat(account["created_at"]),
                ),
            ).fetchone()
        if added is None:
            raise AccountError(409, "An account with this email already exists.")

    def find_account(self, email: str) -> dict | None:
        if not _storable(email):
            return None
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id, email, password_hash, created_at FROM accounts WHERE email = %s",
                (email,),
            ).fetchone()
        if row is None:
            return None
        account_id, email, password_hash, created_at = row
        return {
            "id": account_id,
            "email": email,
            "password_hash": password_hash,
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
        }

    @staticmethod
    def _key(key: str) -> str:
        if not isinstance(key, str) or not SESSION_KEY.fullmatch(key):
            raise ValueError("Session key must be 64 lowercase hexadecimal characters.")
        return key

    def add_session(self, key: str, session: dict) -> None:
        key = self._key(key)
        with self.database.connect() as conn:
            # Expired sessions are removed at each login, so unused ones do not pile up.
            conn.execute("DELETE FROM sessions WHERE expires_at <= %s", (int(time.time()),))
            conn.execute(
                "INSERT INTO sessions (key, account_id, email, expires_at)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (key) DO UPDATE SET"
                " account_id = EXCLUDED.account_id, email = EXCLUDED.email,"
                " expires_at = EXCLUDED.expires_at",
                (key, session["account_id"], session["email"], session["expires_at"]),
            )

    def find_session(self, key: str) -> dict | None:
        key = self._key(key)
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT account_id, email, expires_at FROM sessions WHERE key = %s", (key,)
            ).fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "email": row[1], "expires_at": row[2]}

    def delete_session(self, key: str) -> None:
        key = self._key(key)
        with self.database.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE key = %s", (key,))


def _load(text: str):
    try:
        return json.loads(text)
    except RecursionError as exc:
        # Deeply nested JSON is just another unreadable record, not a server error.
        raise ValueError("Invalid saved JSON.") from exc


class PostgresAttemptStore:
    """Issued quizzes and attempts in PostgreSQL, with the same methods as AttemptStore."""

    def __init__(self, database: Database):
        self.database = database

    def issue(self, document_id: str, questions: list[dict], owner: str | None = None) -> dict:
        quiz_id = uuid4().hex
        quiz = {"quiz_id": quiz_id, "document_id": document_id, "questions": questions}
        if owner is not None:
            quiz["owner"] = owner
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO quizzes (quiz_id, document_id, owner, questions)"
                " VALUES (%s, %s, %s, %s::json)",
                (quiz_id, document_id, owner, json.dumps(questions)),
            )
        return quiz

    @staticmethod
    def _quiz(conn, quiz_id: str, owner: str | None) -> dict:
        row = conn.execute(
            "SELECT document_id, owner, questions::text FROM quizzes WHERE quiz_id = %s",
            (quiz_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Quiz not found. Generate a new quiz.")
        document_id, saved_owner, questions = row
        try:
            questions = _load(questions)
        except ValueError as exc:
            raise RuntimeError("Saved quiz could not be read.") from exc
        quiz = {"quiz_id": quiz_id, "document_id": document_id, "questions": questions}
        if saved_owner is not None:
            quiz["owner"] = saved_owner
        _check_quiz(quiz, quiz_id)
        # Owner is an account ID, or None without accounts. Another student's quiz is not found.
        if saved_owner != owner:
            raise LookupError("Quiz not found. Generate a new quiz.")
        return quiz

    def quiz(self, quiz_id: str, owner: str | None = None) -> dict:
        """Return an issued quiz with its keys; the server filters what it sends."""
        quiz_id = _identifier(quiz_id)
        with self.database.connect() as conn:
            return self._quiz(conn, quiz_id, owner)

    @staticmethod
    def _attempt(conn, quiz_id: str, learner_id: str, owner: str | None) -> dict | None:
        row = conn.execute(
            "SELECT a.result::text FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id"
            " WHERE a.learner_id = %s AND a.quiz_id = %s AND q.owner IS NOT DISTINCT FROM %s",
            (learner_id, quiz_id, owner),
        ).fetchone()
        if row is None:
            return None
        try:
            return _check_attempt(_load(row[0]), learner_id, quiz_id)
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError("Saved attempt could not be read.") from exc

    def submit(
        self, quiz_id: str, learner_id: str, answers: list, owner: str | None = None
    ) -> dict:
        quiz_id, learner_id = _identifier(quiz_id), _identifier(learner_id)
        with self.database.connect() as conn:
            # Check ownership before returning a previous submission.
            quiz = self._quiz(conn, quiz_id, owner)
            saved = self._attempt(conn, quiz_id, learner_id, owner)
            if saved is not None:
                return saved
            created_at = datetime.now(timezone.utc)
            result = {
                **grade_quiz(quiz["questions"], answers),
                "quiz_id": quiz_id,
                "learner_id": learner_id,
                "document_id": quiz["document_id"],
                "created_at": created_at.isoformat(),
            }
            # If a concurrent submit saved its attempt first, the primary key makes this
            # insert do nothing, and that attempt is returned instead. In READ COMMITTED the
            # next statement sees it: the insert waited for the other transaction to commit.
            added = conn.execute(
                "INSERT INTO attempts (learner_id, quiz_id, document_id, created_at, result)"
                " VALUES (%s, %s, %s, %s, %s::json)"
                " ON CONFLICT (learner_id, quiz_id) DO NOTHING RETURNING quiz_id",
                (learner_id, quiz_id, quiz["document_id"], created_at, json.dumps(result)),
            ).fetchone()
            if added is None:
                return self._attempt(conn, quiz_id, learner_id, owner)
        return result

    def attempt(self, quiz_id: str, learner_id: str, owner: str | None = None) -> dict:
        """Return a saved attempt exactly as submit() returned it."""
        quiz_id, learner_id = _identifier(quiz_id), _identifier(learner_id)
        with self.database.connect() as conn:
            attempt = self._attempt(conn, quiz_id, learner_id, owner)
        if attempt is None:
            raise LookupError("Quiz attempt not found.")
        return attempt

    def _attempts(
        self, learner_id: str, document_id: str | None = None, owner: str | None = None
    ) -> list[dict]:
        """Return a learner's readable attempts, oldest first; skip unreadable ones."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT a.quiz_id, a.result::text FROM attempts a"
                " JOIN quizzes q ON q.quiz_id = a.quiz_id"
                " WHERE a.learner_id = %(learner)s AND q.owner IS NOT DISTINCT FROM %(owner)s"
                " AND (%(document)s::text IS NULL OR a.document_id = %(document)s)"
                " ORDER BY a.created_at, a.quiz_id",
                {"learner": learner_id, "document": document_id, "owner": owner},
            ).fetchall()
        attempts = []
        for quiz_id, text in rows:
            try:
                attempts.append(_check_attempt(_load(text), learner_id, quiz_id))
            except (ValueError, TypeError, KeyError):
                LOGGER.warning("Skipping unreadable quiz attempt %s", quiz_id)
        return attempts

    def history(self, learner_id: str, owner: str | None = None) -> list[dict]:
        """Summarize a learner's saved attempts, newest first."""
        learner_id = _identifier(learner_id)
        return [
            {
                key: attempt[key]
                for key in ("quiz_id", "document_id", "correct", "total", "score", "created_at")
            }
            for attempt in reversed(self._attempts(learner_id, owner=owner))
        ]

    def progress(
        self, learner_id: str, document_id: str | None = None, owner: str | None = None
    ) -> dict:
        learner_id = _identifier(learner_id)
        topics = {}
        # A document ID PostgreSQL cannot store matches no saved attempt.
        if document_id is not None and not _storable(document_id):
            return {"topic_scores": topics}
        for attempt in self._attempts(learner_id, document_id, owner):
            for topic, values in attempt["topic_scores"].items():
                totals = topics.setdefault(topic, {"correct": 0, "total": 0})
                totals["correct"] += values["correct"]
                totals["total"] += values["total"]
        return {"topic_scores": topics}
