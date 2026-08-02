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

    def by_natural_key(self, natural_key: str) -> DocumentVersion | None:
        # 跨入口合并"同源 + 同标题 + 同发布日期"：返回最早写入的版本，
        # 后续抓取视为该文档的重复抓取而非新版本。
        return self._session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.natural_key == natural_key)
            .order_by(DocumentVersion.acquired_at.asc())
            .limit(1)
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
        natural_key: str | None = None,
        title: str | None = None,
        byte_size: int | None = None,
        language: str | None = None,
        parse_state: str = "success",
    ) -> DocumentVersion:
        version = DocumentVersion(
            content_sha256=content_sha256,
            source_url=source_url,
            natural_key=natural_key,
            published_at=published_at,
            available_at=available_at,
            acquired_at=acquired_at,
            parser_version=parser_version,
            supersedes_id=supersedes_id,
            title=title,
            byte_size=byte_size,
            language=language,
            parse_state=parse_state,
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
        text_sha256: str | None = None,
        context_hash: str | None = None,
        locator_v1: dict | None = None,
    ) -> SourceSpan:
        span = SourceSpan(
            document_version_id=document_version_id,
            locator=locator,
            verbatim_text=verbatim_text,
            text_sha256=text_sha256,
            context_hash=context_hash,
            locator_v1=locator_v1,
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
