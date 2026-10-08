# AI Study Companion

AI Study Companion is a Senior Design project focused on developing an AI-powered study tool that uses students' course materials to support personalized learning.

## Team

**NOTEWORTHY – Team 26-27**

| Member | Role |
|---|---|
| Fallou Samb | Product Owner / QA |
| David Huynh | Backend / Scrum Master |
| John Cobio | Backend |
| Sunil Thapa Magar | AI/ML |
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
Ollama, cloud credentials, or student files.

The `.env` file is loaded by the shell commands above, not automatically by the
application. `HOST` and `PORT` control the bind address. The installed command
`noteworthy-app` and `python -m noteworthy_app.server` are equivalent entry points.

## Uploaded document storage

`DOCUMENTS_DIR` selects the storage directory, defaulting to `data/documents`
relative to the directory where you start the app. Each successful upload saves
the original `<document-id>.pdf` and a `<document-id>.json` file containing its
display name and extracted passages. The ID is derived from the PDF content;
the uploaded filename is never used as a disk path. Uploading the same PDF again
keeps the same ID and updates its display name.

On startup the app restores saved passages into memory, so the document list,
answer, and quiz APIs work after restarting without another upload or text
extraction. Invalid PDFs are rejected before saving. Storage failures return
HTTP 500 without publishing a new document; unreadable or incomplete stored
records are skipped on startup with a warning. Writes replace each file
atomically, with metadata saved last so an interrupted first upload is not
listed as complete. The default `data/` directory is excluded from Git.

This is file storage for one application process. In a container,
`DOCUMENTS_DIR` must point to a writable persistent volume; files in a
disposable container's temporary filesystem will not survive its replacement.
Compose sets it to `/data/documents`, backed by the `documents` volume, so on
the staging Droplet uploads stay in that volume (see `deploy/STAGING.md`).

## Docker and Compose

Requires a running Docker engine and Docker Compose v2 or newer. From the
repository root:

```bash
docker compose up --build -d --wait
docker compose logs -f app
```

Open <http://127.0.0.1:8000>. The app runs as a non-root user, binds to all
interfaces inside the container, and is published only on your computer's
loopback interface. The image checks `/health` without calling Ollama. PDF
extraction and quizzes work without a model service.

Compose mounts the named `documents` volume at `/data/documents`. Uploaded PDFs
and extracted passages survive container recreation and `docker compose down`.
This volume is separate from the local app's `data/documents` directory. Compose
does not copy existing local uploads into it.

```bash
docker compose up -d --force-recreate --wait   # replace the app, keeping documents
docker compose down                          # stop/remove containers, keeping documents
```

`docker compose down --volumes` deletes the stored uploads. Use that option only
when you intend to reset the data. A Docker volume stays on its Docker host, so
the staging Droplet has its own `documents` volume, separate from the one on
your computer.

Compose automatically reads `.env` for variable substitution. `COMPOSE_PORT`
changes the host port (for example, `COMPOSE_PORT=8080 docker compose up -d`).
The container's internal port remains 8000. `COMPOSE_OLLAMA_URL` controls the
container's model endpoint separately from the local app's `OLLAMA_URL`:

```bash
COMPOSE_OLLAMA_URL=http://host.docker.internal:11434/api/generate docker compose up -d
```

The default reaches Ollama running on the host. Ollama must accept connections
from the Docker network; a service bound only to host loopback may need its
listen address configured. `OLLAMA_MODEL` selects the model as before. No Ollama
container or model download is added by this setup. The host alias is configured
for Docker Desktop and Linux Docker Engine using `host-gateway`.

To build only the runtime image (also usable by SCRUM-59's CI work):

```bash
docker build -t noteworthy-app .
```

The build context includes only application source and required package metadata;
Git history, `.env`, uploads, tests, and development tooling are excluded.

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
synthetic PDFs and a local test model server; they never call Ollama or a hosted
model. The wheel includes the HTML interface, so an installed app can run
outside the source checkout. Runtime dependencies are pinned in
`requirements.txt`, and developer tools are pinned in `requirements-dev.txt`.

## Application structure

| Module | Responsibility |
|---|---|
| `noteworthy_app/core.py` | Typed PDF extraction, passage retrieval, and cited answer prompts |
| `noteworthy_app/quiz.py` | Five source-traceable multiple-choice cloze questions |
| `noteworthy_app/models.py` | Ollama adapter exposing `generate_answer(prompt) -> str` |
| `noteworthy_app/server.py` | HTTP routes, validation, and in-memory document lookup |
| `noteworthy_app/storage.py` | Save original PDFs and passages; restore documents on startup |
| `noteworthy_app/static/index.html` | Existing upload, answer, quiz, and browser-local score interface |
| `tests/` | Offline extraction, quiz, model adapter, and HTTP behavior checks |

The server accepts an answer generator through `create_server(...)`, so the
model adapter can be swapped. Under SCRUM-60, `noteworthy_app/models.py` adds
DigitalOcean serverless inference for staging, chosen by an environment
variable, and keeps Ollama for local development.

| Route | Behavior |
|---|---|
| `GET /` | Serve the study interface |
| `GET /health` | Return `{"status":"ok"}` without calling external services |
| `GET /api/documents` | List current and restored documents from the configured storage directory |
| `POST /api/upload` | Accept `name` and base64 PDF `data`; return document ID and passage count |
| `POST /api/ask` | Accept `document_id` and `question`; return answer and source passages |
| `POST /api/quiz` | Accept `document_id`; return five questions with options and source IDs |

The PDF limit is 12 MiB. Invalid JSON and fields return 400, missing routes or
documents return 404, oversized uploads return 413, storage failures return 500,
and model failures return 503.

## Prototype limits and Sprint 2 handoff

This is a local development baseline. Documents are shared within one server
instance and persist in its configured storage directory; scores live in browser
local storage. The frontend's document library and processing-status work remains
under SCRUM-65; the existing page lists documents after an upload. There is no
login, per-student isolation, database, OCR, or
OneDrive/Canvas import in this scaffold. It retains lexical passage retrieval
and the existing cloze quiz rather than claiming all team prototypes are merged.

The follow-up order under SCRUM-11 is:

1. Complete remaining prototype integration under SCRUM-56 (John).
2. Set up the DigitalOcean account under SCRUM-57 (David), alongside app
   integration.
3. Containerize the app under SCRUM-58 (David). Set `HOST=0.0.0.0` in a container;
   the local default binds only to loopback.
4. Run the check commands and Docker build under SCRUM-59 (David).
5. Add DigitalOcean serverless inference for staging under SCRUM-60 (Sunil), in
   parallel; Ollama stays for local development.
6. Deploy staging under SCRUM-61 (David, steps in `deploy/STAGING.md`), then
   record the actual deployment commands and URL under SCRUM-62 (John).

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request. Its
`repository-check` job installs the pinned dependencies, checks Ruff lint and
formatting, runs the offline unittest suite, and builds the Docker image. Python
3.14.7 matches the runtime image; Node 24 is installed so the browser-logic test
runs in CI rather than being skipped. Pip downloads are cached using both
requirements files as the cache key inputs.

The workflow needs no cloud credentials or running Ollama service. It builds the
image locally on the runner without publishing it or deploying anything. To
require CI before merging, select `repository-check` as a required status check
in the ruleset for `main`.

Docker, Compose, and application CI are now available. The DigitalOcean account,
hosted model, and staging deployment remain outstanding; SCRUM-11 is still In
Progress.
