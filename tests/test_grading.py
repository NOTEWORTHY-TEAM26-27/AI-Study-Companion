"""Grade real question records against hand-checked responses."""

import unittest

from noteworthy_app.grading import grade_quiz


def questions():
    return [
        {
            "type": "Multiple Choice",
            "question": "Choose the prime.",
            "choices": {"A": "4", "B": "7", "C": "8", "D": "9"},
            "answer": "B",
            "topic": "Numbers",
            "source_page": 2,
            "source_excerpt": "Seven is prime.",
        },
        {
            "type": "True/False",
            "question": "Two is even.",
            "choices": {},
            "answer": "True",
            "topic": "Numbers",
            "source_page": 2,
            "source_excerpt": "Two is even.",
        },
        {
            "type": "Fill-in-the-Blank",
            "question": "_____ carry oxygen.",
            "choices": {},
            "answer": "Red blood cells",
            "topic": "Biology",
            "source_page": 3,
            "source_excerpt": "Red blood cells carry oxygen.",
        },
        {
            "type": "Short Answer",
            "question": "Name the process.",
            "choices": {},
            "answer": "Photosynthesis",
            "topic": "Biology",
            "source_page": 4,
            "source_excerpt": "Plants use photosynthesis.",
        },
    ]


class GradingTests(unittest.TestCase):
    def test_all_four_formats_and_source_feedback(self):
        result = grade_quiz(questions(), ["B", " true ", "  RED   blood cells ", "photosynthesis"])
        self.assertEqual((result["correct"], result["total"], result["score"]), (4, 4, 100))
        self.assertEqual(
            result["topic_scores"],
            {"Numbers": {"correct": 2, "total": 2}, "Biology": {"correct": 2, "total": 2}},
        )
        self.assertEqual(result["results"][2]["page"], 3)
        self.assertEqual(result["results"][2]["source_text"], "Red blood cells carry oxygen.")
        self.assertEqual(result["results"][2]["expected_answer"], "Red blood cells")

    def test_partial_and_empty_answers_count_as_incorrect(self):
        result = grade_quiz(questions(), ["A", None, "red", "Photosynthesis"])
        self.assertEqual((result["correct"], result["total"], result["score"]), (1, 4, 25))
        self.assertEqual([r["correct"] for r in result["results"]], [False, False, False, True])
        self.assertEqual(grade_quiz(questions(), [None, "", " ", None])["correct"], 0)

    def test_existing_generated_question_uses_index_and_preserves_source(self):
        item = {
            "question": "_____ is prime.",
            "options": ["4", "7", "8", "9"],
            "answer_index": 1,
            "topic": "Page 2",
            "page": 2,
            "source_id": "p2-1",
            "source_text": "Seven is prime.",
        }
        result = grade_quiz([item], [1])
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["results"][0]["expected_answer"], "7")
        self.assertEqual(result["results"][0]["source_id"], "p2-1")
        with self.assertRaises(ValueError):
            grade_quiz([item], [True])  # bool must not be accepted as integer index 1.

    def test_multiple_blanks_require_all_answers_in_order(self):
        item = {**questions()[2], "question": "_____ and _____", "answer": ["Alpha", "Beta"]}
        self.assertEqual(grade_quiz([item], [[" alpha ", "BETA"]])["correct"], 1)
        self.assertEqual(grade_quiz([item], [["Beta", "Alpha"]])["correct"], 0)
        self.assertEqual(grade_quiz([item], [["Alpha"]])["correct"], 0)

    def test_text_matching_does_not_confuse_numbers_or_substrings(self):
        item = {**questions()[3], "answer": "12"}
        for response in ["2", "120", "12 or 13", "12.0"]:
            self.assertEqual(grade_quiz([item], [response])["correct"], 0)
        self.assertEqual(grade_quiz([item], ["１２"])["correct"], 1)

    def test_answer_shape_and_size_are_validated(self):
        for answers in [
            None,
            {},
            [],
            ["B"],
            ["B"] * 5,
            ["B", {}, "cells", "plants"],
            ["B", "True", "cells", "x" * 4001],
        ]:
            with self.subTest(answers=str(answers)[:70]), self.assertRaises(ValueError):
                grade_quiz(questions(), answers)

    def test_short_answer_is_exact_and_does_not_call_a_model(self):
        item = questions()[3]
        self.assertEqual(grade_quiz([item], ["Plants turn light into food."])["correct"], 0)
        self.assertEqual(grade_quiz([item], ["Photosynthesis"])["correct"], 1)

    def test_short_answer_ignores_trailing_sentence_punctuation(self):
        for answer in ["Photosynthesis.", "  PHOTOSYNTHESIS? ", '"Photosynthesis."']:
            with self.subTest(answer=answer):
                self.assertEqual(grade_quiz([questions()[3]], [answer])["correct"], 1)

    def test_short_answer_keeps_math_and_internal_punctuation_significant(self):
        for key, wrong in [("-1", "1"), ("3.14", "314"), (".5", "5"), ("5!", "5"), ("C++", "C")]:
            with self.subTest(key=key):
                item = {**questions()[3], "answer": key}
                self.assertEqual(grade_quiz([item], [wrong])["correct"], 0)
                self.assertEqual(grade_quiz([item], [key])["correct"], 1)

    def test_empty_or_unknown_question_format_is_rejected(self):
        for items in [[], [{**questions()[0], "type": "Essay"}]]:
            with self.assertRaises(ValueError):
                grade_quiz(items, [] if not items else ["B"])
