from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.parametrize(
    ("kind", "rows", "expected"),
    [
        ("research_run", [], 1),
        ("research_run", [("research_run:container", "loop", 0)], 0),
        ("research_run", [("container", "loop", 0)], 0),
        ("research_run", [("research_run:container", "loop", 60), ("container", "loop", 0)], 1),
        ("acquisition", [("container", "loop", 0)], 1),
        ("acquisition", [("acquisition:container", "loop", 0)], 0),
        ("professional_team", [("professional_team:container", "once", 0)], 1),
        ("company_study", [("company_study:container", "loop", 60)], 1),
    ],
)
def test_cold_probe_preserves_heartbeat_semantics_without_loading_the_application(
    tmp_path, kind, rows, expected
) -> None:
    database = tmp_path / "cold-heartbeat.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE research_worker_heartbeats (worker_id TEXT PRIMARY KEY, "
            "worker_kind TEXT, mode TEXT, state TEXT, started_at TEXT, last_seen_at TEXT)"
        )
        now = datetime.now(UTC)
        for worker_id, mode, age in rows:
            connection.execute(
                "INSERT INTO research_worker_heartbeats VALUES (?, ?, ?, ?, ?, ?)",
                (worker_id, kind, mode, "polling", now.isoformat(), (now - timedelta(seconds=age)).isoformat()),
            )
    arguments = ["--worker-kind", kind, "--worker-id", "container", "--max-age-seconds", "30"]
    script = (
        "import sys\n"
        "from app.scripts.check_worker_heartbeat import main\n"
        f"result = main({arguments!r})\n"
        "assert not any(name == 'sqlalchemy' or name.startswith('app.models') for name in sys.modules), "
        "'heartbeat probe loaded the application model graph'\n"
        "raise SystemExit(result)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database}"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.stderr == ""
    assert result.returncode == expected


def test_probe_database_failure_is_quiet_and_does_not_create_a_database(tmp_path) -> None:
    database = tmp_path / "missing-private-database.db"
    result = subprocess.run(
        [sys.executable, "-m", "app.scripts.check_worker_heartbeat",
         "--worker-kind", "research_run", "--worker-id", "container"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database}"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == result.stderr == ""
    assert not database.exists()


def test_heartbeat_probe_requires_a_fresh_matching_loop_worker(tmp_path, monkeypatch) -> None:
    from app.models.ledger import Base
    from app.scripts import check_worker_heartbeat
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    engine = create_engine(f"sqlite:///{tmp_path / 'worker-heartbeat.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    monkeypatch.setenv("DATABASE_URL", str(engine.url))

    arguments = [
        "--worker-kind",
        "acquisition",
        "--worker-id",
        "acquisition-container",
        "--max-age-seconds",
        "30",
    ]
    assert check_worker_heartbeat.main(arguments) == 1

    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="acquisition-container",
            worker_kind="acquisition",
            mode="loop",
            state="polling",
            seen_at=datetime.now(UTC),
        )
        session.commit()

    assert check_worker_heartbeat.main(arguments) == 0

    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="acquisition-container",
            worker_kind="acquisition",
            mode="loop",
            state="polling",
            seen_at=datetime.now(UTC) - timedelta(seconds=31),
        )
        session.commit()

    assert check_worker_heartbeat.main(arguments) == 1
