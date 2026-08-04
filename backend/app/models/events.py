"""Domain events / outbox.

Every meaningful ledger or operational transition appends a ``DomainEvent`` in
the *same* PostgreSQL transaction as the change (design §9.4).  A background
poller drains the outbox and fans events out to rebuildable projections
(Neo4j graph, PostgreSQL FTS search, activity feed).  Consumers track their
high-watermark in ``projection_checkpoints`` and are idempotent on event id, so
the outbox can be replayed safely after a crash.

First version uses database polling (no Kafka/Debezium yet — that is a P2
swap-in behind this same seam).  ``rebuild_all()`` is always available for
disaster recovery and consistency checks.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


def _next_domain_event_seq(context) -> Any:
    """Dialect-aware default for ``DomainEvent.seq``.

    PostgreSQL: pull the next value from the real sequence created in
    migration 0010.  ``nextval`` is atomic, so concurrent writers never collide.

    SQLite (tests only) has no sequences, so fall back to ``max(seq) + 1``.
    That read-modify-write is safe here because test sessions are
    single-writer; it is never used in production.
    """
    if context.engine.dialect.name == "sqlite":
        current = context.connection.exec_driver_sql(
            "SELECT COALESCE(MAX(seq), 0) FROM domain_events"
        ).scalar()
        return int(current or 0) + 1
    return context.connection.exec_driver_sql(
        "SELECT nextval('domain_events_seq_seq')"
    ).scalar()


# Event types are namespaced by aggregate.  Consumers filter on ``type``.
DomainEventType = Literal[
    # ledger facts
    "document_version_frozen",
    "source_statement_extracted",
    "evidence_link_proposed",
    "evidence_link_published",
    "evidence_link_rejected",
    "causal_edge_published",
    "thesis_version_appended",
    "research_case_version_appended",
    "ai_assessment_frozen",
    "review_decision_recorded",
    # operational transitions
    "job_created",
    "job_progressed",
    "job_finished",
    "proposal_created",
    "proposal_decided",
    "review_assignment_claimed",
    "task_item_created",
    "task_item_updated",
]


class DomainEvent(Base):
    """An append-only outbox row emitted alongside every business transition.

    Never updated or deleted.  ``producer_txn_id`` lets a consumer correlate
    all events written in one business transaction (e.g. one AI propose run
    emits one event per proposed link plus a summary event).
    """

    __tablename__ = "domain_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    # Strictly monotonic ordering key assigned by the database.  Consumers
    # resume from ``seq > watermark``.
    #
    # This MUST NOT be ``id``: ``id`` is a random UUIDv4, so ``id > last_seen``
    # is unrelated to insertion order and would silently skip events forever.
    # ``created_at`` alone is also unsafe (clock skew + equal timestamps within
    # one transaction), so a DB-assigned sequence is the only correct cursor.
    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
        unique=True,
        index=True,
        default=_next_domain_event_seq,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # The aggregate the event is about (entity type, e.g. "evidence_link").
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # The aggregate id (UUID as string).
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Optional secondary reference (e.g. proposal_id for a published link).
    ref_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "ledger" | "operational"
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="ledger")
    # Full payload for projections (content-addressed downstream as needed).
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Actor that caused the event (human:alice / ai:openai/gpt-4).
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Correlation id for cross-system tracing.
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
