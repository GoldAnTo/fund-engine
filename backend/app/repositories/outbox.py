"""Outbox append helper.

Emits a ``DomainEvent`` in the calling transaction.  Consumers poll
``domain_events`` and are idempotent on event id, so re-emitting after a crash
is safe (design §9.4).  The event carries enough payload for projections to
rebuild without re-reading the full aggregate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.events import DomainEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def emit_event(
    session: Session,
    *,
    type: str,
    aggregate_type: str,
    aggregate_id: str | uuid.UUID,
    payload: dict[str, Any],
    origin: str = "ledger",
    ref_type: str | None = None,
    ref_id: str | uuid.UUID | None = None,
    actor: str | None = None,
    correlation_id: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        type=type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        ref_type=ref_type,
        ref_id=None if ref_id is None else str(ref_id),
        origin=origin,
        payload=payload,
        actor=actor,
        correlation_id=correlation_id,
        created_at=_utcnow(),
    )
    session.add(event)
    session.flush()
    return event
