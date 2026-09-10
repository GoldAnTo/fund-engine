"""Grouped ledger search assembly for the v1 API.

Case-insensitive SQL matching across the append-only ledger. Historical replay
(design 10): every type is cutoff-filtered. AI/human boundary (design 9.2/9.3):
machine-generated evidence hits are hidden by default and only revealed under
an explicit research mode; rejected is never returned.

Append-only review state: the frozen ``EvidenceLink.review_state`` column never
changes after insert, so evidence visibility is derived from the latest
``evidence_reviews`` row at/before the cutoff — the same effective-state
derivation dossier/graph/knowledge/compare use
(``app/queries/effective_state.py``), here pushed into SQL so the
``limit + 1`` / ``has_more`` contract stays exact.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.errors import ValidationFailedError
from app.models.ledger import (
    Company,
    CaseTenantAdmission,
    EvidenceLink,
    EvidenceReview,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceStatement,
    Stock,
    ThemeRole,
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
        tenant_id: str,
        authorized_case_ids: Select[tuple[uuid.UUID]],
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
                object_type,
                needle,
                cutoff,
                limit,
                allowed_states,
                tenant_id,
                authorized_case_ids,
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
        tenant_id: str,
        authorized_case_ids: Select[tuple[uuid.UUID]],
    ) -> list[SearchHitDTO]:
        if object_type == "case":
            query = (
                select(ResearchCase)
                .where(func.lower(ResearchCase.title).like(needle))
                .where(ResearchCase.created_at <= cutoff)
                .where(ResearchCase.id.in_(authorized_case_ids))
            )
            query = query.join(
                CaseTenantAdmission,
                CaseTenantAdmission.research_case_id == ResearchCase.id,
            ).where(CaseTenantAdmission.tenant_id == tenant_id).where(
                CaseTenantAdmission.admitted_at <= cutoff
            )
            rows = self._session.scalars(query.limit(limit + 1))
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
            query = (
                select(Thesis)
                .join(ResearchCase, ResearchCase.id == Thesis.research_case_id)
                .where(func.lower(Thesis.statement).like(needle))
                .where(Thesis.created_at <= cutoff)
                .where(ResearchCase.created_at <= cutoff)
                .where(ResearchCase.id.in_(authorized_case_ids))
            )
            query = query.join(
                CaseTenantAdmission,
                CaseTenantAdmission.research_case_id == ResearchCase.id,
            ).where(CaseTenantAdmission.tenant_id == tenant_id).where(
                CaseTenantAdmission.admitted_at <= cutoff
            )
            rows = self._session.scalars(query.limit(limit + 1))
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
            # Derive the effective review state from the latest
            # evidence_reviews row at/before the cutoff; the frozen column
            # alone would keep human-confirmed links machine_generated
            # forever (append-only ledger).  Mirrors
            # effective_state.OUTCOME_TO_STATE.
            latest_ts = (
                select(
                    EvidenceReview.evidence_link_id.label("link_id"),
                    func.max(EvidenceReview.created_at).label("latest_created_at"),
                )
                .where(EvidenceReview.created_at <= cutoff)
                .group_by(EvidenceReview.evidence_link_id)
                .subquery()
            )
            latest_review = (
                select(
                    EvidenceReview.evidence_link_id.label("link_id"),
                    EvidenceReview.outcome.label("outcome"),
                )
                .join(
                    latest_ts,
                    (
                        EvidenceReview.evidence_link_id
                        == latest_ts.c.link_id
                    )
                    & (
                        EvidenceReview.created_at
                        == latest_ts.c.latest_created_at
                    ),
                )
                .subquery()
            )
            effective_state = case(
                (latest_review.c.outcome == "confirmed", "reviewed"),
                (latest_review.c.outcome == "rejected", "rejected"),
                (
                    latest_review.c.outcome == "needs_more_evidence",
                    "machine_generated",
                ),
                else_=EvidenceLink.review_state,
            )
            rows = self._session.execute(
                select(
                    SourceStatement,
                    EvidenceLink,
                    Thesis,
                    effective_state.label("effective_state"),
                )
                .join(
                    EvidenceLink,
                    EvidenceLink.source_statement_id == SourceStatement.id,
                )
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .join(ResearchCase, ResearchCase.id == Thesis.research_case_id)
                .outerjoin(latest_review, latest_review.c.link_id == EvidenceLink.id)
                .where(func.lower(SourceStatement.normalized_text).like(needle))
                .where(EvidenceLink.available_at <= cutoff)
                .where(EvidenceLink.created_at <= cutoff)
                .where(SourceStatement.created_at <= cutoff)
                .where(Thesis.created_at <= cutoff)
                .where(ResearchCase.created_at <= cutoff)
                .where(ResearchCase.id.in_(authorized_case_ids))
                .where(effective_state.in_(list(allowed_states)))
                .where(CaseTenantAdmission.tenant_id == tenant_id)
                .where(CaseTenantAdmission.admitted_at <= cutoff)
                .join(
                    CaseTenantAdmission,
                    CaseTenantAdmission.research_case_id == ResearchCase.id,
                )
                .limit(limit + 1)
            )
            hits: list[SearchHitDTO] = []
            seen_link_ids: set = set()
            for statement, link, thesis, state in rows:
                # Defensive: two reviews sharing the same max created_at
                # would join twice; report each link once.
                if link.id in seen_link_ids:
                    continue
                seen_link_ids.add(link.id)
                hits.append(
                    SearchHitDTO(
                        object_type="evidence",
                        object_id=str(link.id),
                        title=statement.normalized_text,
                        snippet=statement.normalized_text,
                        case_id=str(thesis.research_case_id),
                        review_state=state,
                        available_at=_iso(link.available_at),
                        deep_link=f"/research-cases/{thesis.research_case_id}/dossier",
                    )
                )
            return hits

        if object_type == "company":
            visible_companies = (
                select(
                    Company.id.label("company_id"),
                    ThemeRole.research_case_id.label("case_id"),
                    func.row_number()
                    .over(
                        partition_by=Company.id,
                        order_by=(ThemeRole.created_at, ThemeRole.id),
                    )
                    .label("role_rank"),
                )
                .join(ThemeRole, ThemeRole.company_id == Company.id)
                .join(
                    CaseTenantAdmission,
                    CaseTenantAdmission.research_case_id
                    == ThemeRole.research_case_id,
                )
                .where(func.lower(Company.name).like(needle))
                .where(Company.created_at <= cutoff)
                .where(ThemeRole.created_at <= cutoff)
                .where(CaseTenantAdmission.tenant_id == tenant_id)
                .where(CaseTenantAdmission.admitted_at <= cutoff)
                .where(ThemeRole.research_case_id.in_(authorized_case_ids))
                .where(
                    or_(
                        ThemeRole.applicable_from.is_(None),
                        ThemeRole.applicable_from <= cutoff.date(),
                    )
                )
                .where(
                    or_(
                        ThemeRole.applicable_to.is_(None),
                        ThemeRole.applicable_to >= cutoff.date(),
                    )
                )
                .subquery()
            )
            rows = self._session.execute(
                select(Company, visible_companies.c.case_id)
                .join(visible_companies, visible_companies.c.company_id == Company.id)
                .where(visible_companies.c.role_rank == 1)
                .order_by(func.lower(Company.name), Company.id)
                .limit(limit + 1)
            )
            return [
                SearchHitDTO(
                    object_type="company",
                    object_id=str(company.id),
                    title=company.name,
                    snippet=company.code or "",
                    case_id=str(case_id),
                    review_state=None,
                    available_at=None,
                    deep_link=f"/research-cases/{case_id}/dossier",
                )
                for company, case_id in rows
            ]

        if object_type == "stock":
            visible_stocks = (
                select(
                    Stock.id.label("stock_id"),
                    ThemeRole.research_case_id.label("case_id"),
                    func.row_number()
                    .over(
                        partition_by=Stock.id,
                        order_by=(ThemeRole.created_at, ThemeRole.id),
                    )
                    .label("role_rank"),
                )
                .join(Company, Company.id == Stock.company_id)
                .join(ThemeRole, ThemeRole.company_id == Company.id)
                .join(
                    CaseTenantAdmission,
                    CaseTenantAdmission.research_case_id
                    == ThemeRole.research_case_id,
                )
                .where(func.lower(Stock.name).like(needle))
                .where(Stock.created_at <= cutoff)
                .where(ThemeRole.created_at <= cutoff)
                .where(CaseTenantAdmission.tenant_id == tenant_id)
                .where(CaseTenantAdmission.admitted_at <= cutoff)
                .where(ThemeRole.research_case_id.in_(authorized_case_ids))
                .where(
                    or_(
                        ThemeRole.applicable_from.is_(None),
                        ThemeRole.applicable_from <= cutoff.date(),
                    )
                )
                .where(
                    or_(
                        ThemeRole.applicable_to.is_(None),
                        ThemeRole.applicable_to >= cutoff.date(),
                    )
                )
                .subquery()
            )
            rows = self._session.execute(
                select(Stock, visible_stocks.c.case_id)
                .join(visible_stocks, visible_stocks.c.stock_id == Stock.id)
                .where(visible_stocks.c.role_rank == 1)
                .order_by(func.lower(Stock.name), Stock.id)
                .limit(limit + 1)
            )
            return [
                SearchHitDTO(
                    object_type="stock",
                    object_id=str(stock.id),
                    title=stock.name,
                    snippet=stock.code or "",
                    case_id=str(case_id),
                    review_state=None,
                    available_at=None,
                    deep_link=f"/research-cases/{case_id}/dossier",
                )
                for stock, case_id in rows
            ]

        # fund
        visible_funds = (
            select(
                Fund.id.label("fund_id"),
                ThemeRole.research_case_id.label("case_id"),
                func.row_number()
                .over(
                    partition_by=Fund.id,
                    order_by=(ThemeRole.created_at, ThemeRole.id),
                )
                .label("role_rank"),
            )
            .join(HoldingDisclosure, HoldingDisclosure.fund_id == Fund.id)
            .join(Stock, Stock.id == HoldingDisclosure.stock_id)
            .join(Company, Company.id == Stock.company_id)
            .join(ThemeRole, ThemeRole.company_id == Company.id)
            .join(
                CaseTenantAdmission,
                CaseTenantAdmission.research_case_id == ThemeRole.research_case_id,
            )
            .where(func.lower(Fund.name).like(needle))
            .where(Fund.created_at <= cutoff)
            .where(HoldingDisclosure.created_at <= cutoff)
            .where(HoldingDisclosure.published_at <= cutoff)
            .where(ThemeRole.created_at <= cutoff)
            .where(CaseTenantAdmission.tenant_id == tenant_id)
            .where(CaseTenantAdmission.admitted_at <= cutoff)
            .where(ThemeRole.research_case_id.in_(authorized_case_ids))
            .where(
                or_(
                    ThemeRole.applicable_from.is_(None),
                    ThemeRole.applicable_from <= cutoff.date(),
                )
            )
            .where(
                or_(
                    ThemeRole.applicable_to.is_(None),
                    ThemeRole.applicable_to >= cutoff.date(),
                )
            )
            .subquery()
        )
        rows = self._session.execute(
            select(Fund, visible_funds.c.case_id)
            .join(visible_funds, visible_funds.c.fund_id == Fund.id)
            .where(visible_funds.c.role_rank == 1)
            .order_by(func.lower(Fund.name), Fund.id)
            .limit(limit + 1)
        )
        return [
            SearchHitDTO(
                object_type="fund",
                object_id=str(fund.id),
                title=fund.name,
                snippet=fund.code or "",
                case_id=str(case_id),
                review_state=None,
                available_at=None,
                deep_link=f"/research-cases/{case_id}/dossier",
            )
            for fund, case_id in rows
        ]
