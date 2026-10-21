"""Sprint 1 RAG test (PBI 3): split extracted_text.txt into chunks of up to 1200 characters.
This was the first approach; rag_search.py uses one chunk per page instead, which worked better.
"""

from pathlib import Path

# Read the text extracted from the PDF
text = Path("extracted_text.txt").read_text(encoding="utf-8")

# Split the text into paragraphs
paragraphs = [paragraph.strip() for paragraph in text.split("\n") if paragraph.strip()]

# Group paragraphs into chunks
chunks = []
current_chunk = ""
MAX_CHARS = 1200

for paragraph in paragraphs:
    if len(current_chunk) + len(paragraph) <= MAX_CHARS:
        current_chunk += paragraph + "\n"
    else:
        if current_chunk:
            chunks.append(current_chunk.strip())
        current_chunk = paragraph + "\n"

if current_chunk:
    chunks.append(current_chunk.strip())

# Save chunks so we can inspect them
with open("chunks.txt", "w", encoding="utf-8") as file:
    for i, chunk in enumerate(chunks, start=1):
        file.write(f"\n===== CHUNK {i} =====\n")
        file.write(chunk)
        file.write("\n")

print(f"Created {len(chunks)} chunks.")
print("Chunks saved to chunks.txt")

# Show the first chunk as a quick test
print("\nFirst chunk:\n")
print(chunks[0])
