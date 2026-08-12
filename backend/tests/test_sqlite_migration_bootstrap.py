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
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0051"
        assessment_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("ai_assessments")
        }
        assert {
            "research_protocol_status",
            "effective_binding_id",
            "mechanism_template_version_id",
            "verification_rule_ids",
        }.issubset(assessment_columns)
        assert {"key_factor_candidate_runs", "key_factor_candidates"}.issubset(
            sa.inspect(connection).get_table_names()
        )
        trigger_count = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one()
        assert trigger_count == 1


def test_0051_preserves_legacy_assessment_and_downgrades_cleanly(tmp_path) -> None:
    database_path = tmp_path / "assessment-provenance.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded_to_0050 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0050"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_0050.returncode == 0, upgraded_to_0050.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    ids = {
        "case": "00000000000000000000000000000001",
        "thesis": "00000000000000000000000000000002",
        "snapshot": "00000000000000000000000000000003",
        "assessment": "00000000000000000000000000000004",
    }
    now = "2026-08-12 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO research_cases "
                "(id, title, industry_topic, created_at, created_by) "
                "VALUES (:id, 'legacy case', 'test', :now, 'tester')"
            ),
            {"id": ids["case"], "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO theses "
                "(id, research_case_id, statement, created_at, created_by, "
                "creator_type, review_state, research_protocol_required) "
                "VALUES (:id, :case_id, 'legacy strict thesis', :now, 'tester', "
                "'human', 'confirmed', 1)"
            ),
            {"id": ids["thesis"], "case_id": ids["case"], "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO evidence_snapshots "
                "(id, thesis_id, cutoff, evidence_link_ids, created_at) "
                "VALUES (:id, :thesis_id, :now, '[]', :now)"
            ),
            {"id": ids["snapshot"], "thesis_id": ids["thesis"], "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO ai_assessments "
                "(id, snapshot_id, conclusion, rationale, gaps, "
                "displayed_as_provisional, creator_type, created_at) "
                "VALUES (:id, :snapshot_id, 'insufficient_evidence', "
                "'legacy row', '[]', 1, 'ai', :now)"
            ),
            {
                "id": ids["assessment"],
                "snapshot_id": ids["snapshot"],
                "now": now,
            },
        )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        legacy = connection.execute(
            sa.text(
                "SELECT research_protocol_status, effective_binding_id, "
                "mechanism_template_version_id, verification_rule_ids "
                "FROM ai_assessments WHERE id = :id"
            ),
            {"id": ids["assessment"]},
        ).one()
        assert tuple(legacy) == (None, None, None, None)
        assert connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one() == 1
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO ai_assessments "
                    "(id, snapshot_id, conclusion, rationale, gaps, "
                    "research_protocol_status, effective_binding_id, "
                    "mechanism_template_version_id, verification_rule_ids, "
                    "displayed_as_provisional, creator_type, created_at) "
                    "VALUES ('00000000000000000000000000000005', :snapshot_id, "
                    "'insufficient_evidence', 'invalid typed import', '[]', "
                    "'ready', '00000000000000000000000000000006', "
                    "'00000000000000000000000000000007', '[]', 1, 'ai', :now)"
                ),
                {"snapshot_id": ids["snapshot"], "now": now},
            )

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0050"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM ai_assessments WHERE id = :id"),
            {"id": ids["assessment"]},
        ).scalar_one() == 1
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("ai_assessments")
        }
        assert "research_protocol_status" not in columns
        assert connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one() == 0


def test_0051_migration_trigger_enforces_typed_protocol_scope(tmp_path) -> None:
    import app.models  # noqa: F401 - register protocol mappings
    from app.models.ledger import AIAssessment, EvidenceSnapshot, ResearchCase, Thesis
    from app.models.research_protocol import CaseMechanismSelectionVersion
    from sqlalchemy.orm import Session
    from tests.protocol_provenance import seed_protocol_footprint

    database_path = tmp_path / "assessment-protocol-scope.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert migrated.returncode == 0, migrated.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    now = datetime.now(UTC)

    def assessment(snapshot, **protocol):
        return AIAssessment(
            snapshot_id=snapshot.id,
            conclusion="insufficient_evidence",
            rationale="migration trigger probe",
            gaps=[],
            displayed_as_provisional=True,
            creator_type="ai",
            created_at=now,
            **protocol,
        )

    with Session(engine) as session:
        first_case = ResearchCase(
            title="first migrated scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        other_case = ResearchCase(
            title="other migrated scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add_all([first_case, other_case])
        session.flush()
        first_thesis = Thesis(
            research_case_id=first_case.id,
            statement="first strict thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        other_thesis = Thesis(
            research_case_id=other_case.id,
            statement="other strict thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add_all([first_thesis, other_thesis])
        session.flush()
        first_snapshot = EvidenceSnapshot(
            thesis_id=first_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        other_snapshot = EvidenceSnapshot(
            thesis_id=other_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add_all([first_snapshot, other_snapshot])
        session.flush()
        first = seed_protocol_footprint(session, first_thesis, status="ready")
        other = seed_protocol_footprint(session, other_thesis, status="ready")
        valid_protocol = {
            "research_protocol_status": "ready",
            "effective_binding_id": first.binding.id,
            "mechanism_template_version_id": first.template.id,
            "verification_rule_ids": [str(rule.id) for rule in first.rules],
        }
        session.add(assessment(first_snapshot, **valid_protocol))
        session.flush()

        invalid_protocols = [
            {"research_protocol_status": "ready"},
            {**valid_protocol, "research_protocol_status": None},
            {**valid_protocol, "research_protocol_status": "blocked"},
            {**valid_protocol, "effective_binding_id": other.binding.id},
            {
                **valid_protocol,
                "mechanism_template_version_id": other.template.id,
            },
            {
                **valid_protocol,
                "verification_rule_ids": [str(rule.id) for rule in other.rules],
            },
            {**valid_protocol, "verification_rule_ids": ["not-a-uuid"]},
            {**valid_protocol, "verification_rule_ids": {"not": "an array"}},
        ]
        for invalid_protocol in invalid_protocols:
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(assessment(first_snapshot, **invalid_protocol))
                session.flush()

        session.add(
            CaseMechanismSelectionVersion(
                research_case_id=first_case.id,
                template_version_id=other.template.id,
                reviewer="tester",
                reason="new current template",
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(assessment(first_snapshot, **valid_protocol))
            session.flush()


def test_upgrade_recovers_when_0048_columns_exist_but_revision_is_stale(tmp_path) -> None:
    database_path = tmp_path / "stale-0047.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0047"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "ALTER TABLE fund_disclosure_sync_config_versions "
                "ADD COLUMN report_period DATE"
            )
        )
        connection.execute(
            sa.text(
                "ALTER TABLE fund_disclosure_sync_runs ADD COLUMN report_period DATE"
            )
        )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0051"


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
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0051"


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
