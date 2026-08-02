"""Company-centric read assembly (公司研究).

The CompanyDossier inverts the case-centric view: from one Company outward to
its ThemeRoles across cases, the theses those cases carry, valuation
snapshots and fund holding disclosures on its stocks. It never derives a
company-level conclusion: AI assessments stay visibly provisional and human
reviews are returned as separate records, exactly as on the case dossier.

Point-in-time semantics (spec 4.5): one HistoricalBasis governs every
section. ThemeRoles honour both ``applicable_from/to`` and the ledger
cutoff; valuations and disclosures use the same visibility rules as the
penetration read models.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, time, timezone

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import (
    Company,
    Fund,
    HoldingDisclosure,
    Stock,
    ThemeRole,
    ValuationSnapshot,
)
from app.queries.basis import HistoricalBasis
from app.repositories.instruments import InstrumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.common import CursorPage
from app.schemas.v1.companies import (
    AssessmentViewDTO,
    CompanyDossierResponse,
    CompanyIdentityDTO,
    CompanyListItemDTO,
    CompanyListResponse,
    FundHolderDTO,
    RelatedThesisDTO,
    RoleReviewDTO,
    StockViewDTO,
    ThemeRoleViewDTO,
    ValuationViewDTO,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class CompanyReadQueries:
    """Read-only company list and dossier assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._research = ResearchRepository(session)
        self._instruments = InstrumentRepository(session)

    # --------------------------------------------------------------- list

    def list_companies(
        self, *, query: str | None, limit: int, cursor: str | None
    ) -> CompanyListResponse:
        after_created_at, after_id = (None, None)
        if cursor is not None:
            after_created_at, after_id = self._decode_cursor(cursor)

        stmt = select(Company).order_by(
            Company.created_at.desc(), Company.id.desc()
        )
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(Company.name.like(pattern), Company.code.like(pattern))
            )
        if after_created_at is not None and after_id is not None:
            stmt = stmt.where(
                tuple_(Company.created_at, Company.id)
                < tuple_(after_created_at, after_id)
            )
        companies = list(self._session.scalars(stmt.limit(limit + 1)))
        has_more = len(companies) > limit
        page_items = companies[:limit]

        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)

        items = [self._list_item(company) for company in page_items]
        return CompanyListResponse(
            items=items, page=CursorPage(next_cursor=next_cursor, has_more=has_more)
        )

    def _list_item(self, company: Company) -> CompanyListItemDTO:
        stock_count = self._session.scalar(
            select(func.count())
            .select_from(Stock)
            .where(Stock.company_id == company.id)
        ) or 0
        role_count = self._session.scalar(
            select(func.count())
            .select_from(ThemeRole)
            .where(ThemeRole.company_id == company.id)
        ) or 0
        latest_period = self._session.scalar(
            select(func.max(HoldingDisclosure.report_period))
            .select_from(HoldingDisclosure)
            .join(Stock, Stock.id == HoldingDisclosure.stock_id)
            .where(Stock.company_id == company.id)
        )
        return CompanyListItemDTO(
            id=company.id,
            code=company.code,
            name=company.name,
            type=company.type,
            stock_count=stock_count,
            theme_role_count=role_count,
            latest_report_period=(
                latest_period.isoformat() if latest_period is not None else None
            ),
        )

    # ------------------------------------------------------------ dossier

    def dossier(
        self, *, company_id: uuid.UUID, basis: HistoricalBasis
    ) -> CompanyDossierResponse:
        company = self._session.get(Company, company_id)
        if company is None:
            raise NotFoundError("company not found")
        # Historical replay: a company registered after the cutoff did not
        # exist then (same rule as cases on the dossier).
        if _to_aware(company.created_at) > basis.cutoff:
            raise NotFoundError("company not found")

        stocks = self._instruments.stocks_for_companies([company.id])
        stock_by_id = {stock.id: stock for stock in stocks}
        as_of = basis.cutoff.date()

        theme_roles = self._theme_roles(company.id, as_of=as_of, basis=basis)
        related_theses = self._related_theses(theme_roles, basis=basis)
        valuations = self._valuations(list(stock_by_id), stock_by_id, as_of)
        fund_holders = self._fund_holders(list(stock_by_id), stock_by_id, basis)

        return CompanyDossierResponse(
            basis=basis.to_dto(),
            company=CompanyIdentityDTO(
                id=company.id,
                code=company.code,
                name=company.name,
                type=company.type,
                created_at=_iso(company.created_at),
            ),
            stocks=[
                StockViewDTO(
                    id=stock.id, code=stock.code, name=stock.name, market=stock.market
                )
                for stock in stocks
            ],
            theme_roles=theme_roles,
            related_theses=related_theses,
            valuations=valuations,
            fund_holders=fund_holders,
        )

    # ------------------------------------------------------------ sections

    def _theme_roles(
        self, company_id: uuid.UUID, *, as_of, basis: HistoricalBasis
    ) -> list[ThemeRoleViewDTO]:
        """Active ThemeRoles at the basis: applicable window + ledger cutoff."""
        roles = list(
            self._session.scalars(
                select(ThemeRole)
                .where(ThemeRole.company_id == company_id)
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
        views: list[ThemeRoleViewDTO] = []
        for role in roles:
            case = None
            if role.research_case_id is not None:
                case = self._research.get_case(
                    role.research_case_id, cutoff=basis.cutoff
                )
            statement = None
            span = None
            document_version = None
            if role.source_statement_id is not None:
                statement = self._research.get_statement(role.source_statement_id)
                if statement is not None and (
                    _to_aware(statement.created_at) > basis.cutoff
                ):
                    # A statement registered after the cutoff would leak
                    # future text; drop the backlink but keep the role.
                    statement = None
                if statement is not None:
                    span = self._research.span_for_statement(statement.id)
                    document_version = (
                        self._research.get_document_version_for_statement(
                            statement.id
                        )
                    )
            views.append(
                ThemeRoleViewDTO(
                    id=role.id,
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
                    statement_id=statement.id if statement is not None else None,
                    statement_text=(
                        statement.normalized_text if statement is not None else None
                    ),
                    span_id=span.id if span is not None else None,
                    document_version_id=(
                        document_version.id if document_version is not None else None
                    ),
                )
            )
        return views

    def _related_theses(
        self, theme_roles: list[ThemeRoleViewDTO], *, basis: HistoricalBasis
    ) -> list[RelatedThesisDTO]:
        case_ids = sorted({role.case_id for role in theme_roles if role.case_id})
        views: list[RelatedThesisDTO] = []
        seen: set[uuid.UUID] = set()
        for case_id in case_ids:
            case = self._research.get_case(case_id, cutoff=basis.cutoff)
            if case is None:
                continue
            for thesis in self._research.theses_for_case(
                case_id, cutoff=basis.cutoff
            ):
                if thesis.id in seen:
                    continue
                seen.add(thesis.id)
                assessment_dto = None
                review_dto = None
                assessment = self._research.latest_assessment_for_thesis(
                    thesis.id, cutoff=basis.cutoff
                )
                if assessment is not None:
                    assessment_dto = AssessmentViewDTO(
                        id=assessment.id,
                        conclusion=assessment.conclusion,
                        provisional=bool(assessment.displayed_as_provisional),
                        assessed_at=_iso(assessment.created_at),
                    )
                    review = self._research.latest_review_for_assessment(
                        assessment.id, cutoff=basis.cutoff
                    )
                    if review is not None:
                        review_dto = RoleReviewDTO(
                            outcome=review.outcome,
                            conclusion=review.conclusion,
                            reason=review.reason,
                            reviewer=review.reviewer,
                            reviewed_at=_iso(review.created_at),
                        )
                views.append(
                    RelatedThesisDTO(
                        thesis_id=thesis.id,
                        case_id=case.id,
                        case_title=case.title,
                        statement=thesis.statement,
                        title=thesis.title,
                        ai_assessment=assessment_dto,
                        review=review_dto,
                    )
                )
        return views

    def _valuations(
        self,
        stock_ids: list[uuid.UUID],
        stock_by_id: dict[uuid.UUID, Stock],
        as_of,
    ) -> list[ValuationViewDTO]:
        """Latest snapshot per (stock, metric) visible at the basis date."""
        latest: dict[tuple[uuid.UUID, str], ValuationSnapshot] = {}
        for snap in self._instruments.valuation_snapshots_for_stocks(stock_ids):
            if snap.as_of_date > as_of:
                continue
            latest[(snap.stock_id, snap.metric_name)] = snap
        views = [
            ValuationViewDTO(
                stock_id=snap.stock_id,
                stock_code=stock_by_id[snap.stock_id].code,
                metric_name=snap.metric_name,
                metric_value=float(snap.metric_value),
                as_of_date=snap.as_of_date.isoformat(),
                source=snap.source,
                definition=snap.definition,
            )
            for snap in latest.values()
        ]
        views.sort(key=lambda v: (v.stock_code, v.metric_name))
        return views

    def _fund_holders(
        self,
        stock_ids: list[uuid.UUID],
        stock_by_id: dict[uuid.UUID, Stock],
        basis: HistoricalBasis,
    ) -> list[FundHolderDTO]:
        """Latest disclosure per (fund, stock); visibility by published_at."""
        cutoff_end = datetime.combine(
            basis.cutoff.date(), time(23, 59, 59, 999999), tzinfo=timezone.utc
        )
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for disclosure in self._instruments.holding_disclosures_for_stocks(
            stock_ids
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
        views: list[FundHolderDTO] = []
        for (fund_id, stock_id), disclosure in latest.items():
            fund = funds.get(fund_id)
            if fund is None:
                continue
            views.append(
                FundHolderDTO(
                    fund_id=fund.id,
                    fund_code=fund.code,
                    fund_name=fund.name,
                    stock_id=stock_id,
                    stock_code=stock_by_id[stock_id].code,
                    weight=float(disclosure.weight),
                    report_period=disclosure.report_period.isoformat(),
                    published_at=_iso(disclosure.published_at),
                    acquired_at=_iso(disclosure.acquired_at),
                    source=disclosure.source,
                )
            )
        views.sort(key=lambda v: v.weight, reverse=True)
        return views

    # ----------------------------------------------------------- cursors

    @staticmethod
    def _encode_cursor(created_at: datetime, company_id: uuid.UUID) -> str:
        raw = json.dumps(
            {"created_at": created_at.isoformat(), "id": str(company_id)}
        ).encode()
        return base64.urlsafe_b64encode(raw).decode()

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(raw)
            created_at = datetime.fromisoformat(data["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return created_at, uuid.UUID(data["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationFailedError("malformed cursor") from exc
