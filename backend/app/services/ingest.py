from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from app.models.ledger import DocumentVersion, SourceSpan
from app.repositories.documents import DocumentRepository

PARSER_VERSION = "docling-v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentService:
    """Freezes source material into immutable, content-addressed versions.

    Re-freezing identical bytes returns the existing version. Changed bytes
    append a new version that supersedes the previous one for the same source.
    """

    def __init__(self, repository: DocumentRepository) -> None:
        self._repo = repository

    def freeze(
        self,
        raw: bytes,
        source_url: str,
        published_at: datetime | None = None,
    ) -> DocumentVersion:
        digest = hashlib.sha256(raw).hexdigest()
        existing = self._repo.by_hash(digest)
        if existing is not None:
            return existing
        prior = self._repo.latest_for_source(source_url)
        supersedes_id = (
            prior.id
            if prior is not None and prior.content_sha256 != digest
            else None
        )
        now = _utcnow()
        return self._repo.insert_version(
            content_sha256=digest,
            source_url=source_url,
            published_at=published_at,
            available_at=now,
            acquired_at=now,
            parser_version=PARSER_VERSION,
            supersedes_id=supersedes_id,
        )

    def add_span(
        self,
        document_version_id: uuid.UUID,
        locator: dict,
        verbatim_text: str,
    ) -> SourceSpan:
        return self._repo.insert_span(
            document_version_id=document_version_id,
            locator=locator,
            verbatim_text=verbatim_text,
        )
