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

1. Do not develop directly on `main`; it only changes through a Pull Request.
2. Create a branch for your task named `<github-user>/SCRUM-<n>/<short-desc>`,
   for example `DavidHuynh01/SCRUM-62/readme-setup-deploy`.
3. Make and test your changes (see Checks and packaging).
4. Push your branch to GitHub.
5. Open a Pull Request into `main`.
6. Have another team member review the Pull Request (or do it yourself).
7. Merge the Pull Request after review. Staging then deploys on its own (see
   Staging and deploy).

## Project Status

Currently in **Sprint 2** (10/05 to 10/23). One app runs locally, in Docker, and
on staging at <https://143-244-212-41.sslip.io>. Prototype integration
([SCRUM-56](https://noteworthy-uta.atlassian.net/browse/SCRUM-56)) is still in
progress: retrieval is keyword based, and the question generation and knowledge
graph research code is in `research/` but not used by the app yet.

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

On Windows, use Git Bash (installed with Git for Windows). The steps are the same
except for the activate path, and `make` is not included, so start the app
directly:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps -e .
cp .env.example .env
set -a; source .env; set +a
python -m noteworthy_app.server
```

Open <http://127.0.0.1:8000>. Upload a typed PDF to extract its text and generate
a five-question practice quiz. Answer generation additionally requires Ollama
running at `OLLAMA_URL` with the model named by `OLLAMA_MODEL` available. The
defaults match the existing prototype. Set `AI_PROVIDER=digitalocean` and
`DIGITALOCEAN_API_KEY` to use the hosted model instead (staging does this). An
unavailable model returns HTTP 503; startup, health checks, extraction, quizzes,
and automated tests do not require Ollama, cloud credentials, or student files.

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

## Staging and deploy

Staging runs at <https://143-244-212-41.sslip.io> on one DigitalOcean Droplet,
using `compose.yaml` plus `compose.staging.yaml` (the app and Caddy for HTTPS).
`GET /health` should return `{"status": "ok"}`.

- **Automatic:** every merge to `main` deploys staging after CI passes
  (`.github/workflows/deploy-staging.yml`). The "Deploy to staging" run shows in
  the Actions tab, and it can also be started there with Run workflow.
- **By hand:** connect as the `deploy` user and run the deploy script, which
  pulls `main`, rebuilds, restarts, and waits for `/health`:

  ```bash
  ssh deploy@143.244.212.41
  cd ~/noteworthy && ./scripts/deploy_staging.sh
  ```

- **Settings:** the server's `~/noteworthy/.env` (never committed) holds
  `STAGING_HOST` and the model settings (`AI_PROVIDER=digitalocean` and
  `DIGITALOCEAN_API_KEY`). Run the deploy script after changing it.
- **Logs:** `docker compose -f compose.yaml -f compose.staging.yaml logs --tail=100 app`
  on the server.

First-time server setup, the key for automatic deploys, and how to turn
automatic deploys off are in `deploy/STAGING.md`.

## Checks and packaging

After setup, these commands work locally and can be used by CI:

```bash
make check PYTHON=python          # Ruff lint/format check and offline unittest suite
make test PYTHON=python           # tests only
make build PYTHON=python          # wheel and source distribution in dist/
```

Without `make` (for example in Git Bash on Windows), run the same checks
directly:

```bash
python -m ruff check . && python -m ruff format --check .
python -m unittest discover -s tests -v
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
| `noteworthy_app/models.py` | Ollama and DigitalOcean adapter exposing `generate_answer(prompt) -> str` |
| `noteworthy_app/server.py` | HTTP routes, validation, and in-memory document lookup |
| `noteworthy_app/storage.py` | Save original PDFs and passages; restore documents on startup |
| `noteworthy_app/static/index.html` | Existing upload, answer, quiz, and browser-local score interface |
| `tests/` | Offline extraction, quiz, model adapter, and HTTP behavior checks |
| `research/` | Question generation and knowledge graph research scripts (not used by the app yet) |
| `deploy/`, `scripts/` | Staging server files, the deploy script, and the deploy key setup |

The server accepts an answer generator through `create_server(...)`, so the
model adapter can be swapped. `noteworthy_app/models.py` picks the provider from
`AI_PROVIDER`: `ollama` (default, local) or `digitalocean` (serverless inference,
`DIGITALOCEAN_API_KEY`, optional `DIGITALOCEAN_BASE_URL` and `DIGITALOCEAN_MODEL`).

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

## Prototype limits and Sprint 2 status

This is a development baseline. Documents are shared within one server instance
and persist in its configured storage directory; scores live in browser local
storage. The new upload page and document processing status (SCRUM-63,
SCRUM-65) are not merged yet; the existing page lists documents after an upload.
There is no login, per-student isolation, database, OCR, or OneDrive/Canvas
import yet. The app keeps lexical passage retrieval and the existing cloze quiz
rather than claiming all team prototypes are merged.

Sprint 2 work under SCRUM-11:

- Done: DigitalOcean account (SCRUM-57), Docker and Compose (SCRUM-58), CI
  (SCRUM-59), staging (SCRUM-61), setup and deploy docs (SCRUM-62), and deploy on
  merge (SCRUM-79).
- In review: hosted model switching (SCRUM-60), merged; confirmed once the model
  key is set on staging.
- Open: remaining prototype integration (SCRUM-56, John).

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

`.github/workflows/deploy-staging.yml` deploys staging after CI passes on a push
to `main` (SCRUM-79); see Staging and deploy.
