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
