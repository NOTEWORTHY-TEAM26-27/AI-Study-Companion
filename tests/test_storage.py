import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteworthy_app.core import extract_passages
from noteworthy_app.server import create_server
from noteworthy_app.storage import load_documents, save_document
from tests.fixtures import study_pdf


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name) / "documents"

    def test_incomplete_document_is_not_loaded(self):
        document_id = save_document(
            self.data_dir, "biology.pdf", study_pdf(), extract_passages(study_pdf())
        )
        (self.data_dir / f"{document_id}.pdf").unlink()
        with self.assertLogs("noteworthy_app.storage", level="WARNING"):
            self.assertEqual(load_documents(self.data_dir), {})

    def test_bad_metadata_does_not_hide_valid_documents(self):
        document_id = save_document(
            self.data_dir, "biology.pdf", study_pdf(), extract_passages(study_pdf())
        )
        (self.data_dir / "broken.pdf").write_bytes(study_pdf())
        for metadata in [
            "not json",
            json.dumps({"name": None, "passages": []}),
            json.dumps(
                {"name": "bad.pdf", "passages": [{"id": "p1-1", "page": "one", "text": "text"}]}
            ),
        ]:
            (self.data_dir / "broken.json").write_text(metadata)
            with (
                self.subTest(metadata=metadata),
                self.assertLogs("noteworthy_app.storage", level="WARNING"),
            ):
                documents = load_documents(self.data_dir)
            self.assertEqual(list(documents), [document_id])

    def test_incomplete_save_does_not_create_loadable_record(self):
        self.data_dir.mkdir()
        pdf = study_pdf()
        document_id = hashlib.sha256(pdf).hexdigest()[:16]
        (self.data_dir / f"{document_id}.json").mkdir()
        with self.assertRaises(OSError):
            save_document(self.data_dir, "biology.pdf", pdf, extract_passages(pdf))
        self.assertEqual(list(self.data_dir.glob(".upload-*")), [])
        with self.assertLogs("noteworthy_app.storage", level="WARNING"):
            self.assertEqual(load_documents(self.data_dir), {})

    def test_documents_dir_environment_selects_storage_location(self):
        with patch.dict("os.environ", {"DOCUMENTS_DIR": str(self.data_dir)}):
            server = create_server("127.0.0.1", 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.data_dir, self.data_dir)
        self.assertTrue(self.data_dir.is_dir())
