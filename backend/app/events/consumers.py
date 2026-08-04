"""Outbox polling consumer framework (design §9.4).

A ``Consumer`` reads unconsumed ``DomainEvent`` rows from a watermark stored in
``projection_checkpoints`` and applies them idempotently to a projection.  The
first version polls the database; swapping to Debezium/Kafka later keeps the
same ``apply(event)`` contract and checkpoint semantics.

Consumers are expected to be idempotent on ``event.id`` — reprocessing the
same event after a crash must not double-apply.  The checkpoint stores only
the last *processed* event id, so a crash mid-batch re-runs the tail, which is
safe because apply() is idempotent.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.events import DomainEvent
from app.models.operational import ProjectionCheckpoint
from app.repositories.operational import CheckpointRepository


class Consumer:
    """Base class for an idempotent outbox consumer.

    Subclasses implement ``apply(event)``.  ``run()`` loads events strictly
    after the stored watermark, applies them in order, and advances the
    checkpoint once per batch (so an exception before commit leaves the
    watermark unchanged and the tail replays).
    """

    #: consumer name stored in projection_checkpoints.
    name: str = "base"
    schema_version: str = "v1"

    def __init__(self, session: Session) -> None:
        self._session = session
        self._checkpoints = CheckpointRepository(session)

    def _watermark_seq(self) -> int | None:
        """Last processed ``DomainEvent.seq``, or None to start from the top."""
        cp = self._session.get(ProjectionCheckpoint, self.name)
        if cp is None or cp.watermark is None:
            return None
        try:
            return int(cp.watermark)
        except (TypeError, ValueError):
            return None

    def _unconsumed(self, after_seq: int | None, limit: int) -> list[DomainEvent]:
        # Page on the DB-assigned monotonic ``seq``.  Ordering/filtering on the
        # random UUID ``id`` would skip events non-deterministically, and
        # ``created_at`` is not unique within a transaction.
        query = select(DomainEvent).order_by(DomainEvent.seq)
        if after_seq is not None:
            query = query.where(DomainEvent.seq > after_seq)
        return list(self._session.scalars(query.limit(limit)))

    def run(self, *, limit: int = 500) -> int:
        after = self._watermark_seq()
        events = self._unconsumed(after, limit)
        last_seq: int | None = after
        processed = 0
        last_error: str | None = None
        for event in events:
            try:
                self.apply(event)
            except Exception as exc:  # noqa: BLE001 - record and stop the batch
                last_error = f"{event.id}: {exc}"[:500]
                # Stop on the first failure so the watermark stays behind the
                # bad event and the whole tail replays once the bug is fixed.
                break
            last_seq = event.seq
            processed += 1

        if last_seq is not None or last_error is not None:
            self._checkpoints.upsert(
                consumer=self.name,
                watermark=None if last_seq is None else str(last_seq),
                projection_schema_version=self.schema_version,
                last_error=last_error,
            )
        return processed

    def apply(self, event: DomainEvent) -> None:  # pragma: no cover - overridden
        raise NotImplementedError
