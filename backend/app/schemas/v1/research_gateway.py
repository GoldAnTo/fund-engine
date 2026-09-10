"""Narrow public conversation contract; no provider payloads or credentials."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.research_gateway import (
    SafeRoleEventReason,
    SafeRoleEventSummary,
    safe_role_event_display_text,
)
from app.schemas.v1.common import V1Model

RoleKey = Literal["scope_identity", "sources_evidence", "analysis_counter_evidence", "compilation_checks"]
RoleStatus = Literal["queued", "running", "blocked", "completed", "failed", "cancelled"]
CommandKind = Literal["cancel", "retry", "request_counter_evidence", "scope_change", "pause", "resume", "grounded_question"]
NativeStatus = Literal["queued", "running", "waiting_for_sources", "succeeded", "failed", "cancelled"]
GatewayScopeSourceRole = Literal["company_disclosure", "licensed_provider"]


class ConversationCreateRequest(V1Model):
    initial_message: str = Field(min_length=1, max_length=20_000)


class MessageSendRequest(V1Model):
    text: str = Field(min_length=1, max_length=20_000)


class GatewayReceiptDTO(V1Model):
    conversation_id: str
    intent_id: str
    run_spec_id: str
    native_case_id: str
    native_run_id: str
    status: Literal["queued"]
    latest_sequence: int = Field(ge=0)


class GatewayIntentReceiptDTO(V1Model):
    """A durable, rejected follow-up that launched no native research run."""

    receipt_kind: Literal["intent"]
    conversation_id: str
    intent_id: str
    intent_kind: Literal["unsupported"]
    outcome: Literal["rejected"]
    reason_code: Literal["unsupported_in_gateway_p0"]


class CommandRequest(V1Model):
    kind: CommandKind


class CommandReceiptDTO(V1Model):
    receipt_kind: Literal["command"]
    command_id: str
    conversation_id: str
    run_spec_id: str
    command_kind: CommandKind
    outcome: Literal["accepted", "rejected"]
    reason_code: SafeRoleEventReason | None


class SafeArtifactRef(V1Model):
    kind: Literal["research_case", "research_run", "evidence_link", "document_version", "draft", "validation"]
    id: UUID
    case_id: UUID | None = None
    locator_available: bool | None = None


class SafeRoleEventDTO(V1Model):
    sequence: int = Field(ge=1)
    run_spec_id: str
    role: RoleKey
    type: SafeRoleEventSummary
    status: RoleStatus | None
    summary: str | None = Field(max_length=96)
    reason_code: SafeRoleEventReason | None
    artifacts: list[SafeArtifactRef] = Field(max_length=32)
    occurred_at: datetime

    @field_validator("summary")
    @classmethod
    def require_safe_summary(cls, value: str | None) -> str | None:
        if value is not None and value not in {
            safe_role_event_display_text(kind.value) for kind in SafeRoleEventSummary
        }:
            raise ValueError("summary must be a Gateway-owned display label")
        return value


class ConversationSummaryDTO(V1Model):
    conversation_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    latest_sequence: int = Field(ge=0)


class ConversationListResponse(V1Model):
    conversations: list[ConversationSummaryDTO]


class ResearchMessageDTO(V1Model):
    id: str
    sequence: int = Field(ge=1)
    kind: Literal["user", "system"] = "user"
    text: str
    created_at: datetime


class GatewayExecutionCountsDTO(V1Model):
    discovered: int = Field(ge=0, strict=True)
    fetched: int = Field(ge=0, strict=True)
    frozen: int = Field(ge=0, strict=True)
    admitted: int = Field(ge=0, strict=True)
    exceptions: int = Field(ge=0, strict=True)


class GatewayExecutionTaskDTO(V1Model):
    task_id: UUID
    task_type: Literal["support", "contradict", "alternative", "alternative_explanation", "intake_material"]
    status: Literal["queued", "running", "retry_wait", "succeeded", "partial", "failed", "cancelled"]
    stage: Literal["queued", "searching", "fetching", "freezing", "extracting", "admitting", "succeeded", "partial", "failed", "cancelled"]
    source_kind: Literal["intake_material", "external_sources"]
    source_name: Literal["用户提供材料", "合规外部来源"]
    providers: list[Literal["gildata", "sse", "szse"]] = Field(max_length=3)
    attempt: int = Field(ge=0, strict=True)
    updated_at: datetime
    retry_at: datetime | None
    counts: GatewayExecutionCountsDTO


class GatewayExecutionDTO(V1Model):
    projection_state: Literal["available", "unavailable"]
    stage: Literal["queued", "planning", "retrieve", "analyze", "assessing", "conclude", "complete", "failed", "cancelled", "unknown"]
    updated_at: datetime
    reason_code: Literal["source_unavailable", "source_policy_blocked", "execution_state_unavailable", "native_execution_failed", "worker_unavailable", "research_subject_missing"] | None
    next_action: Literal["wait_for_execution", "wait_for_retry", "check_execution", "review_result", "none"]
    worker_state: Literal["online", "offline", "unknown"]
    worker_last_seen_at: datetime | None
    worker_scope: Literal["acquisition_service"] = "acquisition_service"
    tasks: list[GatewayExecutionTaskDTO]


class GatewayExecutionProgressDTO(V1Model):
    run_spec_id: str
    execution: GatewayExecutionDTO


class GatewayRunScopeSourcePolicyDTO(V1Model):
    input_kind: Literal["topic", "material"]
    allowed_source_types: list[str] = Field(max_length=16)
    allowed_source_roles: list[GatewayScopeSourceRole] = Field(max_length=2)
    has_intake_material: bool

    @field_validator("allowed_source_types")
    @classmethod
    def require_unique_source_types(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("source types must be unique nonblank strings")
        return value

    @field_validator("allowed_source_roles")
    @classmethod
    def require_unique_source_roles(cls, value: list[GatewayScopeSourceRole]) -> list[GatewayScopeSourceRole]:
        if len(value) != len(set(value)):
            raise ValueError("source roles must be unique")
        return value


class GatewayRunScopeDTO(V1Model):
    topic: str = Field(min_length=1, max_length=2000)
    subject_label: str | None = Field(default=None, min_length=1, max_length=512)
    evidence_cutoff_at: datetime
    case_evidence_cutoff_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_policy: GatewayRunScopeSourcePolicyDTO


class GatewayRunDTO(V1Model):
    run_spec_id: str
    native_case_id: str
    native_run_id: str
    status: NativeStatus
    parent_run_spec_id: str | None
    created_at: datetime
    execution: GatewayExecutionDTO | None = None
    scope: GatewayRunScopeDTO | None = None


class GatewayRoleDTO(V1Model):
    run_spec_id: str
    role: RoleKey
    status: RoleStatus
    latest_sequence: int = Field(ge=0)


class ConversationSnapshotDTO(ConversationSummaryDTO):
    event_retention_floor: int = Field(ge=1)
    messages: list[ResearchMessageDTO]
    runs: list[GatewayRunDTO]
    roles: list[GatewayRoleDTO]
    events: list[SafeRoleEventDTO]


ConversationProjectionDTO = ConversationSnapshotDTO
