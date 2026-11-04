"""Offline comparison of passage search on math questions (PBI 15, task 15-3).

Four searches, top 3 passages each, over the app's own passages (noteworthy_app.core):

1. app_embedding: the app's EmbeddingSearch (MiniLM through fastembed, MIN_SCORE, keyword
   fallback), built by create_search("embedding") as on staging.
2. app_keyword: the app's KeywordSearch.
3. symbol_bm25: BM25 with a tokenizer that keeps math notation (rules below).
4. hybrid: reciprocal rank fusion of 1 and 3.

A hit is a passage on the question's gold PDF page. The PDF, the questions file and the
output folder are arguments, so no course material is stored in the repository. The script
never downloads the embedding model: it must already be in the fastembed cache.

Run: python research/math_search/compare_search.py BOOK.pdf QUESTIONS.xlsx OUT_DIR
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import logging
import math
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

# Use the local fastembed cache only. fastembed and huggingface_hub do not download when set.
os.environ["HF_HUB_OFFLINE"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[2]
TOP_K = 3
RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75
SEARCHES = ("app_embedding", "app_keyword", "symbol_bm25", "hybrid")

# The rules of the symbol-aware tokenizer, in the order they run. Saved with the results.
TOKENIZER_RULES = [
    "P1 PDF text: pypdf writes some glyphs of the book's TeX math fonts as control characters."
    " Map them back: 0x00 minus, 0x01 centered dot, 0x06 plus-minus, 0x14/0x15 and 0x8C/0x8D"
    " brackets, 0x18 row equivalence (~); drop bullets (0x0F), big parentheses and figure"
    " arrows (0x1A/0x1B) and brace pieces (0x88).",
    "P2 PDF text: 0x02 is a small left bracket when a 0x03 closes a list of numbers after it,"
    " otherwise the times sign; a lone 0x03 is an asterisk.",
    "P3 PDF text: tall brackets are drawn from pieces that pypdf writes as digits on their own"
    " lines (left: 2, then 6s, then 4; right: 3, then 7s, then 5). Replace a left piece and the"
    " matching right piece after the matrix rows with [ and ], so the bracket digits are not"
    " read as matrix entries.",
    "P4 PDF text: in the math font, parentheses come out as '.' and '/' and commas as ';'."
    " Rewrite '.x; y/' as '(x, y)' when the '.' is followed by a non-space, the group is on one"
    " line, and (if a letter follows the '/') the group holds a digit or a space, which keeps"
    " web addresses out. Rewrite 'fa; bg' as a set '{a, b}'.",
    "P5 PDF text: '=' comes out as a standalone 'D' and '+' as a standalone 'C'. Map them back"
    " when a neighboring word looks like math (has a digit, an operator or bracket, or is one or"
    " two letters).",
    "P6 PDF text: join words hyphenated across a line break, split a lower-case letter"
    " followed by a capital (pypdf drops the space before an italic variable, as in 'ofA'), and"
    " close up letter-spaced headings ('T H E O R E M').",
    "N1 Both sides: Unicode superscripts become '^' notation (A^-1, A^T, x^2), subscript digits"
    " become plain digits (x1), then NFKC folds ligatures and letter-like symbols.",
    "N2 Both sides: the Unicode minus, and a dash right before a digit that is not part of a"
    " range, become '-'. Other dashes become spaces. A^{...} becomes A^...",
    "N3 Both sides: 'm x n', 'm by n' and 'm \u00d7 n' (integers, or single letters with \u00d7)"
    " become one dimension token, dim:mxn.",
    "N4 Both sides: spaces around =, +, \u00d7, the centered dot and a spaced binary minus are"
    " removed, so 'c(x + y) = cx + cy' and 'c(x+y)=cx+cy' give the same expression.",
    "T1 Words are lower-cased, split at hyphens and other symbols, and lose a plural ending"
    " (pivots to pivot, matrices to matrix). English stop words and single letters are dropped.",
    "T2 Variables with subscripts stay one token (x1, a11, v2). A coefficient before a variable"
    " is split off (4x3 gives 4 and x3).",
    "T3 Numbers keep their sign (-3 and 3 differ).",
    "T4 Inside brackets, each pair of consecutive numbers becomes a token (1_2, 2_3), so a"
    " matrix or vector typed as [[1,2],[3,4]] matches the same entries on separate lines.",
    "T5 Expressions with =, +, \u00d7, the centered dot, ^ or function parentheses are kept"
    " whole, and also each side of '=' and each parenthesized group (c(x+y)=cx+cy, c(x+y),"
    " cx+cy, x+y). Pieces that hold brackets are left to T4.",
    "T6 A^T and A^-1 are kept and also give 'transpose' and 'inverse'; x^n also gives xn,"
    " the form pypdf writes for a superscript. Greek letters give their names (lambda).",
    "T7 'determinant' also gives 'det'.",
]

STOPWORDS = frozenset(
    """a about above after again all also an and any are as at be been before being below
    between both but by can could did do does doing during each few for from further had has
    have having he her here hers him his how i if in into is it its itself just may me might
    more most must my no nor not now of off on once only or other our ours out over own same
    shall she should so some such than that the their theirs them then there these they this
    those through to too under until up very was we were what when where which while who whom
    why will with would you your yours""".split()
)
IRREGULAR_PLURALS = {"matrices": "matrix", "vertices": "vertex", "indices": "index"}
ALIASES = {"determinant": "det"}

PDF_CONTROL = str.maketrans(
    {
        "\x00": "-",
        "\x01": "\u00b7",
        "\x06": "\u00b1",
        "\x0f": " ",
        "\x14": "[",
        "\x15": "]",
        "\x8c": "[",
        "\x8d": "]",
        "\x18": "~",
        "\x1a": " ",
        "\x1b": " ",
        "\x88": " ",
    }
)
TALL_BRACE = re.compile(r"8[ \t]*\n\x88*<[ \t]*\n\x88*:")
SMALL_BRACKETS = re.compile(r"\x02((?:\s*-?\d+(?:\.\d+)?)+)\s*\x03")
LEFT_PIECE = re.compile(r"(?<![^\s\]])2\n(6*)4\n")
MATH_PARENS = re.compile(r"(?<=[A-Za-z0-9\s\[(=+\-])\.(?=[^\s.])([^./\n]{1,40}?)/(?=([A-Za-z])?)")
LETTER_SPACED = re.compile(r"\b(?:[A-Z] ){3,}[A-Z]\b")
SET_BRACES = re.compile(r"(?<![A-Za-z])f([^\s;{}]+(?:;\s*[^\s;{}]+)+)g(?![A-Za-z])")
SUPERSCRIPT_RUN = re.compile("[\u2070\u00b9\u00b2\u00b3\u2074-\u207b\u207f\u1d40]+")
SUPERSCRIPTS = str.maketrans(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207f\u1d40",
    "0123456789+-nT",
)
SUBSCRIPTS = str.maketrans(
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089", "0123456789"
)
DIMENSION = re.compile(
    r"(?<![\w.^])(?:(\d+|[a-z])\s*\u00d7\s*(\d+|[a-z])(?![\w(])"
    r"|(\d+)\s+(?:x|by)\s+(\d+)(?![\w.]))"
)
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
ATOM = re.compile(r"(?P<num>-?\d+(?:\.\d+)?)|(?P<word>[a-z]+\d*)|(?P<greek>[\u0370-\u03ff])")
OPERATOR = re.compile("[=+\u00d7\u00b7^]|[a-z0-9]\\([^()]*\\)")


@dataclass
class Unit:
    """One app passage plus its raw text with line breaks, for the symbol tokenizer."""

    passage: object
    raw: str


@dataclass
class Question:
    id: str
    type: str
    text: str
    printed_page: int | None
    pdf_page: int | None


# ---------------------------------------------------------------- normalization


def _mathy(word: str) -> bool:
    word = word.strip(",.;:")
    if re.search("[\\d()\\[\\]=+\\-*~^\u00b7\u00d7\u00b1]", word):
        return True
    return bool(re.fullmatch(r"[A-Za-z]{1,2}", word))


def _tall_brackets(text: str) -> str:
    out, pos = [], 0
    while (left := LEFT_PIECE.search(text, pos)) is not None:
        extension = left.group(1)
        rows = 3 + len(extension) // 2
        start = left.end()
        for _ in range(rows):
            newline = text.find("\n", start)
            start = len(text) if newline < 0 else newline + 1
        right = re.compile(r"(?m)^3\n" + "7" * len(extension) + r"5(?!\d)").search(text, start)
        if right is None or text.count("\n", start, right.start()) > 2:
            out.append(text[pos : left.end()])
            pos = left.end()
            continue
        out.append(text[pos : left.start()] + " [ " + text[left.end() : right.start()] + " ] ")
        pos = right.end()
    out.append(text[pos:])
    return "".join(out)


def _equals_plus(text: str) -> str:
    parts = re.split(r"(\s+)", text)
    words = parts[0::2]
    for i, word in enumerate(words):
        if word not in ("D", "C"):
            continue
        before = words[i - 1] if i else ""
        after = words[i + 1] if i + 1 < len(words) else ""
        if _mathy(before) or _mathy(after):
            words[i] = "=" if word == "D" else "+"
    parts[0::2] = words
    return "".join(parts)


def _parens(match: re.Match) -> str:
    inside = match.group(1)
    if match.group(2) and not re.search(r"[\d\s]", inside):
        return match.group(0)
    return "(" + inside.replace(";", ",") + ")"


def normalize_pdf_text(text: str) -> str:
    """Rules P1 to P6: undo how pypdf writes the book's math fonts."""
    text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", text)
    text = TALL_BRACE.sub(" ", text)
    text = text.translate(PDF_CONTROL)
    text = SMALL_BRACKETS.sub(r"[\1 ]", text)
    text = text.replace("\x02", " \u00d7 ").replace("\x03", "*")
    text = _tall_brackets(text)
    text = MATH_PARENS.sub(_parens, text)
    text = SET_BRACES.sub(lambda m: "{" + m.group(1).replace(";", ",") + "}", text)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = LETTER_SPACED.sub(lambda m: m.group(0).replace(" ", ""), text)
    return _equals_plus(text)


def _dimension(match: re.Match) -> str:
    rows, cols = (match.group(1), match.group(2)) if match.group(1) else match.group(3, 4)
    return f" dim:{rows}x{cols} "


def normalize_math(text: str) -> str:
    """Rules N1 to N4, for passages and questions alike."""
    text = SUPERSCRIPT_RUN.sub(lambda m: "^" + m.group(0).translate(SUPERSCRIPTS), text)
    text = unicodedata.normalize("NFKC", text.translate(SUBSCRIPTS)).replace("\u2212", "-")
    text = re.sub(r"(?<![\w)\]])[\u2010-\u2015](?=\d)", "-", text)
    text = re.sub(r"[\u2010-\u2015]", " ", text)
    text = re.sub(r"\^\{([^{}]*)\}", r"^\1", text)
    text = re.sub(r"\s*\^\s*", "^", text)
    text = DIMENSION.sub(_dimension, text)
    text = re.sub("\\s*([=+\u00d7\u00b7])\\s*", r"\1", text)
    return re.sub(r"(?<=[\w)\]])\s+-\s+(?=[\w(\[])", "-", text)


# ---------------------------------------------------------------- tokenizer


def _word(word: str) -> str:
    if word in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _number(value: str) -> str:
    value = value.lstrip("+")
    return "0" if value in ("-0", "-0.0") else value


def _atoms(piece: str) -> list[str]:
    out = []
    for match in ATOM.finditer(piece):
        if match.group("num"):
            out.append(_number(match.group("num")))
        elif match.group("word"):
            word = match.group("word")
            if word.isalpha() and (len(word) == 1 or word in STOPWORDS):
                continue
            word = _word(word) if word.isalpha() else word
            out.append(word)
            if word in ALIASES:
                out.append(ALIASES[word])
        else:
            name = unicodedata.name(match.group("greek"), "").split(" ")[-1].lower()
            if name:
                out.append("lambda" if name == "lamda" else name)
    return out


def _expression(chunk: str) -> list[str]:
    out = [chunk] if len(chunk) <= 60 else []
    sides = [side for side in chunk.split("=") if side]
    if len(sides) > 1:
        out += sides
    out += re.findall(r"\(([^()]+)\)", chunk)
    extra = []
    for base, power in re.findall(r"([a-z0-9]+)\^(-?[a-z0-9]+)", chunk):
        out.append(f"{base}^{power}")
        if power == "t":
            extra.append("transpose")
        elif power == "-1":
            extra.append("inverse")
        elif power.isalnum() and base + power not in STOPWORDS:
            extra.append(base + power)
    keep = [
        token
        for token in out
        if re.search("[a-z]", token)
        and not re.fullmatch(r"[a-z]+\d*", token)
        and "[" not in token
        and "]" not in token
    ]
    return keep + extra


def _bracket_pairs(text: str) -> list[str]:
    out, depth, start = [], 0, 0
    for i, char in enumerate(text):
        if char == "[":
            if depth == 0:
                start = i + 1
            depth += 1
        elif char == "]" and depth:
            depth -= 1
            if depth == 0:
                run: list[str] = []
                for item in re.split(r"[\s,;\[\]]+", text[start:i]) + [""]:
                    if NUMBER.fullmatch(item):
                        run.append(_number(item))
                        continue
                    out += [f"{a}_{b}" for a, b in zip(run, run[1:])]
                    run = []
    return out


def tokens(text: str) -> list[str]:
    """Rules T1 to T7 on text that went through normalize_math."""
    text = text.lower()
    out = _bracket_pairs(text)
    for chunk in text.split():
        chunk = chunk.strip(".,;:?!\"'")
        if not chunk:
            continue
        if chunk.startswith("dim:"):
            out.append(chunk)
            continue
        if OPERATOR.search(chunk):
            out += _expression(chunk)
        out += _atoms(chunk)
    return out


def passage_tokens(raw: str) -> list[str]:
    return tokens(normalize_math(normalize_pdf_text(raw)))


def question_tokens(question: str) -> list[str]:
    return tokens(normalize_math(question))


# ---------------------------------------------------------------- searches


class SymbolBM25:
    """Okapi BM25 (k1 1.2, b 0.75) over the symbol tokens; each query term counts once."""

    def __init__(self, documents: list[list[str]]):
        self.counts = [Counter(document) for document in documents]
        self.lengths = [len(document) for document in documents]
        self.average = sum(self.lengths) / max(len(documents), 1)
        frequency = Counter(token for count in self.counts for token in count)
        total = len(documents)
        self.idf = {
            token: math.log(1 + (total - df + 0.5) / (df + 0.5)) for token, df in frequency.items()
        }

    def scores(self, query: list[str]) -> list[float]:
        terms = [term for term in dict.fromkeys(query) if term in self.idf]
        result = []
        for count, length in zip(self.counts, self.lengths):
            norm = BM25_K1 * (1 - BM25_B + BM25_B * length / (self.average or 1))
            result.append(
                sum(
                    self.idf[term] * count[term] * (BM25_K1 + 1) / (count[term] + norm)
                    for term in terms
                    if count[term]
                )
            )
        return result


def _order(indexes, passages, score) -> list[int]:
    return sorted(indexes, key=lambda i: (-score(i), passages[i].page, passages[i].id))


def reciprocal_rank_fusion(rankings: list[list[int]], passages) -> list[int]:
    fused: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, index in enumerate(ranking, start=1):
            fused[index] += 1 / (RRF_K + rank)
    return _order(fused, passages, fused.__getitem__)


# ---------------------------------------------------------------- inputs


def load_app(app_root: Path):
    sys.path.insert(0, str(app_root))
    core = importlib.import_module("noteworthy_app.core")
    search = importlib.import_module("noteworthy_app.search")
    return core, search


def pdf_page_texts(pdf: bytes) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages]


def build_units(page_texts: list[str], passage_type, max_words: int = 115) -> list[Unit]:
    """The app's passages (core.extract_passages and core._split_text), with raw spans."""
    units = []
    for page_number, text in enumerate(page_texts, start=1):
        index = 0
        for block in re.split(r"\n\s*\n", text):
            words = list(re.finditer(r"\S+", block))
            for start in range(0, len(words), max_words):
                chunk = words[start : start + max_words]
                part = " ".join(match.group(0) for match in chunk).strip()
                if len(part) < 25:
                    continue
                index += 1
                raw = block[chunk[0].start() : chunk[-1].end()]
                units.append(Unit(passage_type(f"p{page_number}-{index}", page_number, part), raw))
    return units


def load_page_texts(path: Path) -> list[str]:
    """Another reader's text: a JSON list of pages, a JSON object {page: text}, or form feeds."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".json":
        return raw.split("\f")
    data = json.loads(raw)
    if isinstance(data, list):
        return [str(text or "") for text in data]
    pages = {int(page): str(text or "") for page, text in data.items()}
    return [pages.get(number, "") for number in range(1, max(pages, default=0) + 1)]


def _xlsx_rows(path: Path) -> list[list[str]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            shared = [
                "".join(t.text or "" for t in si.iter(f"{ns}t")) for si in root.iter(f"{ns}si")
            ]
        sheet = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{ns}row"):
        values: dict[int, str] = {}
        for cell in row.iter(f"{ns}c"):
            column = 0
            for letter in re.match(r"[A-Z]+", cell.get("r", "A")).group(0):
                column = column * 26 + ord(letter) - 64
            value = cell.find(f"{ns}v")
            if cell.get("t") == "s" and value is not None:
                values[column] = shared[int(value.text)]
            elif cell.get("t") == "inlineStr":
                values[column] = "".join(t.text or "" for t in cell.iter(f"{ns}t"))
            else:
                values[column] = value.text if value is not None and value.text else ""
        rows.append([values.get(i, "") for i in range(1, max(values, default=0) + 1)])
    return rows


def load_questions(path: Path) -> list[Question]:
    """Columns: Question ID, Type, Question, and Textbook page (printed) or PDF page."""
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            records = list(csv.DictReader(handle))
    else:
        rows = _xlsx_rows(path)
        header = [name.strip() for name in rows[0]]
        records = [dict(zip(header, row)) for row in rows[1:] if any(row)]

    def page(value: str | None) -> int | None:
        value = (value or "").strip()
        return int(float(value)) if value else None

    return [
        Question(
            record["Question ID"].strip(),
            record.get("Type", "").strip(),
            record["Question"].strip(),
            page(record.get("Textbook page")),
            page(record.get("PDF page")),
        )
        for record in records
        if record.get("Question ID", "").strip()
    ]


def printed_numbers(text: str) -> set[int]:
    """Page numbers in the running head (first line) or the footer (a last line of digits)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found = set()
    if lines:
        for pattern in (r"^(\d+)\s+\D", r"\D\s(\d+)$"):
            match = re.search(pattern, lines[0])
            if match:
                found.add(int(match.group(1)))
        if re.fullmatch(r"\d+", lines[-1]):
            found.add(int(lines[-1]))
    return found


def map_pages(page_texts: list[str], questions: list[Question], offset: int | None) -> dict:
    """Printed page to PDF page: the most common offset, checked against each gold page."""
    if offset is None:
        offsets = Counter(
            number - printed
            for number, text in enumerate(page_texts, start=1)
            for printed in printed_numbers(text)
        )
        offset = offsets.most_common(1)[0][0] if offsets else 0
    checks = []
    for question in questions:
        if question.pdf_page is None and question.printed_page is not None:
            question.pdf_page = question.printed_page + offset
        number = question.pdf_page
        heads = (
            printed_numbers(page_texts[number - 1])
            if number and number <= len(page_texts)
            else set()
        )
        checks.append(
            {
                "question": question.id,
                "printed_page": question.printed_page,
                "pdf_page": number,
                "printed_numbers_found_on_pdf_page": sorted(heads),
                "confirmed": question.printed_page in heads,
            }
        )
    return {"offset": offset, "checks": checks}


def staging_sources(folder: Path, question_id: str) -> list[dict] | None:
    path = folder / f"{question_id}-response.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    body = data.get("response") or json.loads(data.get("raw_body") or "{}")
    return body.get("sources", [])


# ---------------------------------------------------------------- run


def _summary(questions, results, mismatched, column) -> dict:
    subsets = {
        "all": [q.id for q in questions],
        "source_matched": [q.id for q in questions if q.id not in mismatched],
    }
    for kind in sorted({q.type for q in questions if q.type}):
        subsets[f"type:{kind}"] = [q.id for q in questions if q.type == kind]
    out = {}
    for name, ids in subsets.items():
        rows = [column(results[qid]) for qid in ids]
        rows = [row for row in rows if row is not None]
        out[name] = {
            "n": len(rows),
            "top1": sum(row["hit_top1"] for row in rows),
            "top3": sum(row["hit_top3"] for row in rows),
        }
    return out


def _hits(pages: list[int], gold: int, ranking_pages: list[int] | None = None) -> dict:
    result = {
        "pages": pages,
        "hit_top1": bool(pages) and pages[0] == gold,
        "hit_top3": gold in pages,
    }
    if ranking_pages is not None:
        rank = next((i for i, page in enumerate(ranking_pages, start=1) if page == gold), None)
        result["gold_rank"] = rank
    return result


def run(args: argparse.Namespace) -> dict:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    core, search = load_app(Path(args.app_root).resolve())
    pdf = Path(args.pdf).read_bytes()
    page_texts = pdf_page_texts(pdf)
    sources = {"pypdf": build_units(page_texts, core.Passage)}
    app_passages = core.extract_passages(pdf)
    if [unit.passage for unit in sources["pypdf"]] != app_passages:
        raise SystemExit("Passage rebuild does not match noteworthy_app.core.extract_passages.")
    if args.reader_text:
        reader_pages = load_page_texts(Path(args.reader_text))
        sources[args.reader_name] = build_units(reader_pages, core.Passage)

    questions = load_questions(Path(args.questions))
    pages = map_pages(page_texts, questions, args.page_offset)
    mismatched = {qid.strip() for qid in args.source_mismatch.split(",") if qid.strip()}
    embedding = None if args.skip_embedding else search.create_search("embedding")
    keyword = search.create_search("keyword")
    digest = hashlib.sha256(pdf).hexdigest()

    report = {
        "task": "15-3",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pdf_sha256": digest,
        "app_root": str(Path(args.app_root).resolve()),
        "settings": {
            "top_k": TOP_K,
            "rrf_k": RRF_K,
            "bm25_k1": BM25_K1,
            "bm25_b": BM25_B,
            "embedding_model": search.MODEL_NAME,
            "embedding_min_score": search.MIN_SCORE,
            "embedding_run": embedding is not None,
            "source_mismatch": sorted(mismatched),
        },
        "searches": {
            "app_embedding": "noteworthy_app.search.EmbeddingSearch, as create_search('embedding')",
            "app_keyword": "noteworthy_app.search.KeywordSearch",
            "symbol_bm25": "BM25 over the symbol-aware tokens",
            "hybrid": f"reciprocal rank fusion (k={RRF_K}) of app_embedding and symbol_bm25",
        },
        "tokenizer_rules": TOKENIZER_RULES,
        "page_mapping": pages,
        "text_sources": {},
        "questions": [],
        "summary": {},
    }
    results: dict[str, dict] = {q.id: {} for q in questions}
    for source, units in sources.items():
        passages = [unit.passage for unit in units]
        document_id = f"{source}-{digest[:16]}"
        bm25 = SymbolBM25([passage_tokens(unit.raw) for unit in units])
        report["text_sources"][source] = {"passages": len(passages)}
        if embedding is not None:
            try:
                embedding.index(document_id, passages)
            except RuntimeError as exc:
                cache = os.environ.get("FASTEMBED_CACHE_PATH") or str(
                    Path(tempfile.gettempdir()) / "fastembed_cache"
                )
                raise SystemExit(
                    f"{exc} The MiniLM model is not in the fastembed cache ({cache}). This script"
                    " does not download it. See the README, or pass --skip-embedding."
                ) from exc
        by_id = {passage.id: i for i, passage in enumerate(passages)}
        for question in questions:
            gold = question.pdf_page
            row: dict[str, dict | None] = {}

            def app_ranking(engine):
                full = engine.retrieve(document_id, passages, question.text, limit=len(passages))
                top = engine.retrieve(document_id, passages, question.text, limit=TOP_K)
                if [p.id for p in full[:TOP_K]] != [p.id for p in top]:
                    raise SystemExit(f"{question.id}: top {TOP_K} differs from the full ranking.")
                return [by_id[p.id] for p in full]

            query = question_tokens(question.text)
            bm25_scores = bm25.scores(query)
            positive = [i for i, score in enumerate(bm25_scores) if score > 0]
            rankings = {
                "app_keyword": app_ranking(keyword),
                "symbol_bm25": _order(positive, passages, bm25_scores.__getitem__),
            }
            if embedding is not None:
                rankings["app_embedding"] = app_ranking(embedding)
                rankings["hybrid"] = reciprocal_rank_fusion(
                    [rankings["app_embedding"], rankings["symbol_bm25"]], passages
                )
            for name in SEARCHES:
                if name not in rankings:
                    row[name] = None
                    continue
                ranking = rankings[name]
                top = ranking[:TOP_K]
                row[name] = _hits(
                    [passages[i].page for i in top], gold, [passages[i].page for i in ranking]
                )
                row[name]["ids"] = [passages[i].id for i in top]
                if name == "symbol_bm25":
                    row[name]["scores"] = [round(bm25_scores[i], 3) for i in top]
            row["query_tokens"] = query
            results[question.id][source] = row

    staging = None
    if args.staging_dir:
        staging = {}
        units = {unit.passage.id: unit.passage for unit in sources["pypdf"]}
        for question in questions:
            found = staging_sources(Path(args.staging_dir), question.id)
            if found is None:
                continue
            local = results[question.id]["pypdf"]
            embedding_ids = (local["app_embedding"] or {}).get("ids")
            staging[question.id] = {
                **_hits([s["page"] for s in found], question.pdf_page),
                "ids": [s["id"] for s in found],
                "texts_match_local_passages": all(
                    s["id"] in units and units[s["id"]].text == s["text"] for s in found
                ),
                "same_as_local_app_embedding": (
                    None if embedding_ids is None else embedding_ids == [s["id"] for s in found]
                ),
                "same_as_local_app_keyword": local["app_keyword"]["ids"]
                == [s["id"] for s in found],
            }

    for question in questions:
        report["questions"].append(
            {
                "id": question.id,
                "type": question.type,
                "question": question.text,
                "printed_page": question.printed_page,
                "gold_pdf_page": question.pdf_page,
                "source_matched": question.id not in mismatched,
                "results": results[question.id],
                "staging": (staging or {}).get(question.id),
            }
        )
    for source in sources:
        report["summary"][source] = {
            name: _summary(questions, results, mismatched, lambda r, s=source, n=name: r[s][n])
            for name in SEARCHES
            if results[questions[0].id][source][name] is not None
        }
    if staging:
        report["summary"]["staging_run"] = _summary(
            [q for q in questions if q.id in staging], staging, mismatched, lambda r: r
        )
    return report


def print_report(report: dict) -> None:
    for source, searches in report["summary"].items():
        print(f"\n{source}")
        for name, subsets in searches.items() if source != "staging_run" else [("", searches)]:
            cells = [
                f"{subset} {v['top1']}/{v['n']} top1 {v['top3']}/{v['n']} top3"
                for subset, v in subsets.items()
            ]
            print(f"  {name:14} " + " | ".join(cells))
    print("\nper question (pypdf): gold page, then top-3 pages per search")
    for question in report["questions"]:
        row = question["results"]["pypdf"]
        cells = []
        for name in SEARCHES:
            pages = row[name]["pages"] if row[name] else "not run"
            cells.append(f"{name}={pages}")
        if question["staging"]:
            cells.append(f"staging={question['staging']['pages']}")
        print(f"  {question['id']} gold {question['gold_pdf_page']}: " + " ".join(cells))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pdf", help="textbook PDF (typed text)")
    parser.add_argument("questions", help="questions .xlsx (first sheet) or .csv")
    parser.add_argument("out_dir", help="folder for results.json")
    parser.add_argument("--app-root", default=str(REPO_ROOT), help="checkout with noteworthy_app")
    parser.add_argument("--page-offset", type=int, help="PDF page minus printed page")
    parser.add_argument(
        "--source-mismatch",
        default="",
        help="comma-separated question ids whose prompt differs from the textbook",
    )
    parser.add_argument(
        "--reader-text", help="page texts from another reader (15-2), .json or .txt"
    )
    parser.add_argument("--reader-name", default="reader", help="label for --reader-text")
    parser.add_argument("--staging-dir", help="folder of <QID>-response.json files from /api/ask")
    parser.add_argument(
        "--skip-embedding",
        action="store_true",
        help="run only app_keyword and symbol_bm25 (when the model is not cached)",
    )
    args = parser.parse_args(argv)
    report = run(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\nSaved {out_dir / 'results.json'}")


if __name__ == "__main__":
    main()
