"""Student accounts: class-code sign-up, scrypt password hashes, and login sessions."""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .storage import _atomic_write

LOGGER = logging.getLogger(__name__)

COOKIE_NAME = "noteworthy_session"
# Browsers accept a __Host- cookie only from this exact host, over HTTPS, with Path=/ and
# no Domain, so another *.sslip.io site cannot set or replace it.
SECURE_COOKIE_NAME = f"__Host-{COOKIE_NAME}"
# OWASP's equal-cost choice to N=2**17, r=8, p=1, with less memory: OpenSSL runs the
# p lanes one after another in the same 16 MiB.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 5
# The parameters above need 16 MiB. OpenSSL refuses more than 32 MiB unless told,
# so a later, larger N keeps working.
SCRYPT_MAXMEM = 64 * 1024 * 1024
# Each hash holds 16 MiB and the server starts a thread per request, so at most this
# many hashes run at once; other logins wait their turn.
HASH_SLOTS = threading.BoundedSemaphore(2)
MIN_CLASS_CODE = 16
LOGIN_LIMIT = 5
LOGIN_WINDOW = 15 * 60
KEY_BYTES = 32
SALT_BYTES = 16
MIN_PASSWORD = 8
MAX_PASSWORD = 1024
MAX_EMAIL = 254
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# The format of secrets.token_urlsafe(32), checked before any file lookup.
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
SESSION_KEY = re.compile(r"[0-9a-f]{64}")
LOGIN_FAILED = "Email or password is not correct."
TOO_MANY = "Too many attempts. Try again later."


class AccountError(Exception):
    """An account request the server answers with its own status (401, 403, 409 or 429).

    It is not an OSError, ValueError or LookupError, so the server's general error
    mapping never turns it into a 500, 400 or 404.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _utf8(text: str) -> bytes:
    # JSON can carry lone surrogates; keep them instead of failing, so hashing never raises.
    return text.encode("utf-8", "surrogatepass")


def hash_password(password: str) -> str:
    """Return an scrypt hash with its parameters, so they can be raised later."""
    salt = secrets.token_bytes(SALT_BYTES)
    with HASH_SLOTS:
        key = hashlib.scrypt(
            _utf8(password),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            maxmem=SCRYPT_MAXMEM,
            dklen=KEY_BYTES,
        )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        expected = bytes.fromhex(key)
        if scheme != "scrypt" or not expected:
            return False
        with HASH_SLOTS:
            actual = hashlib.scrypt(
                _utf8(password),
                salt=bytes.fromhex(salt),
                n=int(n),
                r=int(r),
                p=int(p),
                maxmem=SCRYPT_MAXMEM,
                dklen=len(expected),
            )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_email(value) -> str:
    if isinstance(value, str):
        email = value.strip().lower()
        if len(email) <= MAX_EMAIL and EMAIL.fullmatch(email):
            return email
    raise ValueError("Enter a valid email address.")


def cookie_name(secure: bool) -> str:
    return SECURE_COOKIE_NAME if secure else COOKIE_NAME


def session_cookie(token: str | None, max_age: int, secure: bool) -> str:
    """Return a Set-Cookie value; no token gives an empty cookie that the browser removes."""
    value = f"{cookie_name(secure)}={token or ''}; Max-Age={max_age if token else 0}"
    return f"{value}; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


def token_hash(token: str) -> str:
    # The token has 256 random bits, so a fast hash is enough. Only the hash is
    # stored, so a copy of the sessions folder cannot be used to log in.
    return hashlib.sha256(token.encode()).hexdigest()


class AccountStore(Protocol):
    """Where accounts and sessions are kept: FileAccountStore, or PostgresAccountStore in
    database.py when DATABASE_URL is set.

    Records:
    - account: {"id": 32 lowercase hex characters, "email": normalized (trimmed and
      lowercased), "password_hash": the hash_password text, "created_at": ISO 8601 text}
    - session, saved under the SHA-256 of its token (64 lowercase hex characters):
      {"account_id": the account's id, "email": the account's email,
      "expires_at": int Unix seconds, not a datetime}

    Errors: the server answers RuntimeError with 503 and OSError with 500, and any other
    exception closes the connection with no reply. So wrap driver errors (for example a
    psycopg error) in RuntimeError when storage cannot be reached or a record cannot be
    read, and raise AccountError(409) for an email that is already taken.
    """

    def add_account(self, account: dict) -> None:
        """Save a new account; raise AccountError(409) if its email is taken."""

    def find_account(self, email: str) -> dict | None:
        """Return the account for a normalized email, or None if there is none.

        Raise RuntimeError for a record that cannot be read, so the student gets a server
        error instead of a wrong password message.
        """

    def add_session(self, key: str, session: dict) -> None: ...

    def find_session(self, key: str) -> dict | None:
        """Return the session, or None if there is none or it cannot be read.

        An unreadable session is treated as logged out, so the student logs in again.
        """

    def delete_session(self, key: str) -> None:
        """Delete the session; deleting one that does not exist is not an error."""


class FileAccountStore:
    """Accounts and sessions as JSON files, for one application process."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()

    def _account_path(self, email: str) -> Path:
        # A hash of the email names the file, so user input never becomes a path.
        return self.directory / "users" / f"{hashlib.sha256(_utf8(email)).hexdigest()}.json"

    def _session_path(self, key: str) -> Path:
        if not isinstance(key, str) or not SESSION_KEY.fullmatch(key):
            raise ValueError("Session key must be 64 lowercase hexadecimal characters.")
        return self.directory / "sessions" / f"{key}.json"

    @staticmethod
    def _save(path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(record).encode())

    def add_account(self, account: dict) -> None:
        path = self._account_path(account["email"])
        # Checking and writing under one lock keeps each email unique in this process.
        with self.lock:
            if path.exists():
                raise AccountError(409, "An account with this email already exists.")
            self._save(path, account)

    def find_account(self, email: str) -> dict | None:
        try:
            account = json.loads(self._account_path(email).read_text(encoding="utf-8"))
            if (
                not isinstance(account, dict)
                or account["email"] != email
                or not isinstance(account["password_hash"], str)
                or not ACCOUNT_ID.fullmatch(account["id"])
            ):
                raise ValueError("Invalid saved account.")
        except FileNotFoundError:
            return None
        except (ValueError, RecursionError, KeyError, TypeError) as exc:
            raise RuntimeError("Saved account could not be read.") from exc
        return account

    def add_session(self, key: str, session: dict) -> None:
        path = self._session_path(key)
        with self.lock:
            self._save(path, session)

    def find_session(self, key: str) -> dict | None:
        path = self._session_path(key)
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(session, dict)
                or not ACCOUNT_ID.fullmatch(session["account_id"])
                or not isinstance(session["email"], str)
                or type(session["expires_at"]) is not int
            ):
                raise ValueError("Invalid saved session.")
        except FileNotFoundError:
            return None
        except (ValueError, RecursionError, KeyError, TypeError):
            # The file name is not logged: it is the stored form of a login token.
            LOGGER.warning("Ignoring an unreadable login session")
            return None
        return session

    def delete_session(self, key: str) -> None:
        self._session_path(key).unlink(missing_ok=True)


class Accounts:
    """Sign-up, login and sessions on top of an AccountStore."""

    def __init__(
        self, store: AccountStore, class_code: str, session_seconds: int, cookie_secure: bool
    ):
        self.store = store
        self.class_code = class_code
        self.session_seconds = session_seconds
        self.cookie_secure = cookie_secure
        self.cookie_name = cookie_name(cookie_secure)
        # Unknown emails are checked against this hash, so they take as long as a wrong password.
        self.dummy_hash = hash_password(secrets.token_urlsafe())
        # Login tries per email in the current window: SHA-256 of the email to
        # (tries, window start). Kept in memory, so a restart clears it.
        self.tries: dict[bytes, tuple[int, float]] = {}
        self.tries_lock = threading.Lock()

    def sign_up(self, email, password, class_code) -> tuple[dict, str]:
        if not all(isinstance(value, str) for value in (email, password, class_code)):
            raise ValueError("email, password and class_code must be strings.")
        # Checked first, so people without the code learn nothing about which emails exist.
        if not hmac.compare_digest(_utf8(class_code.strip()), _utf8(self.class_code)):
            raise AccountError(403, "The class code is not correct.")
        email = normalize_email(email)
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
        if len(password) > MAX_PASSWORD:
            raise ValueError(f"Password must be at most {MAX_PASSWORD} characters.")
        account = {
            "id": uuid4().hex,
            "email": email,
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.add_account(account)
        return {"email": email}, self._new_session(account)

    def log_in(self, email, password) -> tuple[dict, str]:
        if not isinstance(email, str) or not isinstance(password, str):
            raise ValueError("email and password must be strings.")
        email = email.strip().lower()
        key = hashlib.sha256(_utf8(email)).digest()
        self._count_try(key)
        account = self.store.find_account(email)
        stored = account["password_hash"] if account is not None else self.dummy_hash
        # One message for an unknown email and a wrong password, so neither is revealed.
        if not verify_password(password, stored) or account is None:
            raise AccountError(401, LOGIN_FAILED)
        with self.tries_lock:
            self.tries.pop(key, None)
        return {"email": account["email"]}, self._new_session(account)

    def _count_try(self, key: bytes) -> None:
        """Allow LOGIN_LIMIT tries per email in each LOGIN_WINDOW, before any hashing.

        A try counts when it starts, so parallel requests cannot get past the limit, and a
        successful login clears the count. Unknown emails are counted the same way, so a
        429 does not show whether an account exists.
        """
        now = time.time()
        with self.tries_lock:
            # Drop windows that have ended, so the table only holds recent emails.
            for old in [k for k, (_, start) in self.tries.items() if now - start >= LOGIN_WINDOW]:
                del self.tries[old]
            count, start = self.tries.get(key, (0, now))
            if count >= LOGIN_LIMIT:
                raise AccountError(429, TOO_MANY)
            self.tries[key] = (count + 1, start)

    def log_out(self, token: str | None) -> None:
        if isinstance(token, str) and TOKEN.fullmatch(token):
            self.store.delete_session(token_hash(token))

    def current(self, token: str | None) -> dict | None:
        """Return the logged-in account's id and email, or None."""
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        key = token_hash(token)
        session = self.store.find_session(key)
        if session is None:
            return None
        if session["expires_at"] <= time.time():
            self.store.delete_session(key)
            return None
        return {"id": session["account_id"], "email": session["email"]}

    def _new_session(self, account: dict) -> str:
        token = secrets.token_urlsafe(32)
        # The expiry is fixed at login and does not move with use.
        self.store.add_session(
            token_hash(token),
            {
                "account_id": account["id"],
                "email": account["email"],
                "expires_at": int(time.time()) + self.session_seconds,
            },
        )
        return token

    def cookie(self, token: str | None) -> str:
        return session_cookie(token, self.session_seconds, self.cookie_secure)


def accounts_from_env(directory: Path, store: AccountStore | None = None) -> Accounts | None:
    """Turn accounts on only when CLASS_CODE is set; otherwise return None.

    Accounts are kept in store, or as files in directory/accounts without one.
    """
    class_code = os.environ.get("CLASS_CODE", "").strip()
    if not class_code:
        return None
    # Wrong codes cost the server almost nothing to check, so the code itself must be
    # too long to guess.
    if len(class_code) < MIN_CLASS_CODE:
        raise ValueError(f"CLASS_CODE must be at least {MIN_CLASS_CODE} characters.")
    days = os.environ.get("SESSION_DAYS", "").strip() or "7"
    if not re.fullmatch(r"[0-9]+", days) or int(days) < 1:
        raise ValueError("SESSION_DAYS must be a whole number of days, at least 1.")
    return Accounts(
        store if store is not None else FileAccountStore(directory / "accounts"),
        class_code,
        int(days) * 24 * 60 * 60,
        os.environ.get("COOKIE_SECURE", "").strip() == "1",
    )
