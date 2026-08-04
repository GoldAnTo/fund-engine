"""Operational store tables.

These tables are *not* part of the immutable evidence ledger.  They hold
operational state — AI job progress, review claim leases, the home-page task
queue, idempotency dedup, and projection consumer watermarks — that is allowed
to change over the lifetime of a running system.  Unlike the ledger, mutations
here are normal UPDATEs guarded by application logic (optimistic version for
concurrency), not append-only history.

Every meaningful operational transition ALSO emits a ``DomainEvent`` (see
``app/models/events.py``) so that user-facing activity feeds and projections
are reconstructed from an append-only log rather than scraped from mutable
rows.  The event is written in the same transaction as the operational change.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
JobStatus = Literal["queued", "running", "waiting_for_review", "succeeded", "failed", "cancelled"]
JobKind = Literal["ingest", "extract", "propose", "assess", "project", "parse"]


class Job(Base):
    """A recoverable operational job for an AI/ingest pipeline step.

    First version uses a PostgreSQL job table + worker (design §5.7 / §8.7).
    Long-pause / multi-provider / frequent-resume needs can later hide behind
    this same seam behind Temporal without changing callers.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # Logical progress 0..100; informational only, not a correctness signal.
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 1-based attempt counter; incremented on retry.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Human-readable step label (e.g. "recalling statements", "generating").
    step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Free-form error detail when status == failed.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft cancel: the worker polls this and stops at the next safe boundary.
    cancel_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    # The ledger entity this job ultimately mutates (e.g. thesis_id for an
    # assess job), used to resume and to build task items.
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # The case this job belongs to (for filtering task queues / activity).
    research_case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=True
    )
    # Optional reference back to the AI run that produced/consumed this job.
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Arbitrary caller-supplied correlation id (trace parent).
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def touch_created(self) -> None:
        if self.created_at is None:  # pragma: no cover - set by caller
            from datetime import datetime as _dt, timezone as _tz

            self.created_at = _dt.now(_tz.utc)


class JobEvent(Base):
    """Append-only progress history for a job, replayable for SSE/cursor feeds.

    Written by workers as they advance; never updated or deleted.  This is the
    durable record that ``GET /jobs/{id}/events`` and any SSE stream replay from.
    """

    __tablename__ = "job_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id"), nullable=False
    )
    # An increasing sequence number so consumers can resume from a cursor.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# --------------------------------------------------------------------------- #
# Review assignments (claim leases)
# --------------------------------------------------------------------------- #
class ReviewAssignment(Base):
    """A reviewer's claim lease on a proposal awaiting human decision.

    Claims are optional (design §8.5).  A lease has a ``lease_expires_at``;
    expired leases are ignored when resolving who currently owns a proposal, so
    a reviewer who walked away does not block others indefinitely.
    """

    __tablename__ = "review_assignments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("proposals.id"), nullable=False
    )
    assignee: Mapped[str] = mapped_column(String(128), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # NULL lease = held until released.  Otherwise an absolute expiry; the
    # query layer treats a passed expiry as "no longer claimed".
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optimistic-concurrency version bumped on every state change.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# --------------------------------------------------------------------------- #
# Task items (home-page task queue)
# --------------------------------------------------------------------------- #
TaskStatus = Literal["open", "in_progress", "done", "cancelled"]
TaskPriority = Literal["low", "normal", "high", "urgent"]


class TaskItem(Base):
    """A home-page task queue entry derived from operational state.

    Tasks are not themselves ledger facts; they summarize work that needs a
    human (review a proposal, verify a claim, inspect a failed job).  They are
    re-derived from proposals / jobs and are safe to insert/update.
    """

    __tablename__ = "task_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    # The kind of work: "review_proposal" | "verify_claim" | "inspect_job" etc.
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # The entity the task is about.
    ref_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    research_case_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=True
    )
    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# --------------------------------------------------------------------------- #
# Idempotency keys
# --------------------------------------------------------------------------- #
class IdempotencyKey(Base):
    """Dedup + response cache for write requests carrying Idempotency-Key.

    On the first request with a key, the row is inserted (status=in_progress).
    If the same key is seen again:
      * while in_progress and not expired → 409 idempotency_conflict
      * after success → replay the stored response (status=completed)
      * after failure → allow retry (delete the failed row)
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress"
    )
    # Stored response envelope for replay (dict serialized as JSON column).
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The status code to replay (so a 201 replays as 201, not 200).
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The idempotency row is only valid for the same request fingerprint.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Rows older than this are eligible for garbage collection.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------- #
# Projection checkpoints
# --------------------------------------------------------------------------- #
class ProjectionCheckpoint(Base):
    """Consumer watermark for a rebuildable projection (Neo4j / search / activity).

    Design §9.4: consumers track the last processed ``DomainEvent`` id and the
    schema version of the projection they built, so incremental rebuilds resume
    from the watermark instead of scanning the whole outbox.
    """

    __tablename__ = "projection_checkpoints"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Opaque high-watermark (e.g. last processed domain event id, or cursor).
    watermark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projection_schema_version: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
