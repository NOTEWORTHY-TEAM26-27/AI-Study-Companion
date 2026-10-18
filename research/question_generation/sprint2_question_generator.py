"""Sprint 2 (5-1): generate 20 PDF-grounded questions with an offline fallback.
Run: python sprint2_question_generator.py [path/to/lecture.pdf]
Requires: pip install pypdf
Ollama is optional: if it is unavailable, the PDF fallback still runs.
"""

import json
import random
import re
import sys
import urllib.request
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
# PDF = HERE / 'Week1_B.pdf'
# PDF = HERE / 'fuzzing.pdf'
# PDF = HERE / 'An Introduction to Design Patterns.pdf'
PDF = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "cse4308-sp26-lec19.pdf"

# The output JSON is saved next to the PDF with the same name.
OUTPUT = PDF.with_suffix(".json")

MODEL = "llama3.2:3b"
TYPES = ["Multiple Choice", "True/False", "Fill-in-the-Blank", "Short Answer"]
random.seed()


def clean(s):
    return " ".join(str(s).split())


def load_passages():
    if not PDF.exists():
        raise FileNotFoundError(f"Put {PDF.name} in the same folder as this script.")
    reader = PdfReader(str(PDF))
    passages = []
    seen = set()
    for page_num, page in enumerate(reader.pages, 1):
        raw = page.extract_text() or ""
        # Lines and sentences are both useful for slide-based PDFs.
        for line in raw.splitlines():
            for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
                sentence = clean(part).strip("•-– ")
                key = sentence.casefold()
                if 35 <= len(sentence) <= 240 and len(sentence.split()) >= 6 and key not in seen:
                    seen.add(key)
                    passages.append((page_num, sentence))
    if len(passages) < 8:
        raise ValueError(
            "Not enough readable PDF text. Try a typed/text PDF (not a scanned image)."
        )
    return passages


def valid(q, kind):
    if not isinstance(q, dict) or q.get("type") != kind:
        return False
    if set(q) != {"type", "question", "choices", "answer", "source_page", "source_excerpt"}:
        return False
    if not isinstance(q["question"], str) or not q["question"].strip():
        return False
    if not isinstance(q["choices"], dict):
        return False
    if type(q["source_page"]) is not int or q["source_page"] < 1:
        return False
    if not isinstance(q["source_excerpt"], str) or not q["source_excerpt"].strip():
        return False
    if kind == "Multiple Choice":
        return (
            set(q["choices"]) == {"A", "B", "C", "D"}
            and all(isinstance(x, str) and x.strip() for x in q["choices"].values())
            and q["answer"] in ("A", "B", "C", "D")
        )
    if q["choices"] != {}:
        return False
    if kind == "True/False":
        return q["answer"] in ("True", "False")
    if kind == "Fill-in-the-Blank":
        n = q["question"].count("_____")
        if n == 1:
            return isinstance(q["answer"], str) and bool(q["answer"].strip())
        if n > 1:
            return (
                isinstance(q["answer"], list)
                and len(q["answer"]) == n
                and all(isinstance(x, str) and x.strip() for x in q["answer"])
            )
        return False
    return isinstance(q["answer"], str) and bool(q["answer"].strip())


def from_ollama(kind, passages):
    """One short attempt; any failure is harmless because fallback follows."""
    selected = random.sample(passages, min(12, len(passages)))
    context = "\n".join(f"Page {p}: {s}" for p, s in selected)
    instructions = {
        "Multiple Choice": "Four nonempty choices A B C D; answer one letter.",
        "True/False": "Answer exactly True or False; choices {}.",
        "Fill-in-the-Blank": "Use exactly _____ for each blank; one blank answer string or multiple blank answer list; choices {}.",
        "Short Answer": "Answer is a short nonempty string; choices {}.",
    }
    prompt = (
        f"Create 5 different {kind} questions using ONLY the source passages below. "
        "Each question MUST have exactly these keys: type, question, choices, answer, source_page, source_excerpt. "
        f'Type must be "{kind}". {instructions[kind]} '
        "source_excerpt must be copied EXACTLY from one of the passages; source_page must match. "
        'Return ONLY JSON: {"questions": [five question objects]}.\n\n' + context
    )
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.7, "num_ctx": 4096},
        }
    ).encode()
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", body, {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=90) as response:
            data = json.loads(response.read().decode())
        result = json.loads(data["response"])
        candidates = result.get("questions", [])
        if not isinstance(candidates, list):
            return []
        source = {(p, clean(s)) for p, s in selected}
        output = []
        for q in candidates:
            if valid(q, kind) and (q["source_page"], clean(q["source_excerpt"])) in source:
                output.append(q)
        return output
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"  Ollama unavailable ({exc.__class__.__name__}); using PDF fallback.")
        return []


def fallback(kind, passages, used):
    """Build structurally valid questions directly from the PDF, without AI."""
    pool = passages[:]
    random.shuffle(pool)
    results = []
    for page, excerpt in pool:
        if len(results) == 5:
            break
        if kind == "Multiple Choice":
            # Distractors are exact passages from DIFFERENT pages.
            alternatives = [
                (p, s) for p, s in passages if p != page and s.casefold() not in excerpt.casefold()
            ]
            random.shuffle(alternatives)
            distractors = []
            for other_page, other_text in alternatives:
                if other_text not in distractors:
                    distractors.append(other_text)
                if len(distractors) == 3:
                    break
            if len(distractors) != 3:
                continue
            options = [excerpt] + distractors
            random.shuffle(options)
            choices = dict(zip("ABCD", options))
            answer = "ABCD"[options.index(excerpt)]
            question = f"Which of these passages was selected from page {page} of the PDF?"
        elif kind == "True/False":
            # Exact quotation: true by construction.
            question = f'True or False: Page {page} contains this statement: "{excerpt}"'
            choices, answer = {}, "True"
        elif kind == "Fill-in-the-Blank":
            words = list(re.finditer(r"\b[A-Za-z][A-Za-z-]{4,}\b", excerpt))
            if not words:
                continue
            # Some questions have two blanks; others have one.
            count = 2 if len(words) >= 7 and len(results) % 2 == 1 else 1
            selected = random.sample(words, count)
            selected.sort(key=lambda m: m.start())
            answer_words = [m.group() for m in selected]
            masked = excerpt
            for match in reversed(selected):
                masked = masked[: match.start()] + "_____" + masked[match.end() :]
            question = f"Complete this passage from page {page}: {masked}"
            choices = {}
            answer = answer_words[0] if count == 1 else answer_words
        else:  # Short Answer
            # Answer is copied directly from the PDF, not invented by a model.
            words = excerpt.split()
            if len(words) < 7:
                continue
            prefix = " ".join(words[: min(6, len(words) - 2)])
            suffix = " ".join(words[min(6, len(words) - 2) :])
            question = (
                f'According to page {page}, how does this statement continue: "{prefix} ..."?'
            )
            choices, answer = {}, suffix
        key = (kind, question.casefold())
        if key in used:
            continue
        q = {
            "type": kind,
            "question": question,
            "choices": choices,
            "answer": answer,
            "source_page": page,
            "source_excerpt": excerpt,
        }
        if valid(q, kind):
            results.append(q)
            used.add(key)
    return results


def main():
    passages = load_passages()
    print(f"Loaded {len(passages)} passages from {PDF.name}.")
    all_questions = []
    used = set()
    for kind in TYPES:
        print(f"Generating {kind}...")
        accepted = []
        for q in from_ollama(kind, passages):
            key = (kind, q["question"].strip().casefold())
            if key not in used:
                accepted.append(q)
                used.add(key)
            if len(accepted) == 5:
                break
        from_model = len(accepted)
        if len(accepted) < 5:
            accepted += fallback(kind, passages, used)[: 5 - len(accepted)]
        if len(accepted) != 5:
            raise RuntimeError(f"Only {len(accepted)}/5 {kind} questions; no JSON saved.")
        all_questions += accepted
        print(
            f"  {len(accepted)}/5 completed ({from_model} from the model, "
            f"{len(accepted) - from_model} from the PDF fallback)"
        )
    if len(all_questions) != 20:
        raise RuntimeError("Incomplete question set; no JSON saved.")
    OUTPUT.write_text(json.dumps(all_questions, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved 20 questions and answers to {OUTPUT.name}")


if __name__ == "__main__":
    main()
