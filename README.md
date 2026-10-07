# AI Study Companion

AI Study Companion is a Senior Design project focused on developing an AI-powered study tool that uses students' course materials to support personalized learning.

## Team

**NOTEWORTHY – Team 26-27**

| Member | Role |
|---|---|
| Fallou Samb | Product Owner / QA |
| David Huynh | Backend / Scrum Master |
| John Cobio | Backend |
| Sunil Thapa Magaris | AI/ML |
| James Nguyen | Frontend |

## Project Goals

The AI Study Companion will explore features such as:

- Importing student study materials
- OneDrive and Canvas integration
- Retrieval-Augmented Generation (RAG)
- OCR for typed and handwritten notes
- AI-generated study questions and quizzes
- Quiz scoring and student progress tracking
- and more...

## Development Workflow

1. Do not develop directly on `main`.
2. Create a branch for your task.
3. Make and test your changes.
4. Push your branch to GitHub.
5. Open a Pull Request into `main`.
6. Have another team member review the Pull Request (or do it yourself).
7. Merge the Pull Request after review.

## Project Status

Currently in **Sprint 2**. This scaffold starts
[SCRUM-56](https://noteworthy-uta.atlassian.net/browse/SCRUM-56) by bringing the
available Python PDF, retrieval, and quiz prototype into one runnable app.
The remaining team prototypes still need to be integrated.

## Local setup

Requires **Python 3.11 or newer**. Check `python3 --version` first; on macOS,
use a newer installed Python if the system version is older than 3.11.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps -e .
cp .env.example .env
set -a; source .env; set +a
make run PYTHON=python
```

Open <http://127.0.0.1:8000>. Upload a typed PDF to extract its text and generate
a five-question practice quiz. Answer generation additionally requires Ollama
running at `OLLAMA_URL` with the model named by `OLLAMA_MODEL` available. The
defaults match the existing prototype. An unavailable model returns HTTP 503;
startup, health checks, extraction, quizzes, and automated tests do not require
Ollama, AWS credentials, or student files.

The `.env` file is loaded by the shell commands above, not automatically by the
application. `HOST` and `PORT` control the bind address. The installed command
`noteworthy-app` and `python -m noteworthy_app.server` are equivalent entry points.

## Checks and packaging

After setup, these commands work locally and can be used by CI:

```bash
make check PYTHON=python          # Ruff lint/format check and offline unittest suite
make test PYTHON=python           # tests only
make build PYTHON=python          # wheel and source distribution in dist/
```

Use `make format PYTHON=python` to apply formatting and import fixes. An optional
browser-logic regression test runs with Node.js when installed, and is explicitly
skipped otherwise; Node is not required to run the application. Tests use
synthetic PDFs and a local test model server; they never call Ollama or AWS.
The wheel includes the HTML interface, so an installed app can run outside the
source checkout. Runtime dependencies are pinned in `requirements.txt`, and
developer tools are pinned in `requirements-dev.txt`.

## Application structure

| Module | Responsibility |
|---|---|
| `noteworthy_app/core.py` | Typed PDF extraction, passage retrieval, and cited answer prompts |
| `noteworthy_app/quiz.py` | Five source-traceable multiple-choice cloze questions |
| `noteworthy_app/models.py` | Ollama adapter exposing `generate_answer(prompt) -> str` |
| `noteworthy_app/server.py` | HTTP routes, validation, and in-memory document storage |
| `noteworthy_app/static/index.html` | Existing upload, answer, quiz, and browser-local score interface |
| `tests/` | Offline extraction, quiz, model adapter, and HTTP behavior checks |

The server accepts an answer generator through `create_server(...)`; this keeps
the model adapter replaceable for the Bedrock work in SCRUM-60.

| Route | Behavior |
|---|---|
| `GET /` | Serve the study interface |
| `GET /health` | Return `{"status":"ok"}` without calling external services |
| `GET /api/documents` | List documents uploaded to this server instance |
| `POST /api/upload` | Accept `name` and base64 PDF `data`; return document ID and passage count |
| `POST /api/ask` | Accept `document_id` and `question`; return answer and source passages |
| `POST /api/quiz` | Accept `document_id`; return five questions with options and source IDs |

The PDF limit is 12 MiB. Invalid JSON and fields return 400, missing routes or
documents return 404, oversized uploads return 413, and model failures return 503.

## Prototype limits and Sprint 2 handoff

This is a local development baseline. Documents are shared within one server
instance, stored only in memory, and lost on restart; scores live in browser
local storage. There is no login, per-student isolation, database, OCR, or
OneDrive/Canvas import in this scaffold. It retains lexical passage retrieval
and the existing cloze quiz rather than claiming all team prototypes are merged.

The follow-up order under SCRUM-11 is:

1. Complete remaining prototype integration under SCRUM-56 (John).
2. Prepare AWS access under SCRUM-57 (David), alongside app integration.
3. Containerize the app under SCRUM-58 (David). Set `HOST=0.0.0.0` in a container;
   the local default binds only to loopback.
4. Run the check commands and Docker build under SCRUM-59 (David).
5. Replace model access with Bedrock under SCRUM-60 (Sunil), in parallel.
6. Deploy staging under SCRUM-61 (David), then record the actual deployment
   commands and URL under SCRUM-62 (John).

## Continuous integration

The existing `.github/workflows/ci.yml` runs on pushes and pull requests and
currently checks only that the README exists. SCRUM-59 will wire the application
checks and Docker build into that workflow. Docker, AWS, Bedrock, and staging
deployment are still outstanding; this scaffold does not complete SCRUM-11.
