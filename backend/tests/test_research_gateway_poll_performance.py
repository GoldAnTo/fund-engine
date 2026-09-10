"""Bound idle replay SQL without caching ownership, lineage or display rights.

Set GATEWAY_AUDIT_HISTORY_SIZE=4161 to reproduce the large-history measurement.
All records live in cmd_session's disposable SQLite database; no provider runs.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Barrier
from time import perf_counter

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.api.v1.tenant_context import ResearchActor
from app.models.research_gateway import ResearchConversation, RoleEvent
from app.models.research_monitor import ResearchRunEvent
from app.services.research_gateway import ResearchGateway
from app.services.research_gateway_stream import replay
from tests.test_research_gateway_automatic_adapter import seed_spec

ACTOR = ResearchActor("team-a", frozenset(), "alice")


@contextmanager
def measured_sql(engine):
    measurement = {"statements": 0}

    def count(*_args):
        measurement["statements"] += 1

    event.listen(engine, "before_cursor_execute", count)
    start = perf_counter()
    try:
        yield measurement
    finally:
        measurement["seconds"] = round(perf_counter() - start, 6)
        event.remove(engine, "before_cursor_execute", count)


def add_native_events(session, run_id, count):
    first = session.scalar(select(func.max(ResearchRunEvent.seq)).where(
        ResearchRunEvent.run_id == run_id)) or 0
    session.add_all(ResearchRunEvent(
        run_id=run_id, seq=first + index + 1, stage="retrieve", status="running",
        message="PRIVATE native trace must never leave the projection",
        payload_json={"private": "provider trace"}, created_at=datetime.now(UTC),
    ) for index in range(count))
    session.commit()


def test_idle_replay_sql_does_not_grow_per_historical_native_event(cmd_session):
    spec, run = seed_spec(cmd_session)
    conversation_id, run_id = spec.conversation_id, run.id
    history_size = int(os.environ.get("GATEWAY_AUDIT_HISTORY_SIZE", "64"))
    add_native_events(cmd_session, run_id, history_size)
    factory = sessionmaker(bind=cmd_session.get_bind())
    with measured_sql(cmd_session.get_bind()) as catch_up, factory() as reader:
        initial = ResearchGateway(reader).read_conversation(ACTOR, conversation_id)
    with measured_sql(cmd_session.get_bind()) as snapshot, factory() as reader:
        again = ResearchGateway(reader).read_conversation(ACTOR, conversation_id)
    with measured_sql(cmd_session.get_bind()) as idle:
        caught_up = replay(session_factory=factory, actor=ACTOR,
            conversation_id=conversation_id, after_sequence=initial.latest_sequence)
    print(json.dumps({"native_history_added": history_size, "role_events": len(initial.events),
                      "catch_up": catch_up, "snapshot": snapshot, "idle": idle}, sort_keys=True))
    assert initial.events == again.events
    assert caught_up.events == ()
    assert caught_up.latest_sequence == initial.latest_sequence
    assert "PRIVATE" not in again.model_dump_json()
    assert idle["statements"] < 100, "unchanged history must not reopen a write savepoint per event"
    assert snapshot["statements"] < 100

    add_native_events(cmd_session, run_id, 1)
    with measured_sql(cmd_session.get_bind()) as incremental:
        newer = replay(session_factory=factory, actor=ACTOR,
            conversation_id=conversation_id, after_sequence=initial.latest_sequence)
    assert len(newer.events) == 5  # one native event plus the four current-state facts
    assert newer.latest_sequence == initial.latest_sequence + 5
    assert incremental["statements"] < 150
    persisted = list(cmd_session.scalars(select(RoleEvent).order_by(RoleEvent.sequence)))
    assert len({item.source_key for item in persisted}) == len(persisted)
    assert [item.sequence for item in persisted] == list(range(1, newer.latest_sequence + 1))


def test_acquisition_history_is_also_deduplicated_in_one_poll(cmd_session):
    from app.models.acquisition import AcquisitionJobEvent
    from tests.test_research_gateway_execution import material_run

    spec, _, jobs = material_run(cmd_session)
    conversation_id = spec.conversation_id
    gateway = ResearchGateway(cmd_session)
    gateway.read_conversation(ACTOR, conversation_id)
    with measured_sql(cmd_session.get_bind()) as small:
        gateway.read_conversation(ACTOR, conversation_id)
    first = cmd_session.scalar(select(func.max(AcquisitionJobEvent.seq)).where(
        AcquisitionJobEvent.job_id == jobs[0].id)) or 0
    cmd_session.add_all(AcquisitionJobEvent(
        job_id=jobs[0].id, seq=first + index + 1, stage="fetching", status="running",
        message="PRIVATE acquisition payload", payload_json={"secret": "PRIVATE"},
        created_at=datetime.now(UTC),
    ) for index in range(64))
    cmd_session.commit()
    initial = gateway.read_conversation(ACTOR, conversation_id)
    with measured_sql(cmd_session.get_bind()) as large:
        repeated = gateway.read_conversation(ACTOR, conversation_id)
    assert initial.events == repeated.events
    assert large["statements"] <= small["statements"] + 2
    assert "PRIVATE" not in repeated.model_dump_json()


def test_rolled_back_projection_recovers_on_same_adapter(cmd_session):
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, run = seed_spec(cmd_session)
    spec_id, conversation_id = spec.id, spec.conversation_id
    add_native_events(cmd_session, run.id, 3)
    adapter = GatewayAutomaticResearchAdapter(cmd_session)
    before = cmd_session.scalar(select(func.count(RoleEvent.id)))
    adapter.sync(run_spec_id=spec_id)
    projected_keys = set(cmd_session.scalars(select(RoleEvent.source_key)))
    cmd_session.rollback()
    assert cmd_session.scalar(select(func.count(RoleEvent.id))) == before
    adapter.sync(run_spec_id=spec_id)
    cmd_session.commit()
    assert set(cmd_session.scalars(select(RoleEvent.source_key))) == projected_keys
    assert cmd_session.get(ResearchConversation, conversation_id).next_event_sequence == len(projected_keys) + 1


def test_concurrent_projectors_preserve_contiguous_unique_events(tmp_path):
    from app.models.ledger import Base
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'gateway-projectors.db'}",
        connect_args={"check_same_thread": False, "timeout": 5})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as setup:
            spec, run = seed_spec(setup)
            spec_id = spec.id
            add_native_events(setup, run.id, 16)
        start = Barrier(2)

        def project():
            with factory() as worker:
                start.wait(timeout=5)
                GatewayAutomaticResearchAdapter(worker).sync(run_spec_id=spec_id)
                worker.commit()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(project) for _ in range(2)]
            for future in futures:
                future.result(timeout=15)
        with factory() as reader:
            rows = list(reader.scalars(select(RoleEvent).order_by(RoleEvent.sequence)))
            assert len(rows) == 25  # 4 bootstrap, 1 scope, 16 added, 4 state facts
            assert len({row.source_key for row in rows}) == len(rows)
            assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
            assert [row.run_sequence for row in rows] == list(range(1, len(rows) + 1))
    finally:
        engine.dispose()


@pytest.mark.parametrize("revocation", ["owner", "tenant", "case_admission"])
def test_caught_up_replay_rechecks_private_authority(cmd_session, revocation):
    from app.errors import NotFoundError

    spec, _ = seed_spec(cmd_session)
    conversation_id = spec.conversation_id
    factory = sessionmaker(bind=cmd_session.get_bind())
    initial = replay(session_factory=factory, actor=ACTOR,
        conversation_id=conversation_id, after_sequence=0)
    if revocation == "case_admission":
        # Imported/corrupt admission is simulated only in the disposable DB;
        # production append-only admissions cannot be changed by this path.
        cmd_session.connection().exec_driver_sql(
            "UPDATE case_tenant_admissions SET tenant_id = 'other-team'")
    else:
        conversation = cmd_session.get(ResearchConversation, conversation_id)
        if revocation == "owner":
            conversation.owner_subject_id = "bob"
        else:
            conversation.tenant_id = "other-team"
    cmd_session.commit()
    with pytest.raises(NotFoundError):
        replay(session_factory=factory, actor=ACTOR,
            conversation_id=conversation_id, after_sequence=initial.latest_sequence)
