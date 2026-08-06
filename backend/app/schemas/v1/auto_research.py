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
    created_at: str
    updated_at: str
    next_action: str


class RunListResponse(CursorPage):
    items: list[RunSummaryDTO]


class CancelRunResponse(RunSummaryDTO):
    pass


class ResearchRunEventsItemDTO(V1Model):
    seq: int
    status: str | None = None
    stage: str | None = None
    round: int | None = None
    stop_reason: str | None = None
    message: str | None = None
    created_at: str


class ResearchRunEventsResponse(V1Model):
    run_id: str
    items: list[ResearchRunEventsItemDTO]
    next_cursor: str | None = None
    has_more: bool = False

class StartResearchRunRequest(V1Model):
    max_rounds: int = Field(default=3, ge=1, le=3)
    budget: int = Field(default=100, ge=1)
    auto_execute: bool = True

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
    progress: dict[str, int]
    evidence: dict[str, int]
    by_thesis: dict[str, dict[str, int]]
    gaps: list[str]
    gap_tasks: list[ResearchTaskDTO]
    failed_tasks: list[ResearchTaskDTO]
    assessments: list[dict[str, Any] | None]
    pending_proposals: list[PendingProposalDTO]
    review_tasks: list[ReviewTaskDTO]
    next_action: str
    tasks: list[ResearchTaskDTO]
