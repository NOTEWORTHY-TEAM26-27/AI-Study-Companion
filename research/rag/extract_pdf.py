"""Sprint 1 RAG test (PBI 3): extract the text of a PDF, page by page, into extracted_text.txt.
Run: python extract_pdf.py [path/to/lecture.pdf]
"""

import sys

from pypdf import PdfReader

# Open the PDF
reader = PdfReader(sys.argv[1] if len(sys.argv) > 1 else "Week1_B.pdf")

print(f"Number of pages: {len(reader.pages)}")

# Extract text from every page
all_text = ""

for page_number, page in enumerate(reader.pages, start=1):
    text = page.extract_text()

    if text:
        all_text += f"\n--- PAGE {page_number} ---\n"
        all_text += text

# Save the extracted text
with open("extracted_text.txt", "w", encoding="utf-8") as file:
    file.write(all_text)

print("PDF text extracted successfully!")
print(f"Characters extracted: {len(all_text)}")
print("Saved to extracted_text.txt")
