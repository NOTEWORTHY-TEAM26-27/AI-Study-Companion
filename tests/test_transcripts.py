"""Lecture uploads use the normal study flow and preserve the source time."""

import base64
import json
import unittest

from tests import test_server

VTT = b"""WEBVTT

intro
00:12:40.000 --> 00:12:50.000
Photosynthesis uses sunlight to convert carbon dioxide and water into glucose.

00:13:00.000 --> 00:13:10.000
Mitochondria produce energy that powers important chemical reactions inside cells.

00:13:20.000 --> 00:13:30.000
Ribosomes assemble proteins from amino acids according to genetic instructions.

00:13:40.000 --> 00:13:50.000
Chlorophyll absorbs light energy that plants use during photosynthesis reactions.

00:14:00.000 --> 00:14:10.000
Osmosis moves water across a membrane from dilute to concentrated solutions.
"""


class TranscriptServerTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request
    post = test_server.ServerTests.post

    def upload_transcript(self, name="lecture.vtt", data=VTT):
        status, result = self.post(
            "/api/upload", {"name": name, "data": base64.b64encode(data).decode()}
        )
        self.assertEqual(status, 200, result)
        return result["id"]

    def test_upload_ask_quiz_submit_and_restart_keep_lecture_time(self):
        document_id = self.upload_transcript()
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "How does sunlight help plants?"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["sources"][0]["start_seconds"], 760)
        self.assertEqual(result["sources"][0]["citation"], "lecture, 12:40")
        self.assertNotIn("page", result["sources"][0])
        self.assertIn("lecture, 12:40", self.prompts[-1])
        self.assertNotIn("page None", self.prompts[-1])
        status, quiz = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200, quiz)
        self.assertEqual(len(quiz["questions"]), 5)
        for question in quiz["questions"]:
            self.assertIn("start_seconds", question)
            self.assertIn("citation", question)
            self.assertNotIn("page", question)
            self.assertNotIn("answer_index", question)
            self.assertNotIn("source_text", question)
        status, attempt = self.post(
            "/api/quiz/submit",
            {"quiz_id": quiz["quiz_id"], "learner_id": "a" * 32, "answers": [None] * 5},
        )
        self.assertEqual(status, 200, attempt)
        for item in attempt["results"]:
            self.assertIn("lecture, ", item["citation"])
            self.assertNotIn("page", item)
        # The saved file, not the in-memory lookup, must preserve timing.
        from noteworthy_app.storage import load_documents

        loaded = load_documents(self.data_dir)
        self.assertEqual(loaded[document_id][1][0].start_seconds, 760)
        self.assertEqual(loaded[document_id][1][0].page, None)
        status, content = self.request("/api/documents")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["documents"][0]["name"], "lecture.vtt")
        self.assertIsNotNone(json.loads(content)["documents"][0]["uploaded_at"])

    def test_model_quiz_uses_verified_transcript_locations_for_all_four_formats(self):
        from noteworthy_app.core import extract_passages
        from tests.fixtures import model_questions, study_pdf

        text = " ".join(p.text for p in extract_passages(study_pdf()))
        data = ("WEBVTT\n\n12:40.000 --> 13:00.000\n" + text).encode()
        document_id = self.upload_transcript(data=data)
        items = [{**q, "source_id": "t1", "source_page": None} for q in model_questions()]
        self.server.answer_generator = lambda prompt: json.dumps({"questions": items})
        status, quiz = self.post("/api/quiz", {"document_id": document_id})
        self.assertEqual(status, 200, quiz)
        self.assertEqual(
            {q["type"] for q in quiz["questions"]},
            {"Multiple Choice", "True/False", "Fill-in-the-Blank", "Short Answer"},
        )
        self.assertEqual(len(quiz["questions"]), 8)
        for q in quiz["questions"]:
            self.assertEqual(q["citation"], "lecture, 12:40")
            self.assertEqual(q["start_seconds"], 760)
            self.assertNotIn("page", q)
            self.assertNotIn("source_page", q)
            self.assertNotIn("answer", q)
            self.assertNotIn("source_excerpt", q)

    def test_plain_txt_upload_does_not_invent_a_time_or_page(self):
        document_id = self.upload_transcript(
            "lecture.txt", b"Photosynthesis uses sunlight to make glucose."
        )
        status, result = self.post(
            "/api/ask", {"document_id": document_id, "question": "What uses sunlight?"}
        )
        self.assertEqual(status, 200)
        source = result["sources"][0]
        self.assertEqual(source["citation"], "lecture, time unavailable")
        self.assertNotIn("page", source)
        self.assertIsNone(source["start_seconds"])

    def test_bad_transcript_does_not_publish_a_document(self):
        for name, data in [
            ("lecture.vtt", b"WEBVTT\n\n12:99.000 --> 13:00.000\nBad time."),
            ("lecture.txt", b"\xff\xfe"),
            ("lecture.vtt", b"WEBVTT\n"),
        ]:
            with self.subTest(name=name, data=data):
                status, _ = self.post(
                    "/api/upload", {"name": name, "data": base64.b64encode(data).decode()}
                )
                self.assertEqual(status, 400)
        self.assertEqual(self.server.documents, {})

    def test_extra_blank_lines_in_vtt_upload_are_accepted(self):
        data = VTT.replace(b"\n\n", b"\n\n\n\n")
        document_id = self.upload_transcript(data=data)
        self.assertEqual(self.server.documents[document_id][1][0].start_seconds, 760)

    def test_missing_vtt_separators_return_400_without_saving_partial_text(self):
        for data in [
            VTT.replace(b"WEBVTT\n\n", b"WEBVTT\n", 1),
            VTT.replace(b"glucose.\n\n", b"glucose.\n", 1),
        ]:
            with self.subTest(data=data):
                status, _ = self.post(
                    "/api/upload", {"name": "lecture.vtt", "data": base64.b64encode(data).decode()}
                )
                self.assertEqual(status, 400)
        self.assertEqual(self.server.documents, {})

    def test_transcript_owner_cannot_be_spoofed(self):
        # Account-mode access checks also cover new file types.
        from noteworthy_app.accounts import Accounts, FileAccountStore

        self.server.accounts = Accounts(
            FileAccountStore(self.data_dir / "accounts"), "class", 3600, False
        )
        _, token = self.server.accounts.sign_up(
            "john@example.com", "my long test password", "class"
        )
        status, raw = self.request(
            "/api/upload",
            json.dumps({"name": "lecture.vtt", "data": base64.b64encode(VTT).decode()}),
            {
                "Content-Type": "application/json",
                "Cookie": self.server.accounts.cookie(token).split(";", 1)[0],
            },
        )
        self.assertEqual(status, 200, raw)
        document_id = json.loads(raw)["id"]
        _, other_token = self.server.accounts.sign_up(
            "other@example.com", "my long test password", "class"
        )
        for route in ["/api/ask", "/api/quiz"]:
            status, _ = self.request(
                route,
                json.dumps({"document_id": document_id, "question": "sunlight"}),
                {
                    "Content-Type": "application/json",
                    "Cookie": self.server.accounts.cookie(other_token).split(";", 1)[0],
                },
            )
            self.assertEqual(status, 404)
        self.assertEqual(
            self.server.documents[document_id][2], self.server.accounts.current(token)["id"]
        )


class TranscriptParsingTests(unittest.TestCase):
    def parse(self, data, suffix=".vtt"):
        from noteworthy_app.transcripts import extract_transcript

        return extract_transcript(data, suffix)

    def test_caption_fragments_keep_first_time_and_strip_only_vtt_markup(self):
        passages = self.parse(
            b"\xef\xbb\xbfWEBVTT example\r\n\r\nNOTE ignore me\r\nnot study text\r\n\r\nSTYLE\r\n::cue { color: red; }\r\n\r\none\r\n00:12:40.500 --> 00:12:42.000 align:start\r\n<v Teacher><b>Photosynthesis</b> uses\r\n\r\ntwo\r\n00:12:42.000 --> 00:12:44.000\r\nsunlight &amp; water to make glucose.\r\n"
        )
        self.assertEqual(len(passages), 1)
        self.assertEqual(passages[0].text, "Photosynthesis uses sunlight & water to make glucose.")
        self.assertEqual(passages[0].start_seconds, 760.5)
        self.assertIsNone(passages[0].page)

    def test_short_cues_and_hour_timestamps_are_not_lost(self):
        passages = self.parse(
            b"WEBVTT\n\n01:12:40.000 --> 01:12:41.000\nYes.\n\n01:12:42.000 --> 01:12:45.000\nA value satisfies x &lt; y &gt; 0.\n"
        )
        self.assertEqual([p.start_seconds for p in passages], [4360, 4362])
        self.assertEqual(passages[0].text, "Yes.")
        self.assertIn("x < y > 0", passages[1].text)

    def test_txt_timestamps_and_untimed_text_are_explicit(self):
        passages = self.parse(
            b"[12:40] Photosynthesis uses sunlight.\nContinuation of that same idea.\n[1:13:00] Mitochondria power cells.",
            ".txt",
        )
        self.assertEqual([p.start_seconds for p in passages], [760, 4380])
        self.assertIn("Continuation", passages[0].text)
        plain = self.parse(b"Ordinary text with x < y > 0 stays intact.", ".txt")
        self.assertIsNone(plain[0].start_seconds)
        self.assertIn("x < y > 0", plain[0].text)

    def test_bad_times_or_malformed_vtt_do_not_silently_lose_cues(self):
        for data in [
            b"WEBVTT\n\n00:70.000 --> 01:12.000\nInvalid minutes.",
            b"WEBVTT\n\n01:12.000 --> 01:10.000\nBackwards interval.",
            b"WEBVTT\n\n00:12.000 --> 00:12.000\nEmpty interval.",
            b"WEBVTT\n\n00:12.000 --> 00:15.000\nGood.\n\n00:10.000 --> 00:14.000\nOut of order.",
            b"WEBVTT\n\nnot a timing line\nStudy text.",
            b"no header",
            b"\xff",
            b"WEBVTT\n",
        ]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.parse(data)

    def test_long_cues_have_bounded_passages_with_the_real_start_time(self):
        data = b"WEBVTT\n\n02:00.000 --> 03:00.000\n" + b"molecule " * 300
        passages = self.parse(data)
        self.assertEqual(len(passages), 3)
        self.assertTrue(all(len(p.text.split()) <= 115 for p in passages))
        self.assertTrue(all(p.start_seconds == 120 for p in passages))
