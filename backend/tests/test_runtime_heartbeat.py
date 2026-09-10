from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base
from app.models.operational import ResearchWorkerHeartbeat
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.runtime_heartbeat import RuntimeHeartbeat


def _sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'heartbeat.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def test_runtime_heartbeat_refreshes_during_work_and_finishes_idle(
    tmp_path, monkeypatch
) -> None:
    sessions = _sessions(tmp_path)
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "worker-1")

    with RuntimeHeartbeat(
        sessions,
        service="research-worker",
        mode="loop",
        interval_seconds=0.01,
    ):
        with sessions() as session:
            first_seen = session.get(
                ResearchWorkerHeartbeat,
                "research-worker:worker-1",
            ).last_seen_at
        time.sleep(0.05)
        with sessions() as session:
            refreshed = session.get(
                ResearchWorkerHeartbeat,
                "research-worker:worker-1",
            )
            assert refreshed is not None
            assert refreshed.last_seen_at > first_seen
            assert refreshed.state == "executing"

    with sessions() as session:
        finished = session.get(
            ResearchWorkerHeartbeat,
            "research-worker:worker-1",
        )
        assert finished is not None
        assert finished.state == "idle"


def test_runtime_heartbeat_records_failed_exit(tmp_path, monkeypatch) -> None:
    sessions = _sessions(tmp_path)
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "worker-2")

    with pytest.raises(RuntimeError, match="provider failed"):
        with RuntimeHeartbeat(
            sessions,
            service="acquisition-worker",
            mode="loop",
            interval_seconds=0.01,
        ):
            raise RuntimeError("provider failed")

    with sessions() as session:
        failed = session.get(
            ResearchWorkerHeartbeat,
            "acquisition-worker:worker-2",
        )
        assert failed is not None
        assert failed.state == "failed"


def test_runtime_heartbeat_publishes_only_redacted_configuration_readiness(
    tmp_path,
    monkeypatch,
) -> None:
    sessions = _sessions(tmp_path)
    monkeypatch.setenv("RUNTIME_INSTANCE_ID", "worker-3")
    monkeypatch.setenv("LLM_API_KEY", "secret-value-must-not-be-persisted")

    with RuntimeHeartbeat(
        sessions,
        service="research-worker",
        mode="loop",
        interval_seconds=1,
    ):
        with sessions() as session:
            heartbeat = session.get(
                ResearchWorkerHeartbeat,
                "research-worker:worker-3",
            )
            assert heartbeat is not None
            assert heartbeat.configuration_status == "configured"
            assert heartbeat.configuration_issues == {"missing": [], "invalid": []}
            assert "secret-value-must-not-be-persisted" not in repr(heartbeat.__dict__)


def test_service_status_aggregates_configuration_across_fresh_instances(tmp_path) -> None:
    sessions = _sessions(tmp_path)
    now = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)

    with sessions() as session:
        heartbeats = WorkerHeartbeatService(session)
        heartbeats.touch(
            worker_id="acquisition-worker:configured",
            mode="loop",
            state="polling",
            configuration_status="configured",
            seen_at=now - timedelta(seconds=2),
        )
        heartbeats.touch(
            worker_id="acquisition-worker:misconfigured",
            mode="loop",
            state="polling",
            configuration_status="misconfigured",
            configuration_issues={
                "missing": ["GILDATA_TOKEN"],
                "invalid": ["LLM_CONFIGURATION"],
            },
            seen_at=now - timedelta(seconds=1),
        )
        session.commit()

        mixed = heartbeats.status(service="acquisition-worker", now=now)
        assert mixed["status"] == "available"
        assert mixed["configuration_status"] == "mixed"
        assert mixed["configuration_issues"] == {
            "missing": ["GILDATA_TOKEN"],
            "invalid": ["LLM_CONFIGURATION"],
        }

        configured = session.get(
            ResearchWorkerHeartbeat,
            "acquisition-worker:configured",
        )
        assert configured is not None
        configured.configuration_status = "misconfigured"
        configured.configuration_issues = {"missing": ["LLM_API_KEY"], "invalid": []}
        session.commit()

        unavailable = heartbeats.status(service="acquisition-worker", now=now)
        assert unavailable["configuration_status"] == "misconfigured"
        assert unavailable["configuration_issues"] == {
            "missing": ["GILDATA_TOKEN", "LLM_API_KEY"],
            "invalid": ["LLM_CONFIGURATION"],
        }
