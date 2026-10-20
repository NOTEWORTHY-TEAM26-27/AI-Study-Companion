# RAG search (Sprint 1)

Sunil's retrieval code from the Sprint 1 RAG test (PBI 3, tasks 3-2 and 3-4). It finds the
pages of a lecture PDF that best match a question by meaning, using embeddings, instead of
matching keywords the way the app does today.

## Files

- `extract_pdf.py`: extracts the text of the PDF, page by page, into `extracted_text.txt`.
- `chunk_text.py`: the first approach, chunks of up to 1200 characters, saved to `chunks.txt`.
- `rag_search.py`: one chunk per page, `all-MiniLM-L6-v2` embeddings, and the top 3 pages for a
  question, ranked by cosine similarity.

## How to run

```bash
pip install pypdf numpy sentence-transformers
python research/rag/rag_search.py path/to/lecture.pdf
```

The first run downloads the embedding model (about 90 MB). `sentence-transformers` installs
PyTorch, about 1 GB in total.

## Results

On Week1_B.pdf (24 pages), the Sprint 1 test asked 10 questions. Character chunks answered 9
of 10 correctly. Page chunks answered 10 of 10 with no made-up answers, so page chunks are the
approach to bring into the app. The full report is on Jira (PBI 3).

This folder covers retrieval only. In the app, the hosted model writes the answer from the
retrieved pages; moving this search into `noteworthy_app/core.py` is planned under PBI 10.
