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
    DocumentVersion,
    EvidenceLink,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceSpan,
    SourceStatement,
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
    ThemeEvidenceSummaryDTO,
    ThemeExposurePositionDTO,
    ThemeExpressionCandidateDTO,
    ThemeListItemDTO,
    ThemeListResponse,
    ThemeViewResponse,
)
from app.schemas.v1.companies import ValuationViewDTO
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
        expression_candidates = self._expression_candidates(
            company_roles=company_roles,
            cases=cases,
            fund_exposure=fund_exposure,
            as_of=as_of,
        )

        return ThemeViewResponse(
            basis=basis.to_dto(),
            tag=tag,
            cases=cases,
            company_roles=company_roles,
            fund_exposure=fund_exposure,
            expression_candidates=expression_candidates,
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

    def _evidence_for_thesis(self, thesis_id: uuid.UUID, basis: HistoricalBasis) -> tuple[dict[str, int], list[ThemeEvidenceSummaryDTO]]:
        links = list(self._session.scalars(
            select(EvidenceLink)
            .where(EvidenceLink.thesis_id == thesis_id)
            .where(EvidenceLink.available_at <= basis.cutoff)
            .where(EvidenceLink.created_at <= basis.cutoff)
            .order_by(EvidenceLink.created_at, EvidenceLink.id)
        ))
        counts = {"supports": 0, "contradicts": 0, "contextualizes": 0}
        evidence = []
        for link in links:
            counts[link.role] = counts.get(link.role, 0) + 1
            statement = self._session.get(SourceStatement, link.source_statement_id)
            span = self._session.get(SourceSpan, statement.source_span_id) if statement else None
            document = self._session.get(DocumentVersion, span.document_version_id) if span else None
            evidence.append(ThemeEvidenceSummaryDTO(
                link_id=link.id,
                role=link.role,
                statement=statement.normalized_text if statement else "",
                source_url=document.source_url if document else None,
                locator=span.locator if span else {},
                review_state=link.review_state,
                scope=link.scope or {},
            ))
        return counts, evidence

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
            evidence_counts, evidence = self._evidence_for_thesis(thesis.id, basis)
            theses.append(
                ThemeCaseThesisDTO(
                    thesis_id=thesis.id,
                    statement=thesis.statement,
                    title=thesis.title,
                    ai_assessment=assessment_dto,
                    review=review_dto,
                    evidence_counts=evidence_counts,
                    evidence=evidence,
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
            company_stocks = self._instruments.stocks_for_companies([company.id])
            company_valuations = self._instruments.valuation_snapshots_for_stocks(
                [stock.id for stock in company_stocks]
            )
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
                    valuations=[
                        ValuationViewDTO(
                            stock_id=stock.id,
                            stock_code=stock.code,
                            metric_name=value.metric_name,
                            metric_value=float(value.metric_value),
                            as_of_date=value.as_of_date.isoformat(),
                            source=value.source,
                            definition=value.definition,
                        )
                        for value in company_valuations
                        for stock in company_stocks
                        if value.stock_id == stock.id
                    ],
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

    def _expression_candidates(
        self,
        *,
        company_roles: list[ThemeCompanyRoleDTO],
        cases: list[ThemeCaseDTO],
        fund_exposure: list[ThemeExposurePositionDTO],
        as_of,
    ) -> list[ThemeExpressionCandidateDTO]:
        """Derive stock-level expression candidates from existing theme data.

        Not a recommendation. Each candidate is an auditable mapping from a
        company/theme-role into a stock expression with valuation, holding
        freshness, constraints, and an explicit match explanation.
        """
        thesis_status: dict[uuid.UUID, str] = {}
        for case in cases:
            for thesis in case.theses:
                if thesis.review and thesis.review.outcome == "rejected":
                    thesis_status[thesis.thesis_id] = "unreviewed"
                elif thesis.review and thesis.review.outcome == "modified" and thesis.review.conclusion:
                    thesis_status[thesis.thesis_id] = thesis.review.conclusion
                elif thesis.review and thesis.review.outcome == "confirmed":
                    thesis_status[thesis.thesis_id] = (
                        thesis.ai_assessment.conclusion
                        if thesis.ai_assessment
                        else "unreviewed"
                    )
                else:
                    # AI-only judgments stay visibly unreviewed at expression time.
                    thesis_status[thesis.thesis_id] = "unreviewed"

        holdings_by_stock: dict[uuid.UUID, list[ThemeExposurePositionDTO]] = {}
        for position in fund_exposure:
            holdings_by_stock.setdefault(position.stock_id, []).append(position)

        candidates: list[ThemeExpressionCandidateDTO] = []
        seen_stocks: set[uuid.UUID] = set()
        for role in company_roles:
            stocks = self._instruments.stocks_for_companies([role.company_id])
            related_thesis_ids = [
                thesis.thesis_id
                for case in cases
                if role.case_id is None or case.case_id == role.case_id
                for thesis in case.theses
            ]
            statuses = [thesis_status.get(tid, "unreviewed") for tid in related_thesis_ids]
            if any(status == "contradicted" for status in statuses):
                support_status = "contradicted"
            elif any(status == "supported" for status in statuses):
                support_status = "supported"
            elif any(status == "insufficient_evidence" for status in statuses):
                support_status = "insufficient_evidence"
            else:
                support_status = "unreviewed"

            for stock in stocks:
                if stock.id in seen_stocks:
                    continue
                seen_stocks.add(stock.id)
                stock_valuations = [
                    value
                    for value in role.valuations
                    if value.stock_id == stock.id
                ]
                holdings = holdings_by_stock.get(stock.id, [])
                latest_period = max((h.report_period for h in holdings), default=None)
                if latest_period is None:
                    freshness = "missing"
                else:
                    try:
                        period_date = datetime.fromisoformat(latest_period).date()
                    except ValueError:
                        period_date = as_of
                    age_days = (as_of - period_date).days
                    freshness = "fresh" if age_days <= 180 else "stale"

                constraints: list[str] = []
                if support_status == "contradicted":
                    constraints.append("关联命题存在已确认反向判断，不能仅因主题角色直接表达")
                if support_status == "insufficient_evidence":
                    constraints.append("关键命题证据不足，表达仅可作为观察候选")
                if support_status == "unreviewed":
                    constraints.append("命题尚未完成人工复核，表达状态仍为临时")
                if not stock_valuations:
                    constraints.append("缺少可展示估值，无法判断‘现在多贵’")
                if freshness == "missing":
                    constraints.append("缺少公开基金持仓披露，主题暴露不可复核")
                elif freshness == "stale":
                    constraints.append("最新持仓披露超过约180天，新鲜度不足")

                pe_text = next(
                    (
                        f"{v.metric_name}={v.metric_value}@{v.as_of_date}"
                        for v in stock_valuations
                        if "PE" in v.metric_name.upper()
                    ),
                    "估值缺失",
                )
                match_explanation = (
                    f"{stock.name} 作为主题角色「{role.role}」进入表达候选；"
                    f"关联命题支持状态={support_status}；"
                    f"估值口径={pe_text}；"
                    f"基金披露持仓={len(holdings)} 条，新鲜度={freshness}。"
                    " 这不是买入建议，只是把研究判断映射到可审计的表达标的。"
                )
                candidates.append(
                    ThemeExpressionCandidateDTO(
                        stock_id=stock.id,
                        stock_code=stock.code,
                        stock_name=stock.name,
                        company_role=role.role,
                        thesis_ids=related_thesis_ids,
                        support_status=support_status,
                        valuation=stock_valuations,
                        holding_count=len(holdings),
                        latest_report_period=latest_period,
                        freshness=freshness,
                        constraints=constraints,
                        match_explanation=match_explanation,
                    )
                )

        rank = {
            "supported": 0,
            "insufficient_evidence": 1,
            "unreviewed": 2,
            "contradicted": 3,
        }
        candidates.sort(key=lambda c: (rank.get(c.support_status, 9), c.stock_code))
        return candidates
