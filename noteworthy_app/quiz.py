"""Generate five answerable, source-traceable PDF cloze questions."""

from __future__ import annotations

import random
import re
import unicodedata
from collections.abc import Callable

from .core import STOPWORDS, WORD, Passage

# A sentence ends at . ! or ?, after any closing quotes or brackets.
SENTENCE_END = re.compile(r"[.!?][\"')\]\u2019\u201d]*\s+")
CLOSED = re.compile(r"[.!?][\"')\]\u2019\u201d]*$")
# Slide bullets. pypdf gives symbol-font bullets as private-use characters (U+F0A7 for
# Wingdings); other private-use characters may be letters, such as a Symbol-font theta.
FONT_BULLETS = r"[\uf076\uf0a7\uf0b7\uf0d8\uf0fc]"
BULLET = re.compile(
    r"[\u2022\u2023\u2043\u25a0\u25a1\u25aa\u25ab\u25b6\u25ba\u25cb\u25cf\u25e6\u2713\u2714"
    r"\u2756\u27a2\u27a4]|" + FONT_BULLETS + r"|(?<!\S)\u00a7(?!\S)(?!\s*\d)"
)
# Titles never end a sentence; other abbreviations do when a capital letter follows.
TITLES = frozenset("Dr Mr Mrs Ms Prof".split())
ABBREVIATIONS = frozenset("al cf e.g etc fig i.e no vs".split())
# Generic slide labels and headings. In "Overfitting: fitting the training data..." the
# term is a key, but in "Note: ..." or "EXAMPLE 1 ..." the label is not.
LABEL_WORDS = (
    r"(?:basic idea|definition|example|goal|here|hint|idea|input|key idea|main point|note"
    r"|output|proof|recall|reminder|setup|solution|step \d+|summary|theorem|tip|today"
    r"|warning)s?"
)
LABEL = re.compile(LABEL_WORDS + r":\s+", re.IGNORECASE)
HEADING = re.compile(LABEL_WORDS, re.IGNORECASE)
# Long words that make poor keys.
COMMON = frozenset(
    "another because between example following general however instead several someone "
    "something through usually whether without".split()
)
CAPITALIZED = re.compile(
    r"\b(?:[A-Z]{2,}|[A-Z][a-z]{3,})(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]{3,})){0,2}\b"
)


def sentences(text: str) -> list[tuple[str, bool]]:
    """Split PDF text into sentences and bullet points, each marked complete unless it is
    the unpunctuated end of the text (cut off, or ending in a slide number)."""
    result = []
    pieces = BULLET.split(text)
    for index, piece in enumerate(pieces):
        start = 0
        for end in SENTENCE_END.finditer(piece):
            words = [w.lstrip("([\"'\u2018\u201c") for w in piece[start : end.start() + 1].split()]
            # "e.g." and initials such as "David C. Lay" do not end a sentence, but a
            # variable does ("the columns of A. The next sentence").
            previous, last = ["", *words][-2:]
            capital = piece[end.end() : end.end() + 1].isupper()
            abbreviation = last.rstrip(".").casefold() in ABBREVIATIONS and not capital
            initial = re.fullmatch(r"[A-Z]\.", last) and previous[:1].isupper()
            if last.rstrip(".") in TITLES or abbreviation or initial:
                continue
            result.append((piece[start : end.end()].strip(), True))
            start = end.end()
        rest = piece[start:].strip()
        if rest:
            result.append((rest, index < len(pieces) - 1 or CLOSED.search(rest) is not None))
    return result


def _glyph(character: str) -> bool:
    category = unicodedata.category(character)
    # Control characters and unmapped glyphs show as nothing or as boxes. Symbols such as
    # the degree sign are fine; arrows, shapes, dingbats and emoji are not.
    return category in {"Cc", "Co", "Cn"} or (
        category == "So" and (0x2190 <= ord(character) <= 0x2BFF or ord(character) >= 0x1F000)
    )


def _statement(sentence: str) -> bool:
    """Whether a sentence reads as a full statement, not a title, table, link or fragment."""
    words = WORD.findall(sentence)
    tokens = sentence.split()
    return (
        45 <= len(sentence) <= 300
        # Starts like a sentence, so it is not the cut-off end of one.
        and re.match(r"[\"'(\u2018\u201c]?[A-Z0-9]", sentence) is not None
        # Questions and lead-ins ("...as follows:", "denoted by") are not statements.
        and re.search(r"[,:;?(\-\u2013\u2026][\"')\]\u2019\u201d]*$", sentence) is None
        and (CLOSED.search(sentence) is not None or tokens[-1] not in STOPWORDS)
        and sentence.count("(") == sentence.count(")")
        and sentence.count("[") == sentence.count("]")
        and sentence.count(":") <= 2
        and re.search(r"https?:|www\.|[!?]{2}", sentence) is None
        # Glyphs a font could not map show as boxes, and dingbats are leftover bullets.
        and not any(_glyph(ch) for ch in sentence)
        # Mostly capitalized words are titles, names or all-caps text; mostly numbers are tables.
        and sum(word[0].isupper() for word in words) * 2 <= len(words)
        and sum(any(ch.isdigit() for ch in token) for token in tokens) * 4 <= len(tokens)
    )


def _candidates(passages: list[Passage]) -> list[tuple[Passage, str, str, str]]:
    result = []
    used_answers: set[str] = set()
    for passage in passages:
        for sentence, complete in sentences(passage.text):
            if not complete or not _statement(sentence):
                continue
            # Take the key after a slide label such as "Note:" or "Goal:".
            label = LABEL.match(sentence)
            cut = label.end() if label else 0
            head, body = sentence[:cut], sentence[cut:]
            first = len(body) - len(body.lstrip("([\"'\u2018\u201c"))
            phrases = [
                m.group()
                for m in CAPITALIZED.finditer(body)
                # A lone first word is capitalized only because it starts the sentence,
                # unless it is a term the sentence defines ("Overfitting: ...").
                if (
                    m.start() != first
                    or " " in m.group()
                    or m.group().isupper()
                    or body[m.end() : m.end() + 1] == ":"
                )
                # Labels and headings are never keys, wherever they are.
                and not HEADING.fullmatch(m.group())
                and not LABEL.match(body, m.start())
            ]
            phrases = [
                p for p in phrases if p.lower() not in STOPWORDS and p.lower() not in used_answers
            ]
            if not phrases:
                words = [
                    w
                    for w in WORD.findall(body)
                    if len(w) >= 7
                    and w.lower() not in COMMON | used_answers
                    and not HEADING.fullmatch(w)
                ]
                phrases = words[:1]
            if phrases:
                answer = max(phrases, key=lambda p: (len(p.split()), len(p)))
                used_answers.add(answer.lower())
                result.append((passage, sentence, answer, head + body.replace(answer, "_____", 1)))
    return result


def generate_quiz(
    passages: list[Passage],
    rng: random.Random | None = None,
    skip: Callable[[str], bool] | None = None,
    avoid: Callable[[str], bool] | None = None,
) -> list[dict]:
    """Build up to five questions, leaving out sentences that skip() rejects and wrong
    options that avoid() rejects."""
    rng = rng or random.SystemRandom()
    candidates = _candidates(passages)
    if len(candidates) < 5:
        raise ValueError("The PDF needs at least five complete statements for a quiz.")
    # Skipped sentences still supply distractors through the pool below.
    usable = [candidate for candidate in candidates if not (skip and skip(candidate[1]))]
    selected = []
    seen_pages = set()
    for candidate in usable:
        if candidate[0].page not in seen_pages:
            selected.append(candidate)
            seen_pages.add(candidate[0].page)
        if len(selected) == 5:
            break
    for candidate in usable:
        if len(selected) == 5:
            break
        if candidate not in selected:
            selected.append(candidate)
    pool = [candidate[2] for candidate in candidates if not (avoid and avoid(candidate[2]))]
    questions = []
    for passage, sentence, answer, question in selected:
        # _candidates keeps answers unique ignoring case, so this needs no duplicate check.
        others = [possible for possible in pool if possible.lower() != answer.lower()]
        if len(others) < 3:
            raise ValueError("The PDF needs more distinct terms for a quiz.")
        # Random distractors and key position, so the answer is not the odd one out or a fixed letter.
        options = rng.sample(others, 3)
        answer_index = rng.randrange(4)
        options.insert(answer_index, answer)
        questions.append(
            {
                "question": question,
                "options": options,
                "answer_index": answer_index,
                "source_id": passage.id,
                "page": passage.page,
                "topic": f"Page {passage.page}",
                "source_text": passage.text,
            }
        )
    return questions
