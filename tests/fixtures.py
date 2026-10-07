"""Synthetic study notes; no student data or external services."""

import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def study_pdf():
    statements = [
        "Photosynthesis converts sunlight into chemical energy inside plant cells.",
        "Mitochondria release energy from nutrients for cellular processes.",
        "Chlorophyll absorbs light energy that plants use during photosynthesis.",
        "Ribosomes assemble proteins using instructions from messenger molecules.",
        "Chromosomes organize genetic information within the nucleus of cells.",
        "Membranes regulate the movement of substances into and out of cells.",
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
