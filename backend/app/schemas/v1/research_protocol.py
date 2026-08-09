from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model


class MetricDefinitionRequest(V1Model):
    metric_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    canonical_definition: str = Field(min_length=1)
    entity_scope: Literal["company", "business_line", "product_line"]
    unit: str = Field(min_length=1)
    frequency: str = Field(min_length=1)
    period_semantics: str = Field(min_length=1)
    allowed_source_roles: list[str] = Field(min_length=1)
    role_eligibility: list[Literal["outcome", "driver", "mediator", "context"]] = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MetricDefinitionDTO(V1Model):
    id: str
    metric_id: str
    version: int
    display_name: str
    entity_scope: str
    unit: str
    role_eligibility: list[str]
    approved_by: str
    reason: str
    created_at: datetime


class OutcomeBindingRequest(V1Model):
    metric_definition_id: uuid.UUID
    entity_scope: dict[str, str]
    direction: Literal["increase", "decrease", "stable", "mixed"]
    baseline: dict[str, str]
    horizon_start: date
    horizon_end: date
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ApproveOutcomeBindingRequest(V1Model):
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OutcomeBindingDTO(V1Model):
    id: str
    thesis_id: str
    metric_definition_id: str
    entity_scope: dict[str, str]
    direction: str
    baseline: dict[str, str]
    horizon_start: date
    horizon_end: date
    state: str
    supersedes_id: str | None
    reviewer: str
    reason: str
    created_at: datetime


class ResearchabilityDTO(V1Model):
    status: Literal["not_applicable", "blocked", "ready"]
    reason_codes: list[str]
    effective_binding_id: str | None
    next_action: str
