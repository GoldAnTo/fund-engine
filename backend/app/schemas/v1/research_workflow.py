"""Wire contracts for event-research workflow commands."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.domain.acquisition import WorkflowLedgerStatus
from app.domain.research_workflow import as_utc
from app.schemas.v1.common import V1Model


class WorkflowV1Model(V1Model):
    """Workflow wire values always serialize persisted datetimes as UTC."""

    @field_validator("*", mode="before")
    @classmethod
    def normalize_datetime(cls, value):
        return as_utc(value) if isinstance(value, datetime) else value


WorkflowState = Literal[
    "intake",
    "awaiting_scope_confirmation",
    "planning_acquisition",
    "acquiring",
    "freezing_sources",
    "assessing_coverage",
    "synthesizing_evidence",
    "adjudicating_thesis",
    "generating_report",
    "monitoring",
    "retry_wait",
    "recovering",
    "needs_scope_decision",
    "exhausted",
    "cancelled",
    "failed",
]
WorkflowUserStage = Literal[
    "intake",
    "scope_confirmation",
    "acquisition",
    "evidence_synthesis",
    "thesis_adjudication",
    "report_monitoring",
]


class ConfirmResearchWorkflowRequest(WorkflowV1Model):
    scope_version_id: uuid.UUID
    scope_version: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=512)

    @field_validator("idempotency_key")
    @classmethod
    def validate_nonblank_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("idempotency_key must not be blank")
        return value


class ResumeProtocolWorkflowRequest(ConfirmResearchWorkflowRequest):
    """Resume input intentionally carries no caller-supplied identity."""


class DecideResearchWorkflowRequest(WorkflowV1Model):
    """A business decision only; identity always comes from the session."""

    kind: Literal["keep_scope", "stop"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=512)

    @field_validator("reason", "idempotency_key")
    @classmethod
    def validate_nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class WorkflowNextActionDTO(WorkflowV1Model):
    kind: str
    label: str
    payload: dict[str, object]


class WorkflowSafeActionDTO(WorkflowV1Model):
    kind: str
    label: str
    method: Literal["GET", "POST", "PUT"]
    href: str
    payload: dict[str, object]


class WorkflowNotInitializedDetailsDTO(WorkflowV1Model):
    safe_action: WorkflowSafeActionDTO


class WorkflowNotInitializedErrorBodyDTO(WorkflowV1Model):
    code: Literal["workflow_not_initialized"]
    message: str
    request_id: str
    details: WorkflowNotInitializedDetailsDTO


class WorkflowNotInitializedEnvelopeDTO(WorkflowV1Model):
    schema_version: Literal["v1"] = "v1"
    error: WorkflowNotInitializedErrorBodyDTO


class ConfirmResearchWorkflowResponse(WorkflowV1Model):
    orchestration_id: uuid.UUID
    case_id: uuid.UUID
    scope_version_id: uuid.UUID
    research_run_id: uuid.UUID | None
    state: WorkflowState
    user_stage: WorkflowUserStage
    current_system_action: str | None
    system_action_reason: str | None
    next_action: WorkflowNextActionDTO | None
    version: int


WorkflowStageStatus = Literal[
    "pending",
    "active",
    "completed",
    "failed",
    "recovering",
    "blocked",
    "cancelled",
]
WorkflowStageCode = Literal[
    "event_intake",
    "scope_confirmation",
    "source_acquisition",
    "evidence_synthesis",
    "thesis_adjudication",
    "report_monitoring",
]


class WorkflowEventIdentityDTO(WorkflowV1Model):
    case_id: uuid.UUID
    title: str
    research_question: str


class WorkflowScopeFactorDTO(WorkflowV1Model):
    statement: str
    description: str | None
    position: int


class WorkflowScopeDTO(WorkflowV1Model):
    id: uuid.UUID
    version: int
    factors: list[WorkflowScopeFactorDTO]


class WorkflowStageDTO(WorkflowV1Model):
    code: WorkflowStageCode
    display_name: str
    status: WorkflowStageStatus
    reason: str


class WorkflowSystemActionDTO(WorkflowV1Model):
    label: str | None
    reason: str | None
    started_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    retry_at: datetime | None
    recovery_status: str | None


class WorkflowUserActionDTO(WorkflowV1Model):
    kind: str
    label: str
    reason: str
    recommendation: dict[str, object]
    alternatives: list[dict[str, object]]
    impact: str
    payload: dict[str, object]


class WorkflowEventDTO(WorkflowV1Model):
    id: uuid.UUID
    sequence: int
    transition: str
    actor: str
    message: str
    payload: dict[str, object]
    created_at: datetime


class WorkflowAcquisitionRoundDTO(WorkflowV1Model):
    series_id: uuid.UUID
    query_plan_id: uuid.UUID
    goal_id: str
    round: int
    job_id: uuid.UUID
    status: str
    stage: str
    attempt: int
    retry_at: datetime | None
    lease_expires_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_detail: str | None
    recovery_status: str | None
    planner_version: str
    policy_version: str
    frozen_inputs: dict[str, object]
    ordered_query_count: int
    expansion_trigger: str | None


class WorkflowAcquisitionDTO(WorkflowV1Model):
    series_count: int
    rounds: list[WorkflowAcquisitionRoundDTO]


class WorkflowResearchExecutionDTO(WorkflowV1Model):
    job_id: uuid.UUID
    status: str
    step: str | None
    attempt: int
    failure_count: int
    started_at: datetime | None
    finished_at: datetime | None
    recovery_count: int
    last_recovered_at: datetime | None


class WorkflowSourceLedgerCountsDTO(WorkflowV1Model):
    total: int
    reviewed: int
    automatically_admitted: int
    deduplicated: int
    quarantined: int
    by_status: dict[WorkflowLedgerStatus, int]


WorkflowSourceLedgerStatus = WorkflowLedgerStatus


class WorkflowSourceLedgerDrilldownDTO(WorkflowV1Model):
    kind: Literal[
        "acquisition_job",
        "acquisition_events",
        "acquisition_evidence",
        "acquisition_exceptions",
    ]
    href: str


class WorkflowSourceLedgerItemDTO(WorkflowV1Model):
    record_id: uuid.UUID
    record_type: str
    status: WorkflowSourceLedgerStatus
    reason: str
    reason_code: str
    recorded_at: datetime
    evidence_link_id: uuid.UUID | None
    review_state: str | None
    role: str | None
    source_role: str | None
    mapping_disposition: str | None
    mapping_kind: str | None
    adapter_key: str | None
    attempt_id: uuid.UUID | None
    source_url: str | None
    final_url: str | None
    retrieved_at: datetime | None
    content_sha256: str | None
    publication_key: str | None
    dedup_relation: str | None
    admission_outcome: str | None
    drilldown: WorkflowSourceLedgerDrilldownDTO | None
    drilldown_unavailable_reason: str | None


class WorkflowSourceLedgerDTO(WorkflowV1Model):
    counts: WorkflowSourceLedgerCountsDTO
    items: list[WorkflowSourceLedgerItemDTO]
    total: int
    has_more: bool


class WorkflowLedgerPageDTO(WorkflowV1Model):
    items: list[WorkflowSourceLedgerItemDTO]
    total: int
    has_more: bool
    next_cursor: str | None


class WorkflowCoverageDTO(WorkflowV1Model):
    goal_id: str
    thesis_id: uuid.UUID | None
    objective: str
    status: str
    reason_codes: list[str]
    required_authority_count: int
    observed_authority_count: int
    required_independent_source_count: int
    observed_independent_source_count: int
    contrary_search_completed: bool
    evidence_link_ids: list[str]
    unresolved: list[object]
    unknown: list[object]
    evaluation_round: int


class WorkflowConclusionDTO(WorkflowV1Model):
    id: uuid.UUID
    state: str
    text: str
    primary_factor: str | None
    evidence_link_ids: list[str]
    citations: list[WorkflowSourceLedgerItemDTO]
    reviewer: str | None
    system_generated: bool
    human_reviewed: bool
    review_label: str
    created_at: datetime


class WorkflowMonitorDTO(WorkflowV1Model):
    id: uuid.UUID
    version: int
    status: str
    frequency: str
    factor_ids: list[str]
    source_types: list[str]
    next_verification_event: str
    created_at: datetime


class WorkflowRecoveryDTO(WorkflowV1Model):
    status: str | None
    reason: str | None
    source: Literal[
        "orchestration",
        "research_job",
        "acquisition_job",
        "worker",
        "checkpoint",
    ] | None
    attempt: int | None
    evaluated_at: datetime
    orchestration_heartbeat_at: datetime | None
    worker_heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    retry_at: datetime | None
    failed_at: datetime | None
    last_checkpoint: dict[str, object]
    decision_diagnostic: dict[str, object] | None


class ResearchWorkflowResponse(WorkflowV1Model):
    orchestration_id: uuid.UUID
    research_run_id: uuid.UUID | None
    research_execution: WorkflowResearchExecutionDTO | None
    event: WorkflowEventIdentityDTO
    scope: WorkflowScopeDTO
    state: WorkflowState
    stages: list[WorkflowStageDTO]
    system_action: WorkflowSystemActionDTO
    user_action: WorkflowUserActionDTO | None
    user_action_summary: str
    events: list[WorkflowEventDTO]
    acquisition: WorkflowAcquisitionDTO
    source_ledger: WorkflowSourceLedgerDTO
    coverage: list[WorkflowCoverageDTO]
    conclusion: WorkflowConclusionDTO | None
    monitor: WorkflowMonitorDTO | None
    recovery: WorkflowRecoveryDTO
    version: int
    updated_at: datetime


class WorkflowEventsPageDTO(WorkflowV1Model):
    items: list[WorkflowEventDTO]
    next_cursor: int | None
    has_more: bool
    cursor_direction: Literal["after"] = "after"
