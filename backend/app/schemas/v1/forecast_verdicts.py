"""Commands and replay DTOs for human-published report forecast verdicts."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model
from app.schemas.v1.market_expression import ExpressionSourceDTO


class CreateForecastTargetRequest(V1Model):
    key_factor_id: uuid.UUID
    report_claim_id: uuid.UUID
    forecast_source_statement_id: uuid.UUID
    baseline_source_statement_id: uuid.UUID | None = None
    metric_name: str = Field(min_length=1)
    entity_key: str = Field(min_length=1)
    baseline_value: float | None = None
    expected_value: float
    unit: str = Field(min_length=1)
    forecast_period_start: date
    forecast_period_end: date
    comparator: Literal["at_least", "at_most", "within_tolerance"]
    relative_tolerance: float | None = None
    review_reason: str = Field(min_length=1)


class RecordActualMetricObservationRequest(V1Model):
    forecast_target_id: uuid.UUID
    source_statement_id: uuid.UUID
    entity_key: str = Field(min_length=1)
    observed_value: float
    unit: str = Field(min_length=1)
    observed_period_start: date
    observed_period_end: date
    available_at: datetime
    record_reason: str = Field(min_length=1)


class EvaluateForecastTargetRequest(V1Model):
    actual_observation_id: uuid.UUID
    cutoff: datetime


class CreateForecastVerdictRequest(V1Model):
    decision: Literal["confirmed", "modified", "rejected"]
    outcome: Literal["supported", "contradicted", "insufficient_evidence", "not_due"] | None = None
    reason: str = Field(min_length=1)
    supersedes_id: uuid.UUID | None = None


class ForecastTargetDTO(V1Model):
    id: str
    case_id: str
    key_factor_id: str
    report_claim_id: str
    metric_name: str
    entity_key: str
    baseline_value: float | None
    expected_value: float
    unit: str
    forecast_period_start: date
    forecast_period_end: date
    comparator: str
    relative_tolerance: float | None
    reviewed_by: str
    review_reason: str
    reviewed_at: datetime
    forecast_source: ExpressionSourceDTO
    baseline_source: ExpressionSourceDTO | None


class ActualMetricObservationDTO(V1Model):
    id: str
    forecast_target_id: str
    entity_key: str
    observed_value: float
    unit: str
    observed_period_start: date
    observed_period_end: date
    available_at: datetime
    recorded_by: str
    record_reason: str
    source: ExpressionSourceDTO


class ForecastEvaluationCandidateDTO(V1Model):
    id: str
    forecast_target_id: str
    actual_observation_id: str
    cutoff: datetime
    outcome: str
    rule_version: str
    inputs: dict[str, str]
    rationale: str
    review_state: str
    created_at: datetime


class ForecastVerdictDTO(V1Model):
    id: str
    candidate_id: str
    supersedes_id: str | None
    decision: str
    outcome: str
    reason: str
    reviewed_by: str
    reviewed_at: datetime
    target: ForecastTargetDTO
    actual: ActualMetricObservationDTO
    rule_version: str
    inputs: dict[str, str]
    candidate_rationale: str
    forecast_source: ExpressionSourceDTO
    actual_source: ExpressionSourceDTO


class ForecastVerdictHistoryResponse(V1Model):
    case_id: str
    cutoff: datetime
    items: list[ForecastVerdictDTO]
