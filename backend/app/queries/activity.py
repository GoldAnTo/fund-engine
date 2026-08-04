"""Activity feed + evidence-changes read models (design §8.7).

Both feeds are derived from the ``domain_events`` outbox, which makes them
rebuildable and consistent with the ledger (design §9.4).  For the first
version we read the outbox directly — this is itself a valid projection and
avoids a second materialized table; a later consumer can cache it behind
``projection_checkpoints`` if volume demands.

Every returned item carries ``event_id`` so clients can page with a stable
cursor (after=event_id) and resume exactly where they left off.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.events import DomainEvent

# Event types that describe evidence changing in a case (feed #2).
_EVIDENCE_EVENT_TYPES = frozenset(
    {
        "source_statement_extracted",
        "evidence_link_proposed",
        "evidence_link_published",
        "evidence_link_rejected",
        "causal_edge_published",
        "ai_assessment_frozen",
        "review_decision_recorded",
        "proposal_decided",
    }
)


class ActivityQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _page(
        self,
        *,
        event_types: set[str] | None,
        case_id: uuid.UUID | None,
        actor_id: str | None,
        after_id: uuid.UUID | None,
        limit: int,
    ) -> list[DomainEvent]:
        query = select(DomainEvent).order_by(
            DomainEvent.created_at.desc(), DomainEvent.id.desc()
        )
        if event_types is not None:
            query = query.where(DomainEvent.type.in_(event_types))
        if case_id is not None:
            query = query.where(DomainEvent.aggregate_id == str(case_id))
        if actor_id is not None:
            query = query.where(DomainEvent.actor == actor_id)
        if after_id is not None:
            query = query.where(DomainEvent.id < after_id)
        return list(self._db.scalars(query.limit(limit + 1)))

    def activity(
        self,
        *,
        case_id: uuid.UUID | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        after_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[DomainEvent], bool]:
        types = {event_type} if event_type else None
        rows = self._page(
            event_types=types,
            case_id=case_id,
            actor_id=actor_id,
            after_id=after_id,
            limit=limit,
        )
        has_more = len(rows) > limit
        return rows[:limit], has_more

    def evidence_changes(
        self,
        *,
        case_id: uuid.UUID | None = None,
        after_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[DomainEvent], bool]:
        rows = self._page(
            event_types=set(_EVIDENCE_EVENT_TYPES),
            case_id=case_id,
            actor_id=None,
            after_id=after_id,
            limit=limit,
        )
        has_more = len(rows) > limit
        return rows[:limit], has_more
