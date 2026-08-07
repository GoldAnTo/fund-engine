"""Persistence helpers for the mutable event-research lifecycle projection."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.operational import EventResearchLifecycle, TaskItem


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventResearchLifecycleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, case_id: uuid.UUID) -> EventResearchLifecycle | None:
        return self._session.get(EventResearchLifecycle, case_id)

    def pending_key_review_count(self, case_id: uuid.UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(TaskItem)
                .where(TaskItem.research_case_id == case_id)
                .where(TaskItem.task_type == "review_proposal")
                .where(TaskItem.status.in_(("open", "in_progress")))
            )
            or 0
        )

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
