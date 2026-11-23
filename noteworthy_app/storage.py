"""Store source files and extracted passages on disk for the single-process prototype."""

import hashlib
import json
import logging
import math
import re
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .core import Passage

LOGGER = logging.getLogger(__name__)
OWNER = re.compile(r"[0-9a-f]{32}")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".upload-", delete=False)
    try:
        with temporary:
            temporary.write(data)
        Path(temporary.name).replace(path)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def save_document(
    data_dir: Path, name: str, pdf: bytes, passages: list[Passage], owner: str | None = None
) -> str:
    """Save a document and its passages; return its ID. owner is a student's account ID."""
    data_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix.lower()
    if suffix not in {".pdf", ".vtt", ".txt"}:
        raise ValueError("Choose a PDF, VTT or TXT file.")
    record = {"name": name, "passages": [asdict(passage) for passage in passages]}
    if suffix != ".pdf":
        record["file_suffix"] = suffix
    if owner is None:
        document_id = hashlib.sha256(pdf).hexdigest()[:16]
    else:
        # The owner is part of the ID, so two students with the same PDF never share files.
        document_id = hashlib.sha256(owner.encode() + b":" + pdf).hexdigest()[:16]
        record["owner"] = owner
    metadata = json.dumps(record).encode()
    _atomic_write(data_dir / f"{document_id}{suffix}", pdf)
    # Publish metadata last: startup ignores PDFs left by an interrupted upload.
    _atomic_write(data_dir / f"{document_id}.json", metadata)
    return document_id


def uploaded_at(data_dir: Path, document_id: str, name: str = "document.pdf") -> str | None:
    """Return when the source was last saved, in UTC, or None if it is gone."""
    try:
        modified = (data_dir / f"{document_id}{Path(name).suffix.lower()}").stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, timezone.utc).isoformat()


def load_documents(data_dir: Path) -> dict[str, tuple[str, list[Passage], str | None]]:
    """Return each stored document's name, passages and owner (None before accounts)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    documents = {}
    for path in sorted(data_dir.glob("*.json")):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("Invalid document metadata.")
            suffix = metadata.get("file_suffix", ".pdf")
            if suffix not in {".pdf", ".vtt", ".txt"} or not path.with_suffix(suffix).is_file():
                raise ValueError("Original document is missing.")
            name = metadata["name"]
            passages = [Passage(**item) for item in metadata["passages"]]
            if not isinstance(name, str) or not name.strip() or not passages:
                raise ValueError("Invalid document metadata.")
            if any(
                not isinstance(p.id, str)
                or (p.page is not None and (type(p.page) is not int or p.page < 1))
                or (suffix == ".pdf") != (p.page is not None)
                or (
                    p.start_seconds is not None
                    and (
                        type(p.start_seconds) not in {int, float}
                        or not math.isfinite(p.start_seconds)
                        or p.start_seconds < 0
                    )
                )
                or not isinstance(p.text, str)
                or not p.text.strip()
                for p in passages
            ):
                raise ValueError("Invalid passage metadata.")
            # Ownership is read from the metadata, so an old document can be given to a student.
            owner = metadata.get("owner")
            if owner is not None and (not isinstance(owner, str) or not OWNER.fullmatch(owner)):
                raise ValueError("Invalid document owner.")
            documents[path.stem] = (name, passages, owner)
        except (OSError, ValueError, TypeError, KeyError):
            LOGGER.warning("Skipping unreadable stored document %s", path.name)
    return documents
