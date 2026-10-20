import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from noteworthy_app.core import Passage
from noteworthy_app.search import (
    BATCH_SIZE,
    MODEL_NAME,
    EmbeddingSearch,
    KeywordSearch,
    create_search,
    fastembed_embedder,
)

PASSAGES = [
    Passage("p1-1", 1, "Photosynthesis converts sunlight into chemical energy."),
    Passage("p1-2", 1, "Mitochondria release energy from nutrients."),
    Passage("p2-1", 2, "Ribosomes assemble proteins."),
]

# Hand-made vectors stand in for the model so tests stay offline and fast.
VECTORS = {
    PASSAGES[0].text: [1.0, 0.0, 0.0],
    PASSAGES[1].text: [0.6, 0.8, 0.0],
    PASSAGES[2].text: [0.0, 0.0, 1.0],
    "How do plants make food?": [1.0, 0.1, 0.0],
    "Where are proteins built?": [0.0, 0.0, 2.0],
    "quantum gravity": [0.0, -1.0, 0.0],
    "Mitochondria": [0.0, -1.0, 0.0],
}


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [VECTORS[text] for text in texts]


class EmbeddingSearchTests(unittest.TestCase):
    def setUp(self):
        self.embed = FakeEmbedder()
        self.search = EmbeddingSearch(self.embed)

    def test_ranks_passages_by_meaning_and_drops_weak_matches(self):
        matches = self.search.retrieve("doc", PASSAGES, "How do plants make food?")
        self.assertEqual([passage.id for passage in matches], ["p1-1", "p1-2"])
        matches = self.search.retrieve("doc", PASSAGES, "Where are proteins built?", limit=1)
        self.assertEqual([passage.id for passage in matches], ["p2-1"])

    def test_passages_are_embedded_once_per_document(self):
        self.search.index("doc", PASSAGES)
        self.search.retrieve("doc", PASSAGES, "How do plants make food?")
        self.search.retrieve("doc", PASSAGES, "Where are proteins built?")
        passage_calls = [call for call in self.embed.calls if len(call) == len(PASSAGES)]
        self.assertEqual(len(passage_calls), 1)

    def test_reupload_reuses_cached_vectors(self):
        self.search.index("doc", PASSAGES)
        self.search.index("doc", PASSAGES)
        self.assertEqual(len(self.embed.calls), 1)

    def test_simultaneous_first_questions_embed_the_document_once(self):
        started = threading.Event()

        def slow_embed(texts):
            started.set()
            time.sleep(0.05)
            return self.embed(texts)

        search = EmbeddingSearch(slow_embed)
        threads = [
            threading.Thread(
                target=search.retrieve, args=("doc", PASSAGES, "How do plants make food?")
            )
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        passage_calls = [call for call in self.embed.calls if len(call) == len(PASSAGES)]
        self.assertTrue(started.is_set())
        self.assertEqual(len(passage_calls), 1)

    def test_changed_passages_are_embedded_again(self):
        self.search.index("doc", PASSAGES[:2])
        matches = self.search.retrieve("doc", PASSAGES, "Where are proteins built?", limit=1)
        self.assertEqual([passage.id for passage in matches], ["p2-1"])

    def test_returns_at_most_limit_passages(self):
        passages = [Passage(f"p{n}-1", n, f"text {n}") for n in range(1, 6)]
        search = EmbeddingSearch(lambda texts: [[1.0, 0.05 * len(text)] for text in texts])
        self.assertEqual(len(search.retrieve("doc", passages, "q")), 3)
        self.assertEqual(len(search.retrieve("doc", passages, "q", limit=2)), 2)

    def test_score_threshold_is_point_two_five(self):
        vectors = {"above": [0.3, 0.954], "below": [0.2, 0.98], "question": [1.0, 0.0]}
        passages = [Passage("p1-1", 1, "below"), Passage("p2-1", 2, "above")]
        search = EmbeddingSearch(lambda texts: [vectors[text] for text in texts])
        matches = search.retrieve("doc", passages, "question")
        self.assertEqual([passage.id for passage in matches], ["p2-1"])

    def test_low_scores_fall_back_to_keyword_search(self):
        self.assertEqual(self.search.retrieve("doc", PASSAGES, "quantum gravity"), [])
        matches = self.search.retrieve("doc", PASSAGES, "Mitochondria")
        self.assertEqual([passage.id for passage in matches], ["p1-2"])

    def test_model_errors_are_reported_as_unavailable(self):
        def broken(texts):
            raise OSError("model files missing")

        with self.assertRaisesRegex(RuntimeError, "search model is unavailable"):
            EmbeddingSearch(broken).index("doc", PASSAGES)
        with self.assertRaisesRegex(RuntimeError, "search model is unavailable"):
            EmbeddingSearch(lambda texts: []).retrieve("doc", PASSAGES, "quantum gravity")

    def test_zero_vectors_do_not_divide_by_zero(self):
        vectors = {PASSAGES[0].text: [0.0, 0.0], PASSAGES[1].text: [0.0, 3.0], "q": [0.0, 1.0]}
        search = EmbeddingSearch(lambda texts: [vectors.get(text, [0.0, 0.0]) for text in texts])
        with np.errstate(all="raise"):
            matches = search.retrieve("doc", PASSAGES, "q")
        self.assertEqual([passage.id for passage in matches], ["p1-2"])

    def test_no_passages_returns_nothing(self):
        self.assertEqual(self.search.retrieve("doc", [], "quantum gravity"), [])
        self.assertEqual(self.embed.calls, [])


class FakeTextEmbedding:
    """Stands in for fastembed.TextEmbedding; records how the app calls it."""

    created = []
    batch_sizes = []
    fail = False
    meeting = None

    def __init__(self, model_name, **options):
        FakeTextEmbedding.created.append((model_name, options))
        time.sleep(0.05)
        if FakeTextEmbedding.fail:
            raise OSError("download failed")

    def embed(self, texts, batch_size):
        FakeTextEmbedding.batch_sizes.append(batch_size)
        if FakeTextEmbedding.meeting:
            FakeTextEmbedding.meeting.wait()
        return iter([[1.0, 0.0] for _ in texts])


class FastembedEmbedderTests(unittest.TestCase):
    def setUp(self):
        FakeTextEmbedding.created = []
        FakeTextEmbedding.batch_sizes = []
        FakeTextEmbedding.fail = False
        FakeTextEmbedding.meeting = None
        module = types.ModuleType("fastembed")
        module.TextEmbedding = FakeTextEmbedding
        self.enterContext(mock.patch.dict(sys.modules, {"fastembed": module}))

    def test_model_loads_once_with_low_memory_settings(self):
        embed = fastembed_embedder()
        self.assertEqual(FakeTextEmbedding.created, [])
        self.assertEqual(len(embed(["a", "b"])), 2)
        embed(["c"])
        self.assertEqual(FakeTextEmbedding.created, [(MODEL_NAME, {"enable_cpu_mem_arena": False})])
        self.assertEqual(FakeTextEmbedding.batch_sizes, [BATCH_SIZE, BATCH_SIZE])

    def test_parallel_first_calls_load_once_and_embed_side_by_side(self):
        # Both embeds must be running at the same time to pass the barrier, so a question
        # is never queued behind another request's whole upload.
        FakeTextEmbedding.meeting = threading.Barrier(2, timeout=5)
        embed = fastembed_embedder()
        results = []
        threads = [threading.Thread(target=lambda: results.append(embed(["a"]))) for _ in "ab"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(FakeTextEmbedding.created), 1)

    def test_failed_load_is_not_retried_right_away(self):
        FakeTextEmbedding.fail = True
        embed = fastembed_embedder()
        with self.assertRaises(OSError):
            embed(["a"])
        with self.assertRaisesRegex(RuntimeError, "failed to load recently"):
            embed(["a"])
        self.assertEqual(len(FakeTextEmbedding.created), 1)
        FakeTextEmbedding.fail = False
        with mock.patch("noteworthy_app.search.time.monotonic", return_value=float("inf")):
            self.assertEqual(len(embed(["a"])), 1)
        self.assertEqual(len(FakeTextEmbedding.created), 2)


class CreateSearchTests(unittest.TestCase):
    def test_keyword_is_the_default(self):
        with mock.patch.dict(os.environ, clear=True):
            self.assertIsInstance(create_search(), KeywordSearch)

    def test_embedding_mode_loads_the_model_on_first_use(self):
        with mock.patch.dict(os.environ, {"RETRIEVAL": " Embedding "}):
            self.assertIsInstance(create_search(), EmbeddingSearch)
        self.assertNotIn("fastembed", sys.modules)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported RETRIEVAL"):
            create_search("vector")

    def test_keyword_search_matches_core_retrieve(self):
        matches = KeywordSearch().retrieve("doc", PASSAGES, "proteins")
        self.assertEqual([passage.id for passage in matches], ["p2-1"])

    def test_docker_image_downloads_the_same_model(self):
        dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
        self.assertIn(f"TextEmbedding('{MODEL_NAME}')", dockerfile.read_text())


if __name__ == "__main__":
    unittest.main()
