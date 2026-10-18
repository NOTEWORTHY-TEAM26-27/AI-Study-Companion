"""Sprint 2 (5-2): validate the shared JSON question format.
Run: python validate_questions.py [path/to/questions.json]
"""

import json
import sys
from collections import Counter
from pathlib import Path

PATH = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parent / "sprint2_questions.json"
)
TYPES = ["Multiple Choice", "True/False", "Fill-in-the-Blank", "Short Answer"]
FIELDS = {"type", "question", "choices", "answer", "source_page", "source_excerpt"}


def errors_for(q):
    errors = []
    if not isinstance(q, dict):
        return ["Not a JSON object"]
    if set(q) != FIELDS:
        return [f"Expected exactly six fields: {sorted(FIELDS)}"]
    kind = q["type"]
    if kind not in TYPES:
        errors.append("Unknown question type")
    if not isinstance(q["question"], str) or not q["question"].strip():
        errors.append("Question must contain text")
    if type(q["source_page"]) is not int or q["source_page"] < 1:
        errors.append("Invalid source_page")
    if not isinstance(q["source_excerpt"], str) or not q["source_excerpt"].strip():
        errors.append("Missing source_excerpt")
    choices = q["choices"]
    answer = q["answer"]
    if not isinstance(choices, dict):
        errors.append("choices must be an object")
        return errors
    if kind == "Multiple Choice":
        if set(choices) != {"A", "B", "C", "D"} or not all(
            isinstance(v, str) and v.strip() for v in choices.values()
        ):
            errors.append("MCQ needs four nonempty choices A-D")
        if not isinstance(answer, str) or answer not in ("A", "B", "C", "D"):
            errors.append("MCQ answer must be A, B, C, or D")
    elif kind == "True/False":
        if answer not in ("True", "False"):
            errors.append("True/False answer must be True or False")
        if choices:
            errors.append("choices must be empty")
    elif kind == "Fill-in-the-Blank":
        blanks = q["question"].count("_____") if isinstance(q["question"], str) else 0
        if blanks == 0:
            errors.append("Missing _____ blank")
        elif blanks == 1:
            if not isinstance(answer, str) or not answer.strip():
                errors.append("One blank needs one text answer")
        elif (
            not isinstance(answer, list)
            or len(answer) != blanks
            or not all(isinstance(a, str) and a.strip() for a in answer)
        ):
            errors.append("Multiple blanks need one nonempty list answer per blank")
        if choices:
            errors.append("choices must be empty")
    elif kind == "Short Answer":
        if not isinstance(answer, str) or not answer.strip():
            errors.append("Missing short answer")
        if choices:
            errors.append("choices must be empty")
    return errors


def main():
    if not PATH.exists():
        print(f"No {PATH.name} found. Run the generator first.")
        return
    try:
        questions = json.loads(PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"Cannot read JSON: {exc}")
        return
    if not isinstance(questions, list):
        print("FAIL: JSON must be a list of questions.")
        return
    invalid = 0
    for i, q in enumerate(questions, 1):
        errors = errors_for(q)
        if errors:
            invalid += 1
            print(f"Question {i}: INVALID - {'; '.join(errors)}")
        else:
            print(f"Question {i}: VALID")
    counts = Counter(q.get("type") for q in questions if isinstance(q, dict))
    print(f"\nTotal: {len(questions)} | Valid: {len(questions) - invalid} | Invalid: {invalid}")
    for kind in TYPES:
        print(f"{kind}: {counts[kind]}/5")
    if len(questions) == 20 and invalid == 0 and all(counts[t] == 5 for t in TYPES):
        print("PASS: All 20 questions follow the shared format.")
    else:
        print("FAIL: Questions do not all follow the shared format.")


if __name__ == "__main__":
    main()
