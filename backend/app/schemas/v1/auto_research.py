from __future__ import annotations
from typing import Any
from pydantic import Field
from app.schemas.v1.common import CursorPage, V1Model


class RunSummaryDTO(V1Model):
    id: str
    status: str
    stage: str
    round: int
    max_rounds: int
    budget: int
    budget_used: int
    stop_reason: str | None
    scope_thesis_ids: list[str]
    created_at: str
    updated_at: str
    next_action: str


class RunListResponse(CursorPage):
    items: list[RunSummaryDTO]


class FrozenRunScopeDTO(V1Model):
    """Scope recorded when a run started; never reconstructed from current settings."""

    trigger: str | None = None
    monitor_version_id: str | None = None
    factor_ids: list[str] = Field(default_factory=list)
    factor_statements: list[str] = Field(default_factory=list)
    allowed_source_types: list[str] = Field(default_factory=list)
    budget: int | None = None
    frequency: str | None = None
    next_verification_event: str | None = None
    configured_by: str | None = None
    configuration_change_reason: str | None = None


class ActiveResearchRunDTO(V1Model):
    run_id: str
    case_id: str
    case_title: str
    status: str
    stage: str
    updated_at: str
    processed_count: int
    next_action: str
    scope: FrozenRunScopeDTO


class ActiveResearchRunsResponse(CursorPage):
    items: list[ActiveResearchRunDTO]


class ResearchWorkerStatusDTO(V1Model):
    """Current liveness of the process that advances queued research work."""

    status: str
    last_seen_at: str | None = None
    mode: str | None = None
    state: str | None = None


class ResearchRunArchiveDTO(ActiveResearchRunDTO):
    """A global, replayable run record, including terminal runs."""

    created_at: str
    stop_reason: str | None = None


class ResearchRunArchiveResponse(CursorPage):
    items: list[ResearchRunArchiveDTO]


class CancelRunResponse(RunSummaryDTO):
    pass


class CancelRunRequest(V1Model):
    """Human decision recorded when an in-progress run is stopped."""

    actor: str = Field(min_length=1, max_length=200)
    change_reason: str = Field(min_length=1, max_length=2_000)


class ResearchRunEventsItemDTO(V1Model):
    seq: int
    status: str | None = None
    stage: str | None = None
    round: int | None = None
    stop_reason: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ResearchRunEventsResponse(V1Model):
    run_id: str
    items: list[ResearchRunEventsItemDTO]
    next_cursor: str | None = None
    has_more: bool = False

class StartResearchRunRequest(V1Model):
    max_rounds: int = Field(default=3, ge=1, le=3)
    budget: int = Field(default=100, ge=1)
    # Retained only for clients during rollout.  The API always queues work
    # for the durable worker and never executes in the request process.
    auto_execute: bool = False

class ResearchTaskDTO(V1Model):
    id: str
    thesis_id: str | None
    status: str
    stage: str
    round: int
    task_type: str
    query: str
    evidence_count: int
    gap_reason: str | None
    result: dict[str, Any] | None

class PendingProposalDTO(V1Model):
    id: str
    thesis_id: str | None
    task_id: str | None
    status: str

class ReviewTaskDTO(V1Model):
    id: str
    status: str
    task_type: str
    ref_type: str | None
    ref_id: str | None


class PendingAssessmentDTO(V1Model):
    assessment_id: str
    conclusion: str
    rationale: str
    gaps: list[str]
    task_id: str
    task_status: str


class ResearchRunResponse(V1Model):
    id: str
    case_id: str
    status: str
    stage: str
    round: int
    max_rounds: int
    budget: int
    budget_used: int
    stop_reason: str | None
    scope_thesis_ids: list[str]
    progress: dict[str, int]
    evidence: dict[str, int]
    by_thesis: dict[str, dict[str, int]]
    gaps: list[str]
    gap_tasks: list[ResearchTaskDTO]
    failed_tasks: list[ResearchTaskDTO]
    assessments: list[dict[str, Any] | None]
    pending_assessments: list[PendingAssessmentDTO]
    pending_proposals: list[PendingProposalDTO]
    review_tasks: list[ReviewTaskDTO]
    next_action: str
    tasks: list[ResearchTaskDTO]
