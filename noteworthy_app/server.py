"""Development HTTP app for the shared PDF, retrieval, and quiz prototype."""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .accounts import AccountError, Accounts, accounts_from_env
from .attempts import AttemptStore
from .core import build_prompt, extract_passages
from .models import generate_answer
from .question_generator import generate_questions
from .search import EmbeddingSearch, KeywordSearch, create_search
from .storage import load_documents, save_document, uploaded_at

STATIC_DIR = Path(__file__).parent / "static"
# Lower-case letters, digits and hyphens only, so a URL cannot name any other path.
# The length cap keeps names under every file system's limit.
STATIC_PAGE = re.compile(r"/([a-z0-9][a-z0-9-]{0,63}\.(?:html|css|js|svg))")
STATIC_TYPES = {
    "html": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "svg": "image/svg+xml",
}
QUIZ_ROUTE = re.compile(r"/api/quiz/([^/]+)")
ATTEMPT_ROUTE = re.compile(r"/api/attempts/([^/]+)")
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_PDF_BYTES * 2
DATA_ROUTES = {"/api/upload", "/api/ask", "/api/quiz", "/api/quiz/submit"}
# These exist only when accounts are on (CLASS_CODE is set).
ACCOUNT_ROUTES = {"/api/signup", "/api/login", "/api/logout"}
MAX_ACCOUNT_BYTES = 16 * 1024
# A refused request's body is read and dropped in pieces of this size, so the reply is not lost.
DRAIN_BYTES = 64 * 1024
LOG_IN = "Log in to continue."
PUBLIC_QUESTION_FIELDS = {
    "type",
    "question",
    "options",
    "choices",
    "source_id",
    "page",
    "source_page",
    "topic",
}


def _public_quiz(quiz: dict) -> dict:
    # Keep authoritative keys and excerpts on disk until the quiz is submitted.
    return {
        "quiz_id": quiz["quiz_id"],
        "document_id": quiz["document_id"],
        "questions": [
            {key: value for key, value in item.items() if key in PUBLIC_QUESTION_FIELDS}
            for item in quiz["questions"]
        ],
    }


class StudyServer(ThreadingHTTPServer):
    """Serve documents from one storage directory with an in-memory lookup."""

    def __init__(
        self,
        address: tuple[str, int],
        answer_generator: Callable[[str], str],
        data_dir: Path,
        search: KeywordSearch | EmbeddingSearch,
        accounts: Accounts | None = None,
    ):
        self.data_dir = data_dir
        self.documents = load_documents(data_dir)
        self.document_lock = threading.Lock()
        self.attempts = AttemptStore(data_dir)
        self.answer_generator = answer_generator
        self.search = search
        # None keeps the anonymous learner ID; otherwise every API call needs a session.
        self.accounts = accounts
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: StudyServer

    def _json(self, status: int, payload: dict, cookie: str | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _session_token(self) -> str | None:
        # Split by hand: SimpleCookie drops every cookie if any other cookie for this host
        # has a value it does not accept, such as JSON or a space.
        wanted = self.server.accounts.cookie_name
        tokens = []
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == wanted:
                tokens.append(value)
        # The server sets one cookie for this host only. A second one was set by another
        # site under the same parent domain, so neither is trusted.
        return tokens[0] if len(tokens) == 1 else None

    def _account(self) -> dict | None:
        """Return the logged-in account, or None when logged out or accounts are off."""
        if self.server.accounts is None:
            return None
        return self.server.accounts.current(self._session_token())

    def _drain(self) -> None:
        # Replying without reading the body can reset the connection before the client
        # reads the reply, so read and discard the body first, up to the request limit.
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if remaining > MAX_REQUEST_BYTES:
            return
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, DRAIN_BYTES))
            if not chunk:
                break
            remaining -= len(chunk)

    def _reject(self, status: int, message: str) -> None:
        self._drain()
        self._json(status, {"error": message})

    @staticmethod
    def _string(payload: dict, key: str, limit: int) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{key} must be a nonempty string of at most {limit} characters.")
        return value.strip()

    def _static(self, name: str) -> None:
        folder = STATIC_DIR.resolve()
        path = (folder / name).resolve()
        # The name pattern already blocks separators; this also stops links out of the folder.
        if path.parent != folder or not path.is_file():
            self._json(404, {"error": "Not found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", STATIC_TYPES[name.rsplit(".", 1)[1]])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        page = STATIC_PAGE.fullmatch(path)
        if path == "/" or page:
            self._static(page[1] if page else "index.html")
            return
        if path == "/health":
            self._json(200, {"status": "ok"})
            return
        # The learner ID comes in a header so it stays out of URLs and request logs.
        learner_id = self.headers.get("X-Learner-Id")
        # Documents and quizzes are visible only to their owner: an account ID, or None
        # without accounts (records from before accounts have no owner).
        account = owner = None
        try:
            if self.server.accounts is not None and path.startswith("/api/"):
                # Every API path needs a session, including ones added later.
                account = self._account()
                if account is None:
                    self._json(401, {"error": LOG_IN})
                    return
                # The account replaces the browser's learner ID.
                owner = learner_id = account["id"]
            if path == "/api/me" and account is not None:
                self._json(200, {"email": account["email"]})
            elif path == "/api/documents":
                with self.server.document_lock:
                    # Text is extracted during upload, so every stored document is ready to use.
                    documents = [
                        {
                            "id": key,
                            "name": name,
                            "passages": len(passages),
                            "status": "ready",
                            "uploaded_at": uploaded_at(self.server.data_dir, key),
                        }
                        for key, (name, passages, document_owner) in self.server.documents.items()
                        if document_owner == owner
                    ]
                self._json(200, {"documents": documents})
            elif path == "/api/progress":
                params = parse_qs(urlsplit(self.path).query)
                progress = self.server.attempts.progress(
                    learner_id, params.get("document_id", [None])[0]
                )
                self._json(200, progress)
            elif path == "/api/attempts":
                attempts = self.server.attempts.history(learner_id)
                with self.server.document_lock:
                    names = {
                        key: name
                        for key, (name, _, document_owner) in self.server.documents.items()
                        if document_owner == owner
                    }
                for item in attempts:
                    # Attempts outlive a removed document; they are listed without its name.
                    item["document_name"] = names.get(item["document_id"])
                self._json(200, {"attempts": attempts})
            elif quiz := QUIZ_ROUTE.fullmatch(path):
                self._json(200, _public_quiz(self.server.attempts.quiz(quiz[1], owner)))
            elif attempt := ATTEMPT_ROUTE.fullmatch(path):
                self._json(200, self.server.attempts.attempt(attempt[1], learner_id))
            else:
                self._json(404, {"error": "Not found"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})
        except LookupError as exc:
            self._json(404, {"error": str(exc)})
        except OSError:
            self.log_error("Could not read data from storage")
            self._json(500, {"error": "Could not read data. Please try again."})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        accounts = self.server.accounts
        if path not in DATA_ROUTES and (accounts is None or path not in ACCOUNT_ROUTES):
            self._json(404, {"error": "Not found"})
            return
        owner = None
        try:
            if accounts is not None:
                # A cross-site page cannot send this type without a CORS preflight, which
                # this server refuses, so it also guards against forged requests.
                if self.headers.get_content_type() != "application/json":
                    self._reject(415, "Send JSON with Content-Type: application/json.")
                    return
                if path == "/api/logout":
                    # Needs no body and no session, so logging out twice is fine.
                    self._drain()
                    accounts.log_out(self._session_token())
                    self._json(200, {"status": "logged out"}, accounts.cookie(None))
                    return
                if path not in ACCOUNT_ROUTES:
                    account = self._account()
                    if account is None:
                        self._reject(401, LOG_IN)
                        return
                    owner = account["id"]
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Content-Length must be a positive integer.")
            if path in ACCOUNT_ROUTES and length > MAX_ACCOUNT_BYTES:
                self._json(413, {"error": "Request is too large."})
                return
            if length > MAX_REQUEST_BYTES:
                self._json(413, {"error": "Request is too large. PDF limit is 12 MB."})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except RecursionError:
                # Deep nesting is bad input; as a RuntimeError it would become a 503.
                raise ValueError("Send a JSON object.") from None
            if not isinstance(payload, dict):
                raise ValueError("Send a JSON object.")
            if path == "/api/signup":
                user, token = accounts.sign_up(
                    payload.get("email"), payload.get("password"), payload.get("class_code")
                )
                # Signing up also logs the student in.
                self._json(201, user, accounts.cookie(token))
                return
            if path == "/api/login":
                user, token = accounts.log_in(payload.get("email"), payload.get("password"))
                self._json(200, user, accounts.cookie(token))
                return
            if path == "/api/quiz/submit":
                # With accounts on, the session decides whose attempt this is, not the body.
                learner_id = owner if owner is not None else payload.get("learner_id")
                result = self.server.attempts.submit(
                    payload.get("quiz_id"), learner_id, payload.get("answers"), owner
                )
                self._json(200, result)
                return
            if path == "/api/upload":
                name = self._string(payload, "name", 180)
                if not name.lower().endswith(".pdf"):
                    raise ValueError("Choose a PDF file.")
                if payload.get("data") == "":
                    raise ValueError("The file is empty. Choose a PDF that has content.")
                data = self._string(payload, "data", MAX_REQUEST_BYTES)
                pdf = base64.b64decode(data, validate=True)
                if len(pdf) > MAX_PDF_BYTES:
                    self._json(413, {"error": "PDF must be at most 12 MB."})
                    return
                passages = extract_passages(pdf)
                with self.server.document_lock:
                    document_id = save_document(self.server.data_dir, name, pdf, passages, owner)
                    self.server.documents[document_id] = (name, passages, owner)
                try:
                    self.server.search.index(document_id, passages)
                except RuntimeError:
                    # The upload is saved; the first question indexes the document again.
                    self.log_error("Could not index document %s for search", document_id)
                self._json(
                    200,
                    {
                        "id": document_id,
                        "name": name,
                        "passages": len(passages),
                        "status": "ready",
                        "uploaded_at": uploaded_at(self.server.data_dir, document_id),
                    },
                )
                return
            document_id = self._string(payload, "document_id", 64)
            with self.server.document_lock:
                document = self.server.documents.get(document_id)
            # Another student's document is not found either, before any model call.
            if document is None or document[2] != owner:
                self._json(404, {"error": "Upload the PDF again to continue."})
                return
            passages = document[1]
            if path == "/api/quiz":
                questions = generate_questions(passages, self.server.answer_generator)
                quiz = self.server.attempts.issue(document_id, questions, owner)
                self._json(200, _public_quiz(quiz))
                return
            question = self._string(payload, "question", 1000)
            matches = self.server.search.retrieve(document_id, passages, question)
            if not matches:
                self._json(200, {"answer": "The PDF does not say.", "sources": []})
                return
            answer = self.server.answer_generator(build_prompt(question, matches))
            self._json(
                200,
                {
                    "answer": answer,
                    "sources": [
                        {"id": passage.id, "page": passage.page, "text": passage.text}
                        for passage in matches
                    ],
                },
            )
        except AccountError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})
        except LookupError as exc:
            self._json(404, {"error": str(exc)})
        except OSError:
            self.log_error("Could not save data to storage")
            self._json(500, {"error": "Could not save data. Please try again."})


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    answer_generator: Callable[[str], str] = generate_answer,
    data_dir: str | Path | None = None,
    search: KeywordSearch | EmbeddingSearch | None = None,
) -> StudyServer:
    directory = Path(
        data_dir if data_dir is not None else os.environ.get("DOCUMENTS_DIR", "data/documents")
    )
    return StudyServer(
        (host, port),
        answer_generator,
        directory,
        search if search is not None else create_search(),
        accounts_from_env(directory),
    )


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    with create_server(host, port) as server:
        print(f"Study companion ready at http://{host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
