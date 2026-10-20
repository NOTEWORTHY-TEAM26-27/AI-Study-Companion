"""Passage search: keyword matching (default) or meaning-based search with embeddings.

RETRIEVAL=embedding turns on embedding search with the all-MiniLM-L6-v2 model (from Sunil's
Sprint 1 RAG test, research/rag) through fastembed, which runs it without PyTorch.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Sequence

import numpy as np

from .core import Passage, retrieve

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Best match scores measured on Week1_B.pdf: related questions 0.27 to 0.78, unrelated 0.02 to 0.22.
MIN_SCORE = 0.25
# Small batches without onnxruntime's memory arena keep embedding near 260 MB on the 2 GB
# staging Droplet (about 60 MB more for each upload running at the same time). fastembed's
# default batch of 256 held over 1 GB after a 200-passage PDF.
BATCH_SIZE = 16
# After a failed model load, fail fast for this long instead of retrying the download each time.
RETRY_SECONDS = 60

Embedder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


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
    restart) and kept in memory. Passages scoring below min_score are not returned; when none
    are left, the keyword search decides, so a question that shares no words with the PDF still
    gets "The PDF does not say." without a model call.
    """

    def __init__(self, embed: Embedder, min_score: float = MIN_SCORE):
        self._embed = embed
        self._min_score = min_score
        self._vectors: dict[str, tuple[tuple[str, ...], np.ndarray]] = {}
        self._indexing: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        try:
            vectors = np.asarray(self._embed(list(texts)), dtype=np.float32)
        except Exception as exc:
            raise RuntimeError("The search model is unavailable.") from exc
        if vectors.ndim != 2 or len(vectors) != len(texts):
            raise RuntimeError("The search model is unavailable.")
        lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(lengths == 0, 1, lengths)

    def _cached(self, document_id: str, key: tuple[str, ...]) -> np.ndarray | None:
        with self._lock:
            cached = self._vectors.get(document_id)
        return cached[1] if cached and cached[0] == key else None

    def index(self, document_id: str, passages: list[Passage]) -> np.ndarray:
        """Embed a document's passages, unless the same passages are already cached.

        The document id is a hash of the PDF, so a re-upload reuses the cached vectors. Requests
        for one document wait for a single embedding instead of each computing their own.
        """
        key = tuple(passage.id for passage in passages)
        with self._lock:
            document_lock = self._indexing.setdefault(document_id, threading.Lock())
        with document_lock:
            vectors = self._cached(document_id, key)
            if vectors is None:
                vectors = self._encode([passage.text for passage in passages])
                with self._lock:
                    self._vectors[document_id] = (key, vectors)
        return vectors

    def retrieve(
        self, document_id: str, passages: list[Passage], question: str, limit: int = 3
    ) -> list[Passage]:
        if not passages:
            return []
        key = tuple(passage.id for passage in passages)
        vectors = self._cached(document_id, key)
        if vectors is None:
            vectors = self.index(document_id, passages)
        scores = vectors @ self._encode([question])[0]
        order = sorted(
            range(len(passages)), key=lambda i: (-scores[i], passages[i].page, passages[i].id)
        )
        best = [i for i in order[:limit] if scores[i] >= self._min_score]
        if not best:
            return retrieve(passages, question, limit)
        return [passages[i] for i in best]


def fastembed_embedder(model_name: str = MODEL_NAME) -> Embedder:
    """Load the fastembed model on first use, from FASTEMBED_CACHE_PATH when it is set.

    Only loading is locked. Embedding runs in parallel, so a question is not stuck behind
    another user's upload.
    """
    model = None
    failed_at: float | None = None
    lock = threading.Lock()

    def embed(texts: Sequence[str]) -> list[np.ndarray]:
        nonlocal model, failed_at
        with lock:
            if model is None:
                if failed_at is not None and time.monotonic() - failed_at < RETRY_SECONDS:
                    raise RuntimeError("The search model failed to load recently.")
                try:
                    from fastembed import TextEmbedding

                    model = TextEmbedding(model_name, enable_cpu_mem_arena=False)
                except Exception:
                    failed_at = time.monotonic()
                    raise
            loaded = model
        return list(loaded.embed(list(texts), batch_size=BATCH_SIZE))

    return embed


def create_search(mode: str | None = None) -> KeywordSearch | EmbeddingSearch:
    mode = (mode if mode is not None else os.environ.get("RETRIEVAL", "keyword")).strip().lower()
    if mode == "keyword":
        return KeywordSearch()
    if mode == "embedding":
        return EmbeddingSearch(fastembed_embedder())
    raise ValueError(f"Unsupported RETRIEVAL: {mode}. Use keyword or embedding.")
