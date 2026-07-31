"""Grouped ledger search assembly for the v1 API.

Case-insensitive SQL matching across the append-only ledger. Historical replay
(design 10): every type is cutoff-filtered. AI/human boundary (design 9.2/9.3):
machine-generated evidence hits are hidden by default and only revealed under
an explicit research mode; rejected is never returned.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ValidationFailedError
from app.models.ledger import (
    Company,
    EvidenceLink,
    Fund,
    ResearchCase,
    SourceStatement,
    Stock,
    Thesis,
)
from app.queries.basis import HistoricalBasis
from app.schemas.v1.common import CursorPage
from app.schemas.v1.search import SearchGroupDTO, SearchHitDTO, SearchResponse

_VALID_TYPES = ("case", "thesis", "evidence", "company", "stock", "fund")
_VALID_TYPES_SET = frozenset(_VALID_TYPES)
_REVIEWED_STATES = frozenset({"reviewed"})
_RESEARCH_STATES = frozenset({"reviewed", "machine_generated"})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class LedgerSearchQueries:
    """Read-only grouped search across the ledger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def search(
        self,
        *,
        q: str,
        types: set[str] | None,
        basis: HistoricalBasis,
        limit: int,
        research_mode: bool = False,
    ) -> SearchResponse:
        requested = types if types is not None else _VALID_TYPES_SET
        unknown = requested - _VALID_TYPES_SET
        if unknown:
            raise ValidationFailedError(
                f"unknown search types: {','.join(sorted(unknown))}"
            )
        needle = f"%{q.lower()}%"
        cutoff = basis.cutoff
        allowed_states = _RESEARCH_STATES if research_mode else _REVIEWED_STATES

        groups: list[SearchGroupDTO] = []
        has_more = False
        for object_type in _VALID_TYPES:
            if object_type not in requested:
                continue
            hits = self._search_type(
                object_type, needle, cutoff, limit, allowed_states
            )
            if len(hits) > limit:
                has_more = True
                hits = hits[:limit]
            groups.append(SearchGroupDTO(object_type=object_type, hits=hits))

        return SearchResponse(
            basis=basis.to_dto(),
            groups=groups,
            page=CursorPage(has_more=has_more),
        )

    def _search_type(
        self,
        object_type: str,
        needle: str,
        cutoff: datetime,
        limit: int,
        allowed_states: frozenset[str],
    ) -> list[SearchHitDTO]:
        if object_type == "case":
            rows = self._session.scalars(
                select(ResearchCase)
                .where(func.lower(ResearchCase.title).like(needle))
                .where(ResearchCase.created_at <= cutoff)
                .limit(limit + 1)
            )
            return [
                SearchHitDTO(
                    object_type="case",
                    object_id=str(r.id),
                    title=r.title,
                    snippet=r.industry_topic or "",
                    case_id=str(r.id),
                    review_state=None,
                    available_at=None,
                    deep_link=f"/research-cases/{r.id}",
                )
                for r in rows
            ]

        if object_type == "thesis":
            rows = self._session.scalars(
                select(Thesis)
                .join(ResearchCase, ResearchCase.id == Thesis.research_case_id)
                .where(func.lower(Thesis.statement).like(needle))
                .where(Thesis.created_at <= cutoff)
                .where(ResearchCase.created_at <= cutoff)
                .limit(limit + 1)
            )
            return [
                SearchHitDTO(
                    object_type="thesis",
                    object_id=str(r.id),
                    title=r.statement,
                    snippet=r.statement,
                    case_id=str(r.research_case_id),
                    review_state=None,
                    available_at=None,
                    deep_link=f"/research-cases/{r.research_case_id}/dossier",
                )
                for r in rows
            ]

        if object_type == "evidence":
            rows = self._session.execute(
                select(SourceStatement, EvidenceLink, Thesis)
                .join(
                    EvidenceLink,
                    EvidenceLink.source_statement_id == SourceStatement.id,
                )
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .join(ResearchCase, ResearchCase.id == Thesis.research_case_id)
                .where(func.lower(SourceStatement.normalized_text).like(needle))
                .where(EvidenceLink.available_at <= cutoff)
                .where(EvidenceLink.created_at <= cutoff)
                .where(SourceStatement.created_at <= cutoff)
                .where(Thesis.created_at <= cutoff)
                .where(ResearchCase.created_at <= cutoff)
                .where(EvidenceLink.review_state.in_(list(allowed_states)))
                .limit(limit + 1)
            )
            hits: list[SearchHitDTO] = []
            for statement, link, thesis in rows:
                hits.append(
                    SearchHitDTO(
                        object_type="evidence",
                        object_id=str(link.id),
                        title=statement.normalized_text,
                        snippet=statement.normalized_text,
                        case_id=str(thesis.research_case_id),
                        review_state=link.review_state,
                        available_at=_iso(link.available_at),
                        deep_link=f"/research-cases/{thesis.research_case_id}/dossier",
                    )
                )
            return hits

        if object_type == "company":
            rows = self._session.scalars(
                select(Company)
                .where(func.lower(Company.name).like(needle))
                .where(Company.created_at <= cutoff)
                .limit(limit + 1)
            )
            return [
                SearchHitDTO(
                    object_type="company",
                    object_id=str(r.id),
                    title=r.name,
                    snippet=r.code or "",
                    case_id=None,
                    review_state=None,
                    available_at=None,
                    deep_link=f"/instruments/companies/{r.id}",
                )
                for r in rows
            ]

        if object_type == "stock":
            rows = self._session.scalars(
                select(Stock)
                .where(func.lower(Stock.name).like(needle))
                .where(Stock.created_at <= cutoff)
                .limit(limit + 1)
            )
            return [
                SearchHitDTO(
                    object_type="stock",
                    object_id=str(r.id),
                    title=r.name,
                    snippet=r.code or "",
                    case_id=None,
                    review_state=None,
                    available_at=None,
                    deep_link=f"/instruments/stocks/{r.id}",
                )
                for r in rows
            ]

        # fund
        rows = self._session.scalars(
            select(Fund)
            .where(func.lower(Fund.name).like(needle))
            .where(Fund.created_at <= cutoff)
            .limit(limit + 1)
        )
        return [
            SearchHitDTO(
                object_type="fund",
                object_id=str(r.id),
                title=r.name,
                snippet=r.code or "",
                case_id=None,
                review_state=None,
                available_at=None,
                deep_link=f"/instruments/funds/{r.id}",
            )
            for r in rows
        ]
