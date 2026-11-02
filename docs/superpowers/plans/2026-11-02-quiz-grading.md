# Quiz grading and saved attempts implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Complete SCRUM-81 and SCRUM-83 with server grading and durable attempts.

**Architecture:** Keep the existing HTTP server, document store, and quiz generator. Grade against server-issued questions, store quizzes and attempts in subdirectories of DOCUMENTS_DIR, and aggregate topic scores from saved attempts. Use an anonymous browser identifier to keep browsers' progress separate until accounts exist.

**Tech Stack:** Python standard library, existing HTML/JavaScript, unittest and Node VM checks. No additional dependencies.

**Spec:** SCRUM-81 and SCRUM-83 in Sprint 3; existing four-format question schema in research/question_generation/validate_questions.py.

## Global constraints

- Save attempts and scores as files on the server, the same way uploads are stored.
- PostgreSQL and authenticated accounts remain later work.
- Preserve the current generator and UI; do not replace James's separate quiz page or Sunil's generator integration.
- Never accept a client-supplied answer key or score.
- Browser identifiers separate progress; they are not authentication.

## Review focus

- A forged client score must not affect server grading.
- Repeated submission of the same issued quiz must not double-count progress.
- Concurrent submissions and restarts must preserve saved attempts.
- Failed writes must return an error and permit retry.
- Empty, malformed, and partially answered quizzes must have predictable results.

### Task 1: Deterministic grading (SCRUM-81)

**Files:** Create noteworthy_app/grading.py and tests/test_grading.py.

**Interfaces:** grade_quiz(questions: list[dict], answers: list) -> dict with correct, total, score, results, and topic_scores. Consume the existing options/answer_index schema and the shared type/choices/answer schema. Text answers use Unicode normalization, case folding and collapsed whitespace; no semantic/LLM grading.

- [ ] Write tests for all four formats, partial/empty answers, wrong numeric answers, invalid answer types and counts, and retained source references.
- [ ] Run `.venv/bin/python -m unittest tests.test_grading -v`; expect failures before implementation.
- [ ] Implement deterministic grading and run the same tests; expect all passing.
- [ ] Commit as `SCRUM-81: Add server quiz grading`.

### Task 2: Durable API and browser integration (SCRUM-83, SCRUM-81)

**Files:** Create noteworthy_app/attempts.py; modify server.py, static/index.html, README.md, tests/test_server.py and tests/test_ui.py; create tests/test_attempts.py.

**Interfaces:** POST /api/quiz adds quiz_id; POST /api/quiz/submit takes quiz_id, learner_id and answers; GET /api/progress?learner_id=... optionally filters document_id and returns attempts and topic_scores. Learner and quiz IDs are UUID hex strings. Persist one attempt per learner/quiz, return the saved result on retry. Keep quiz response fields for compatibility.

- [ ] Write failing tests for real HTTP submit/progress, forged keys/scores, restart recovery, per-browser isolation, duplicate and concurrent submit, invalid IDs and unknown quizzes, corrupt records, and failed storage writes.
- [ ] Implement file storage using existing atomic writes and serialize submit with one server lock. Store authoritative questions independently of the browser response.
- [ ] Replace browser-side grading/localStorage scores with submission and restored server progress, retaining retry on server failure and dropping late responses after a document/quiz switch.
- [ ] Exercise the browser submission/retry/restore behavior using Node VM tests.
- [ ] Document endpoint payloads, matching rules, file locations and anonymous scope for teammates.
- [ ] Run `make check PYTHON=.venv/bin/python`; expect all tests and lint passing. Run a real-process restart smoke check and Docker build.
- [ ] Commit as `SCRUM-83: Save quiz attempts and topic scores on the server`.
- [ ] Obtain an independent branch review, fix material findings, open the Jira-linked PR, verify CI and update Jira to In Review with evidence in PBI 12.
