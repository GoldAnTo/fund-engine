"""Read-only, reviewed market-expression DTOs."""
from __future__ import annotations

from datetime import date, datetime

from app.schemas.v1.common import V1Model


class ExpressionSourceDTO(V1Model):
    source_statement_id: str | None
    document_version_id: str | None
    document_title: str | None
    source_url: str | None
    locator: dict | None
    available_at: datetime | None
    permission_status: str


class ClaimVerificationDTO(V1Model):
    outcome: str
    rationale: str
    reviewed_by: str
    reviewed_at: datetime
    source: ExpressionSourceDTO


class ReportClaimDTO(V1Model):
    id: str
    text: str
    claim_kind: str
    asserted_period: date | None
    asserted_by: str
    reviewed_by: str
    review_reason: str
    reviewed_at: datetime
    source: ExpressionSourceDTO


class KeyFactorDTO(V1Model):
    id: str
    thesis_id: str | None
    report_claim_id: str | None
    name: str
    expected_direction: str
    metric_name: str
    allowed_source_types: list[str]
    verification_window_start: date | None
    verification_window_end: date | None
    support_condition: str
    refutation_condition: str
    next_verification_event: str
    reviewed_by: str
    review_reason: str
    reviewed_at: datetime
    verification: ClaimVerificationDTO | None


class FundamentalImpactDTO(V1Model):
    id: str
    key_factor_id: str
    company_id: str
    company_name: str
    stock_id: str | None
    stock_code: str | None
    stock_name: str | None
    metric_name: str
    expected_direction: str
    rationale: str
    reviewed_by: str
    review_reason: str
    reviewed_at: datetime
    source: ExpressionSourceDTO


class MarketObservationDTO(V1Model):
    id: str
    key_factor_id: str | None
    stock_id: str
    stock_code: str
    stock_name: str
    event_at: datetime
    available_at: datetime
    window_label: str
    benchmark: str
    price_source: str
    relative_return: float | None
    reviewed_by: str
    review_reason: str
    reviewed_at: datetime


class FundDisclosurePositionDTO(V1Model):
    stock_id: str
    stock_code: str
    stock_name: str
    weight: float
    report_period: date
    published_at: datetime
    acquired_at: datetime
    source: str
    coverage_status: str
    freshness_status: str


class FundDisclosureExposureDTO(V1Model):
    fund_id: str
    fund_code: str
    fund_name: str
    # A sum across disclosed positions is meaningful only when the provider
    # also recorded complete portfolio coverage. `null` is intentional: the
    # individual historical holdings stay inspectable without becoming a
    # fabricated fund-level exposure number.
    disclosed_exposure: float | None
    positions: list[FundDisclosurePositionDTO]


class MarketExpressionResponse(V1Model):
    case_id: str
    as_of: date
    cutoff: datetime
    claims: list[ReportClaimDTO]
    factors: list[KeyFactorDTO]
    fundamentals: list[FundamentalImpactDTO]
    market_observations: list[MarketObservationDTO]
    fund_exposure: list[FundDisclosureExposureDTO]
