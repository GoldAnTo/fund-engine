"""Read-only, reviewed market-expression DTOs."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model


class ExpressionSourceDTO(V1Model):
    source_statement_id: str | None
    document_version_id: str | None
    document_title: str | None
    source_url: str | None
    locator: dict | None
    available_at: datetime | None
    permission_status: str


class SourceStatementOptionDTO(V1Model):
    """A Case-owned, display-and-processing-admitted statement selectable by a researcher."""

    id: str
    kind: str
    text: str
    document_version_id: str
    document_title: str
    source_url: str | None
    locator: dict
    available_at: datetime
    permission_status: Literal["admitted"]


class SourceStatementOptionsResponse(V1Model):
    items: list[SourceStatementOptionDTO]


class RegisterReportClaimRequest(V1Model):
    source_statement_id: uuid.UUID
    text: str = Field(min_length=1)
    claim_kind: Literal["disclosed_fact", "forecast", "research_opinion"]
    asserted_period: date | None = None
    asserted_by: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class RegisterKeyFactorRequest(V1Model):
    report_claim_id: uuid.UUID
    thesis_id: uuid.UUID | None = None
    name: str = Field(min_length=1)
    expected_direction: Literal["positive", "negative", "neutral"]
    metric_name: str = Field(min_length=1)
    allowed_source_types: list[str] = Field(min_length=1)
    verification_window_start: date | None = None
    verification_window_end: date | None = None
    support_condition: str = Field(min_length=1)
    refutation_condition: str = Field(min_length=1)
    next_verification_event: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class RegisterClaimVerificationRequest(V1Model):
    source_statement_id: uuid.UUID
    outcome: Literal["supported", "contradicted", "insufficient_evidence", "not_due"]
    rationale: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class RegisterMarketInstrumentBindingRequest(V1Model):
    company_id: uuid.UUID
    stock_id: uuid.UUID | None = None
    source_statement_id: uuid.UUID
    relationship_role: Literal["directly_affected", "supply_chain", "competitor", "beneficiary", "risk_exposure"]
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class RegisterFundamentalImpactRequest(V1Model):
    market_instrument_binding_id: uuid.UUID
    source_statement_id: uuid.UUID
    metric_name: str = Field(min_length=1)
    expected_direction: Literal["positive", "negative", "neutral"]
    rationale: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class RegisterMarketObservationRequest(V1Model):
    market_instrument_binding_id: uuid.UUID
    event_at: datetime
    available_at: datetime
    window_label: str = Field(min_length=1)
    benchmark: str = Field(min_length=1)
    price_source: str = Field(min_length=1)
    after_hours_treatment: str = Field(min_length=1)
    relative_return: float | None = None
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class MarketInstrumentBindingDTO(V1Model):
    id: str
    company_id: str
    company_code: str
    company_name: str
    stock_id: str | None
    stock_code: str | None
    stock_name: str | None
    relationship_role: str
    reviewed_by: str
    review_reason: str
    reviewed_at: datetime
    source: ExpressionSourceDTO


class MarketInstrumentBindingsResponse(V1Model):
    items: list[MarketInstrumentBindingDTO]


class MarketInstrumentStockOptionDTO(V1Model):
    id: str
    code: str
    name: str
    market: str


class MarketInstrumentCatalogItemDTO(V1Model):
    company_id: str
    company_code: str
    company_name: str
    company_type: str
    stocks: list[MarketInstrumentStockOptionDTO]


class MarketInstrumentCatalogResponse(V1Model):
    items: list[MarketInstrumentCatalogItemDTO]


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
    after_hours_treatment: str
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
    source_document_version_id: str | None
    # A source may be display-admitted but belong to another Case.  Only
    # Case-owned sources can be opened through this Case's frozen-material UI.
    source_visible_in_case: bool = False
    source_locator: dict | None
    provider_record_id: str | None
    source_permission_status: str
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
