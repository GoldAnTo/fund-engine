"""Document library read assembly for the v1 API."""

from __future__ import annotations

import base64
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
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

_REVIEWED_STATES = frozenset({"reviewed"})
_RESEARCH_STATES = frozenset({"reviewed", "machine_generated"})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _encode_cursor(available_at: datetime, version_id: uuid.UUID) -> str:
    payload = json.dumps(
        {"available_at": available_at.isoformat(), "id": str(version_id)}
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(raw)
        available_at = datetime.fromisoformat(data["available_at"])
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        return available_at, uuid.UUID(data["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailedError("malformed cursor") from exc


class DocumentReadQueries:
    """Read-only document library assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._docs = DocumentRepository(session)
        self._research = ResearchRepository(session)

    def list_documents(
        self,
        *,
        query: str | None,
        basis: HistoricalBasis,
        limit: int,
        cursor: str | None,
    ) -> DocumentListResponse:
        cursor_at, cursor_id = (None, None)
        if cursor is not None:
            cursor_at, cursor_id = _decode_cursor(cursor)
        versions = self._docs.visible_versions(
            cutoff=basis.cutoff,
            limit=limit,
            query=query,
            cursor_at=cursor_at,
            cursor_id=cursor_id,
        )
        has_more = len(versions) > limit
        page_items = versions[:limit]
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = _encode_cursor(last.available_at, last.id)
        items: list[DocumentSummaryDTO] = []
        for version in page_items:
            spans = self._docs.spans_for_version(version.id)
            statements = self._research.statements_for_span_ids(
                [s.id for s in spans]
            )
            items.append(
                self._summary(
                    version, len(spans), len(statements), spans=spans
                )
            )
        return DocumentListResponse(
            basis=basis.to_dto(),
            items=items,
            page=CursorPage(next_cursor=next_cursor, has_more=has_more),
        )

    def detail(
        self, *, version_id: uuid.UUID, research_mode: bool = False
    ) -> DocumentDetailResponse:
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

        # AI/human boundary (design 9.2/9.3): machine-generated citations are
        # hidden by default; rejected is never returned.
        allowed_states = _RESEARCH_STATES if research_mode else _REVIEWED_STATES
        links = [
            link
            for link in self._research.links_for_statement_ids(
                [st.id for st in statements]
            )
            if link.review_state in allowed_states
        ]
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
            document=self._summary(
                version, len(spans), len(statements), spans=spans
            ),
            spans=span_dtos,
        )

    @staticmethod
    def _locator_metadata(spans: list) -> dict:
        """Pick display metadata (title/org/kind) from span locators.

        Prefers the first locator that actually carries a ``title``; falls
        back to the first locator with any of the known keys.  Never invents
        values — missing keys stay absent so the DTO fields remain ``None``.
        """
        candidates = [s.locator for s in spans if isinstance(s.locator, dict)]
        for loc in candidates:
            if loc.get("title"):
                return {
                    "title": loc.get("title") or None,
                    "org": loc.get("org") or None,
                    "doc_kind": loc.get("kind") or None,
                }
        for loc in candidates:
            if loc.get("kind") or loc.get("org"):
                return {
                    "title": loc.get("title") or None,
                    "org": loc.get("org") or None,
                    "doc_kind": loc.get("kind") or None,
                }
        return {"title": None, "org": None, "doc_kind": None}

    def _summary(
        self,
        version: DocumentVersion,
        span_count: int,
        statement_count: int,
        *,
        spans: list | None = None,
    ) -> DocumentSummaryDTO:
        meta = self._locator_metadata(spans or [])
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
            title=meta["title"],
            org=meta["org"],
            doc_kind=meta["doc_kind"],
        )
