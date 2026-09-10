"""Operational / proposal / activity v1 wire DTOs.

Covers jobs (§8.7), the unified proposal review queue (§8.5 / §5.3), activity
and evidence-changes feeds (§8.7), and the task queue (§8.1 / §8.7).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.v1.common import CursorPage, V1Model


# --------------------------------------------------------------------------- #
# Proposals + review decisions
# --------------------------------------------------------------------------- #
class ProposalItemDTO(V1Model):
    id: str
    kind: str
    payload: dict[str, Any]
    target_context: dict[str, Any]
    proposed_by_type: str
    proposed_by_ref: str
    proposed_at: str
    basis_cutoff: str | None = None
    status: str
    version: int


class ReviewDecisionRequest(V1Model):
    outcome: Literal["confirmed", "modified", "rejected"]
    reason: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    reviewer_id: str = Field(min_length=1)
    replacement_payload: dict[str, Any] | None = None


class ReviewDecisionDTO(V1Model):
    id: str
    proposal_id: str
    outcome: str
    reason: str
    reviewer_id: str
    expected_proposal_version: int
    decided_at: str
    # Set when a formal entity was published (e.g. evidence_link_version id).
    published_entity_id: str | None = None


class ReviewQueueResponse(V1Model):
    items: list[Any]
    next_cursor: str | None = None
    has_more: bool = False


class ClaimResponse(V1Model):
    proposal_id: str
    assignee: str
    claimed_at: str
    lease_expires_at: str | None = None


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
class JobDTO(V1Model):
    id: str
    kind: str
    status: str
    progress: int
    attempt: int
    step: str | None = None
    error: str | None = None
    cancel_requested: bool
    target_type: str | None = None
    target_id: str | None = None
    research_case_id: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class JobEventDTO(V1Model):
    seq: int
    status: str | None = None
    step: str | None = None
    progress: int | None = None
    message: str | None = None
    created_at: str


class JobEventsResponse(V1Model):
    job_id: str
    events: list[JobEventDTO]
    next_cursor: str | None = None
    has_more: bool = False


# --------------------------------------------------------------------------- #
# Activity + evidence changes
# --------------------------------------------------------------------------- #
class ActivityItemDTO(V1Model):
    event_id: str
    type: str
    aggregate_type: str
    aggregate_id: str
    ref_type: str | None = None
    ref_id: str | None = None
    origin: str
    payload: dict[str, Any]
    actor: str | None = None
    created_at: str


class ActivityResponse(V1Model):
    items: list[ActivityItemDTO]
    next_cursor: str | None = None
    has_more: bool = False


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
class TaskCreateRequest(V1Model):
    title: str
    description: str | None = None
    task_type: str = "counter_research"
    priority: str = "normal"
    ref_type: str | None = None
    ref_id: str | None = None
    research_case_id: str | None = None
    assignee: str | None = None


class TaskUpdateRequest(V1Model):
    status: Literal["open", "in_progress", "done", "cancelled"]
    assignee: str | None = None


class TaskItemDTO(V1Model):
    id: str
    title: str
    description: str | None = None
    status: str
    priority: str
    task_type: str
    ref_type: str | None = None
    ref_id: str | None = None
    research_case_id: str | None = None
    assignee: str | None = None
    created_at: str
    due_at: str | None = None


class TasksResponse(V1Model):
    items: list[TaskItemDTO]
    next_cursor: str | None = None
    has_more: bool = False
