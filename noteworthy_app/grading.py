"""Deterministic scoring for the prototype and the shared four-format schema."""

import unicodedata


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def grade_quiz(questions: list[dict], answers: list) -> dict:
    """Score against server-owned keys. Missing answers are represented by null."""
    if not questions:
        raise ValueError("A quiz must contain questions.")
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise ValueError("Send one answer per question; use null for unanswered questions.")
    results = []
    topic_scores = {}
    for index, (question, answer) in enumerate(zip(questions, answers, strict=True)):
        kind = question.get("type", "Multiple Choice" if "options" in question else "")
        if kind == "Multiple Choice":
            if "options" in question:
                expected = question["answer_index"]
                display = question["options"][expected]
                if answer is not None and (
                    type(answer) is not int or not 0 <= answer < len(question["options"])
                ):
                    raise ValueError("A multiple choice answer must be a valid option index.")
            else:
                expected = question["answer"]
                display = question["choices"][expected]
                if answer is not None and (
                    not isinstance(answer, str) or answer not in question["choices"]
                ):
                    raise ValueError("A multiple choice answer must be an option label.")
            correct = answer is not None and answer == expected
        elif kind in {"True/False", "Fill-in-the-Blank", "Short Answer"}:
            expected = display = question["answer"]
            if isinstance(expected, list):
                if answer is not None and (
                    not isinstance(answer, list)
                    or len(answer) > 20
                    or any(not isinstance(a, str) or len(a) > 4000 for a in answer)
                ):
                    raise ValueError("Send a list of text answers for multiple blanks.")
                correct = answer is not None and [_normalize(a) for a in answer] == [
                    _normalize(a) for a in expected
                ]
            else:
                if answer is not None and (not isinstance(answer, str) or len(answer) > 4000):
                    raise ValueError("Text answers must be strings of at most 4000 characters.")
                correct = bool(answer and _normalize(answer) == _normalize(expected))
        else:
            raise ValueError("Unknown question format.")
        page = question.get("page", question.get("source_page"))
        topic = question.get("topic") or f"Page {page}"
        totals = topic_scores.setdefault(topic, {"correct": 0, "total": 0})
        totals["total"] += 1
        totals["correct"] += int(correct)
        results.append(
            {
                "question_index": index,
                "answer": answer,
                "correct": correct,
                "expected_answer": display,
                "topic": topic,
                "page": page,
                "source_id": question.get("source_id"),
                "source_text": question.get("source_text", question.get("source_excerpt", "")),
            }
        )
    correct = sum(result["correct"] for result in results)
    return {
        "correct": correct,
        "total": len(questions),
        "score": round(100 * correct / len(questions), 2),
        "results": results,
        "topic_scores": topic_scores,
    }
