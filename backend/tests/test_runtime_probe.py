from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.ledger import Base
from app.services.research_worker_heartbeat import WorkerHeartbeatService


NOW = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)


def _sessions(tmp_path, *, revision: str = "0061"):
    engine = create_engine(f"sqlite:///{tmp_path / 'probe.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )
    return sessionmaker(bind=engine, future=True)


def test_probe_checks_database_migration_provider_and_fresh_heartbeat_without_fetching(
    tmp_path,
    monkeypatch,
) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path)
    environment = {
        "RUNTIME_INSTANCE_ID": "worker-1",
        "LLM_API_KEY": "secret-value-must-not-be-reported",
    }
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )
    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="research-worker:worker-1",
            mode="loop",
            state="polling",
            seen_at=NOW - timedelta(seconds=10),
        )
        session.commit()

    report = inspect_runtime(
        "research-worker",
        session_factory=sessions,
        environment=environment,
        now=NOW,
    )

    assert report["status"] == "healthy"
    assert report["checks"]["database"]["status"] == "healthy"
    assert report["checks"]["migration"] == {
        "status": "healthy",
        "current": ["0061"],
        "expected": ["0061"],
    }
    assert report["checks"]["providers"] == {
        "status": "healthy",
        "missing": [],
    }
    assert report["checks"]["heartbeat"]["status"] == "healthy"
    assert "secret-value-must-not-be-reported" not in str(report)


def test_probe_fails_closed_for_stale_heartbeat_and_missing_provider_config(
    tmp_path,
    monkeypatch,
) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path)
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )
    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="scheduler:scheduler-1",
            mode="loop",
            state="polling",
            seen_at=NOW - timedelta(minutes=10),
        )
        session.commit()

    report = inspect_runtime(
        "scheduler",
        session_factory=sessions,
        environment={"RUNTIME_INSTANCE_ID": "scheduler-1"},
        now=NOW,
    )

    assert report["status"] == "unhealthy"
    assert report["checks"]["heartbeat"]["status"] == "stale"
    assert report["checks"]["providers"] == {
        "status": "unhealthy",
        "missing": ["GILDATA_TOKEN"],
    }


def test_probe_reports_migration_drift_without_upgrading_the_database(
    tmp_path,
    monkeypatch,
) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path, revision="0060")
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )

    report = inspect_runtime(
        "api",
        session_factory=sessions,
        environment={
            "OIDC_ISSUER": "https://id.example/realms/research",
            "OIDC_AUDIENCE": "research-api",
            "OIDC_JWKS_URL": "https://id.example/realms/research/certs",
            "OIDC_TENANT_CLAIM": "tenant_id",
            "LLM_API_KEY": "configured",
            "GILDATA_TOKEN": "configured",
        },
        now=NOW,
    )

    assert report["status"] == "unhealthy"
    assert report["checks"]["migration"] == {
        "status": "unhealthy",
        "current": ["0060"],
        "expected": ["0061"],
    }


def test_probe_rejects_invalid_adapter_configuration(tmp_path, monkeypatch) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path)
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )
    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="acquisition-worker:worker-1",
            mode="loop",
            state="polling",
            seen_at=NOW,
        )
        session.commit()

    report = inspect_runtime(
        "acquisition-worker",
        session_factory=sessions,
        environment={
            "RUNTIME_INSTANCE_ID": "worker-1",
            "LLM_API_KEY": "configured",
            "ACQUISITION_ENABLED_ADAPTERS": "bogus",
        },
        now=NOW,
    )

    assert report["status"] == "unhealthy"
    assert report["checks"]["providers"]["invalid"] == [
        "ACQUISITION_ENABLED_ADAPTERS"
    ]


def test_probe_rejects_heartbeat_from_the_future(tmp_path, monkeypatch) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path)
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )
    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="research-worker:worker-1",
            mode="loop",
            state="polling",
            seen_at=NOW + timedelta(minutes=10),
        )
        session.commit()

    report = inspect_runtime(
        "research-worker",
        session_factory=sessions,
        environment={
            "RUNTIME_INSTANCE_ID": "worker-1",
            "LLM_API_KEY": "configured",
        },
        now=NOW,
    )

    assert report["status"] == "unhealthy"
    assert report["checks"]["heartbeat"]["status"] == "stale"


def test_probe_rejects_llm_configuration_that_worker_cannot_parse(
    tmp_path, monkeypatch
) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path)
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )
    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="research-worker:worker-1",
            mode="loop",
            state="polling",
            seen_at=NOW,
        )
        session.commit()

    report = inspect_runtime(
        "research-worker",
        session_factory=sessions,
        environment={
            "RUNTIME_INSTANCE_ID": "worker-1",
            "LLM_API_KEY": "configured",
            "LLM_TEMPERATURE": "not-a-number",
        },
        now=NOW,
    )

    assert report["status"] == "unhealthy"
    assert report["checks"]["providers"]["invalid"] == ["LLM_CONFIGURATION"]


def test_probe_rejects_fresh_failed_heartbeat(tmp_path, monkeypatch) -> None:
    from app.scripts.runtime_probe import inspect_runtime

    sessions = _sessions(tmp_path)
    monkeypatch.setattr(
        "app.scripts.runtime_probe.expected_migration_heads",
        lambda: {"0061"},
    )
    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="research-worker:worker-1",
            mode="loop",
            state="failed",
            seen_at=NOW,
        )
        session.commit()

    report = inspect_runtime(
        "research-worker",
        session_factory=sessions,
        environment={
            "RUNTIME_INSTANCE_ID": "worker-1",
            "LLM_API_KEY": "configured",
        },
        now=NOW,
    )

    assert report["status"] == "unhealthy"
    assert report["checks"]["heartbeat"]["status"] == "stale"
