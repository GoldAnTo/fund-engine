"""Persistence helpers for the mutable event-research lifecycle projection."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operational import EventResearchLifecycle, TaskItem
from app.models.proposals import Proposal
from app.queries.review_queue import proposal_evidence_context
from app.services.event_research_scope_evidence import current_scope_thesis_ids


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventResearchLifecycleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, case_id: uuid.UUID) -> EventResearchLifecycle | None:
        return self._session.get(EventResearchLifecycle, case_id)

    def pending_key_review_count(self, case_id: uuid.UUID) -> int:
        active_thesis_ids = current_scope_thesis_ids(self._session, case_id)
        tasks = self._session.scalars(
            select(TaskItem)
            .where(TaskItem.research_case_id == case_id)
            .where(TaskItem.task_type == "review_proposal")
            .where(TaskItem.status.in_(("open", "in_progress")))
        )
        count = 0
        for task in tasks:
            # Keep legacy unlinked task rows visible until their producer is
            # migrated.  New event review tasks always refer to a Proposal.
            if task.ref_type != "proposal" or task.ref_id is None:
                count += 1
                continue
            proposal = self._session.get(Proposal, task.ref_id)
            proposal_thesis_id = _proposal_thesis_id(proposal)
            if (
                proposal is not None
                and proposal.kind == "evidence_link"
                and proposal.status == "pending"
                and (
                    active_thesis_ids is None
                    or proposal_thesis_id in active_thesis_ids
                )
                and proposal_evidence_context(self._session, proposal).admission.can_accept
            ):
                count += 1
        return count
    def update(
        self,
        lifecycle: EventResearchLifecycle,
        *,
        status: str,
        summary: str,
        active_run_id: uuid.UUID | None = None,
        current_round: int | None = None,
        current_gap: str | None = None,
        next_human_action: str | None = None,
    ) -> EventResearchLifecycle:
        lifecycle.status = status
        lifecycle.status_summary = summary
        lifecycle.active_run_id = active_run_id
        if current_round is not None:
            lifecycle.current_round = current_round
        lifecycle.current_gap = current_gap
        lifecycle.next_human_action = next_human_action
        lifecycle.updated_at = _utcnow()
        self._session.flush()
        return lifecycle


def _proposal_thesis_id(proposal: Proposal | None) -> uuid.UUID | None:
    if proposal is None or not isinstance(proposal.target_context, dict):
        return None
    try:
        return uuid.UUID(str(proposal.target_context.get("thesis_id")))
    except (TypeError, ValueError):
        return None
