"""Append-only records behind the reviewed market-expression read model."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, JSON, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class ReportClaim(Base):
    __tablename__ = "report_claims"
    __table_args__ = (
        CheckConstraint("claim_kind IN ('disclosed_fact', 'forecast', 'research_opinion')", name="ck_report_claims_kind"),
        CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_report_claims_review_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    source_statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    asserted_period: Mapped[date | None] = mapped_column(Date, nullable=True)
    asserted_by: Mapped[str] = mapped_column(Text, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KeyFactor(Base):
    __tablename__ = "key_factors"
    __table_args__ = (
        CheckConstraint("expected_direction IN ('positive', 'negative', 'neutral')", name="ck_key_factors_direction"),
        CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_key_factors_review_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    thesis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("theses.id"), nullable=True, index=True)
    report_claim_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("report_claims.id"), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_source_types: Mapped[list] = mapped_column(JSON, nullable=False)
    verification_window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    verification_window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    support_condition: Mapped[str] = mapped_column(Text, nullable=False)
    refutation_condition: Mapped[str] = mapped_column(Text, nullable=False)
    next_verification_event: Mapped[str] = mapped_column(Text, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KeyFactorCandidateRun(Base):
    """One reproducible, source-bound deterministic parsing attempt."""

    __tablename__ = "key_factor_candidate_runs"
    __table_args__ = (
        CheckConstraint("status IN ('completed')", name="ck_key_factor_candidate_runs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    source_statement_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_count: Mapped[int] = mapped_column(nullable=False)
    skipped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KeyFactorCandidate(Base):
    """Machine proposal retained separately from a reviewed KeyFactor."""

    __tablename__ = "key_factor_candidates"
    __table_args__ = (
        CheckConstraint(
            "review_state IN ('machine_generated')",
            name="ck_key_factor_candidates_review_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("key_factor_candidate_runs.id"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_window_start: Mapped[date] = mapped_column(Date, nullable=False)
    verification_window_end: Mapped[date] = mapped_column(Date, nullable=False)
    support_condition: Mapped[str] = mapped_column(Text, nullable=False)
    refutation_condition: Mapped[str] = mapped_column(Text, nullable=False)
    next_verification_event: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClaimVerification(Base):
    __tablename__ = "claim_verifications"
    __table_args__ = (
        CheckConstraint("outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')", name="ck_claim_verifications_outcome"),
        CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_claim_verifications_review_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    key_factor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("key_factors.id"), nullable=False, index=True)
    source_statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketInstrumentBinding(Base):
    """A reviewed, source-backed Case association to a company or security.

    This is deliberately distinct from ``ThemeRole``.  Theme taxonomy can be
    useful context, but it is not evidence that a Case's factor applies to a
    company or stock.
    """

    __tablename__ = "market_instrument_bindings"
    __table_args__ = (
        CheckConstraint(
            "relationship_role IN ('directly_affected', 'supply_chain', 'competitor', 'beneficiary', 'risk_exposure')",
            name="ck_market_instrument_bindings_role",
        ),
        CheckConstraint(
            "review_state IN ('machine_generated', 'reviewed', 'rejected')",
            name="ck_market_instrument_bindings_review_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("companies.id"), nullable=False)
    stock_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("stocks.id"), nullable=True)
    source_statement_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=False)
    relationship_role: Mapped[str] = mapped_column(String(32), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FundamentalImpact(Base):
    __tablename__ = "fundamental_impacts"
    __table_args__ = (
        CheckConstraint("expected_direction IN ('positive', 'negative', 'neutral')", name="ck_fundamental_impacts_direction"),
        CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_fundamental_impacts_review_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    key_factor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("key_factors.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("companies.id"), nullable=False)
    stock_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("stocks.id"), nullable=True)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    source_statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=True)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketObservation(Base):
    __tablename__ = "market_observations"
    __table_args__ = (CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_market_observations_review_state"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    key_factor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("key_factors.id"), nullable=True)
    stock_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stocks.id"), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_label: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark: Mapped[str] = mapped_column(Text, nullable=False)
    price_source: Mapped[str] = mapped_column(String(128), nullable=False)
    after_hours_treatment: Mapped[str] = mapped_column(Text, nullable=False, default="not_recorded")
    relative_return: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForecastTargetVersion(Base):
    """A reviewed numeric forecast frozen from a report claim."""

    __tablename__ = "forecast_target_versions"
    __table_args__ = (
        CheckConstraint(
            "comparator IN ('at_least', 'at_most', 'within_tolerance')",
            name="ck_forecast_target_comparator",
        ),
        CheckConstraint(
            "relative_tolerance IS NULL OR relative_tolerance >= 0",
            name="ck_forecast_target_tolerance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False, index=True)
    key_factor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("key_factors.id"), nullable=False, index=True)
    report_claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("report_claims.id"), nullable=False)
    forecast_source_statement_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=False)
    baseline_source_statement_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=True)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6), nullable=True)
    expected_value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    forecast_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    comparator: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_tolerance: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    review_reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActualMetricObservation(Base):
    """A source-backed actual for exactly one frozen target."""

    __tablename__ = "actual_metric_observations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    forecast_target_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("forecast_target_versions.id"), nullable=False, index=True)
    source_statement_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_statements.id"), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    observed_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    record_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForecastEvaluationCandidate(Base):
    """A deterministic comparison awaiting an explicit human verdict."""

    __tablename__ = "forecast_evaluation_candidates"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')",
            name="ck_forecast_evaluation_outcome",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    forecast_target_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("forecast_target_versions.id"), nullable=False, index=True)
    actual_observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("actual_metric_observations.id"), nullable=False)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), nullable=False, default="machine_generated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForecastVerdict(Base):
    """An append-only human publication or withdrawal of one candidate."""

    __tablename__ = "forecast_verdicts"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('confirmed', 'modified', 'rejected')",
            name="ck_forecast_verdict_decision",
        ),
        CheckConstraint(
            "outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')",
            name="ck_forecast_verdict_outcome",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("forecast_evaluation_candidates.id"), nullable=False, index=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("forecast_verdicts.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
