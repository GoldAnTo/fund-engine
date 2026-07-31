from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
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
        self,
        *,
        cutoff: datetime,
        limit: int,
        query: str | None = None,
        cursor_at: datetime | None = None,
        cursor_id: uuid.UUID | None = None,
    ) -> list[DocumentVersion]:
        # A version is visible at cutoff only if it was available AND acquired
        # by then; filtering only available_at would allow hindsight leakage.
        # `query` is pushed down to SQL so limit+1 reflects the filtered set.
        stmt = select(DocumentVersion).where(
            DocumentVersion.available_at <= cutoff,
            DocumentVersion.acquired_at <= cutoff,
        )
        if query:
            stmt = stmt.where(DocumentVersion.source_url.ilike(f"%{query}%"))
        if cursor_at is not None and cursor_id is not None:
            # Order is (available_at DESC, id DESC); fetch rows strictly before
            # the cursor tuple.
            stmt = stmt.where(
                or_(
                    DocumentVersion.available_at < cursor_at,
                    and_(
                        DocumentVersion.available_at == cursor_at,
                        DocumentVersion.id < cursor_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            DocumentVersion.available_at.desc(), DocumentVersion.id.desc()
        ).limit(limit + 1)
        return list(self._session.scalars(stmt))

    def spans_for_version(self, version_id: uuid.UUID) -> list[SourceSpan]:
        return list(
            self._session.scalars(
                select(SourceSpan)
                .where(SourceSpan.document_version_id == version_id)
                .order_by(SourceSpan.id)
            )
        )
