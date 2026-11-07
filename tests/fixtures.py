"""Synthetic study notes; no student data or external services."""

import io
import json

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from noteworthy_app.core import Passage

# The bullet pypdf gives for a symbol-font slide bullet.
BULLET = "\uf0a7"


def study_pdf():
    statements = [
        "Photosynthesis converts sunlight into chemical energy inside plant cells.",
        "Mitochondria release energy from nutrients for cellular processes.",
        "Chlorophyll absorbs light energy that plants use during photosynthesis.",
        "Ribosomes assemble proteins using instructions from messenger molecules.",
        "Chromosomes organize genetic information within the nucleus of cells.",
        "Membranes regulate the movement of substances into and out of cells.",
        "Enzymes speed up chemical reactions without being consumed themselves.",
        "Vacuoles store water and nutrients that plant cells need later.",
    ]
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 12 Tf 40 740 Td 18 TL "
            + " ".join(f"({line}) Tj T*" for line in statements)
            + " ET"
        ).encode()
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def model_questions():
    """A model reply: two questions per format, each on its own study_pdf() sentence."""
    photosynthesis = "Photosynthesis converts sunlight into chemical energy inside plant cells."
    mitochondria = "Mitochondria release energy from nutrients for cellular processes."
    ribosomes = "Ribosomes assemble proteins using instructions from messenger molecules."
    return [
        {
            "type": "Multiple Choice",
            "question": "What converts sunlight into chemical energy inside plant cells?",
            "choices": {
                "A": "Photosynthesis",
                "B": "Respiration",
                "C": "Digestion",
                "D": "Osmosis",
            },
            "answer": "A",
            "source_page": 1,
            "source_excerpt": photosynthesis,
        },
        {
            "type": "Multiple Choice",
            "question": "Which structures assemble proteins?",
            "choices": {
                "A": "Membranes",
                "B": "Mitochondria",
                "C": "Ribosomes",
                "D": "Chlorophyll",
            },
            "answer": "C",
            "source_page": 1,
            "source_excerpt": ribosomes,
        },
        {
            "type": "True/False",
            "question": "Mitochondria release energy from nutrients.",
            "choices": {},
            "answer": "True",
            "source_page": 1,
            "source_excerpt": mitochondria,
        },
        {
            "type": "True/False",
            "question": "Membranes block every substance from entering cells.",
            "choices": {},
            "answer": "false",
            "source_page": 1,
            "source_excerpt": "Membranes regulate the movement of substances into and out of cells.",
        },
        {
            "type": "Fill-in-the-Blank",
            "question": "Chlorophyll absorbs _____ energy that plants use during photosynthesis.",
            "choices": {},
            "answer": "light",
            "source_page": 1,
            "source_excerpt": "Chlorophyll absorbs light energy that plants use during photosynthesis.",
        },
        {
            "type": "Fill-in-the-Blank",
            "question": "____ organize genetic information within the _____ of cells.",
            "choices": {},
            "answer": ["Chromosomes", "nucleus"],
            "source_page": 1,
            "source_excerpt": "Chromosomes organize genetic information within the nucleus of cells.",
        },
        {
            "type": "Short Answer",
            "question": "What do enzymes speed up without being consumed?",
            "choices": {},
            "answer": "chemical reactions",
            "source_page": 1,
            "source_excerpt": "Enzymes speed up chemical reactions without being consumed themselves.",
        },
        {
            "type": "Short Answer",
            "question": "Besides nutrients, what do vacuoles store?",
            "choices": {},
            "answer": "water",
            "source_page": 1,
            "source_excerpt": "Vacuoles store water and nutrients that plant cells need later.",
        },
    ]


def slide_passages():
    """Slide text as pypdf extracts it: symbol-font bullets, no periods, slide numbers."""
    b = BULLET
    return [
        Passage(
            "p1-1",
            1,
            "BIOL 1441: Cell Biology Cell Structures Professor Jane Doe The University of "
            "Somewhere [These slides were adapted from A. Smith, with materials from "
            "http://cells.example.edu.] 1",
        ),
        Passage(
            "p2-1",
            2,
            f"Cell Parts {b} Mitochondria release energy from nutrients for the whole cell "
            f"{b} Ribosomes assemble proteins from amino acids (e.g. enzymes and hormones) "
            f"{b} Note: the nucleus stores most of the genetic information 2",
        ),
        Passage(
            "p3-1",
            3,
            f"Plant Cells {b} Chloroplasts capture light energy for photosynthesis {b} "
            f"Large vacuoles store water and nutrients for later use {b} Cell walls give plant "
            "cells a rigid shape 3",
        ),
        Passage("p4-1", 4, "Organelle Sizes Nucleus 6 Mitochondria 1 Ribosome 0.02 Vacuole 30 4"),
        Passage(
            "p5-1",
            5,
            f"Membranes {b} Membranes control which substances enter and leave cells {b} "
            f"Channels let small charged particles cross the membrane quickly {b} Questions? 5",
        ),
    ]


SLIDE_STATEMENTS = {
    "Mitochondria release energy from nutrients for the whole cell",
    "Ribosomes assemble proteins from amino acids (e.g. enzymes and hormones)",
    "Chloroplasts capture light energy for photosynthesis",
    "Large vacuoles store water and nutrients for later use",
    "Membranes control which substances enter and leave cells",
    "Channels let small charged particles cross the membrane quickly",
}


def model_reply(questions=None):
    """Wrap questions the way models often do, in a fenced JSON block."""
    questions = model_questions() if questions is None else questions
    return "```json\n" + json.dumps({"questions": questions}) + "\n```"
