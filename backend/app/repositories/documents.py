from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import DocumentVersion, SourceSpan


class DocumentRepository:
    """Append-only persistence for document versions and source spans.

    No update or delete methods are exposed by design.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def by_hash(self, content_sha256: str) -> DocumentVersion | None:
        return self._session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.content_sha256 == content_sha256
            )
        )

    def latest_for_source(self, source_url: str) -> DocumentVersion | None:
        return self._session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.source_url == source_url)
            .order_by(DocumentVersion.acquired_at.desc())
            .limit(1)
        )

    def insert_version(
        self,
        *,
        content_sha256: str,
        source_url: str,
        published_at: datetime | None,
        available_at: datetime,
        acquired_at: datetime,
        parser_version: str,
        supersedes_id: uuid.UUID | None,
    ) -> DocumentVersion:
        version = DocumentVersion(
            content_sha256=content_sha256,
            source_url=source_url,
            published_at=published_at,
            available_at=available_at,
            acquired_at=acquired_at,
            parser_version=parser_version,
            supersedes_id=supersedes_id,
        )
        self._session.add(version)
        self._session.flush()
        return version

    def insert_span(
        self,
        *,
        document_version_id: uuid.UUID,
        locator: dict,
        verbatim_text: str,
    ) -> SourceSpan:
        span = SourceSpan(
            document_version_id=document_version_id,
            locator=locator,
            verbatim_text=verbatim_text,
        )
        self._session.add(span)
        self._session.flush()
        return span

    # ------------------------------------------------------------------ readers

    def visible_versions(
        self, *, cutoff: datetime, limit: int
    ) -> list[DocumentVersion]:
        # A version is visible at cutoff only if it was available AND acquired
        # by then; filtering only available_at would allow hindsight leakage.
        return list(
            self._session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.available_at <= cutoff)
                .where(DocumentVersion.acquired_at <= cutoff)
                .order_by(
                    DocumentVersion.available_at.desc(), DocumentVersion.id.desc()
                )
                .limit(limit + 1)
            )
        )

    def spans_for_version(self, version_id: uuid.UUID) -> list[SourceSpan]:
        return list(
            self._session.scalars(
                select(SourceSpan)
                .where(SourceSpan.document_version_id == version_id)
                .order_by(SourceSpan.id)
            )
        )
