"""SQLite is the supported self-contained demonstration database.

This is intentionally an end-to-end migration test rather than a metadata
test: a freshly created demo database must be able to replay the same Alembic
history used by the application and retain a durable revision marker.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
import pytest


def test_fresh_sqlite_database_upgrades_to_alembic_head(tmp_path) -> None:
    database_path = tmp_path / "research-demo.db"
    backend = Path(__file__).parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0043"


def test_live_case_runner_bootstraps_its_database_before_materializing(
    monkeypatch, tmp_path
) -> None:
    from app.scripts import run_industrial_foxconn_forecast_case as runner

    database_url = f"sqlite:///{tmp_path / 'industrial-foxconn.db'}"
    calls: list[str] = []
    monkeypatch.setattr(runner, "upgrade_database_to_head", calls.append)
    monkeypatch.setattr(runner, "load_local_env", lambda: None)
    monkeypatch.setattr(runner.GildataMCPClient, "from_env", lambda: object())
    monkeypatch.setattr(runner, "load_industrial_foxconn_sources", lambda _: object())
    monkeypatch.setattr(
        runner,
        "materialize_live_industrial_foxconn_case",
        lambda *_args, **_kwargs: SimpleNamespace(
            case_id="case", verdict_id="verdict", baseline_value=1,
            expected_value=2, actual_value=3, outcome="supported",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_industrial_foxconn_forecast_case", "--database-url", database_url],
    )

    assert runner.main() == 0
    assert calls == [database_url]


def test_adopts_a_complete_legacy_orm_database_without_losing_rows(tmp_path) -> None:
    import app.models  # noqa: F401 - register the complete metadata
    from app.db_migrations import upgrade_database_to_head
    from app.models.ledger import Base, ResearchCase
    from sqlalchemy.orm import Session

    database_url = f"sqlite:///{tmp_path / 'legacy-demo.db'}"
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            ResearchCase(
                title="existing evidence", industry_topic="semiconductor",
                created_by="tester", created_at=datetime.now(UTC),
            )
        )
        session.commit()

    upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM research_cases")).scalar_one() == 1
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0043"


def test_refuses_to_stamp_an_incomplete_unmanaged_database(tmp_path) -> None:
    from app.db_migrations import (
        UnmanagedDatabaseSchemaError,
        upgrade_database_to_head,
    )

    database_url = f"sqlite:///{tmp_path / 'incomplete-demo.db'}"
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE incomplete_legacy_schema (id INTEGER)"))

    with pytest.raises(UnmanagedDatabaseSchemaError, match="missing_tables"):
        upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert "alembic_version" not in sa.inspect(connection).get_table_names()
