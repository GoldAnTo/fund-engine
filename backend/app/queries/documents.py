"""Document library read assembly for the v1 API."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import DocumentVersion
from app.queries.basis import HistoricalBasis
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.common import CursorPage
from app.schemas.v1.documents import (
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentSummaryDTO,
    SourceSpanDTO,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class DocumentReadQueries:
    """Read-only document library assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._docs = DocumentRepository(session)
        self._research = ResearchRepository(session)

    def list_documents(
        self, *, query: str | None, basis: HistoricalBasis, limit: int
    ) -> DocumentListResponse:
        versions = self._docs.visible_versions(cutoff=basis.cutoff, limit=limit)
        has_more = len(versions) > limit
        page_items = versions[:limit]
        items: list[DocumentSummaryDTO] = []
        for version in page_items:
            if query is not None and query.lower() not in version.source_url.lower():
                continue
            spans = self._docs.spans_for_version(version.id)
            statements = self._research.statements_for_span_ids(
                [s.id for s in spans]
            )
            items.append(self._summary(version, len(spans), len(statements)))
        return DocumentListResponse(
            basis=basis.to_dto(),
            items=items,
            page=CursorPage(has_more=has_more),
        )

    def detail(self, *, version_id: uuid.UUID) -> DocumentDetailResponse:
        version = self._session.get(DocumentVersion, version_id)
        if version is None:
            raise NotFoundError("document version not found")

        spans = self._docs.spans_for_version(version_id)
        statements = self._research.statements_for_span_ids(
            [s.id for s in spans]
        )
        span_to_statements: dict[uuid.UUID, list] = defaultdict(list)
        for st in statements:
            span_to_statements[st.source_span_id].append(st)

        links = self._research.links_for_statement_ids(
            [st.id for st in statements]
        )
        stmt_to_links: dict[uuid.UUID, list] = defaultdict(list)
        for link in links:
            stmt_to_links[link.source_statement_id].append(link)

        span_dtos: list[SourceSpanDTO] = []
        for span in spans:
            citations: list[dict] = []
            for st in span_to_statements.get(span.id, []):
                for link in stmt_to_links.get(st.id, []):
                    citations.append(
                        {
                            "link_id": str(link.id),
                            "thesis_id": str(link.thesis_id),
                            "role": link.role,
                            "review_state": link.review_state,
                        }
                    )
            span_dtos.append(
                SourceSpanDTO(
                    id=str(span.id),
                    document_version_id=str(version_id),
                    locator=span.locator,
                    verbatim_text=span.verbatim_text,
                    citations=citations,
                )
            )

        return DocumentDetailResponse(
            document=self._summary(version, len(spans), len(statements)),
            spans=span_dtos,
        )

    def _summary(
        self,
        version: DocumentVersion,
        span_count: int,
        statement_count: int,
    ) -> DocumentSummaryDTO:
        return DocumentSummaryDTO(
            id=str(version.id),
            content_sha256=version.content_sha256,
            source_url=version.source_url,
            published_at=_iso(version.published_at),
            available_at=_iso(version.available_at),
            acquired_at=_iso(version.acquired_at),
            parser_version=version.parser_version,
            supersedes_id=(
                str(version.supersedes_id) if version.supersedes_id else None
            ),
            span_count=span_count,
            statement_count=statement_count,
            parse_state="parsed" if span_count >= 1 else "unparsed",
        )
