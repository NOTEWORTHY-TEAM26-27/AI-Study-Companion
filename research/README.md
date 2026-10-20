# Research

Research code for the AI features. None of this runs in the web app. The app in
`noteworthy_app/` is what gets deployed, and the Docker image does not include this folder.
The app's embedding search (`noteworthy_app/search.py`) uses the same model as `rag/`.

| Folder | What it is | Jira |
|---|---|---|
| `question_generation/` | Question generator in four formats and a format validator | PBI 5 (5-1 to 5-4) |
| `knowledge_graph/` | Concept graphs from three lecture PDFs merged into one graph | PBI 6 (6-1, 6-2) |
| `rag/` | Page search by meaning with MiniLM embeddings, from the Sprint 1 RAG test | PBI 3 (3-2, 3-4) |

Lecture PDFs are not committed because they are course material. They are attached to the
Jira tasks.

Work by Sunil Thapa Magar, Sprints 1 and 2.
