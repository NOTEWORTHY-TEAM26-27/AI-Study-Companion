"""Store PDFs and extracted passages on disk for the single-process prototype."""

import hashlib
import json
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from .core import Passage

LOGGER = logging.getLogger(__name__)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".upload-", delete=False)
    try:
        with temporary:
            temporary.write(data)
        Path(temporary.name).replace(path)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def save_document(data_dir: Path, name: str, pdf: bytes, passages: list[Passage]) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    document_id = hashlib.sha256(pdf).hexdigest()[:16]
    metadata = json.dumps(
        {"name": name, "passages": [asdict(passage) for passage in passages]}
    ).encode()
    _atomic_write(data_dir / f"{document_id}.pdf", pdf)
    # Publish metadata last: startup ignores PDFs left by an interrupted upload.
    _atomic_write(data_dir / f"{document_id}.json", metadata)
    return document_id


def load_documents(data_dir: Path) -> dict[str, tuple[str, list[Passage]]]:
    data_dir.mkdir(parents=True, exist_ok=True)
    documents = {}
    for path in sorted(data_dir.glob("*.json")):
        try:
            if not path.with_suffix(".pdf").is_file():
                raise ValueError("Original PDF is missing.")
            metadata = json.loads(path.read_text(encoding="utf-8"))
            name = metadata["name"]
            passages = [Passage(**item) for item in metadata["passages"]]
            if not isinstance(name, str) or not name.strip() or not passages:
                raise ValueError("Invalid document metadata.")
            if any(
                not isinstance(p.id, str)
                or not isinstance(p.page, int)
                or p.page < 1
                or not isinstance(p.text, str)
                or not p.text.strip()
                for p in passages
            ):
                raise ValueError("Invalid passage metadata.")
            documents[path.stem] = (name, passages)
        except (OSError, ValueError, TypeError, KeyError):
            LOGGER.warning("Skipping unreadable stored document %s", path.name)
    return documents
