"""Deterministic evaluation for frozen report forecasts.

This module deliberately does not fetch data, infer a metric from text, or
publish a research conclusion.  It only compares two already-frozen numbers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import CaseDocumentVersion, DocumentVersion, SourceSpan, SourceStatement, ValidationError
from app.models.research_expression import ActualMetricObservation, ForecastEvaluationCandidate, ForecastTargetVersion, ForecastVerdict, KeyFactor, ReportClaim
from app.models.source_governance import SourceContract
from app.repositories.research import ResearchRepository
from app.services.source_admission import source_contract_is_active


@dataclass(frozen=True)
class NumericForecastEvaluation:
    outcome: str
    rule_version: str
    inputs: dict[str, str]
    rationale: str


ForecastOutcome = Literal["supported", "contradicted", "insufficient_evidence", "not_due"]
ForecastComparator = Literal["at_least", "at_most", "within_tolerance"]


@dataclass(frozen=True, slots=True)
class ForecastTargetInput:
    key_factor_id: uuid.UUID
    report_claim_id: uuid.UUID
    forecast_source_statement_id: uuid.UUID
    baseline_source_statement_id: uuid.UUID | None
    metric_name: str
    entity_key: str
    baseline_value: Decimal | None
    expected_value: Decimal
    unit: str
    forecast_period_start: date
    forecast_period_end: date
    comparator: ForecastComparator
    relative_tolerance: Decimal | None
    reviewed_by: str
    review_reason: str


@dataclass(frozen=True, slots=True)
class ActualObservationInput:
    source_statement_id: uuid.UUID
    entity_key: str
    observed_value: Decimal
    unit: str
    observed_period_start: date
    observed_period_end: date
    available_at: datetime
    recorded_by: str
    record_reason: str


@dataclass(frozen=True, slots=True)
class ForecastVerdictInput:
    decision: Literal["confirmed", "modified", "rejected"]
    outcome: ForecastOutcome | None
    reason: str
    reviewed_by: str
    supersedes_id: uuid.UUID | None = None


def evaluate_numeric_forecast(
    *,
    expected_value: Decimal,
    actual_value: Decimal,
    comparator: ForecastComparator,
    relative_tolerance: Decimal | None,
) -> NumericForecastEvaluation:
    """Evaluate one frozen numeric forecast without assigning causality."""
    if comparator not in {"at_least", "at_most", "within_tolerance"}:
        raise ValueError("unsupported forecast comparator")
    if relative_tolerance is not None and relative_tolerance < 0:
        raise ValueError("relative tolerance must not be negative")
    if comparator == "within_tolerance" and relative_tolerance is None:
        raise ValueError("within_tolerance requires a relative tolerance")
    if comparator == "within_tolerance" and expected_value == 0:
        raise ValueError("within_tolerance cannot compare a zero expected_value")

    if comparator == "at_least":
        supported = actual_value >= expected_value
    elif comparator == "at_most":
        supported = actual_value <= expected_value
    else:
        supported = abs(actual_value - expected_value) <= abs(expected_value) * relative_tolerance

    inputs = {
        "expected_value": str(expected_value),
        "actual_value": str(actual_value),
        "comparator": comparator,
        "relative_tolerance": str(relative_tolerance) if relative_tolerance is not None else "",
    }
    return NumericForecastEvaluation(
        outcome="supported" if supported else "contradicted",
        rule_version="forecast-numeric-v1",
        inputs=inputs,
        rationale=(
            "实际值满足冻结的数值比较规则。"
            if supported
            else "实际值不满足冻结的数值比较规则。"
        ),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ForecastVerdictService:
    """Create frozen inputs, deterministic candidates and human verdicts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_target(self, case_id: uuid.UUID, value: ForecastTargetInput) -> ForecastTargetVersion:
        self._require_case(case_id)
        factor = self._session.get(KeyFactor, value.key_factor_id)
        claim = self._session.get(ReportClaim, value.report_claim_id)
        if factor is None or factor.research_case_id != case_id or factor.review_state != "reviewed":
            raise ValidationError("key factor must be a reviewed record in this research case")
        if claim is None or claim.research_case_id != case_id or claim.review_state != "reviewed":
            raise ValidationError("report claim must be a reviewed record in this research case")
        if factor.report_claim_id != claim.id:
            raise ValidationError("forecast target factor must be linked to its report claim")
        if claim.source_statement_id != value.forecast_source_statement_id:
            raise ValidationError("forecast source statement must be the report claim source")
        self._require_admitted_case_statement(case_id, value.forecast_source_statement_id)
        if value.baseline_source_statement_id is not None:
            self._require_admitted_case_statement(case_id, value.baseline_source_statement_id)
        if value.forecast_period_start > value.forecast_period_end:
            raise ValidationError("forecast_period_start must not be after forecast_period_end")
        self._require_finite(value.expected_value, "expected_value")
        if value.baseline_value is not None:
            self._require_finite(value.baseline_value, "baseline_value")
        if value.relative_tolerance is not None and value.relative_tolerance < 0:
            raise ValidationError("relative_tolerance must not be negative")
        if value.comparator == "within_tolerance" and value.relative_tolerance is None:
            raise ValidationError("within_tolerance requires relative_tolerance")
        for name in ("metric_name", "entity_key", "unit", "reviewed_by", "review_reason"):
            self._require_text(getattr(value, name), name)
        now = _utcnow()
        record = ForecastTargetVersion(
            research_case_id=case_id, key_factor_id=factor.id, report_claim_id=claim.id,
            forecast_source_statement_id=value.forecast_source_statement_id,
            baseline_source_statement_id=value.baseline_source_statement_id,
            metric_name=value.metric_name.strip(), entity_key=value.entity_key.strip(),
            baseline_value=value.baseline_value, expected_value=value.expected_value,
            unit=value.unit.strip(), forecast_period_start=value.forecast_period_start,
            forecast_period_end=value.forecast_period_end, comparator=value.comparator,
            relative_tolerance=value.relative_tolerance, reviewed_by=value.reviewed_by.strip(),
            review_reason=value.review_reason.strip(), reviewed_at=now, created_at=now,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def record_actual(self, target_id: uuid.UUID, value: ActualObservationInput) -> ActualMetricObservation:
        target = self._session.get(ForecastTargetVersion, target_id)
        if target is None:
            raise ValidationError("forecast target not found")
        self._require_admitted_case_statement(target.research_case_id, value.source_statement_id)
        if value.entity_key.strip() != target.entity_key:
            raise ValidationError("actual observation entity must equal the frozen target entity")
        if value.unit.strip() != target.unit:
            raise ValidationError("actual observation unit must equal the frozen target unit")
        if (value.observed_period_start, value.observed_period_end) != (target.forecast_period_start, target.forecast_period_end):
            raise ValidationError("actual observation period must equal the frozen forecast period")
        if value.available_at.tzinfo is None:
            raise ValidationError("available_at must include a timezone")
        available_at = _as_utc(value.available_at)
        if _as_utc(self._statement_available_at(value.source_statement_id)) != available_at:
            raise ValidationError("actual observation available_at must equal its frozen source availability")
        self._require_finite(value.observed_value, "observed_value")
        for name in ("entity_key", "recorded_by", "record_reason"):
            self._require_text(getattr(value, name), name)
        record = ActualMetricObservation(
            forecast_target_id=target.id, source_statement_id=value.source_statement_id,
            entity_key=value.entity_key.strip(), observed_value=value.observed_value,
            unit=value.unit.strip(), observed_period_start=value.observed_period_start,
            observed_period_end=value.observed_period_end, available_at=available_at,
            recorded_by=value.recorded_by.strip(), record_reason=value.record_reason.strip(),
            created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def evaluate(self, target_id: uuid.UUID, actual_id: uuid.UUID, *, cutoff: datetime) -> ForecastEvaluationCandidate:
        target = self._session.get(ForecastTargetVersion, target_id)
        actual = self._session.get(ActualMetricObservation, actual_id)
        if target is None or actual is None or actual.forecast_target_id != target.id:
            raise ValidationError("actual observation must belong to the selected forecast target")
        if cutoff.tzinfo is None:
            raise ValidationError("cutoff must include a timezone")
        cutoff = _as_utc(cutoff)
        available_at = _as_utc(actual.available_at)
        if cutoff < available_at:
            evaluation = NumericForecastEvaluation(
                outcome="not_due", rule_version="forecast-numeric-v1",
                inputs={"expected_value": str(target.expected_value), "actual_value": str(actual.observed_value), "cutoff": cutoff.isoformat(), "available_at": available_at.isoformat()},
                rationale="实际观测在该查询截点尚不可用。",
            )
        else:
            evaluation = evaluate_numeric_forecast(
                expected_value=target.expected_value, actual_value=actual.observed_value,
                comparator=target.comparator, relative_tolerance=target.relative_tolerance,
            )
            evaluation.inputs.update({"unit": target.unit, "cutoff": cutoff.isoformat(), "available_at": available_at.isoformat()})
        record = ForecastEvaluationCandidate(
            forecast_target_id=target.id, actual_observation_id=actual.id, cutoff=cutoff,
            outcome=evaluation.outcome, rule_version=evaluation.rule_version, inputs=evaluation.inputs,
            rationale=evaluation.rationale, review_state="machine_generated", created_at=_utcnow(),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def create_verdict(self, candidate_id: uuid.UUID, value: ForecastVerdictInput) -> ForecastVerdict:
        candidate = self._session.get(ForecastEvaluationCandidate, candidate_id)
        if candidate is None:
            raise ValidationError("forecast evaluation candidate not found")
        if value.decision == "confirmed":
            outcome = candidate.outcome
        elif value.decision == "modified":
            if value.outcome is None:
                raise ValidationError("modified verdict requires an explicit outcome")
            outcome = value.outcome
        else:
            outcome = value.outcome or candidate.outcome
        if value.supersedes_id is not None:
            previous = self._session.get(ForecastVerdict, value.supersedes_id)
            if previous is None or previous.candidate_id != candidate.id:
                raise ValidationError("superseded verdict must belong to the same candidate")
        for name in ("reason", "reviewed_by"):
            self._require_text(getattr(value, name), name)
        now = _utcnow()
        record = ForecastVerdict(
            candidate_id=candidate.id, supersedes_id=value.supersedes_id,
            decision=value.decision, outcome=outcome, reason=value.reason.strip(),
            reviewed_by=value.reviewed_by.strip(), reviewed_at=now, created_at=now,
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
        case_document = self._session.scalar(select(CaseDocumentVersion.id).where(CaseDocumentVersion.research_case_id == case_id).where(CaseDocumentVersion.document_version_id == span.document_version_id).limit(1))
        contract = self._session.scalar(select(SourceContract).where(SourceContract.document_version_id == span.document_version_id))
        if case_document is None or contract is None or not contract.allow_ai_processing or not contract.allow_display or not source_contract_is_active(contract):
            raise ValidationError("source statement must be attached to this case and admitted for processing and display")
        return statement

    def _statement_available_at(self, statement_id: uuid.UUID) -> datetime:
        statement = self._session.get(SourceStatement, statement_id)
        span = self._session.get(SourceSpan, statement.source_span_id) if statement else None
        document = self._session.get(DocumentVersion, span.document_version_id) if span else None
        if document is None:
            raise ValidationError("source document not found")
        return document.available_at

    @staticmethod
    def _require_text(value: str, name: str) -> None:
        if not value or not value.strip():
            raise ValidationError(f"{name} must not be empty")

    @staticmethod
    def _require_finite(value: Decimal, name: str) -> None:
        if not value.is_finite():
            raise ValidationError(f"{name} must be finite")
