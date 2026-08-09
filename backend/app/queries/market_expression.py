"""Historical, reviewed-only projection for a Case's market expression."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import Company, DocumentVersion, Fund, HoldingDisclosure, SourceSpan, SourceStatement, Stock
from app.models.research_expression import ClaimVerification, FundamentalImpact, KeyFactor, MarketObservation, ReportClaim
from app.repositories.research import ResearchRepository
from app.schemas.v1.market_expression import (
    ClaimVerificationDTO,
    ExpressionSourceDTO,
    FundDisclosureExposureDTO,
    FundDisclosurePositionDTO,
    FundamentalImpactDTO,
    KeyFactorDTO,
    MarketExpressionResponse,
    MarketObservationDTO,
    ReportClaimDTO,
)


class MarketExpressionQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, *, case_id: uuid.UUID, as_of: date, cutoff: datetime) -> MarketExpressionResponse:
        if ResearchRepository(self._db).get_case(case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")
        claims = [item for item in self._db.scalars(select(ReportClaim).where(ReportClaim.research_case_id == case_id).where(ReportClaim.review_state == "reviewed").where(ReportClaim.created_at <= cutoff).where(ReportClaim.reviewed_at <= cutoff).order_by(ReportClaim.created_at, ReportClaim.id)) if self._case_has_source(case_id, item.source_statement_id)]
        claim_ids = [item.id for item in claims]
        factors = list(self._db.scalars(select(KeyFactor).where(KeyFactor.research_case_id == case_id).where(KeyFactor.review_state == "reviewed").where(KeyFactor.created_at <= cutoff).where(KeyFactor.reviewed_at <= cutoff).where(or_(KeyFactor.report_claim_id.is_(None), KeyFactor.report_claim_id.in_(claim_ids) if claim_ids else False)).order_by(KeyFactor.created_at, KeyFactor.id)))
        factor_ids = [item.id for item in factors]
        fundamentals = list(self._db.scalars(select(FundamentalImpact).where(FundamentalImpact.research_case_id == case_id).where(FundamentalImpact.review_state == "reviewed").where(FundamentalImpact.created_at <= cutoff).where(FundamentalImpact.reviewed_at <= cutoff).where(FundamentalImpact.key_factor_id.in_(factor_ids) if factor_ids else False).order_by(FundamentalImpact.created_at, FundamentalImpact.id)))
        observations = list(self._db.scalars(select(MarketObservation).where(MarketObservation.research_case_id == case_id).where(MarketObservation.review_state == "reviewed").where(MarketObservation.created_at <= cutoff).where(MarketObservation.reviewed_at <= cutoff).where(MarketObservation.available_at <= cutoff).where(MarketObservation.key_factor_id.in_(factor_ids) if factor_ids else False).order_by(MarketObservation.event_at, MarketObservation.id)))
        return MarketExpressionResponse(
            case_id=str(case_id), as_of=as_of, cutoff=cutoff,
            claims=[self._claim(item) for item in claims],
            factors=[self._factor(item, cutoff) for item in factors],
            fundamentals=[self._fundamental(item) for item in fundamentals],
            market_observations=[self._observation(item) for item in observations],
            fund_exposure=self._fund_exposure(fundamentals, as_of, cutoff),
        )

    def _source(self, statement_id: uuid.UUID | None) -> ExpressionSourceDTO:
        if statement_id is None:
            return ExpressionSourceDTO(source_statement_id=None, document_title=None, source_url=None, locator=None, available_at=None, permission_status="not_recorded")
        statement = self._db.get(SourceStatement, statement_id)
        span = self._db.get(SourceSpan, statement.source_span_id) if statement else None
        document = self._db.get(DocumentVersion, span.document_version_id) if span else None
        return ExpressionSourceDTO(source_statement_id=str(statement_id), document_title=document.title if document else None, source_url=document.source_url if document else None, locator=span.locator if span else None, available_at=document.available_at if document else None, permission_status="not_recorded")

    def _case_has_source(self, case_id: uuid.UUID, statement_id: uuid.UUID | None) -> bool:
        if statement_id is None:
            return False
        statement = self._db.get(SourceStatement, statement_id)
        span = self._db.get(SourceSpan, statement.source_span_id) if statement else None
        if span is None:
            return False
        from app.models.ledger import CaseDocumentVersion
        return self._db.scalar(select(CaseDocumentVersion.id).where(CaseDocumentVersion.research_case_id == case_id).where(CaseDocumentVersion.document_version_id == span.document_version_id).limit(1)) is not None

    def _claim(self, item: ReportClaim) -> ReportClaimDTO:
        return ReportClaimDTO(id=str(item.id), text=item.text, claim_kind=item.claim_kind, asserted_period=item.asserted_period, asserted_by=item.asserted_by, reviewed_by=item.reviewed_by or "未记录", review_reason=item.review_reason or "未记录", reviewed_at=item.reviewed_at or item.created_at, source=self._source(item.source_statement_id))

    def _factor(self, item: KeyFactor, cutoff: datetime) -> KeyFactorDTO:
        verification = self._db.scalar(select(ClaimVerification).where(ClaimVerification.key_factor_id == item.id).where(ClaimVerification.review_state == "reviewed").where(ClaimVerification.created_at <= cutoff).where(ClaimVerification.reviewed_at <= cutoff).order_by(ClaimVerification.created_at.desc(), ClaimVerification.id.desc()).limit(1))
        return KeyFactorDTO(id=str(item.id), report_claim_id=str(item.report_claim_id) if item.report_claim_id else None, name=item.name, expected_direction=item.expected_direction, metric_name=item.metric_name, allowed_source_types=list(item.allowed_source_types or []), verification_window_start=item.verification_window_start, verification_window_end=item.verification_window_end, support_condition=item.support_condition, refutation_condition=item.refutation_condition, next_verification_event=item.next_verification_event, reviewed_by=item.reviewed_by or "未记录", review_reason=item.review_reason or "未记录", reviewed_at=item.reviewed_at or item.created_at, verification=ClaimVerificationDTO(outcome=verification.outcome, rationale=verification.rationale, reviewed_by=verification.reviewed_by or "未记录", reviewed_at=verification.reviewed_at or verification.created_at, source=self._source(verification.source_statement_id)) if verification else None)

    def _fundamental(self, item: FundamentalImpact) -> FundamentalImpactDTO:
        company = self._db.get(Company, item.company_id)
        stock = self._db.get(Stock, item.stock_id) if item.stock_id else None
        return FundamentalImpactDTO(id=str(item.id), key_factor_id=str(item.key_factor_id), company_id=str(item.company_id), company_name=company.name if company else "已删除公司", stock_id=str(item.stock_id) if item.stock_id else None, stock_code=stock.code if stock else None, stock_name=stock.name if stock else None, metric_name=item.metric_name, expected_direction=item.expected_direction, rationale=item.rationale, reviewed_by=item.reviewed_by or "未记录", review_reason=item.review_reason or "未记录", reviewed_at=item.reviewed_at or item.created_at, source=self._source(item.source_statement_id))

    def _observation(self, item: MarketObservation) -> MarketObservationDTO:
        stock = self._db.get(Stock, item.stock_id)
        return MarketObservationDTO(id=str(item.id), key_factor_id=str(item.key_factor_id) if item.key_factor_id else None, stock_id=str(item.stock_id), stock_code=stock.code if stock else "已删除股票", stock_name=stock.name if stock else "已删除股票", event_at=item.event_at, available_at=item.available_at, window_label=item.window_label, benchmark=item.benchmark, price_source=item.price_source, relative_return=float(item.relative_return) if item.relative_return is not None else None, reviewed_by=item.reviewed_by or "未记录", review_reason=item.review_reason or "未记录", reviewed_at=item.reviewed_at or item.created_at)

    def _fund_exposure(self, fundamentals: list[FundamentalImpact], as_of: date, cutoff: datetime) -> list[FundDisclosureExposureDTO]:
        stock_ids = [item.stock_id for item in fundamentals if item.stock_id is not None]
        if not stock_ids:
            return []
        latest: dict[tuple[uuid.UUID, uuid.UUID], HoldingDisclosure] = {}
        for disclosure in self._db.scalars(select(HoldingDisclosure).where(HoldingDisclosure.stock_id.in_(stock_ids)).where(HoldingDisclosure.report_period <= as_of).where(HoldingDisclosure.published_at <= cutoff).order_by(HoldingDisclosure.report_period.desc(), HoldingDisclosure.created_at.desc())):
            latest.setdefault((disclosure.fund_id, disclosure.stock_id), disclosure)
        grouped: dict[uuid.UUID, list[HoldingDisclosure]] = {}
        for disclosure in latest.values():
            grouped.setdefault(disclosure.fund_id, []).append(disclosure)
        result: list[FundDisclosureExposureDTO] = []
        for fund_id, disclosures in grouped.items():
            fund = self._db.get(Fund, fund_id)
            if fund is None:
                continue
            positions = []
            for disclosure in sorted(disclosures, key=lambda value: value.weight, reverse=True):
                stock = self._db.get(Stock, disclosure.stock_id)
                if stock is None:
                    continue
                positions.append(FundDisclosurePositionDTO(stock_id=str(stock.id), stock_code=stock.code, stock_name=stock.name, weight=float(disclosure.weight), report_period=disclosure.report_period, published_at=disclosure.published_at, acquired_at=disclosure.acquired_at, source=disclosure.source, coverage_status="not_recorded", freshness_status="unknown"))
            coverage_complete = positions and all(
                position.coverage_status == "complete" for position in positions
            )
            result.append(FundDisclosureExposureDTO(
                fund_id=str(fund.id),
                fund_code=fund.code,
                fund_name=fund.name,
                disclosed_exposure=(sum((position.weight for position in positions), 0.0) if coverage_complete else None),
                positions=positions,
            ))
        return sorted(result, key=lambda value: value.disclosed_exposure or 0.0, reverse=True)
