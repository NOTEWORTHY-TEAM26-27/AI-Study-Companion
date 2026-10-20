import os
import unittest
from pathlib import Path
from unittest import mock

from noteworthy_app.core import Passage
from noteworthy_app.search import (
    MODEL_NAME,
    EmbeddingSearch,
    KeywordSearch,
    create_search,
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

    def test_ranks_passages_by_meaning(self):
        matches = self.search.retrieve("doc", PASSAGES, "How do plants make food?")
        self.assertEqual([passage.id for passage in matches], ["p1-1", "p1-2", "p2-1"])
        matches = self.search.retrieve("doc", PASSAGES, "Where are proteins built?", limit=1)
        self.assertEqual([passage.id for passage in matches], ["p2-1"])

    def test_passages_are_embedded_once_per_document(self):
        self.search.index("doc", PASSAGES)
        self.search.retrieve("doc", PASSAGES, "How do plants make food?")
        self.search.retrieve("doc", PASSAGES, "Where are proteins built?")
        passage_calls = [call for call in self.embed.calls if len(call) == len(PASSAGES)]
        self.assertEqual(len(passage_calls), 1)

    def test_changed_passages_are_embedded_again(self):
        self.search.index("doc", PASSAGES[:2])
        matches = self.search.retrieve("doc", PASSAGES, "Where are proteins built?", limit=1)
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
        search = EmbeddingSearch(lambda texts: [[0.0, 0.0] for _ in texts])
        self.assertEqual(search.retrieve("doc", PASSAGES, "quantum gravity"), [])


class CreateSearchTests(unittest.TestCase):
    def test_keyword_is_the_default(self):
        with mock.patch.dict(os.environ, clear=True):
            self.assertIsInstance(create_search(), KeywordSearch)

    def test_embedding_mode_loads_the_model_on_first_use(self):
        with mock.patch.dict(os.environ, {"RETRIEVAL": " Embedding "}):
            self.assertIsInstance(create_search(), EmbeddingSearch)

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
