# Math search comparison (PBI 15, task 15-3)

An offline script that checks how well passage search finds the right textbook page for math
questions. It compares the app's current search with a keyword search that keeps math
notation, such as A^T, A^-1, det, lambda, x1, Ax=b, and matrices written as lists of numbers.

## Files

- `compare_search.py`: builds the app's passages from a PDF, runs four searches for each
  question, and saves the pages each search returned.

## What it measures

The script uses the app's own code from `noteworthy_app/`: `extract_passages` (pypdf, passages
of up to 115 words, pages counted from 1) and the two searches in `search.py`.

| Search | What it is |
|---|---|
| `app_embedding` | `EmbeddingSearch` with the MiniLM model, `MIN_SCORE` and the keyword fallback, built by `create_search("embedding")` as on staging |
| `app_keyword` | `KeywordSearch`, the app's plain keyword search |
| `symbol_bm25` | BM25 with a tokenizer that keeps math notation |
| `hybrid` | Reciprocal rank fusion (k = 60) of `app_embedding` and `symbol_bm25` |

Each search returns 3 passages. A hit is a passage on the question's gold PDF page. The script
reports how often the gold page comes first (top 1) and how often it is in the top 3, for all
questions, for the questions whose prompt matches the textbook, and for each question type.
It also saves the rank of the first passage on the gold page.

The tokenizer rules are listed in `TOKENIZER_RULES` at the top of the script and saved with
the results. Rules P1 to P6 undo how pypdf writes the textbook's math fonts (for example, the
minus sign comes out as a control character and the equals sign as the letter D). Rules N1 to
N4 and T1 to T7 apply to passages and questions alike.

## Inputs

- A typed PDF.
- A questions file, `.xlsx` (first sheet) or `.csv`, with the columns `Question ID`, `Type`,
  `Question`, and either `Textbook page` (the printed page) or `PDF page`. Printed pages are
  turned into PDF pages with the page numbers found in the running heads. The check is saved
  in `page_mapping`.

The PDF, the questions and the results are course material and stay out of the repository.

## How to run

From the repository root, with the app's requirements installed:

```bash
python research/math_search/compare_search.py BOOK.pdf QUESTIONS.xlsx OUT_DIR \
    --source-mismatch Q06,Q09,Q10
```

Options:

- `--source-mismatch`: question ids whose prompt differs from the textbook. They are left out
  of the `source_matched` totals.
- `--reader-text`: page text from another PDF reader (task 15-2), as a JSON list of pages, a
  JSON object of page number to text, or a text file with one form feed between pages. The
  same searches then run on that text too.
- `--staging-dir`: a folder of `<QID>-response.json` files saved from `/api/ask`. The script
  compares the pages staging returned with the local run.
- `--app-root`: another checkout of the app to test, such as a worktree of `main`.
- `--skip-embedding`: run only `app_keyword` and `symbol_bm25`.

The embedding searches need the MiniLM model in the fastembed cache (`FASTEMBED_CACHE_PATH`,
or `fastembed_cache` in the system temp folder). The script never downloads it. To get it once
(about 90 MB), run the same line the Dockerfile runs:

```bash
python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"
```

The script makes no model calls and costs nothing to run.

## Output

`OUT_DIR/results.json` has the settings, the tokenizer rules, the page check, the top 3
passages and pages for every question and search, the gold page rank, and the totals. The
script also prints the totals and a per-question table.
