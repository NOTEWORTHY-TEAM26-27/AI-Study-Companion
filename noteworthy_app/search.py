"""Passage search: keyword matching (default) or meaning-based search with embeddings.

RETRIEVAL=embedding turns on embedding search with the all-MiniLM-L6-v2 model (from Sunil's
Sprint 1 RAG test, research/rag) through fastembed, which runs it without PyTorch.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable, Sequence

from .core import Passage, retrieve

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Best match scores measured on Week1_B.pdf: related questions 0.27 to 0.78, unrelated 0.02 to 0.22.
MIN_SCORE = 0.25

Embedder = Callable[[Sequence[str]], list[list[float]]]


class KeywordSearch:
    """The original keyword search; nothing to prepare at upload."""

    def index(self, document_id: str, passages: list[Passage]) -> None:
        return None

    def retrieve(
        self, document_id: str, passages: list[Passage], question: str, limit: int = 3
    ) -> list[Passage]:
        return retrieve(passages, question, limit)


class EmbeddingSearch:
    """Rank passages by cosine similarity to the question, with keyword search as a fallback.

    Passage vectors are computed once per document (at upload, or on the first question after a
    restart) and kept in memory. When no passage is similar enough, the keyword search decides,
    so an unrelated question still gets "The PDF does not say." without a model call.
    """

    def __init__(self, embed: Embedder, min_score: float = MIN_SCORE):
        self._embed = embed
        self._min_score = min_score
        self._vectors: dict[str, tuple[tuple[str, ...], list[list[float]]]] = {}
        self._lock = threading.Lock()

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            vectors = self._embed(list(texts))
        except Exception as exc:
            raise RuntimeError("The search model is unavailable.") from exc
        if len(vectors) != len(texts):
            raise RuntimeError("The search model is unavailable.")
        return [_normalize(vector) for vector in vectors]

    def index(self, document_id: str, passages: list[Passage]) -> list[list[float]]:
        key = tuple(passage.id for passage in passages)
        vectors = self._encode([passage.text for passage in passages])
        with self._lock:
            self._vectors[document_id] = (key, vectors)
        return vectors

    def retrieve(
        self, document_id: str, passages: list[Passage], question: str, limit: int = 3
    ) -> list[Passage]:
        key = tuple(passage.id for passage in passages)
        with self._lock:
            cached = self._vectors.get(document_id)
        vectors = cached[1] if cached and cached[0] == key else self.index(document_id, passages)
        query = self._encode([question])[0]
        scores = [sum(a * b for a, b in zip(vector, query)) for vector in vectors]
        order = sorted(
            range(len(passages)), key=lambda i: (-scores[i], passages[i].page, passages[i].id)
        )
        if not order or scores[order[0]] < self._min_score:
            return retrieve(passages, question, limit)
        return [passages[i] for i in order[:limit]]


def _normalize(vector: Sequence[float]) -> list[float]:
    values = [float(value) for value in vector]
    length = math.sqrt(sum(value * value for value in values))
    return [value / length for value in values] if length else values


def fastembed_embedder(model_name: str = MODEL_NAME) -> Embedder:
    """Load the fastembed model on first use, from FASTEMBED_CACHE_PATH when it is set."""
    model = None
    lock = threading.Lock()

    def embed(texts: Sequence[str]) -> list[list[float]]:
        nonlocal model
        with lock:
            if model is None:
                from fastembed import TextEmbedding

                model = TextEmbedding(model_name)
        return [vector.tolist() for vector in model.embed(list(texts))]

    return embed


def create_search(mode: str | None = None) -> KeywordSearch | EmbeddingSearch:
    mode = (mode if mode is not None else os.environ.get("RETRIEVAL", "keyword")).strip().lower()
    if mode == "keyword":
        return KeywordSearch()
    if mode == "embedding":
        return EmbeddingSearch(fastembed_embedder())
    raise ValueError(f"Unsupported RETRIEVAL: {mode}. Use keyword or embedding.")
