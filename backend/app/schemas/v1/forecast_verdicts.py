"""HTTP contracts for immutable forecast evaluation and human verdicts."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
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
    baseline_value: Decimal | None = None
    expected_value: Decimal
    unit: str = Field(min_length=1)
    forecast_period_start: date
    forecast_period_end: date
    comparator: Literal["at_least", "at_most", "within_tolerance"]
    relative_tolerance: Decimal | None = None
    reviewed_by: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)


class RecordActualMetricObservationRequest(V1Model):
    source_statement_id: uuid.UUID
    entity_key: str = Field(min_length=1)
    observed_value: Decimal
    unit: str = Field(min_length=1)
    observed_period_start: date
    observed_period_end: date
    available_at: datetime
    recorded_by: str = Field(min_length=1)
    record_reason: str = Field(min_length=1)


class EvaluateForecastTargetRequest(V1Model):
    actual_observation_id: uuid.UUID
    cutoff: datetime


class CreateForecastVerdictRequest(V1Model):
    decision: Literal["confirmed", "modified", "rejected"]
    outcome: Literal["supported", "contradicted", "insufficient_evidence", "not_due"] | None = None
    reason: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    supersedes_id: uuid.UUID | None = None


class ForecastVerdictDTO(V1Model):
    id: str
    target_id: str
    key_factor_id: str
    outcome: str
    decision: str
    metric_name: str
    entity_key: str
    baseline_value: Decimal | None
    expected_value: Decimal
    actual_value: Decimal
    unit: str
    forecast_period_start: date
    forecast_period_end: date
    comparator: str
    relative_tolerance: Decimal | None
    rule_version: str
    rationale: str
    reviewed_by: str
    reason: str
    reviewed_at: datetime
    forecast_source: ExpressionSourceDTO
    baseline_source: ExpressionSourceDTO | None
    actual_source: ExpressionSourceDTO


class ForecastVerdictsResponse(V1Model):
    case_id: str
    cutoff: datetime
    items: list[ForecastVerdictDTO]
