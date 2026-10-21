"""Sprint 1 RAG search (PBI 3, task 3-2): find the PDF pages that best match a question.
Run: python rag_search.py [path/to/lecture.pdf]
"""

import sys

import numpy as np
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

# Load PDF
reader = PdfReader(sys.argv[1] if len(sys.argv) > 1 else "Week1_B.pdf")

# Make each PDF page one chunk
chunks = []

for page_number, page in enumerate(reader.pages, start=1):
    text = page.extract_text()

    if text:
        chunks.append(f"--- PAGE {page_number} ---\n{text.strip()}")

print(f"Loaded {len(chunks)} page-based chunks.")

# Load embedding model
print("Loading embedding model...")
model = SentenceTransformer("all-MiniLM-L6-v2")

# Create embeddings
print("Creating embeddings...")
chunk_embeddings = model.encode(chunks, normalize_embeddings=True)

print("RAG retrieval system is ready!")

# Ask question
question = input("\nAsk a question about the PDF: ")

question_embedding = model.encode([question], normalize_embeddings=True)[0]

# Calculate similarity
scores = np.dot(chunk_embeddings, question_embedding)

# Retrieve top 3 pages
top_indices = np.argsort(scores)[::-1][:3]

print("\n--- TOP RETRIEVED CHUNKS ---")

for rank, index in enumerate(top_indices, start=1):
    print(f"\nRESULT {rank}")
    print(f"Similarity score: {scores[index]:.4f}")
    print(chunks[index])
    print("-" * 60)
