"""Cross-case theme read assembly (横切主题 ThemeView).

A pure aggregation projection: theme identity comes from effective
case-level tags (folded add/remove events), and every number derives from
case-level effective state — thesis judgments reuse the same
assessment + latest-review derivation as the case/company dossiers, theme
roles honour the applicable window plus the ledger cutoff, and fund
exposure reuses the penetration visibility rules (latest report per
(fund, stock), ``published_at <= cutoff``).

Nothing here is a theme-level conclusion: ``thesis_counts`` are per-case
buckets of case-level effective judgments, and ``derived_from`` lists every
ledger id behind the aggregation so any row can be expanded back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import (
    Company,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    Stock,
    ThemeRole,
)
from app.queries.basis import HistoricalBasis
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.companies import AssessmentViewDTO, RoleReviewDTO
from app.schemas.v1.themes import (
    DerivedFromDTO,
    ThemeCaseDTO,
    ThemeCaseThesisDTO,
    ThemeCompanyRoleDTO,
    ThemeExposurePositionDTO,
    ThemeListItemDTO,
    ThemeListResponse,
    ThemeViewResponse,
)
from app.services.themes import THEME_TAG_VOCABULARY

# Effective-judgment buckets for the per-case thesis matrix.
_CONCLUSION_BUCKETS = frozenset(
    {"supported", "contradicted", "insufficient_evidence"}
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class ThemeReadQueries:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._research = ResearchRepository(session)
        self._instruments = InstrumentRepository(session)

    # --------------------------------------------------------- tag index

    def _tags_by_case(self) -> dict[uuid.UUID, set[str]]:
        # Events arrive in global creation order, which preserves per-case
        # relative order, so a single pass folds each case's effective set.
        tags: dict[uuid.UUID, set[str]] = {}
        for event in self._research.theme_tag_events():
            current = tags.setdefault(event.research_case_id, set())
            if event.op == "add":
                current.add(event.tag)
            elif event.op == "remove":
                current.discard(event.tag)
        return tags

    # --------------------------------------------------------------- list

    def list_themes(self) -> ThemeListResponse:
        tags_by_case = self._tags_by_case()
        by_tag: dict[str, list[uuid.UUID]] = {}
        for case_id, tags in tags_by_case.items():
            for tag in tags:
                by_tag.setdefault(tag, []).append(case_id)

        items: list[ThemeListItemDTO] = []
        for tag, case_ids in sorted(by_tag.items()):
            company_ids = {
                role.company_id
                for role in self._session.scalars(
                    select(ThemeRole).where(
                        ThemeRole.research_case_id.in_(case_ids)
                    )
                )
            }
            thesis_count = sum(
                len(self._research.theses_for_case(case_id)) for case_id in case_ids
            )
            items.append(
                ThemeListItemDTO(
                    tag=tag,
                    case_count=len(case_ids),
                    company_count=len(company_ids),
                    thesis_count=thesis_count,
                )
            )
        return ThemeListResponse(items=items)

    # --------------------------------------------------------------- view

    def theme_view(self, *, tag: str, basis: HistoricalBasis) -> ThemeViewResponse:
        if tag not in THEME_TAG_VOCABULARY:
            raise NotFoundError("theme tag not found")

        tags_by_case = self._tags_by_case()
        case_ids = sorted(
            case_id for case_id, tags in tags_by_case.items() if tag in tags
        )
        as_of = basis.cutoff.date()

        cases: list[ThemeCaseDTO] = []
        thesis_ids: list[uuid.UUID] = []
        visible_case_ids: list[uuid.UUID] = []
        for case_id in case_ids:
            case = self._research.get_case(case_id, cutoff=basis.cutoff)
            if case is None:
                # Created after the cutoff: did not exist then.
                continue
            visible_case_ids.append(case_id)
            case_dto = self._case_section(case, basis)
            cases.append(case_dto)
            thesis_ids.extend(t.thesis_id for t in case_dto.theses)

        company_roles, theme_role_ids = self._company_roles(
            visible_case_ids, as_of=as_of, basis=basis
        )
        fund_exposure, disclosure_ids = self._fund_exposure(
            company_roles, basis=basis
        )

        return ThemeViewResponse(
            basis=basis.to_dto(),
            tag=tag,
            cases=cases,
            company_roles=company_roles,
            fund_exposure=fund_exposure,
            derived_from=DerivedFromDTO(
                case_ids=visible_case_ids,
                thesis_ids=thesis_ids,
                theme_role_ids=theme_role_ids,
                disclosure_ids=disclosure_ids,
            ),
        )

    # ------------------------------------------------------------ sections

    def _effective_bucket(self, thesis_id: uuid.UUID, basis: HistoricalBasis):
        """Latest assessment + latest review -> (bucket, assessment_dto, review_dto)."""
        assessment = self._research.latest_assessment_for_thesis(
            thesis_id, cutoff=basis.cutoff
        )
        if assessment is None:
            return "no_assessment", None, None
        assessment_dto = AssessmentViewDTO(
            id=assessment.id,
            conclusion=assessment.conclusion,
            provisional=bool(assessment.displayed_as_provisional),
            assessed_at=_iso(assessment.created_at),
        )
        review = self._research.latest_review_for_assessment(
            assessment.id, cutoff=basis.cutoff
        )
        if review is None:
            # Unreviewed AI draft: never counted as an effective conclusion.
            return "ai_pending", assessment_dto, None
        review_dto = RoleReviewDTO(
            outcome=review.outcome,
            conclusion=review.conclusion,
            reason=review.reason,
            reviewer=review.reviewer,
            reviewed_at=_iso(review.created_at),
        )
        if review.outcome == "rejected":
            return "rejected", assessment_dto, review_dto
        effective = assessment.conclusion
        if review.outcome == "modified" and review.conclusion:
            effective = review.conclusion
        if effective not in _CONCLUSION_BUCKETS:
            effective = "no_assessment"
        return effective, assessment_dto, review_dto

    def _case_section(self, case: ResearchCase, basis: HistoricalBasis) -> ThemeCaseDTO:
        counts = {
            "supported": 0,
            "contradicted": 0,
            "insufficient_evidence": 0,
            "ai_pending": 0,
            "rejected": 0,
            "no_assessment": 0,
        }
        theses: list[ThemeCaseThesisDTO] = []
        for thesis in self._research.theses_for_case(case.id, cutoff=basis.cutoff):
            bucket, assessment_dto, review_dto = self._effective_bucket(
                thesis.id, basis
            )
            counts[bucket] += 1
            theses.append(
                ThemeCaseThesisDTO(
                    thesis_id=thesis.id,
                    statement=thesis.statement,
                    title=thesis.title,
                    ai_assessment=assessment_dto,
                    review=review_dto,
                )
            )
        return ThemeCaseDTO(
            case_id=case.id,
            case_title=case.title,
            thesis_counts=counts,
            theses=theses,
        )

    def _company_roles(
        self,
        case_ids: list[uuid.UUID],
        *,
        as_of,
        basis: HistoricalBasis,
    ) -> tuple[list[ThemeCompanyRoleDTO], list[uuid.UUID]]:
        if not case_ids:
            return [], []
        roles = list(
            self._session.scalars(
                select(ThemeRole)
                .where(ThemeRole.research_case_id.in_(case_ids))
                .where(ThemeRole.created_at <= basis.cutoff)
                .where(
                    or_(
                        ThemeRole.applicable_from.is_(None),
                        ThemeRole.applicable_from <= as_of,
                    )
                )
                .where(
                    or_(
                        ThemeRole.applicable_to.is_(None),
                        ThemeRole.applicable_to >= as_of,
                    )
                )
                .order_by(ThemeRole.created_at)
            )
        )
        companies = {
            company.id: company
            for company in self._instruments.companies_by_ids(
                list({role.company_id for role in roles})
            )
        }
        cases = {
            case.id: case
            for case in (
                self._research.get_case(case_id, cutoff=basis.cutoff)
                for case_id in case_ids
            )
            if case is not None
        }
        views: list[ThemeCompanyRoleDTO] = []
        for role in roles:
            company = companies.get(role.company_id)
            if company is None:
                continue
            case = cases.get(role.research_case_id)
            views.append(
                ThemeCompanyRoleDTO(
                    company_id=company.id,
                    company_code=company.code,
                    company_name=company.name,
                    case_id=case.id if case is not None else None,
                    case_title=case.title if case is not None else None,
                    role=role.role,
                    scope=dict(role.scope),
                    applicable_from=(
                        role.applicable_from.isoformat()
                        if role.applicable_from
                        else None
                    ),
                    applicable_to=(
                        role.applicable_to.isoformat() if role.applicable_to else None
                    ),
                    statement_id=role.source_statement_id,
                )
            )
        views.sort(key=lambda v: (v.company_code, v.case_title or ""))
        return views, [role.id for role in roles]

    def _fund_exposure(
        self,
        company_roles: list[ThemeCompanyRoleDTO],
        *,
        basis: HistoricalBasis,
    ) -> tuple[list[ThemeExposurePositionDTO], list[uuid.UUID]]:
        company_ids = list({role.company_id for role in company_roles})
        stocks = self._instruments.stocks_for_companies(company_ids)
        stock_by_id = {stock.id: stock for stock in stocks}
        if not stock_by_id:
            return [], []

        cutoff_end = datetime.combine(
            basis.cutoff.date(), time(23, 59, 59, 999999), tzinfo=timezone.utc
        )
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for disclosure in self._instruments.holding_disclosures_for_stocks(
            list(stock_by_id)
        ):
            if disclosure.published_at is None:
                continue
            published = _to_aware(disclosure.published_at)
            if published > cutoff_end:
                continue
            key = (disclosure.fund_id, disclosure.stock_id)
            previous = latest.get(key)
            if previous is None or disclosure.report_period > previous.report_period:
                latest[key] = disclosure

        funds: dict[uuid.UUID, Fund] = {
            fund.id: fund
            for fund in self._instruments.funds_by_ids(
                list({key[0] for key in latest})
            )
        }
        positions: list[ThemeExposurePositionDTO] = []
        for (fund_id, stock_id), disclosure in latest.items():
            fund = funds.get(fund_id)
            stock = stock_by_id.get(stock_id)
            if fund is None or stock is None:
                continue
            positions.append(
                ThemeExposurePositionDTO(
                    fund_id=fund.id,
                    fund_code=fund.code,
                    fund_name=fund.name,
                    stock_id=stock.id,
                    stock_code=stock.code,
                    stock_name=stock.name,
                    weight=float(disclosure.weight),
                    report_period=disclosure.report_period.isoformat(),
                    source=disclosure.source,
                )
            )
        positions.sort(key=lambda p: (p.fund_code, -p.weight))
        return positions, [d.id for d in latest.values()]
