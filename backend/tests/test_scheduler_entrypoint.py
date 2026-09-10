from __future__ import annotations

from datetime import UTC, datetime
import os

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base
from app.models.operational import ResearchWorkerHeartbeat


def _sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduler.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_scheduler_owns_dispatch_and_recovery_under_one_lease(
    tmp_path,
    monkeypatch,
) -> None:
    from app.scripts import run_scheduler

    sessions = _sessions(tmp_path)
    calls: list[str] = []
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "scheduler-test-1")
    monkeypatch.setattr(
        run_scheduler,
        "try_acquire_scheduler_lease",
        lambda _session: calls.append("lease") or True,
    )
    monkeypatch.setattr(
        run_scheduler,
        "release_scheduler_lease",
        lambda _session: calls.append("release") or True,
    )
    monkeypatch.setattr(
        run_scheduler.MonitorScheduler,
        "dispatch_due",
        lambda _self, **_kwargs: calls.append("monitors") or [object()],
    )
    monkeypatch.setattr(
        run_scheduler.FundDisclosureSyncScheduler,
        "dispatch_due",
        lambda _self, **_kwargs: calls.append("funds") or [],
    )
    monkeypatch.setattr(
        run_scheduler.ResearchOrchestrationService,
        "start_stale_acquisition_recoveries",
        lambda _self, **_kwargs: calls.append("start_recovery") or 1,
    )
    monkeypatch.setattr(
        run_scheduler.ResearchOrchestrationService,
        "complete_acquisition_recoveries",
        lambda _self, **_kwargs: calls.append("complete_recovery") or 1,
    )
    monkeypatch.setattr(
        run_scheduler.ResearchOrchestrationService,
        "reconcile_batch",
        lambda _self, **_kwargs: calls.append("reconcile") or 1,
    )

    found = run_scheduler.run_once(
        session_factory=sessions,
        now=datetime(2026, 8, 17, 1, 0, tzinfo=UTC),
    )

    assert found is True
    assert calls == [
        "lease",
        "monitors",
        "funds",
        "start_recovery",
        "complete_recovery",
        "reconcile",
        "release",
    ]
    with sessions() as session:
        heartbeat = session.get(
            ResearchWorkerHeartbeat,
            "scheduler:scheduler-test-1",
        )
        assert heartbeat is not None
        assert heartbeat.mode == "loop"
        assert heartbeat.state == "idle"


def test_scheduler_does_nothing_without_the_lease(tmp_path, monkeypatch) -> None:
    from app.scripts import run_scheduler

    sessions = _sessions(tmp_path)
    monkeypatch.setattr(
        run_scheduler,
        "try_acquire_scheduler_lease",
        lambda _session: False,
    )
    monkeypatch.setattr(
        run_scheduler.MonitorScheduler,
        "dispatch_due",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dispatch must be lease fenced")
        ),
    )

    assert run_scheduler.run_once(session_factory=sessions) is False


def test_research_worker_no_longer_runs_scheduler_work() -> None:
    from app.scripts import run_research_worker

    assert not hasattr(run_research_worker, "MonitorScheduler")
    assert not hasattr(run_research_worker, "FundDisclosureSyncScheduler")


def test_all_runtime_processes_use_distinct_named_heartbeat_ids(monkeypatch) -> None:
    from app.services.runtime_identity import runtime_heartbeat_id

    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "pod-7")

    assert runtime_heartbeat_id("research-worker") == "research-worker:pod-7"
    assert runtime_heartbeat_id("acquisition-worker") == "acquisition-worker:pod-7"
    assert runtime_heartbeat_id("scheduler") == "scheduler:pod-7"


def test_runtime_instance_id_cannot_collapse_to_a_shared_unknown(monkeypatch) -> None:
    from app.services.runtime_identity import runtime_heartbeat_id

    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "   ")
    with pytest.raises(RuntimeError, match="must not be empty"):
        runtime_heartbeat_id("scheduler")

    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "x" * 128)
    with pytest.raises(RuntimeError, match="exceeds 128"):
        runtime_heartbeat_id("scheduler")

    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "pod/7")
    with pytest.raises(RuntimeError, match="letters, digits"):
        runtime_heartbeat_id("scheduler")


def test_scheduler_rejects_non_positive_recovery_window(tmp_path) -> None:
    from app.scripts import run_scheduler

    with pytest.raises(ValueError, match="greater than zero"):
        run_scheduler.run_once(
            session_factory=_sessions(tmp_path),
            recover_after_minutes=0,
        )


@pytest.mark.pg_only
def test_postgres_scheduler_lease_is_exclusive_and_released_on_own_session() -> None:
    from app.scripts.run_scheduler import (
        release_scheduler_lease,
        try_acquire_scheduler_lease,
    )

    engine = create_engine(os.environ["TEST_DATABASE_URL"], future=True)
    sessions = sessionmaker(bind=engine, future=True)
    try:
        with sessions() as owner, sessions() as contender:
            assert try_acquire_scheduler_lease(owner) is True
            assert try_acquire_scheduler_lease(contender) is False
            assert release_scheduler_lease(owner) is True
            owner.commit()
            assert try_acquire_scheduler_lease(contender) is True
            assert release_scheduler_lease(contender) is True
            contender.commit()
    finally:
        engine.dispose()
