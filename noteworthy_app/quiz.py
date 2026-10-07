"""Generate five answerable, source-traceable PDF cloze questions."""

from __future__ import annotations

import re

from .core import STOPWORDS, WORD, Passage

SENTENCE = re.compile(r"(?<=[.!?])\s+")
CAPITALIZED = re.compile(
    r"\b(?:[A-Z]{2,}|[A-Z][a-z]{3,})(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]{3,})){0,2}\b"
)


def _candidates(passages: list[Passage]) -> list[tuple[Passage, str, str]]:
    result = []
    used_answers: set[str] = set()
    for passage in passages:
        for sentence in SENTENCE.split(passage.text):
            sentence = sentence.strip()
            if not 45 <= len(sentence) <= 300:
                continue
            phrases = [m.group() for m in CAPITALIZED.finditer(sentence)]
            phrases = [
                p for p in phrases if p.lower() not in STOPWORDS and p.lower() not in used_answers
            ]
            if not phrases:
                words = [
                    w
                    for w in WORD.findall(sentence)
                    if len(w) >= 7 and w.lower() not in STOPWORDS and w.lower() not in used_answers
                ]
                phrases = words[:1]
            if phrases:
                answer = max(phrases, key=lambda p: (len(p.split()), len(p)))
                used_answers.add(answer.lower())
                result.append((passage, sentence, answer))
    return result


def generate_quiz(passages: list[Passage]) -> list[dict]:
    candidates = _candidates(passages)
    if len(candidates) < 5:
        raise ValueError("The PDF needs at least five complete statements for a quiz.")
    selected = []
    seen_pages = set()
    for candidate in candidates:
        if candidate[0].page not in seen_pages:
            selected.append(candidate)
            seen_pages.add(candidate[0].page)
        if len(selected) == 5:
            break
    for candidate in candidates:
        if len(selected) == 5:
            break
        if candidate not in selected:
            selected.append(candidate)
    pool = [candidate[2] for candidate in candidates]
    questions = []
    for index, (passage, sentence, answer) in enumerate(selected):
        distractors = []
        for possible in pool:
            if possible.lower() != answer.lower() and possible.lower() not in {
                d.lower() for d in distractors
            }:
                distractors.append(possible)
            if len(distractors) == 3:
                break
        if len(distractors) < 3:
            raise ValueError("The PDF needs more distinct terms for a quiz.")
        answer_index = index % 4
        options = distractors.copy()
        options.insert(answer_index, answer)
        questions.append(
            {
                "question": f"Which term completes this PDF statement: “{sentence.replace(answer, '_____', 1)}”",
                "options": options,
                "answer_index": answer_index,
                "source_id": passage.id,
                "page": passage.page,
                "topic": f"Page {passage.page}",
                "source_text": passage.text,
            }
        )
    return questions
