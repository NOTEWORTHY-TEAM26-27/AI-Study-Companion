"""Development HTTP app for the shared PDF, retrieval, and quiz prototype."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import Passage, build_prompt, extract_passages, retrieve
from .models import generate_answer
from .quiz import generate_quiz

STATIC = Path(__file__).parent / "static" / "index.html"
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_PDF_BYTES * 2


class StudyServer(ThreadingHTTPServer):
    """Keep uploaded documents local to this process/server instance."""

    def __init__(self, address: tuple[str, int], answer_generator: Callable[[str], str]):
        self.documents: dict[str, tuple[str, list[Passage]]] = {}
        self.document_lock = threading.Lock()
        self.answer_generator = answer_generator
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: StudyServer

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _string(payload: dict, key: str, limit: int) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{key} must be a nonempty string of at most {limit} characters.")
        return value.strip()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            body = STATIC.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/health":
            self._json(200, {"status": "ok"})
        elif path == "/api/documents":
            with self.server.document_lock:
                documents = [
                    {"id": key, "name": name, "passages": len(passages)}
                    for key, (name, passages) in self.server.documents.items()
                ]
            self._json(200, {"documents": documents})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/api/upload", "/api/ask", "/api/quiz"}:
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Content-Length must be a positive integer.")
            if length > MAX_REQUEST_BYTES:
                self._json(413, {"error": "Request is too large. PDF limit is 12 MB."})
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Send a JSON object.")
            if path == "/api/upload":
                name = self._string(payload, "name", 180)
                data = self._string(payload, "data", MAX_REQUEST_BYTES)
                if not name.lower().endswith(".pdf"):
                    raise ValueError("Choose a PDF file.")
                pdf = base64.b64decode(data, validate=True)
                if len(pdf) > MAX_PDF_BYTES:
                    self._json(413, {"error": "PDF must be at most 12 MB."})
                    return
                passages = extract_passages(pdf)
                document_id = hashlib.sha256(pdf).hexdigest()[:16]
                with self.server.document_lock:
                    self.server.documents[document_id] = (name, passages)
                self._json(200, {"id": document_id, "name": name, "passages": len(passages)})
                return
            document_id = self._string(payload, "document_id", 64)
            with self.server.document_lock:
                document = self.server.documents.get(document_id)
            if document is None:
                self._json(404, {"error": "Upload the PDF again to continue."})
                return
            passages = document[1]
            if path == "/api/quiz":
                self._json(200, {"questions": generate_quiz(passages)})
                return
            question = self._string(payload, "question", 1000)
            matches = retrieve(passages, question)
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
        except (TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    answer_generator: Callable[[str], str] = generate_answer,
) -> StudyServer:
    return StudyServer((host, port), answer_generator)


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
