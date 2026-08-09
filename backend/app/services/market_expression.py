"""Append-only commands for manually reviewed market-expression records."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import CaseDocumentVersion, Company, SourceSpan, SourceStatement, Stock, Thesis, ValidationError
from app.models.research_expression import ClaimVerification, FundamentalImpact, KeyFactor, MarketInstrumentBinding, MarketObservation, ReportClaim
from app.models.source_governance import SourceContract
from app.repositories.research import ResearchRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReportClaimInput:
    source_statement_id: uuid.UUID
    text: str
    claim_kind: str
    asserted_period: date | None
    asserted_by: str
    reviewed_by: str
    review_reason: str


@dataclass(frozen=True, slots=True)
class KeyFactorInput:
    report_claim_id: uuid.UUID
    thesis_id: uuid.UUID | None
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


@dataclass(frozen=True, slots=True)
class ClaimVerificationInput:
    source_statement_id: uuid.UUID
    outcome: str
    rationale: str
    reviewed_by: str
    review_reason: str


@dataclass(frozen=True, slots=True)
class MarketInstrumentBindingInput:
    company_id: uuid.UUID
    stock_id: uuid.UUID | None
    source_statement_id: uuid.UUID
    relationship_role: str
    reviewed_by: str
    review_reason: str


@dataclass(frozen=True, slots=True)
class FundamentalImpactInput:
    market_instrument_binding_id: uuid.UUID
    source_statement_id: uuid.UUID
    metric_name: str
    expected_direction: str
    rationale: str
    reviewed_by: str
    review_reason: str


@dataclass(frozen=True, slots=True)
class MarketObservationInput:
    market_instrument_binding_id: uuid.UUID
    event_at: datetime
    available_at: datetime
    window_label: str
    benchmark: str
    price_source: str
    after_hours_treatment: str
    relative_return: Decimal | None
    reviewed_by: str
    review_reason: str


class MarketExpressionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def register_report_claim(self, case_id: uuid.UUID, value: ReportClaimInput) -> ReportClaim:
        self._require_case(case_id)
        self._require_admitted_case_statement(case_id, value.source_statement_id)
        self._require_text(value.text, "claim text")
        self._require_text(value.asserted_by, "asserted_by")
        self._require_text(value.reviewed_by, "reviewed_by")
        self._require_text(value.review_reason, "review_reason")
        record = ReportClaim(
            research_case_id=case_id,
            source_statement_id=value.source_statement_id,
            text=value.text.strip(),
            claim_kind=value.claim_kind,
            asserted_period=value.asserted_period,
            asserted_by=value.asserted_by.strip(),
            review_state="reviewed",
            reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(),
            reviewed_at=_utcnow(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def register_key_factor(self, case_id: uuid.UUID, value: KeyFactorInput) -> KeyFactor:
        self._require_case(case_id)
        claim = self._session.get(ReportClaim, value.report_claim_id)
        if claim is None or claim.research_case_id != case_id or claim.review_state != "reviewed":
            raise ValidationError("report claim must be a reviewed record in this research case")
        if value.thesis_id is not None:
            thesis = self._session.get(Thesis, value.thesis_id)
            if thesis is None or thesis.research_case_id != case_id or thesis.review_state != "confirmed":
                raise ValidationError("thesis must be a confirmed proposition in this research case")
        if value.verification_window_start and value.verification_window_end and value.verification_window_start > value.verification_window_end:
            raise ValidationError("verification_window_start must not be after verification_window_end")
        for name in ("name", "metric_name", "support_condition", "refutation_condition", "next_verification_event", "reviewed_by", "review_reason"):
            self._require_text(getattr(value, name), name)
        if not value.allowed_source_types or not all(item.strip() for item in value.allowed_source_types):
            raise ValidationError("allowed_source_types must not be empty")
        record = KeyFactor(
            research_case_id=case_id,
            thesis_id=value.thesis_id,
            report_claim_id=claim.id,
            name=value.name.strip(),
            expected_direction=value.expected_direction,
            metric_name=value.metric_name.strip(),
            allowed_source_types=[item.strip() for item in value.allowed_source_types],
            verification_window_start=value.verification_window_start,
            verification_window_end=value.verification_window_end,
            support_condition=value.support_condition.strip(),
            refutation_condition=value.refutation_condition.strip(),
            next_verification_event=value.next_verification_event.strip(),
            review_state="reviewed",
            reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(),
            reviewed_at=_utcnow(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def register_claim_verification(
        self, case_id: uuid.UUID, factor_id: uuid.UUID, value: ClaimVerificationInput
    ) -> ClaimVerification:
        self._require_case(case_id)
        factor = self._session.get(KeyFactor, factor_id)
        if factor is None or factor.research_case_id != case_id or factor.review_state != "reviewed":
            raise ValidationError("key factor must be a reviewed record in this research case")
        self._require_admitted_case_statement(case_id, value.source_statement_id)
        for name in ("rationale", "reviewed_by", "review_reason"):
            self._require_text(getattr(value, name), name)
        record = ClaimVerification(
            key_factor_id=factor.id,
            source_statement_id=value.source_statement_id,
            outcome=value.outcome,
            rationale=value.rationale.strip(),
            review_state="reviewed",
            reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(),
            reviewed_at=_utcnow(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def register_market_instrument_binding(
        self, case_id: uuid.UUID, value: MarketInstrumentBindingInput
    ) -> MarketInstrumentBinding:
        self._require_case(case_id)
        company = self._session.get(Company, value.company_id)
        if company is None:
            raise ValidationError("company not found")
        if value.stock_id is not None:
            stock = self._session.get(Stock, value.stock_id)
            if stock is None or stock.company_id != company.id:
                raise ValidationError("stock must belong to the selected company")
        self._require_admitted_case_statement(case_id, value.source_statement_id)
        for name in ("reviewed_by", "review_reason"):
            self._require_text(getattr(value, name), name)
        record = MarketInstrumentBinding(
            research_case_id=case_id,
            company_id=company.id,
            stock_id=value.stock_id,
            source_statement_id=value.source_statement_id,
            relationship_role=value.relationship_role,
            review_state="reviewed",
            reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(),
            reviewed_at=_utcnow(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def register_fundamental_impact(
        self, case_id: uuid.UUID, factor_id: uuid.UUID, value: FundamentalImpactInput
    ) -> FundamentalImpact:
        self._require_case(case_id)
        factor = self._session.get(KeyFactor, factor_id)
        if factor is None or factor.research_case_id != case_id or factor.review_state != "reviewed":
            raise ValidationError("key factor must be a reviewed record in this research case")
        binding = self._session.get(MarketInstrumentBinding, value.market_instrument_binding_id)
        if binding is None or binding.research_case_id != case_id or binding.review_state != "reviewed":
            raise ValidationError("market instrument binding must be a reviewed record in this research case")
        self._require_admitted_case_statement(case_id, binding.source_statement_id)
        self._require_admitted_case_statement(case_id, value.source_statement_id)
        for name in ("metric_name", "rationale", "reviewed_by", "review_reason"):
            self._require_text(getattr(value, name), name)
        record = FundamentalImpact(
            research_case_id=case_id,
            key_factor_id=factor.id,
            company_id=binding.company_id,
            stock_id=binding.stock_id,
            metric_name=value.metric_name.strip(),
            expected_direction=value.expected_direction,
            rationale=value.rationale.strip(),
            source_statement_id=value.source_statement_id,
            review_state="reviewed",
            reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(),
            reviewed_at=_utcnow(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def register_market_observation(
        self, case_id: uuid.UUID, factor_id: uuid.UUID, value: MarketObservationInput
    ) -> MarketObservation:
        self._require_case(case_id)
        factor = self._session.get(KeyFactor, factor_id)
        if factor is None or factor.research_case_id != case_id or factor.review_state != "reviewed":
            raise ValidationError("key factor must be a reviewed record in this research case")
        binding = self._session.get(MarketInstrumentBinding, value.market_instrument_binding_id)
        if binding is None or binding.research_case_id != case_id or binding.review_state != "reviewed" or binding.stock_id is None:
            raise ValidationError("market observation requires a reviewed stock binding in this research case")
        self._require_admitted_case_statement(case_id, binding.source_statement_id)
        if value.event_at.tzinfo is None or value.available_at.tzinfo is None:
            raise ValidationError("event_at and available_at must include a timezone")
        if value.available_at < value.event_at:
            raise ValidationError("available_at must not be before event_at")
        for name in ("window_label", "benchmark", "price_source", "after_hours_treatment", "reviewed_by", "review_reason"):
            self._require_text(getattr(value, name), name)
        if value.relative_return is not None and not value.relative_return.is_finite():
            raise ValidationError("relative_return must be finite when recorded")
        record = MarketObservation(
            research_case_id=case_id,
            key_factor_id=factor.id,
            stock_id=binding.stock_id,
            event_at=value.event_at,
            available_at=value.available_at,
            window_label=value.window_label.strip(),
            benchmark=value.benchmark.strip(),
            price_source=value.price_source.strip(),
            after_hours_treatment=value.after_hours_treatment.strip(),
            relative_return=value.relative_return,
            review_state="reviewed",
            reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(),
            reviewed_at=_utcnow(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def _require_case(self, case_id: uuid.UUID) -> None:
        if ResearchRepository(self._session).get_case(case_id) is None:
            raise ValidationError("research case not found")

    def _require_admitted_case_statement(self, case_id: uuid.UUID, statement_id: uuid.UUID) -> SourceStatement:
        statement = self._session.get(SourceStatement, statement_id)
        span = self._session.get(SourceSpan, statement.source_span_id) if statement else None
        if statement is None or span is None:
            raise ValidationError("source statement not found")
        case_document = self._session.scalar(
            select(CaseDocumentVersion.id)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(CaseDocumentVersion.document_version_id == span.document_version_id)
            .limit(1)
        )
        contract = self._session.scalar(
            select(SourceContract).where(SourceContract.document_version_id == span.document_version_id)
        )
        if case_document is None or contract is None or not contract.allow_ai_processing or not contract.allow_display:
            raise ValidationError("source statement must be attached to this case and admitted for processing and display")
        return statement

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not value.strip():
            raise ValidationError(f"{name} must not be empty")
