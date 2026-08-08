"""Content-addressed, local immutable storage for directly uploaded documents."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from app.models.ledger import DocumentBlob


class BlobIntegrityError(RuntimeError):
    """Raised when an immutable object is missing or does not match its ledger."""


class LocalImmutableBlobStore:
    """Persist and retrieve bytes under a SHA-256 content-addressed key.

    The backing directory is explicit through ``DOCUMENT_BLOB_DIR`` and
    defaults to a backend-local runtime directory. Existing content is never
    overwritten: a repeated upload verifies and reuses the same object.
    """

    def __init__(self, root: Path | None = None) -> None:
        configured = os.environ.get("DOCUMENT_BLOB_DIR")
        self._root = (
            root
            or (Path(configured) if configured else Path(__file__).resolve().parents[2] / "var" / "document-blobs")
        ).resolve()

    def persist(self, raw: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(raw).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}"
        target = self._path_for_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        except FileExistsError:
            self._verify_bytes(key, raw, digest)
            return key, digest
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            os.fchmod(fd, 0o444)
        except Exception:
            os.close(fd)
            # Only remove the exact object this invocation created. Leaving a
            # partial content-addressed object would make every later retry
            # fail its integrity check forever.
            target.unlink(missing_ok=True)
            raise
        else:
            os.close(fd)
        self._verify_bytes(key, raw, digest)
        return key, digest

    def read(self, blob: DocumentBlob) -> bytes:
        path = self._path_for_key(blob.storage_key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobIntegrityError("uploaded document blob is unavailable") from exc
        actual = hashlib.sha256(raw).hexdigest()
        if actual != blob.content_sha256 or len(raw) != blob.byte_size:
            raise BlobIntegrityError("uploaded document blob failed integrity verification")
        return raw

    def _verify_bytes(self, key: str, expected: bytes, digest: str) -> None:
        path = self._path_for_key(key)
        try:
            current = path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobIntegrityError("immutable blob was not written") from exc
        if current != expected or hashlib.sha256(current).hexdigest() != digest:
            raise BlobIntegrityError("existing immutable blob does not match content hash")

    def _path_for_key(self, storage_key: str) -> Path:
        key = PurePosixPath(storage_key)
        if key.is_absolute() or ".." in key.parts:
            raise BlobIntegrityError("invalid immutable blob key")
        path = (self._root / Path(*key.parts)).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise BlobIntegrityError("immutable blob key escapes configured storage") from exc
        return path
