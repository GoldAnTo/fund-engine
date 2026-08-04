"""Tests for the DomainEvent/Outbox consumer framework (design §9.4) and the
versioned domain model (design §6.1).

Guarantees covered here:
  * ``emit_event`` appends to the outbox inside the caller's transaction.
  * A consumer advances its checkpoint and never re-applies consumed events.
  * A failing consumer does NOT advance past the bad event, so the tail replays.
  * Version chains link successors to predecessors via ``supersedes_id``.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.events.consumers import Consumer
from app.models.events import DomainEvent
from app.models.operational import ProjectionCheckpoint
from app.models.versions import ThesisVersion
from app.repositories.outbox import emit_event


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _emit(session, type_: str = "evidence_link_proposed") -> DomainEvent:
    return emit_event(
        session,
        type=type_,
        aggregate_type="evidence_link",
        aggregate_id=str(uuid.uuid4()),
        payload={"n": 1},
        origin="ledger",
    )


class _RecordingConsumer(Consumer):
    name = "test_recording"

    def __init__(self, session):
        super().__init__(session)
        self.seen: list[uuid.UUID] = []

    def apply(self, event: DomainEvent) -> None:
        self.seen.append(event.id)


class _FailingConsumer(Consumer):
    name = "test_failing"

    def __init__(self, session, *, fail_on: int):
        super().__init__(session)
        self._fail_on = fail_on
        self.seen: list[uuid.UUID] = []

    def apply(self, event: DomainEvent) -> None:
        if len(self.seen) == self._fail_on:
            raise RuntimeError("projection blew up")
        self.seen.append(event.id)


# --------------------------------------------------------------------------- #
# Outbox
# --------------------------------------------------------------------------- #
def test_emit_event_appends_to_outbox(cmd_session):
    event = _emit(cmd_session)
    cmd_session.commit()

    rows = cmd_session.scalars(select(DomainEvent)).all()
    assert len(rows) == 1
    assert rows[0].id == event.id
    assert rows[0].type == "evidence_link_proposed"
    assert rows[0].payload == {"n": 1}


def test_consumer_processes_and_checkpoints(cmd_session):
    for _ in range(3):
        _emit(cmd_session)
    cmd_session.commit()

    consumer = _RecordingConsumer(cmd_session)
    processed = consumer.run()
    cmd_session.commit()

    assert processed == 3
    assert len(consumer.seen) == 3

    checkpoint = cmd_session.get(ProjectionCheckpoint, _RecordingConsumer.name)
    assert checkpoint is not None
    assert checkpoint.watermark is not None
    assert checkpoint.last_error is None


def test_consumer_does_not_reprocess_consumed_events(cmd_session):
    for _ in range(2):
        _emit(cmd_session)
    cmd_session.commit()

    first = _RecordingConsumer(cmd_session)
    assert first.run() == 2
    cmd_session.commit()

    # Second run with no new events must be a no-op.
    second = _RecordingConsumer(cmd_session)
    assert second.run() == 0
    assert second.seen == []
    cmd_session.commit()

    # A newly emitted event is picked up, and only that one.
    fresh = _emit(cmd_session)
    cmd_session.commit()

    third = _RecordingConsumer(cmd_session)
    assert third.run() == 1
    assert third.seen == [fresh.id]


def test_failing_consumer_leaves_tail_for_replay(cmd_session):
    for _ in range(3):
        _emit(cmd_session)
    cmd_session.commit()

    # Fails on the 2nd event (index 1): only the 1st is committed.
    failing = _FailingConsumer(cmd_session, fail_on=1)
    processed = failing.run()
    cmd_session.commit()

    assert processed == 1
    checkpoint = cmd_session.get(ProjectionCheckpoint, _FailingConsumer.name)
    assert checkpoint.last_error is not None

    # After the bug is fixed, the same consumer replays the unprocessed tail
    # (fail_on far beyond the batch => it now succeeds on everything left).
    fixed = _FailingConsumer(cmd_session, fail_on=99)
    assert fixed.run() == 2
    cmd_session.commit()

    checkpoint = cmd_session.get(ProjectionCheckpoint, _FailingConsumer.name)
    assert checkpoint.last_error is None


# --------------------------------------------------------------------------- #
# Versioned domain model
# --------------------------------------------------------------------------- #
def test_thesis_version_chain_links_supersedes(cmd_seeded):
    from app.models.ledger import Thesis

    thesis = cmd_seeded.scalars(select(Thesis)).first()

    v1 = ThesisVersion(
        thesis_id=thesis.id,
        version=1,
        statement="AI capex lifts DC switch demand",
        applicable_from=date(2024, 1, 1),
        created_at=_utcnow(),
    )
    cmd_seeded.add(v1)
    cmd_seeded.flush()

    v2 = ThesisVersion(
        thesis_id=thesis.id,
        version=2,
        statement="AI capex lifts 800G DC switch demand",
        applicable_from=date(2024, 7, 1),
        supersedes_id=v1.id,
        created_at=_utcnow(),
    )
    cmd_seeded.add(v2)
    cmd_seeded.commit()

    # The correction appends a successor — the original is preserved verbatim.
    versions = cmd_seeded.scalars(
        select(ThesisVersion)
        .where(ThesisVersion.thesis_id == thesis.id)
        .order_by(ThesisVersion.version)
    ).all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].supersedes_id is None
    assert versions[1].supersedes_id == versions[0].id
    assert versions[0].statement == "AI capex lifts DC switch demand"


def test_version_head_is_the_unsuperseded_row(cmd_seeded):
    from app.models.ledger import Thesis

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    v1 = ThesisVersion(
        thesis_id=thesis.id, version=1, statement="v1", created_at=_utcnow()
    )
    cmd_seeded.add(v1)
    cmd_seeded.flush()
    v2 = ThesisVersion(
        thesis_id=thesis.id,
        version=2,
        statement="v2",
        supersedes_id=v1.id,
        created_at=_utcnow(),
    )
    cmd_seeded.add(v2)
    cmd_seeded.commit()

    superseded = select(ThesisVersion.supersedes_id).where(
        ThesisVersion.supersedes_id.is_not(None)
    )
    head = cmd_seeded.scalars(
        select(ThesisVersion)
        .where(ThesisVersion.thesis_id == thesis.id)
        .where(ThesisVersion.id.not_in(superseded))
    ).all()
    assert len(head) == 1
    assert head[0].statement == "v2"
