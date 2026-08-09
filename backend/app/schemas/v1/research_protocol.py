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


class MechanismNodeDTO(V1Model):
    id: str
    node_key: str
    display_name: str
    role: str


class MechanismEdgeDTO(V1Model):
    id: str
    edge_key: str
    source_node_id: str
    target_node_id: str


class MechanismTemplateDTO(V1Model):
    id: str
    template_key: str
    version: int
    display_name: str
    industry_scope: str
    approved_by: str
    reason: str
    created_at: datetime
    nodes: list[MechanismNodeDTO]
    edges: list[MechanismEdgeDTO]


class SelectMechanismTemplateRequest(V1Model):
    template_version_id: uuid.UUID
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MechanismSelectionDTO(V1Model):
    id: str
    research_case_id: str
    template_version_id: str
    supersedes_id: str | None
    reviewer: str
    reason: str
    created_at: datetime


class VerificationRuleRequest(V1Model):
    metric_definition_id: uuid.UUID
    expected_direction: Literal["increase", "decrease", "stable", "mixed"]
    support_predicate: str = Field(min_length=1)
    contradiction_predicate: str = Field(min_length=1)
    allowed_source_roles: list[str] = Field(min_length=1)
    observed_period_start: date
    observed_period_end: date
    available_at_deadline: date
    next_verification_event: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class VerificationRuleDTO(V1Model):
    id: str
    research_case_id: str | None
    mechanism_edge_id: str
    metric_definition_id: str
    expected_direction: str
    support_predicate: str
    contradiction_predicate: str
    allowed_source_roles: list[str]
    observed_period_start: date
    observed_period_end: date
    available_at_deadline: date
    next_verification_event: str
    supersedes_id: str | None
    reviewer: str
    reason: str
    created_at: datetime


class CaseMechanismProtocolDTO(V1Model):
    selection: MechanismSelectionDTO | None
    template: MechanismTemplateDTO | None
    rules: list[VerificationRuleDTO]
    rule_history: list[VerificationRuleDTO]
