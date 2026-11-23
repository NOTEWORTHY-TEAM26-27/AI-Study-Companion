"""Extract UTF-8 lecture transcripts without a transcription service or extra dependencies."""

import html
import re

from .core import Passage

VTT_TIME = r"(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}"
TIMING = re.compile(rf"({VTT_TIME})[ \t]+-->[ \t]+({VTT_TIME})(?:[ \t]+.*)?")
TXT_TIME = re.compile(r"^\[?((?:\d+:)?\d{1,2}:\d{2}(?:\.\d{1,3})?)\]?(?:[ \t]+|$)")
# Only WebVTT markup is removed; literal mathematical comparisons stay as text.
VTT_TAG = re.compile(rf"</?(?:c(?:\.[\w-]+)*|v|lang|b|i|u|ruby|rt)(?:[ .][^>]*)?>|<{VTT_TIME}>")


def _seconds(stamp: str) -> float:
    parts = stamp.split(":")
    seconds, minutes = float(parts[-1]), int(parts[-2])
    if seconds >= 60 or (len(parts) == 3 and minutes >= 60):
        raise ValueError("Transcript timestamps must have minutes and seconds below 60.")
    return (int(parts[0]) * 3600 if len(parts) == 3 else 0) + minutes * 60 + seconds


def _vtt(text: str) -> list[tuple[float, str]]:
    blocks = re.split(r"\n(?:[ \t]*\n)+", text.strip())
    if not re.fullmatch(r"WEBVTT(?:[ \t].*)?", blocks[0].split("\n")[0]):
        raise ValueError("A VTT transcript must start with WEBVTT.")
    if "-->" in blocks[0]:
        raise ValueError("Separate the VTT header and each cue with a blank line.")
    cues = []
    previous = -1
    for block in blocks[1:]:
        lines = block.splitlines()
        if lines[0] in {"STYLE", "REGION"} or re.match(r"NOTE(?:[ \t]|$)", lines[0]):
            continue
        # A cue may have an identifier before its timing line.
        index = 0 if "-->" in lines[0] else 1
        timing = TIMING.fullmatch(lines[index]) if index < len(lines) else None
        if timing is None:
            raise ValueError("The VTT transcript has an invalid cue timing line.")
        if any("-->" in line for line in lines[index + 1 :]):
            raise ValueError("Separate each VTT cue with a blank line.")
        start, end = _seconds(timing[1]), _seconds(timing[2])
        if end <= start or start < previous:
            raise ValueError("VTT cues need increasing start times and an end after their start.")
        previous = start
        body = html.unescape(VTT_TAG.sub("", " ".join(lines[index + 1 :])))
        if body.strip():
            cues.append((start, body))
    return cues


def _txt(text: str) -> list[tuple[float | None, str]]:
    segments = []
    start = None
    lines = []
    for line in text.splitlines():
        match = TXT_TIME.match(line.strip())
        if match:
            if lines:
                segments.append((start, " ".join(lines)))
            stamp = _seconds(match[1])
            if start is not None and stamp < start:
                raise ValueError("Transcript timestamps must be in reading order.")
            start, lines = stamp, [line.strip()[match.end() :]]
        else:
            lines.append(line)
    if lines:
        segments.append((start, " ".join(lines)))
    return segments


def extract_transcript(data: bytes, suffix: str) -> list[Passage]:
    """Group VTT caption fragments into bounded passages; TXT times are optional.

    Every chunk retains its first real source time. Untimed TXT never receives a made-up time.
    """
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise ValueError("Save the transcript as UTF-8 text.") from exc
    if "\x00" in text:
        raise ValueError("Save the transcript as UTF-8 text.")
    if suffix not in {".vtt", ".txt"}:
        raise ValueError("Choose a VTT or TXT transcript.")
    segments = _vtt(text) if suffix == ".vtt" else _txt(text)
    passages = []
    words = []
    start = None

    def flush():
        if words:
            passages.append(Passage(f"t{len(passages) + 1}", None, " ".join(words), start))
            words.clear()

    for stamp, body in segments:
        if suffix == ".txt":
            flush()
        for word in body.split():
            if not words:
                start = stamp
            words.append(word)
            if len(words) == 115:
                flush()
        # A caption can be a sentence fragment; keep it with the next cue until it ends.
        if words and re.search(r"[.!?][\"')\]]*$", words[-1]):
            flush()
    flush()
    if not passages:
        raise ValueError("The transcript has no readable text.")
    return passages
