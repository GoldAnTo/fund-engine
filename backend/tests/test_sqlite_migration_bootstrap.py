"""SQLite is the supported self-contained demonstration database.

This is intentionally an end-to-end migration test rather than a metadata
test: a freshly created demo database must be able to replay the same Alembic
history used by the application and retain a durable revision marker.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from uuid import UUID
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
import pytest
from sqlalchemy.orm import registry, sessionmaker

from tests.legacy_market_conflicts import (
    TABLES as LEGACY_MARKET_TABLES,
    clone_conflicting_insert_sql,
    seed_0067_market_conflicts,
)


def _expected_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    backend = Path(__file__).parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


WAVE2_TABLES = {
    "uw_source_manifest_versions",
    "uw_metric_definition_versions",
    "uw_metric_observations",
    "uw_mechanism_pack_versions",
    "uw_industry_state_versions",
    "uw_industry_scenario_versions",
    "uw_company_exposure_versions",
    "uw_earnings_engine_versions",
    "uw_forecast_input_versions",
    "uw_falsifier_versions",
}


def _search_term_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_0066_migration_freezes_search_normalization_without_app_imports() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0066_research_object_aliases.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert "from app." not in source
    assert "import app." not in source
    assert "unicodedata.normalize" in source


def test_0066_migration_freezes_search_digest_without_app_imports() -> None:
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0066_research_object_aliases.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert "from app." not in source
    assert "import app." not in source
    assert "hashlib.sha256" in source


def test_0066_backfill_keeps_server_side_cursor_option_off_the_shared_bind() -> None:
    """Later PostgreSQL DDL must not inherit a streaming server cursor.

    SQLAlchemy 2.x mutates a ``Connection`` when its ``execution_options``
    method is called.  PostgreSQL cannot DECLARE a cursor for ``CREATE
    TRIGGER``, so the backfill must attach ``stream_results`` to each SELECT,
    rather than to Alembic's shared migration bind.
    """
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0066_research_object_aliases.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "migration_0066_backfill_execution_options",
        migration_path,
    )
    assert module_spec is not None
    assert module_spec.loader is not None
    migration = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(migration)

    metadata = sa.MetaData()
    objects = sa.Table(
        "uw_research_objects",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("external_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    identities = sa.Table(
        "uw_object_identity_versions",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("canonical_name", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    terms = sa.Table(
        "uw_research_object_search_terms",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("identity_version_id", sa.String(), nullable=True),
        sa.Column("term_kind", sa.String(), nullable=False),
        sa.Column("raw_value", sa.String(), nullable=False),
        sa.Column("normalized_value", sa.String(), nullable=False),
        sa.Column("normalized_digest", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            objects.insert(),
            {"id": "object", "external_key": "US:EXAMPLE", "created_at": now},
        )
        connection.execute(
            identities.insert(),
            {
                "id": "identity",
                "object_id": "object",
                "canonical_name": "Example",
                "symbol": "EX",
                "created_at": now,
            },
        )
        migration.op = SimpleNamespace(get_bind=lambda: connection)

        migration._backfill_search_terms()

        assert "stream_results" not in connection.get_execution_options()
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(terms)
            ).scalar_one()
            == 3
        )


CANDIDATE_EVIDENCE_TABLES = {
    "uw_evidence_candidate_dossier_versions",
    "uw_evidence_candidate_review_versions",
}


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
        assert (
            connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == _expected_head()
        )
        event_columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns(
                "uw_company_research_events"
            )
        }
        assert event_columns["hash_version"]["nullable"] is False
        worker_indexes = {
            index["name"]: index
            for index in sa.inspect(connection).get_indexes("jobs")
        }
        assert worker_indexes[
            "ix_jobs_company_research_worker_candidates"
        ]["column_names"] == ["status", "created_at", "id"]
        worker_plan = " ".join(
            row[-1]
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT id FROM jobs "
                "WHERE kind = 'prepare_company_research' "
                "AND target_type = 'company_research_preparation' "
                "AND research_case_id IS NULL AND status = 'queued' "
                "ORDER BY created_at, id LIMIT 100"
            )
        )
        assert "ix_jobs_company_research_worker_candidates" in worker_plan
        assert "TEMP B-TREE" not in worker_plan
        assert any(
            constraint["name"]
            == "ck_uw_company_research_event_hash_version"
            for constraint in sa.inspect(connection).get_check_constraints(
                "uw_company_research_events"
            )
        )
        event_triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'uw_company_research_events'"
                )
            )
        }
        assert event_triggers == {
            "no_update_uw_company_research_events",
            "no_delete_uw_company_research_events",
        }
        assert "uw_market_capture_envelopes" in sa.inspect(connection).get_table_names()
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
        assert {
            "uw_research_objects",
            "uw_object_relations",
            "uw_mandate_versions",
            "uw_historical_bases",
            "uw_ledger_entries",
            "uw_research_versions",
            "uw_answerability_evaluations",
        }.issubset(sa.inspect(connection).get_table_names())
        assert WAVE2_TABLES.issubset(sa.inspect(connection).get_table_names())
        assert CANDIDATE_EVIDENCE_TABLES.issubset(sa.inspect(connection).get_table_names())
        research_source_type = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("source_contracts")
        }["research_source_type"]
        assert research_source_type["nullable"] is False
        heartbeat_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("research_worker_heartbeats")
        }
        assert "worker_kind" in heartbeat_columns
        assert "claim_token" in {
            column["name"] for column in sa.inspect(connection).get_columns("jobs")
        }
        trigger_count = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one()
        assert trigger_count == 1
        assert {
            "uw_company_research_preparations",
            "uw_company_research_artifact_versions",
            "uw_company_research_events",
        }.issubset(sa.inspect(connection).get_table_names())
        inspector = sa.inspect(connection)
        assert "parent_content_hash" in {
            column["name"]
            for column in inspector.get_columns(
                "uw_company_research_artifact_versions"
            )
        }
        assert {"sequence", "previous_event_hash"}.issubset(
            {
                column["name"]
                for column in inspector.get_columns("uw_company_research_events")
            }
        )
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "uw_company_research_preparations"
            )
        } >= {("job_id",)}
        immutable_company_research_trigger_count = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' AND name IN ("
                "'no_update_uw_company_research_artifact_versions', "
                "'no_delete_uw_company_research_artifact_versions', "
                "'no_update_uw_company_research_events', "
                "'no_delete_uw_company_research_events')"
            )
        ).scalar_one()
        assert immutable_company_research_trigger_count == 4


def test_0068_backfills_legacy_market_capture_and_makes_it_immutable(tmp_path) -> None:
    database_path = tmp_path / "market-provenance-0067.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0067"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    snapshot_id = "11111111111111111111111111111111"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO uw_fx_snapshots ("
                "id, base_currency, quote_currency, rate, quote_direction, "
                "market_at, available_at, source_id, raw_hash, content_hash, created_at"
                ") VALUES ("
                ":id, 'USD', 'CNY', 7.18, 'quote_per_base', :market_at, "
                ":available_at, :source_id, :raw_hash, :content_hash, :created_at)"
            ),
            {
                "id": snapshot_id,
                "market_at": datetime(2026, 8, 21, tzinfo=UTC),
                "available_at": datetime(2026, 8, 24, tzinfo=UTC),
                "source_id": "https://legacy.example/fx",
                "raw_hash": "1" * 64,
                "content_hash": "2" * 64,
                "created_at": datetime(2026, 8, 27, 12, tzinfo=UTC),
            },
        )
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0068"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT snapshot_kind, snapshot_id, provenance_role, source_url, "
                "source_locator, provider_policy_version, raw_hash, raw_components, "
                "content_hash FROM uw_market_capture_envelopes"
            )
        ).one()
        assert row[:4] == ("fx", snapshot_id, "primary", "https://legacy.example/fx")
        assert row[4:7] == (
            "legacy snapshot: exact source locator was not captured",
            "legacy_snapshot_without_exact_provenance.v1",
            "1" * 64,
        )
        assert row[7] == "[]"
        assert len(row[8]) == 64
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "UPDATE uw_market_capture_envelopes "
                    "SET source_locator = 'rewritten'"
                )
            )


def test_0068_grandfathers_every_legacy_market_business_time_conflict(
    tmp_path,
) -> None:
    database_path = tmp_path / "market-conflicts-0067.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0067"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.begin() as connection:
        legacy_ids = seed_0067_market_conflicts(connection)

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0068"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        partial_indexes = connection.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name LIKE 'uq_uw_%_business_time'"
            )
        ).scalars().all()
        assert len(partial_indexes) == 4
        assert all(
            "where legacy_business_conflict = false" in sql.casefold()
            for sql in partial_indexes
        )
        for table in LEGACY_MARKET_TABLES:
            rows = connection.execute(
                sa.text(
                    f"SELECT id, content_hash, legacy_business_conflict FROM {table} "
                    "ORDER BY id"
                )
            ).all()
            assert {str(row.id) for row in rows} == set(legacy_ids[table])
            assert {row.content_hash for row in rows} == {"3" * 64, "4" * 64}
            assert [bool(row.legacy_business_conflict) for row in rows] == [True, True]

    for table in LEGACY_MARKET_TABLES:
        with pytest.raises(sa.exc.IntegrityError, match="append-only"):
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        f"UPDATE {table} SET legacy_business_conflict = false"
                    )
                )
        for legacy in (False, True):
            with pytest.raises(sa.exc.IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        sa.text(clone_conflicting_insert_sql(table)),
                        {
                            "id": str(UUID(int=UUID(legacy_ids[table][0]).int + 100)),
                            "raw": "8" * 64,
                            "content": "9" * 64,
                            "legacy": legacy,
                            "fresh_at": datetime(2026, 8, 25, 19, tzinfo=UTC),
                        },
                    )
        with pytest.raises(sa.exc.IntegrityError, match="quarantined"):
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        clone_conflicting_insert_sql(
                            table,
                            fresh_business_key=True,
                        )
                    ),
                    {
                        "id": str(UUID(int=UUID(legacy_ids[table][0]).int + 200)),
                        "raw": "7" * 64,
                        "content": "6" * 64,
                        "legacy": True,
                        "fresh_at": datetime(2026, 8, 25, 19, tzinfo=UTC),
                    },
                )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0067"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'reject_legacy_conflict_insert_%'"
            )
        ).scalar_one() == 0
        for table in LEGACY_MARKET_TABLES:
            assert "legacy_business_conflict" not in {
                column["name"] for column in inspector.get_columns(table)
            }
            assert not any(
                index["name"].endswith("_business_time")
                for index in inspector.get_indexes(table)
            )


def test_0068_orm_naive_datetime_backfill_replays_through_production_capture(
    tmp_path,
) -> None:
    from app.underwriting.services.company_research_market_inputs import (
        CompanyResearchMarketInputs,
    )

    database_path = tmp_path / "orm-market-provenance-0067.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0067"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    engine = sa.create_engine(environment["DATABASE_URL"])
    snapshot_id = UUID("22222222-2222-2222-2222-222222222222")
    legacy_table = sa.Table(
        "uw_fx_snapshots",
        sa.MetaData(),
        autoload_with=engine,
    )
    mapper_registry = registry()

    class LegacyFXSnapshot:
        pass

    mapper_registry.map_imperatively(LegacyFXSnapshot, legacy_table)
    with sessionmaker(engine)() as session:
        session.add(
            LegacyFXSnapshot(
                id=snapshot_id.hex,
                base_currency="USD",
                quote_currency="CNY",
                rate=Decimal("7.18"),
                quote_direction="quote_per_base",
                market_at=datetime(2026, 8, 21, tzinfo=UTC),
                available_at=datetime(2026, 8, 24, tzinfo=UTC),
                source_id="https://legacy.example/orm-fx",
                raw_hash="3" * 64,
                content_hash="4" * 64,
                created_at=datetime(2026, 8, 27, 12, tzinfo=UTC),
            )
        )
        session.commit()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0068"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    with sessionmaker(engine)() as session:
        capture = CompanyResearchMarketInputs(session)._capture("fx", snapshot_id)
        assert capture.authenticated_available_at.replace(tzinfo=UTC) == datetime(
            2026, 8, 24, tzinfo=UTC
        )
        for kind, target_id, role in (
            ("price", UUID("33333333-3333-3333-3333-333333333333"), "primary"),
            ("price", snapshot_id, "primary"),
            ("fx", snapshot_id, "class_b_units"),
        ):
            with pytest.raises(sa.exc.IntegrityError):
                session.execute(
                    sa.text(
                        "INSERT INTO uw_market_capture_envelopes ("
                        "id,snapshot_kind,snapshot_id,provenance_role,source_url,"
                        "source_locator,provider_policy_version,raw_hash,raw_components,"
                        "content_hash,authenticated_available_at,acquired_at) VALUES ("
                        ":id,:kind,:snapshot_id,:role,'https://example.test','locator',"
                        "'policy.v1',:hash,'[]',:hash,:now,:now)"
                    ),
                    {
                        "id": UUID(int=target_id.int + 1).hex,
                        "kind": kind,
                        "snapshot_id": target_id.hex,
                        "role": role,
                        "hash": "5" * 64,
                        "now": datetime(2026, 8, 27, tzinfo=UTC),
                    },
                )
            session.rollback()
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0067"],
        cwd=backend,
        env=environment,
        check=True,
        capture_output=True,
    )
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert "uw_market_capture_envelopes" not in inspector.get_table_names()
        assert "uq_uw_fx_snapshot_business_time" not in {
            item["name"] for item in inspector.get_indexes("uw_fx_snapshots")
        }


def test_0066_sqlite_alias_schema_is_constrained_immutable_and_reversible(
    tmp_path,
) -> None:
    database_path = tmp_path / "research-object-aliases.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0065"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    object_id = "11111111111111111111111111111111"
    identity_id = "12121212121212121212121212121212"
    security_id = "13131313131313131313131313131313"
    security_identity_id = "14141414141414141414141414141414"
    alias_id = "22222222222222222222222222222222"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_objects "
                "(id, kind, external_key, canonical_name, created_at) "
                "VALUES (:id, 'company', 'US:TEST:COMPANY', 'Test Inc.', :now)"
            ),
            {"id": object_id, "now": datetime.now(UTC)},
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_object_identity_versions "
                "(id, object_id, version, canonical_name, symbol, exchange, "
                "share_class, trading_currency, effective_from, effective_to, "
                "supersedes_id, content_hash, created_at) VALUES "
                "(:id, :object_id, 1, '  ÉCOLE Holdings  ', NULL, NULL, NULL, NULL, "
                ":now, NULL, NULL, :digest, :now)"
            ),
            {
                "id": identity_id,
                "object_id": object_id,
                "digest": "a" * 64,
                "now": datetime.now(UTC),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_objects "
                "(id, kind, external_key, canonical_name, created_at) "
                "VALUES (:id, 'security', 'FR:TEST:A', 'Unrelated Share', :now)"
            ),
            {"id": security_id, "now": datetime.now(UTC)},
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_object_identity_versions "
                "(id, object_id, version, canonical_name, symbol, exchange, "
                "share_class, trading_currency, effective_from, effective_to, "
                "supersedes_id, content_hash, created_at) VALUES "
                "(:id, :object_id, 1, 'Unrelated Share', 'ÉCOLE', 'TEST', "
                "'ordinary', 'USD', :now, NULL, NULL, :digest, :now)"
            ),
            {
                "id": security_identity_id,
                "object_id": security_id,
                "digest": "b" * 64,
                "now": datetime.now(UTC),
            },
        )

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0066"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert migrated.returncode == 0, migrated.stderr

    with engine.begin() as connection:
        inspector = sa.inspect(connection)
        assert "uw_research_object_aliases" in inspector.get_table_names()
        assert "uw_research_object_search_terms" in inspector.get_table_names()
        assert {
            column["name"]
            for column in inspector.get_columns("uw_research_object_aliases")
        } == {
            "id",
            "object_id",
            "alias",
            "normalized_alias",
            "locale",
            "created_at",
        }
        assert {
            index["name"]
            for index in inspector.get_indexes("uw_research_object_aliases")
        } == {"ix_uw_object_alias_normalized"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "uw_research_object_aliases"
            )
        } == {"uq_uw_object_alias_object_value"}
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "uw_research_object_aliases"
            )
        } == {
            "ck_uw_object_alias_text",
            "ck_uw_object_alias_normalized",
            "ck_uw_object_alias_normalized_text",
        }
        assert {
            column["name"]
            for column in inspector.get_columns("uw_research_object_search_terms")
        } == {
            "id",
            "object_id",
            "identity_version_id",
            "term_kind",
            "raw_value",
            "normalized_value",
            "normalized_digest",
            "created_at",
        }
        term_indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("uw_research_object_search_terms")
        }
        assert term_indexes["ix_uw_search_term_normalized_digest"] == (
            "normalized_digest",
        )
        assert all(
            "normalized_value" not in columns for columns in term_indexes.values()
        )
        assert set(
            connection.execute(
                sa.text(
                    "SELECT term_kind, raw_value, normalized_value, normalized_digest "
                    "FROM uw_research_object_search_terms ORDER BY term_kind"
                )
            ).all()
        ) == {
            (
                "canonical_name",
                "  ÉCOLE Holdings  ",
                "école holdings",
                _search_term_digest("école holdings"),
            ),
            (
                "canonical_name",
                "Unrelated Share",
                "unrelated share",
                _search_term_digest("unrelated share"),
            ),
            (
                "external_key",
                "US:TEST:COMPANY",
                "us:test:company",
                _search_term_digest("us:test:company"),
            ),
            (
                "external_key",
                "FR:TEST:A",
                "fr:test:a",
                _search_term_digest("fr:test:a"),
            ),
            ("symbol", "ÉCOLE", "école", _search_term_digest("école")),
        }
        explain = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT object_id "
            "FROM uw_research_object_search_terms "
            "WHERE normalized_digest = :digest AND normalized_value = 'école holdings'",
            {"digest": _search_term_digest("école holdings")},
        ).all()
        assert any(
            "ix_uw_search_term_normalized_digest" in str(detail) for detail in explain
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_object_aliases "
                "(id, object_id, alias, normalized_alias, locale, created_at) "
                "VALUES (:id, :object_id, 'Google', 'google', 'en', :now)"
            ),
            {"id": alias_id, "object_id": object_id, "now": datetime.now(UTC)},
        )
        assert connection.execute(
            sa.text(
                "SELECT alias, normalized_alias, locale "
                "FROM uw_research_object_aliases WHERE id = :id"
            ),
            {"id": alias_id},
        ).one() == ("Google", "google", "en")

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO uw_research_object_aliases "
                    "(id, object_id, alias, normalized_alias, locale, created_at) "
                    "VALUES (:id, :object_id, 'Bad', ' BAD ', 'en', :now)"
                ),
                {
                    "id": "33333333333333333333333333333333",
                    "object_id": object_id,
                    "now": datetime.now(UTC),
                },
            )
    for duplicate_id, alias, normalized_alias in (
        ("44444444444444444444444444444444", "GOOGLE", "google"),
        ("55555555555555555555555555555555", "   ", "empty"),
        ("66666666666666666666666666666666", "Google", "unrelated"),
    ):
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO uw_research_object_aliases "
                        "(id, object_id, alias, normalized_alias, locale, created_at) "
                        "VALUES (:id, :object_id, :alias, :normalized_alias, 'en', :now)"
                    ),
                    {
                        "id": duplicate_id,
                        "object_id": object_id,
                        "alias": alias,
                        "normalized_alias": normalized_alias,
                        "now": datetime.now(UTC),
                    },
                )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_object_aliases "
                "(id, object_id, alias, normalized_alias, locale, created_at) "
                "VALUES (:id, :object_id, 'Straße', 'straße', 'de', :now)"
            ),
            {
                "id": "77777777777777777777777777777777",
                "object_id": object_id,
                "now": datetime.now(UTC),
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_object_aliases "
                "(id, object_id, alias, normalized_alias, locale, created_at) "
                "VALUES (:id, :object_id, 'ÉCOLE', 'école', 'fr', :now)"
            ),
            {
                "id": "88888888888888888888888888888888",
                "object_id": object_id,
                "now": datetime.now(UTC),
            },
        )
    for mutation in (
        "UPDATE uw_research_object_aliases SET alias = 'Changed' WHERE id = :id",
        "DELETE FROM uw_research_object_aliases WHERE id = :id",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with engine.begin() as connection:
                connection.execute(sa.text(mutation), {"id": alias_id})
    for mutation in (
        "UPDATE uw_research_object_search_terms SET raw_value = 'Changed'",
        "DELETE FROM uw_research_object_search_terms",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with engine.begin() as connection:
                connection.execute(sa.text(mutation))

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0065"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with engine.connect() as connection:
        assert (
            "uw_research_object_aliases" not in sa.inspect(connection).get_table_names()
        )
        assert (
            "uw_research_object_search_terms"
            not in sa.inspect(connection).get_table_names()
        )
        assert (
            connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0065"
        )
        assert (
            connection.execute(
                sa.text(
                    "SELECT canonical_name FROM uw_research_objects WHERE id = :id"
                ),
                {"id": object_id},
            ).scalar_one()
            == "Test Inc."
        )
    engine.dispose()


def test_0069_event_hash_version_migration_downgrades_cleanly(tmp_path) -> None:
    database_path = tmp_path / "event-hash-version.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0069"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0068"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr

    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0068"
        assert "hash_version" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "uw_company_research_events"
            )
        }
        assert {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'uw_company_research_events'"
                )
            )
        } == {
            "no_update_uw_company_research_events",
            "no_delete_uw_company_research_events",
        }
    engine.dispose()


def test_0069_frozen_hash_algorithms_match_production_golden_values() -> None:
    from app.company_research_event_schema import _event_hash as runtime_event_hash
    from app.underwriting.persistence.company_research_repository import (
        CompanyResearchRepository,
    )

    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0069_company_research_event_hash_version.py"
    )
    module_spec = importlib.util.spec_from_file_location(
        "migration_0069_event_hash_golden", migration_path
    )
    assert module_spec is not None
    assert module_spec.loader is not None
    migration = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(migration)
    repair_migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0070_repair_company_research_event_schema.py"
    )
    repair_spec = importlib.util.spec_from_file_location(
        "migration_0070_event_hash_golden", repair_migration_path
    )
    assert repair_spec is not None
    assert repair_spec.loader is not None
    repair_migration = importlib.util.module_from_spec(repair_spec)
    repair_spec.loader.exec_module(repair_migration)
    preparation_id = UUID("11111111-1111-1111-1111-111111111111")
    created_at = datetime(2026, 8, 29, tzinfo=UTC)
    payload = {"request_hash": "a" * 64}
    row = {
        "preparation_id": preparation_id.hex,
        "sequence": 1,
        "previous_event_hash": None,
        "event_type": "initialized",
        "payload": payload,
        "created_at": created_at.replace(tzinfo=None),
    }

    assert migration._event_hash(row, version=1) == (
        "69e3d6a8ae4295e2221c57578fa1287e9b4a92eda9fe6f2535529a0574c3142f"
    )
    assert migration._event_hash(row, version=2) == (
        "7951a8987bd0e01081cf0a66628e6c6df1e1bf3dd1766c9dea09634f4a7854ed"
    )
    assert CompanyResearchRepository.event_content_hash(
        preparation_id=preparation_id,
        sequence=1,
        previous_event_hash=None,
        event_type="initialized",
        payload=payload,
    ) == migration._event_hash(row, version=1)
    assert CompanyResearchRepository.event_content_hash_v2(
        preparation_id=preparation_id,
        sequence=1,
        previous_event_hash=None,
        event_type="initialized",
        payload=payload,
        created_at=created_at,
    ) == migration._event_hash(row, version=2)
    assert repair_migration._event_hash({**row, "hash_version": 1}) == (
        migration._event_hash(row, version=1)
    )
    assert repair_migration._event_hash({**row, "hash_version": 2}) == (
        migration._event_hash(row, version=2)
    )
    assert runtime_event_hash({**row, "hash_version": 1}) == (
        migration._event_hash(row, version=1)
    )
    assert runtime_event_hash({**row, "hash_version": 2}) == (
        migration._event_hash(row, version=2)
    )


def _company_event_hash(
    *,
    version: int,
    preparation_id: UUID,
    sequence: int,
    previous_event_hash: str | None,
    event_type: str,
    payload: dict[str, object],
    created_at: datetime,
) -> str:
    value: dict[str, object] = {
        "schema_version": f"company-research-event.v{version}",
        "preparation_id": str(preparation_id),
        "sequence": sequence,
        "previous_event_hash": previous_event_hash,
        "event_type": event_type,
        "payload": payload,
    }
    if version == 2:
        value["created_at"] = created_at.astimezone(UTC).isoformat()
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _seed_pre_0069_event_history(
    database_url: str, *, versions: tuple[int, ...], unmatched: bool = False
) -> UUID:
    preparation_id = UUID("11111111-1111-1111-1111-111111111111")
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            sa.text(
                "DROP TRIGGER no_update_uw_company_research_events"
            )
        )
        connection.execute(
            sa.text(
                "DROP TRIGGER no_delete_uw_company_research_events"
            )
        )
        previous_hash = None
        created_at = datetime(2026, 8, 29, tzinfo=UTC)
        for sequence, version in enumerate(versions, start=1):
            payload = {"stage": f"stage-{sequence}"}
            content_hash = _company_event_hash(
                version=version,
                preparation_id=preparation_id,
                sequence=sequence,
                previous_event_hash=previous_hash,
                event_type="test_event",
                payload=payload,
                created_at=created_at,
            )
            if unmatched and sequence == len(versions):
                content_hash = "f" * 64
            connection.execute(
                sa.text(
                    "INSERT INTO uw_company_research_events ("
                    "id, preparation_id, sequence, previous_event_hash, "
                    "event_type, payload, content_hash, created_at"
                    ") VALUES ("
                    ":id, :preparation_id, :sequence, :previous_event_hash, "
                    ":event_type, :payload, :content_hash, :created_at)"
                ),
                {
                    "id": UUID(int=sequence).hex,
                    "preparation_id": preparation_id.hex,
                    "sequence": sequence,
                    "previous_event_hash": previous_hash,
                    "event_type": "test_event",
                    "payload": json.dumps(payload),
                    "content_hash": content_hash,
                    "created_at": created_at.replace(tzinfo=None),
                },
            )
            previous_hash = content_hash
            created_at += timedelta(seconds=1)
        connection.commit()
    engine.dispose()
    return preparation_id


@pytest.mark.parametrize(
    ("versions", "expected"),
    (
        ((1, 1), (1, 1)),
        ((2, 2), (2, 2)),
        ((1, 2), (1, 2)),
    ),
)
def test_0069_authenticates_populated_event_hash_histories(
    tmp_path, versions: tuple[int, ...], expected: tuple[int, ...]
) -> None:
    from app.underwriting.persistence.company_research_repository import (
        CompanyResearchRepository,
    )

    database_path = tmp_path / f"event-hashes-{'-'.join(map(str, versions))}.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0068"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    preparation_id = _seed_pre_0069_event_history(
        environment["DATABASE_URL"], versions=versions
    )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0069"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    with sessionmaker(engine)() as session:
        events = CompanyResearchRepository(session).events(preparation_id)
        assert tuple(event.hash_version for event in events) == expected
    engine.dispose()


@pytest.mark.parametrize(
    ("versions", "unmatched"),
    (((2, 1), False), ((1,), True)),
)
def test_0069_rejects_unauthenticated_or_downgraded_event_history(
    tmp_path, versions: tuple[int, ...], unmatched: bool
) -> None:
    database_path = tmp_path / "invalid-event-hashes.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0068"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    _seed_pre_0069_event_history(
        environment["DATABASE_URL"], versions=versions, unmatched=unmatched
    )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0069"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert upgraded.returncode != 0
    assert "cannot authenticate company research event history" in upgraded.stderr


def _seed_stamped_company_event(
    connection, *, drop_triggers: bool = True
) -> tuple[str, str]:
    preparation_id = UUID("11111111-1111-1111-1111-111111111111")
    event_id = UUID("22222222-2222-2222-2222-222222222222")
    created_at = datetime(2026, 8, 29, tzinfo=UTC)
    payload = {"stage": "stamped-0069"}
    content_hash = _company_event_hash(
        version=2,
        preparation_id=preparation_id,
        sequence=1,
        previous_event_hash=None,
        event_type="test_event",
        payload=payload,
        created_at=created_at,
    )
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    object_id = UUID("33333333-3333-3333-3333-333333333333")
    project_id = UUID("44444444-4444-4444-4444-444444444444")
    job_id = UUID("55555555-5555-5555-5555-555555555555")
    connection.execute(
        sa.text(
            "INSERT INTO uw_research_objects ("
            "id, kind, external_key, canonical_name, created_at"
            ") VALUES (:id, 'company', 'TEST:COMPANY', 'Test Company', :created_at)"
        ),
        {"id": object_id.hex, "created_at": created_at.replace(tzinfo=None)},
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_research_projects ("
            "id, primary_company_id, content_hash, created_at"
            ") VALUES (:id, :company_id, :content_hash, :created_at)"
        ),
        {
            "id": project_id.hex,
            "company_id": object_id.hex,
            "content_hash": "a" * 64,
            "created_at": created_at.replace(tzinfo=None),
        },
    )
    connection.execute(
        sa.text(
            "INSERT INTO jobs ("
            "id, kind, status, progress, attempt, step, cancel_requested, "
            "target_type, target_id, created_at"
            ") VALUES ("
            ":id, 'prepare_company_research', 'failed', 30, 1, "
            "'model_bundle', 0, 'company_research_preparation', "
            ":target_id, :created_at)"
        ),
        {
            "id": job_id.hex,
            "target_id": preparation_id.hex,
            "created_at": created_at.replace(tzinfo=None),
        },
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_company_research_preparations ("
            "id, project_id, idempotency_key, request_hash, strategy_version, "
            "status, current_step, progress, attempt, last_error_code, job_id, "
            "created_at, updated_at"
            ") VALUES ("
            ":id, :project_id, 'stamped-0069', :request_hash, 'test-v1', "
            "'blocked', 'model_bundle', 30, 1, 'validation_failed', :job_id, "
            ":created_at, :created_at)"
        ),
        {
            "id": preparation_id.hex,
            "project_id": project_id.hex,
            "request_hash": "b" * 64,
            "job_id": job_id.hex,
            "created_at": created_at.replace(tzinfo=None),
        },
    )
    connection.execute(
        sa.text(
            "INSERT INTO uw_company_research_events ("
            "id, preparation_id, sequence, hash_version, previous_event_hash, "
            "event_type, payload, content_hash, created_at"
            ") VALUES ("
            ":id, :preparation_id, 1, 2, NULL, 'test_event', :payload, "
            ":content_hash, :created_at)"
        ),
        {
            "id": event_id.hex,
            "preparation_id": preparation_id.hex,
            "payload": json.dumps(payload),
            "content_hash": content_hash,
            "created_at": created_at.replace(tzinfo=None),
        },
    )
    if drop_triggers:
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS no_update_uw_company_research_events"
        )
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS no_delete_uw_company_research_events"
        )
    connection.commit()
    return preparation_id.hex, event_id.hex


def test_0070_repairs_a_stamped_0069_database_without_event_triggers(
    tmp_path,
) -> None:
    database_path = tmp_path / "stamped-0069-triggerless.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0069"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.connect() as connection:
        _preparation_id, event_id = _seed_stamped_company_event(connection)

    repaired = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert repaired.returncode == 0, repaired.stderr
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == _expected_head()
        for statement in (
            "UPDATE uw_company_research_events SET event_type = 'changed' "
            f"WHERE id = '{event_id}'",
            "DELETE FROM uw_company_research_events "
            f"WHERE id = '{event_id}'",
        ):
            with pytest.raises(sa.exc.DatabaseError, match="append-only"):
                connection.exec_driver_sql(statement)
            connection.rollback()
    engine.dispose()


@pytest.mark.parametrize(
    ("versions", "unmatched", "succeeds"),
    (
        ((1,), False, True),
        ((2,), False, True),
        ((1, 2), False, True),
        ((2, 1), False, False),
        ((2,), True, False),
    ),
)
def test_0070_authenticates_populated_stamped_0069_histories(
    tmp_path,
    versions: tuple[int, ...],
    unmatched: bool,
    succeeds: bool,
) -> None:
    database_path = tmp_path / (
        f"stamped-0069-history-{'-'.join(map(str, versions))}-{unmatched}.db"
    )
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0069"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.connect() as connection:
        preparation_id, event_id = _seed_stamped_company_event(connection)
        preparation_uuid = UUID(preparation_id)
        created_at = datetime(2026, 8, 29, tzinfo=UTC)
        root_payload = {"stage": "stamped-0069"}
        root_hash = _company_event_hash(
            version=versions[0],
            preparation_id=preparation_uuid,
            sequence=1,
            previous_event_hash=None,
            event_type="test_event",
            payload=root_payload,
            created_at=created_at,
        )
        if unmatched:
            root_hash = "f" * 64
        connection.exec_driver_sql(
            "UPDATE uw_company_research_events SET hash_version = ?, "
            "content_hash = ? WHERE id = ?",
            (versions[0], root_hash, event_id),
        )
        previous_hash = root_hash
        for sequence, version in enumerate(versions[1:], start=2):
            event_at = created_at + timedelta(seconds=sequence - 1)
            payload = {"stage": f"stamped-{sequence}"}
            digest = _company_event_hash(
                version=version,
                preparation_id=preparation_uuid,
                sequence=sequence,
                previous_event_hash=previous_hash,
                event_type="test_event",
                payload=payload,
                created_at=event_at,
            )
            connection.execute(
                sa.text(
                    "INSERT INTO uw_company_research_events ("
                    "id, preparation_id, sequence, hash_version, "
                    "previous_event_hash, event_type, payload, content_hash, "
                    "created_at) VALUES (:id, :preparation_id, :sequence, "
                    ":version, :previous, 'test_event', :payload, :digest, "
                    ":created_at)"
                ),
                {
                    "id": UUID(int=sequence + 100).hex,
                    "preparation_id": preparation_id,
                    "sequence": sequence,
                    "version": version,
                    "previous": previous_hash,
                    "payload": json.dumps(payload),
                    "digest": digest,
                    "created_at": event_at.replace(tzinfo=None),
                },
            )
            previous_hash = digest
        connection.commit()

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert (upgraded.returncode == 0) is succeeds
    if succeeds:
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            ) == _expected_head()
            assert tuple(
                connection.scalars(
                    sa.text(
                        "SELECT hash_version FROM uw_company_research_events "
                        "ORDER BY sequence"
                    )
                )
            ) == versions
    else:
        assert "cannot authenticate company research event history" in upgraded.stderr
    engine.dispose()


def test_runtime_repair_replaces_named_noop_company_event_triggers(
    tmp_path,
) -> None:
    database_path = tmp_path / "named-noop-triggers.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.connect() as connection:
        _preparation_id, event_id = _seed_stamped_company_event(connection)
        connection.exec_driver_sql(
            "CREATE TRIGGER no_update_uw_company_research_events "
            "BEFORE UPDATE ON uw_company_research_events BEGIN SELECT 1; END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER no_delete_uw_company_research_events "
            "BEFORE DELETE ON uw_company_research_events BEGIN SELECT 1; END"
        )
        connection.commit()
    repaired = subprocess.run(
        [sys.executable, "-m", "app.scripts.verify_company_research_schema"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert repaired.returncode == 0, repaired.stderr
    with engine.connect() as connection:
        for statement in (
            "UPDATE uw_company_research_events SET event_type = 'changed' "
            f"WHERE id = '{event_id}'",
            "DELETE FROM uw_company_research_events "
            f"WHERE id = '{event_id}'",
        ):
            with pytest.raises(sa.exc.DatabaseError, match="append-only"):
                connection.exec_driver_sql(statement)
            connection.rollback()
    engine.dispose()


def test_0061_downgrade_removes_only_wave2_economic_model_tables(tmp_path) -> None:
    database_path = tmp_path / "economic-model-0061.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0061"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0060"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0060"
            table_names = set(sa.inspect(connection).get_table_names())
            assert not WAVE2_TABLES & table_names
            assert {
                "uw_research_objects",
                "uw_historical_bases",
                "uw_answerability_evaluations",
            }.issubset(table_names)
    finally:
        engine.dispose()


def test_0063_downgrade_preserves_0062_candidate_evidence_tables(tmp_path) -> None:
    database_path = tmp_path / "candidate-evidence-0062.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0062"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0063"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"])
    try:
        inspector = sa.inspect(engine)
        checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "uw_evidence_candidate_dossier_versions"
            )
        }
        review_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "uw_evidence_candidate_review_versions"
            )
        }
        assert "ck_uw_evidence_candidate_dossier_status" in checks
        assert {
            "uq_uw_evidence_candidate_review_identity",
            "uq_uw_evidence_candidate_review_role",
            "uq_uw_evidence_candidate_review_reviewer",
        }.issubset(review_uniques)
    finally:
        engine.dispose()

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0062"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr

    engine = sa.create_engine(environment["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0062"
            table_names = set(sa.inspect(connection).get_table_names())
            assert CANDIDATE_EVIDENCE_TABLES.issubset(table_names)
            assert WAVE2_TABLES.issubset(table_names)
    finally:
        engine.dispose()


def test_0064_rejects_new_review_for_a_superseded_candidate_but_keeps_prior_review(tmp_path) -> None:
    database_path = tmp_path / "candidate-evidence-0064.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    for revision in ("0063", "0064"):
        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=backend, env=environment, text=True, capture_output=True, check=False,
        )
        assert upgraded.returncode == 0, upgraded.stderr

    v1, v2, prior_review, stale_review = ("1" * 32, "2" * 32, "3" * 32, "4" * 32)
    common = {
        "object_id": "5" * 32, "basis_id": "6" * 32,
        "source_manifest_id": "7" * 32, "dossier_key": "candidate-family",
        "created_at": "2025-05-15 15:59:59",
    }
    engine = sa.create_engine(environment["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            insert_dossier = sa.text("""
                INSERT INTO uw_evidence_candidate_dossier_versions (
                    id, dossier_key, version, object_id, basis_id, source_manifest_id,
                    scope_statement, purpose, status, rejected_calculations, payload,
                    source_manifest_hash, content_hash, supersedes_id, created_at
                ) VALUES (
                    :id, :dossier_key, :version, :object_id, :basis_id, :source_manifest_id,
                    'scope', 'evidence_candidate', 'candidate', '[]', '{}',
                    :digest, :digest, :supersedes_id, :created_at
                )
            """)
            insert_review = sa.text("""
                INSERT INTO uw_evidence_candidate_review_versions (
                    id, dossier_id, dossier_content_hash, reviewer_identity, reviewer_role,
                    decision, rationale, payload, content_hash, reviewed_at, created_at
                ) VALUES (
                    :id, :dossier_id, :digest, :reviewer_identity, :reviewer_role,
                    'approve', 'review', '{}', :digest, :created_at, :created_at
                )
            """)
            connection.execute(insert_dossier, {**common, "id": v1, "version": 1, "digest": "a" * 64, "supersedes_id": None})
            connection.execute(insert_review, {**common, "id": prior_review, "dossier_id": v1, "digest": "a" * 64, "reviewer_identity": "reviewer:prior", "reviewer_role": "provenance"})
            connection.execute(insert_dossier, {**common, "id": v2, "version": 2, "digest": "b" * 64, "supersedes_id": v1})
            assert connection.execute(sa.text(
                "SELECT id FROM uw_evidence_candidate_review_versions WHERE id = :id"
            ), {"id": prior_review}).scalar_one() == prior_review
            with pytest.raises(sa.exc.IntegrityError, match="candidate dossier is no longer current"):
                connection.execute(insert_review, {**common, "id": stale_review, "dossier_id": v1, "digest": "a" * 64, "reviewer_identity": "reviewer:stale", "reviewer_role": "methodology"})
    finally:
        engine.dispose()


def test_0059_migrates_automatic_research_state_and_downgrades_safely(
    tmp_path,
) -> None:
    database_path = tmp_path / "automatic-research-0059.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded_to_0058 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0058"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_0058.returncode == 0, upgraded_to_0058.stderr

    now = "2026-08-17 00:00:00"
    case_id = "10000000000000000000000000000001"
    brief_id = "20000000000000000000000000000001"
    engine = sa.create_engine(environment["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO research_cases "
                    "(id, title, industry_topic, created_at, created_by) "
                    "VALUES (:id, 'legacy event', 'event_research', :now, 'tester')"
                ),
                {"id": case_id, "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO event_research_briefs ("
                    "id, research_case_id, raw_input, source_url, source_type, "
                    "source_metadata, event_title, company_name, ticker, event_at, "
                    "market_reaction, research_question, extraction_state, created_at"
                    ") VALUES ("
                    ":id, :case_id, 'legacy raw input', NULL, 'pasted_snapshot', "
                    "NULL, 'legacy event', NULL, NULL, NULL, NULL, "
                    "'what changed?', 'human_confirmed', :now)"
                ),
                {"id": brief_id, "case_id": case_id, "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO event_research_lifecycles ("
                    "research_case_id, status, active_run_id, current_round, "
                    "status_summary, current_gap, next_human_action, updated_at"
                    ") VALUES ("
                    ":case_id, 'draft_ready', NULL, 0, 'legacy draft', NULL, NULL, :now)"
                ),
                {"case_id": case_id, "now": now},
            )

        upgraded_to_0059 = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0059"],
            cwd=backend,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded_to_0059.returncode == 0, upgraded_to_0059.stderr

        with engine.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT workflow_mode FROM event_research_briefs WHERE id = :id"
                ),
                {"id": brief_id},
            ).scalar_one() == "reviewed"

        with engine.begin() as connection:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(
                    sa.text(
                        "UPDATE event_research_briefs SET workflow_mode = 'invalid' "
                        "WHERE id = :id"
                    ),
                    {"id": brief_id},
                )

        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE event_research_lifecycles SET status = 'completed' "
                    "WHERE research_case_id = :case_id"
                ),
                {"case_id": case_id},
            )

        downgraded_to_0058 = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0058"],
            cwd=backend,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert downgraded_to_0058.returncode == 0, downgraded_to_0058.stderr

        with engine.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT status FROM event_research_lifecycles "
                    "WHERE research_case_id = :case_id"
                ),
                {"case_id": case_id},
            ).scalar_one() == "draft_ready"
            assert "workflow_mode" not in {
                column["name"]
                for column in sa.inspect(connection).get_columns(
                    "event_research_briefs"
                )
            }
    finally:
        engine.dispose()


def test_0058_freezes_or_recovers_existing_authorized_preparations(tmp_path) -> None:
    database_path = tmp_path / "authorized-preparations.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0057"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-14 00:00:00"
    valid_plan = {
        "items": [{
            "factor": "authorized factor",
            "evidence_target": "primary disclosure",
            "allowed_source_roles": ["primary_disclosure"],
            "priority": "high",
            "stop_condition": "one reviewed source",
            "budget": 10,
        }]
    }
    def plan_for(*factors: str) -> dict:
        return {
            "items": [{
                "factor": factor,
                "evidence_target": "primary disclosure",
                "allowed_source_roles": ["primary_disclosure"],
                "priority": "high",
                "stop_condition": "one reviewed source",
                "budget": 10,
            } for factor in factors]
        }

    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.begin() as connection:
        for case_id, run_id, preparation_id, title in (
            ("00000000000000000000000000000001", "00000000000000000000000000000011", "00000000000000000000000000000021", "case 01"),
            ("00000000000000000000000000000002", "00000000000000000000000000000012", "00000000000000000000000000000022", "case 02"),
            ("00000000000000000000000000000003", "00000000000000000000000000000013", "00000000000000000000000000000023", "case 03"),
            ("00000000000000000000000000000004", "00000000000000000000000000000014", "00000000000000000000000000000024", "case 04"),
        ):
            connection.execute(sa.text(
                "INSERT INTO research_cases (id, title, industry_topic, created_at, created_by) "
                "VALUES (:id, :title, 'test', :now, 'tester')"
            ), {"id": case_id, "title": title, "now": now})
            connection.execute(sa.text(
                "INSERT INTO research_runs (id, research_case_id, status, stage, round, max_rounds, budget, budget_used, created_at, updated_at) "
                "VALUES (:id, :case_id, 'queued', 'planning', 0, 3, 100, 0, :now, :now)"
            ), {"id": run_id, "case_id": case_id, "now": now})
            connection.execute(sa.text(
                "INSERT INTO research_preparations (id, research_case_id, version, input_fingerprint, status, parse_claims_state, draft_protocol_state, draft_evidence_plan_state, claim_review_state, protocol_review_state, plan_review_state, research_run_id, created_at, updated_at) "
                "VALUES (:id, :case_id, 1, :fingerprint, 'authorized', 'succeeded', 'succeeded', 'succeeded', 'confirmed', 'confirmed', 'confirmed', :run_id, :now, :now)"
                ), {"id": preparation_id, "case_id": case_id, "fingerprint": "a" * 64, "run_id": run_id, "now": now})
        for scope_id, case_id, version, factors in (
            ("00000000000000000000000000000061", "00000000000000000000000000000001", 1, ("authorized factor",)),
            ("00000000000000000000000000000062", "00000000000000000000000000000002", 1, ("stale factor",)),
            ("00000000000000000000000000000065", "00000000000000000000000000000002", 2, ("current factor",)),
            ("00000000000000000000000000000063", "00000000000000000000000000000003", 1, ("missing current one", "missing current two")),
            ("00000000000000000000000000000064", "00000000000000000000000000000004", 1, ("extra current only",)),
        ):
            connection.execute(sa.text(
                "INSERT INTO event_research_scope_versions (id, research_case_id, version, changed_by, change_summary, created_at) "
                "VALUES (:id, :case_id, :version, 'tester', 'current scope', :now)"
            ), {"id": scope_id, "case_id": case_id, "version": version, "now": now})
            for position, statement in enumerate(factors, start=1):
                connection.execute(sa.text(
                    "INSERT INTO event_research_scope_factors (id, scope_version_id, statement, position) "
                    "VALUES (:id, :scope_id, :statement, :position)"
                ), {"id": f"0000000000000000000000000000007{scope_id[-1]}{position}", "scope_id": scope_id, "statement": statement, "position": position})
        connection.execute(sa.text(
            "INSERT INTO research_preparation_artifacts (id, research_preparation_id, kind, sequence, preparation_version, input_fingerprint, context_fingerprint, payload, state, invalidated_reason, created_at) "
            "VALUES (:id, :preparation_id, 'evidence_acquisition_plan', 1, 1, :fingerprint, NULL, :payload, 'current', NULL, :now)"
        ), {"id": "00000000000000000000000000000031", "preparation_id": "00000000000000000000000000000021", "fingerprint": "a" * 64, "payload": __import__("json").dumps(valid_plan), "now": now})
        for artifact_id, preparation_id, payload in (
            ("00000000000000000000000000000032", "00000000000000000000000000000022", plan_for("stale factor")),
            ("00000000000000000000000000000033", "00000000000000000000000000000023", plan_for("missing current one")),
            ("00000000000000000000000000000034", "00000000000000000000000000000024", plan_for("extra current only", "extra plan factor")),
        ):
            connection.execute(sa.text(
                "INSERT INTO research_preparation_artifacts (id, research_preparation_id, kind, sequence, preparation_version, input_fingerprint, context_fingerprint, payload, state, invalidated_reason, created_at) "
                "VALUES (:id, :preparation_id, 'evidence_acquisition_plan', 1, 1, :fingerprint, NULL, :payload, 'current', NULL, :now)"
            ), {"id": artifact_id, "preparation_id": preparation_id, "fingerprint": "a" * 64, "payload": __import__("json").dumps(payload), "now": now})
        connection.execute(sa.text(
            "INSERT INTO jobs (id, kind, status, progress, attempt, cancel_requested, target_type, target_id, research_case_id, created_at) "
            "VALUES (:id, 'research_run', 'queued', 0, 1, 0, 'research_run', :run_id, :case_id, :now)"
        ), {"id": "00000000000000000000000000000041", "run_id": "00000000000000000000000000000012", "case_id": "00000000000000000000000000000002", "now": now})
        connection.execute(sa.text(
            "INSERT INTO research_tasks (id, run_id, research_case_id, thesis_id, status, stage, round, task_type, query, evidence_count, gap_reason, result, created_at, updated_at) "
            "VALUES (:id, :run_id, :case_id, NULL, 'queued', 'planned', 1, 'support', 'legacy queued task', 0, NULL, NULL, :now, :now)"
        ), {"id": "00000000000000000000000000000051", "run_id": "00000000000000000000000000000012", "case_id": "00000000000000000000000000000002", "now": now})

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0058"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr + upgraded.stdout
    with engine.connect() as connection:
        kept = connection.execute(sa.text(
            "SELECT status, research_run_id, authorized_evidence_plan, last_error_code "
            "FROM research_preparations WHERE id = '00000000000000000000000000000021'"
        )).one()
        stale = connection.execute(sa.text(
            "SELECT status, research_run_id, authorized_evidence_plan, last_error_code, draft_evidence_plan_state, plan_review_state "
            "FROM research_preparations WHERE id = '00000000000000000000000000000022'"
        )).one()
        missing = connection.execute(sa.text(
            "SELECT status, research_run_id, authorized_evidence_plan, last_error_code "
            "FROM research_preparations WHERE id = '00000000000000000000000000000023'"
        )).one()
        extra = connection.execute(sa.text(
            "SELECT status, research_run_id, authorized_evidence_plan, last_error_code "
            "FROM research_preparations WHERE id = '00000000000000000000000000000024'"
        )).one()
        assert kept.status == "authorized" and kept.research_run_id is not None
        assert __import__("json").loads(kept.authorized_evidence_plan) == valid_plan
        assert connection.execute(sa.text(
            "SELECT status FROM research_runs WHERE id = '00000000000000000000000000000011'"
        )).scalar_one() == "queued"
        assert stale == ("recoverable_failure", None, None, "preparation_authorized_plan_migration_required", "failed", "locked")
        assert missing == extra == ("recoverable_failure", None, None, "preparation_authorized_plan_migration_required")
        cancelled_run = connection.execute(sa.text(
            "SELECT status, stage, stop_reason FROM research_runs WHERE id = '00000000000000000000000000000012'"
        )).one()
        cancelled_job = connection.execute(sa.text(
            "SELECT status, cancel_requested, error, finished_at FROM jobs WHERE id = '00000000000000000000000000000041'"
        )).one()
        cancelled_task = connection.execute(sa.text(
            "SELECT status, stage FROM research_tasks WHERE id = '00000000000000000000000000000051'"
        )).one()
        migration_event = connection.execute(sa.text(
            "SELECT status, payload_json FROM research_run_events WHERE run_id = '00000000000000000000000000000012'"
        )).one()
        assert cancelled_run == ("cancelled", "stopped", "preparation_authorized_plan_migration_required")
        assert cancelled_job.status == "cancelled" and cancelled_job.cancel_requested and cancelled_job.error == "preparation_authorized_plan_migration_required"
        assert cancelled_job.finished_at is not None
        assert cancelled_task == ("cancelled", "stopped")
        assert migration_event.status == "cancelled" and __import__("json").loads(migration_event.payload_json) == {"stop_reason": "preparation_authorized_plan_migration_required"}

    from app.models.operational import Job, ResearchRun
    from app.models.research_preparation import ResearchPreparation
    from app.services.research_preparation import ResearchPreparationService

    with sessionmaker(bind=engine, future=True)() as session:
        case_id = __import__("uuid").UUID("00000000000000000000000000000002")
        preparation = session.get(ResearchPreparation, __import__("uuid").UUID("00000000000000000000000000000022"))
        assert preparation is not None

        ResearchPreparationService(session).retry_failed_step(
            case_id, actor="reviewer", revision=preparation.version
        )
        session.commit()

        assert preparation.draft_evidence_plan_state == "queued"
        assert preparation.status == "preparing"
        assert preparation.research_run_id is None
        assert preparation.authorized_evidence_plan is None
        retry_jobs = list(session.scalars(sa.select(Job).where(
            Job.kind == "prepare_research",
            Job.target_type == "research_preparation",
            Job.target_id == preparation.id,
            Job.status == "queued",
        )))
        assert len(retry_jobs) == 1
        assert retry_jobs[0].correlation_id == f"{preparation.id}:{preparation.version}:draft_evidence_plan"
        runs = list(session.scalars(sa.select(ResearchRun).where(ResearchRun.research_case_id == case_id)))
        assert len(runs) == 1 and runs[0].status == "cancelled"

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0057"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    assert "authorized_evidence_plan" not in {
        column["name"] for column in sa.inspect(engine).get_columns("research_preparations")
    }


def test_0055_upgrades_a_0051_database_with_preparation_constraints(tmp_path) -> None:
    database_path = tmp_path / "research-preparation.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded_to_0051 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_0051.returncode == 0, upgraded_to_0051.stderr
    upgraded_to_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_head.returncode == 0, upgraded_to_head.stderr

    normal_app_fk_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.db import SessionLocal, engine

assert engine.dialect.name == 'sqlite'
with SessionLocal() as session:
    assert session.connection().exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
    now = '2026-08-13 00:00:00'
    case_a = '00000000000000000000000000000021'
    case_b = '00000000000000000000000000000022'
    case_c = '00000000000000000000000000000023'
    run_a = '00000000000000000000000000000024'
    prep_a = '00000000000000000000000000000025'
    for case_id, title in ((case_a, 'case a'), (case_b, 'case b'), (case_c, 'case c')):
        session.execute(text(
            'INSERT INTO research_cases (id, title, industry_topic, created_at, created_by) '
            "VALUES (:id, :title, 'test', :now, 'tester')"
        ), {'id': case_id, 'title': title, 'now': now})
    session.execute(text(
        'INSERT INTO research_runs (id, research_case_id, status, stage, round, max_rounds, budget, budget_used, created_at, updated_at) '
        "VALUES (:id, :case_id, 'queued', 'planning', 0, 3, 100, 0, :now, :now)"
    ), {'id': run_a, 'case_id': case_a, 'now': now})
    session.execute(text(
        'INSERT INTO research_preparations (id, research_case_id, version, input_fingerprint, status, parse_claims_state, draft_protocol_state, draft_evidence_plan_state, claim_review_state, protocol_review_state, plan_review_state, research_run_id, authorized_evidence_plan, created_at, updated_at) '
        "VALUES (:id, :case_id, 1, :fingerprint, 'authorized', 'queued', 'queued', 'queued', 'locked', 'locked', 'locked', :run_id, :plan, :now, :now)"
    ), {'id': prep_a, 'case_id': case_a, 'fingerprint': 'a' * 64, 'run_id': run_a, 'plan': '{"items": []}', 'now': now})
    session.execute(text(
        'INSERT INTO research_preparation_events (id, research_preparation_id, seq, type, step, message, detail, created_at) '
        "VALUES ('00000000000000000000000000000018', :preparation_id, 1, 'claims_parsed', NULL, NULL, '{}', :now)"
    ), {'preparation_id': prep_a, 'now': now})
    session.commit()
    for case_id, prep_id, referenced_run_id in (
        (case_b, '00000000000000000000000000000026', run_a),
        (case_c, '00000000000000000000000000000027', '00000000000000000000000000000028'),
    ):
        try:
            session.execute(text(
                'INSERT INTO research_preparations (id, research_case_id, version, input_fingerprint, status, parse_claims_state, draft_protocol_state, draft_evidence_plan_state, claim_review_state, protocol_review_state, plan_review_state, research_run_id, authorized_evidence_plan, created_at, updated_at) '
                "VALUES (:id, :case_id, 1, :fingerprint, 'authorized', 'queued', 'queued', 'queued', 'locked', 'locked', 'locked', :run_id, :plan, :now, :now)"
            ), {'id': prep_id, 'case_id': case_id, 'fingerprint': 'a' * 64, 'run_id': referenced_run_id, 'plan': '{"items": []}', 'now': now})
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError('invalid research_run_id reference was accepted')
    try:
        session.execute(text("UPDATE research_preparation_events SET message = 'rewritten' WHERE id = '00000000000000000000000000000018'"))
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError('raw preparation event update was accepted')
    try:
        session.execute(text("DELETE FROM research_preparation_events WHERE id = '00000000000000000000000000000018'"))
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError('raw preparation event delete was accepted')
""",
        ],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert normal_app_fk_probe.returncode == 0, normal_app_fk_probe.stderr

    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == _expected_head()
        assert {
            "research_preparations",
            "research_preparation_artifacts",
            "research_preparation_events",
        }.issubset(sa.inspect(connection).get_table_names())
        artifact_columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("research_preparation_artifacts")
        }
        assert artifact_columns["preparation_version"]["nullable"] is False
        assert artifact_columns["context_fingerprint"]["nullable"] is True

        now = "2026-08-13 00:00:00"

        def insert_case(case_id: str, title: str) -> None:
            connection.execute(
                sa.text(
                    "INSERT INTO research_cases "
                    "(id, title, industry_topic, created_at, created_by) "
                    "VALUES (:id, :title, 'test', :now, 'tester')"
                ),
                {"id": case_id, "title": title, "now": now},
            )

        def insert_preparation(case_id: str, *, preparation_id: str, status: str, run_id: str | None = None) -> None:
            connection.execute(
                sa.text(
                    "INSERT INTO research_preparations "
                    "(id, research_case_id, version, input_fingerprint, status, "
                    "parse_claims_state, draft_protocol_state, draft_evidence_plan_state, "
                    "claim_review_state, protocol_review_state, plan_review_state, "
                    "research_run_id, created_at, updated_at) "
                    "VALUES (:id, :case_id, 1, :fingerprint, :status, "
                    "'queued', 'queued', 'queued', 'locked', 'locked', 'locked', "
                    ":run_id, :now, :now)"
                ),
                {
                    "id": preparation_id,
                    "case_id": case_id,
                    "fingerprint": "a" * 64,
                    "status": status,
                    "run_id": run_id,
                    "now": now,
                },
            )

        valid_case = "00000000000000000000000000000011"
        run_id = "00000000000000000000000000000012"
        insert_case(valid_case, "valid preparation")
        connection.execute(
            sa.text(
                "INSERT INTO research_runs "
                "(id, research_case_id, status, stage, round, max_rounds, budget, budget_used, created_at, updated_at) "
                "VALUES (:id, :case_id, 'queued', 'planning', 0, 3, 100, 0, :now, :now)"
            ),
            {"id": run_id, "case_id": valid_case, "now": now},
        )
        insert_preparation(
            valid_case,
            preparation_id="00000000000000000000000000000013",
            status="preparing",
        )
        connection.execute(
            sa.text(
                "INSERT INTO research_preparation_events "
                "(id, research_preparation_id, seq, type, step, message, detail, created_at) "
                "VALUES ('00000000000000000000000000000019', "
                "'00000000000000000000000000000013', 1, 'claims_parsed', NULL, NULL, '{}', :now)"
            ),
            {"now": now},
        )

        invalid_status_case = "00000000000000000000000000000014"
        insert_case(invalid_status_case, "invalid status")
        with pytest.raises(sa.exc.IntegrityError):
            insert_preparation(
                invalid_status_case,
                preparation_id="00000000000000000000000000000015",
                status="not_a_preparation_status",
            )

        unauthorized_run_case = "00000000000000000000000000000016"
        insert_case(unauthorized_run_case, "unauthorized run")
        with pytest.raises(sa.exc.IntegrityError):
            insert_preparation(
                unauthorized_run_case,
                preparation_id="00000000000000000000000000000017",
                status="preparing",
                run_id=run_id,
            )


def test_0056_classifies_legacy_preparation_heartbeats(tmp_path) -> None:
    database_path = tmp_path / "legacy-preparation-heartbeats.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    before = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0055"],
        cwd=backend, env=environment, text=True, capture_output=True, check=False,
    )
    assert before.returncode == 0, before.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    now = "2026-08-13 00:00:00"
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO research_worker_heartbeats "
            "(worker_id, mode, state, started_at, last_seen_at) "
            "VALUES ('host-preparation', 'loop', 'polling', :now, :now), "
            "('host-old-mode', 'research_preparation', 'polling', :now, :now), "
            "('host-run', 'loop', 'polling', :now, :now)"
        ), {"now": now})
    after = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend, env=environment, text=True, capture_output=True, check=False,
    )
    assert after.returncode == 0, after.stderr
    with engine.connect() as connection:
        rows = dict(connection.execute(sa.text(
            "SELECT worker_id, worker_kind FROM research_worker_heartbeats"
        )).all())
    assert rows == {
        "host-preparation": "research_preparation",
        "host-old-mode": "research_preparation",
        "host-run": "research_run",
    }
    from sqlalchemy.orm import Session
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    with Session(engine) as session:
        observed_at = datetime(2026, 8, 13, tzinfo=UTC)
        assert WorkerHeartbeatService(session).status(now=observed_at)["status"] == "available"
        assert WorkerHeartbeatService(session).status(
            now=observed_at, worker_kind="research_preparation"
        )["status"] == "available"

def test_0055_downgrade_removes_preparation_tables(tmp_path) -> None:
    database_path = tmp_path / "research-preparation-downgrade.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded_to_0051 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_0051.returncode == 0, upgraded_to_0051.stderr
    upgraded_to_head = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_head.returncode == 0, upgraded_to_head.stderr
    downgraded_to_0051 = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded_to_0051.returncode == 0, downgraded_to_0051.stderr

    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0051"
        assert not {
            "research_preparation_events",
            "research_preparation_artifacts",
            "research_preparations",
        }.intersection(sa.inspect(connection).get_table_names())
        assert "uq_research_runs_case_id" not in {
            index["name"] for index in sa.inspect(connection).get_indexes("research_runs")
        }


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
    from app.models.research_protocol import (
        CaseMechanismSelectionVersion,
        MechanismEdgeVersion,
        OutcomeBindingVersion,
        VerificationRuleVersion,
    )
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

    def assessment(snapshot, *, conclusion="insufficient_evidence", **protocol):
        return AIAssessment(
            snapshot_id=snapshot.id,
            conclusion=conclusion,
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
            {
                **valid_protocol,
                "research_protocol_status": "single_metric_monitoring",
            },
            {
                **valid_protocol,
                "verification_rule_ids": valid_protocol[
                    "verification_rule_ids"
                ][:-1],
            },
            {
                **valid_protocol,
                "verification_rule_ids": [
                    *valid_protocol["verification_rule_ids"],
                    valid_protocol["verification_rule_ids"][0],
                ],
            },
        ]
        for invalid_protocol in invalid_protocols:
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(assessment(first_snapshot, **invalid_protocol))
                session.flush()

        monitoring_case = ResearchCase(
            title="migrated monitoring scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(monitoring_case)
        session.flush()
        monitoring_thesis = Thesis(
            research_case_id=monitoring_case.id,
            statement="migrated monitoring thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add(monitoring_thesis)
        session.flush()
        monitoring_snapshot = EvidenceSnapshot(
            thesis_id=monitoring_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add(monitoring_snapshot)
        session.flush()
        monitoring = seed_protocol_footprint(session, monitoring_thesis)
        monitoring_protocol = {
            "research_protocol_status": "single_metric_monitoring",
            "effective_binding_id": monitoring.binding.id,
            "mechanism_template_version_id": monitoring.template.id,
            "verification_rule_ids": [str(rule.id) for rule in monitoring.rules],
        }
        for invalid_assessment in (
            assessment(
                monitoring_snapshot,
                conclusion="supported",
                **monitoring_protocol,
            ),
            assessment(
                monitoring_snapshot,
                **{**monitoring_protocol, "research_protocol_status": "ready"},
            ),
        ):
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(invalid_assessment)
                session.flush()

        non_string_case = ResearchCase(
            title="migrated non-string truthy business line",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(non_string_case)
        session.flush()
        non_string_thesis = Thesis(
            research_case_id=non_string_case.id,
            statement="migrated integer business line thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add(non_string_thesis)
        session.flush()
        non_string_snapshot = EvidenceSnapshot(
            thesis_id=non_string_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add(non_string_snapshot)
        session.flush()
        non_string = seed_protocol_footprint(
            session,
            non_string_thesis,
            business_line=1,
        )
        non_string_ready = {
            "research_protocol_status": "ready",
            "effective_binding_id": non_string.binding.id,
            "mechanism_template_version_id": non_string.template.id,
            "verification_rule_ids": [str(rule.id) for rule in non_string.rules],
        }
        for claimed_status, conclusion in (
            ("ready", "insufficient_evidence"),
            ("single_metric_monitoring", "supported"),
        ):
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(
                    assessment(
                        non_string_snapshot,
                        conclusion=conclusion,
                        **{
                            **non_string_ready,
                            "research_protocol_status": claimed_status,
                        },
                    )
                )
                session.flush()

        scoped_rule = first.rules[0]
        legacy_rule = VerificationRuleVersion(
            research_case_id=None,
            mechanism_edge_id=scoped_rule.mechanism_edge_id,
            metric_definition_id=scoped_rule.metric_definition_id,
            expected_direction=scoped_rule.expected_direction,
            support_predicate=scoped_rule.support_predicate,
            contradiction_predicate=scoped_rule.contradiction_predicate,
            allowed_source_roles=list(scoped_rule.allowed_source_roles),
            observed_period_start=scoped_rule.observed_period_start,
            observed_period_end=scoped_rule.observed_period_end,
            available_at_deadline=scoped_rule.available_at_deadline,
            next_verification_event=scoped_rule.next_verification_event,
            reviewer="legacy",
            reason="migrated pre-case-scope rule",
            created_at=datetime.now(UTC),
        )
        session.add(legacy_rule)
        session.flush()
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(
                assessment(
                    first_snapshot,
                    **{
                        **valid_protocol,
                        "verification_rule_ids": [
                            *valid_protocol["verification_rule_ids"],
                            str(legacy_rule.id),
                        ],
                    },
                )
            )
            session.flush()

        old_binding = first.binding
        current_binding = OutcomeBindingVersion(
            thesis_id=old_binding.thesis_id,
            metric_definition_id=old_binding.metric_definition_id,
            entity_scope=dict(old_binding.entity_scope),
            direction=old_binding.direction,
            baseline=dict(old_binding.baseline),
            horizon_start=old_binding.horizon_start,
            horizon_end=old_binding.horizon_end,
            state="approved",
            supersedes_id=old_binding.id,
            reviewer="tester",
            reason="migrated current binding",
            created_at=datetime.now(UTC),
        )
        old_rule = first.rules[0]
        current_rule = VerificationRuleVersion(
            research_case_id=old_rule.research_case_id,
            mechanism_edge_id=old_rule.mechanism_edge_id,
            metric_definition_id=old_rule.metric_definition_id,
            expected_direction=old_rule.expected_direction,
            support_predicate=old_rule.support_predicate,
            contradiction_predicate=old_rule.contradiction_predicate,
            allowed_source_roles=list(old_rule.allowed_source_roles),
            observed_period_start=old_rule.observed_period_start,
            observed_period_end=old_rule.observed_period_end,
            available_at_deadline=old_rule.available_at_deadline,
            next_verification_event=old_rule.next_verification_event,
            supersedes_id=old_rule.id,
            reviewer="tester",
            reason="migrated current rule",
            created_at=datetime.now(UTC),
        )
        session.add_all([current_binding, current_rule])
        session.flush()
        current_rule_ids = [
            str(current_rule.id) if rule.id == old_rule.id else str(rule.id)
            for rule in first.rules
        ]
        stale_protocols = [
            {
                **valid_protocol,
                "effective_binding_id": old_binding.id,
                "verification_rule_ids": current_rule_ids,
            },
            {
                **valid_protocol,
                "effective_binding_id": current_binding.id,
            },
        ]
        for stale_protocol in stale_protocols:
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(assessment(first_snapshot, **stale_protocol))
                session.flush()

        first_edge = session.get(
            MechanismEdgeVersion,
            first.rules[0].mechanism_edge_id,
        )
        session.add(
            MechanismEdgeVersion(
                template_version_id=first.template.id,
                edge_key="migrated-unruled-required-edge",
                source_node_id=first_edge.source_node_id,
                target_node_id=first_edge.target_node_id,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(
                assessment(
                    first_snapshot,
                    **{
                        **valid_protocol,
                        "effective_binding_id": current_binding.id,
                        "verification_rule_ids": current_rule_ids,
                    },
                )
            )
            session.flush()

        no_counter_case = ResearchCase(
            title="migrated no-counter scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(no_counter_case)
        session.flush()
        no_counter_thesis = Thesis(
            research_case_id=no_counter_case.id,
            statement="migrated no-counter thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add(no_counter_thesis)
        session.flush()
        no_counter_snapshot = EvidenceSnapshot(
            thesis_id=no_counter_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add(no_counter_snapshot)
        session.flush()
        no_counter = seed_protocol_footprint(
            session,
            no_counter_thesis,
            status="ready",
            counter_hypothesis=False,
        )
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(
                assessment(
                    no_counter_snapshot,
                    research_protocol_status="ready",
                    effective_binding_id=no_counter.binding.id,
                    mechanism_template_version_id=no_counter.template.id,
                    verification_rule_ids=[
                        str(rule.id) for rule in no_counter.rules
                    ],
                )
            )
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
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == _expected_head()


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
    from app.db_migrations import (
        UnmanagedDatabaseSchemaError,
        require_company_research_event_schema,
        upgrade_database_to_head,
    )
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

    with pytest.raises(
        UnmanagedDatabaseSchemaError,
        match="company research event schema",
    ):
        require_company_research_event_schema(database_url)

    upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM research_cases")).scalar_one() == 1
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == _expected_head()
        assert {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'uw_company_research_events'"
                )
            )
        } == {
            "no_update_uw_company_research_events",
            "no_delete_uw_company_research_events",
        }
    require_company_research_event_schema(database_url)

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql(
            "INSERT INTO uw_company_research_events ("
            "id, preparation_id, sequence, hash_version, previous_event_hash, "
            "event_type, payload, content_hash, created_at"
            ") VALUES ("
            "'00000000000000000000000000000001', "
            "'11111111111111111111111111111111', 1, 2, NULL, "
            "'test', '{}', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'2026-08-29 00:00:00')"
        )
        connection.commit()
        with pytest.raises(sa.exc.DatabaseError, match="append-only"):
            connection.exec_driver_sql(
                "UPDATE uw_company_research_events SET event_type = 'changed'"
            )
        connection.rollback()
        with pytest.raises(sa.exc.DatabaseError, match="append-only"):
            connection.exec_driver_sql("DELETE FROM uw_company_research_events")
        connection.rollback()
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM uw_company_research_events"
        ).scalar_one() == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "invalid_digest",
        "broken_preparation_fk",
        "sequence_gap",
        "wrong_predecessor",
        "descending_timestamp",
    ),
)
def test_unmanaged_adoption_authenticates_existing_company_event_history(
    tmp_path, mutation: str
) -> None:
    import app.models  # noqa: F401
    from app.db_migrations import (
        UnmanagedDatabaseSchemaError,
        upgrade_database_to_head,
    )
    from app.models.ledger import Base

    database_url = f"sqlite:///{tmp_path / f'invalid-adoption-{mutation}.db'}"
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        preparation_id, event_id = _seed_stamped_company_event(connection)
        if mutation == "invalid_digest":
            connection.exec_driver_sql(
                "UPDATE uw_company_research_events SET content_hash = ? WHERE id = ?",
                ("f" * 64, event_id),
            )
        elif mutation == "broken_preparation_fk":
            missing = UUID("66666666-6666-6666-6666-666666666666")
            created_at = datetime(2026, 8, 29, tzinfo=UTC)
            payload = {"stage": "stamped-0069"}
            digest = _company_event_hash(
                version=2,
                preparation_id=missing,
                sequence=1,
                previous_event_hash=None,
                event_type="test_event",
                payload=payload,
                created_at=created_at,
            )
            connection.exec_driver_sql(
                "UPDATE uw_company_research_events SET preparation_id = ?, "
                "content_hash = ? WHERE id = ?",
                (missing.hex, digest, event_id),
            )
        elif mutation == "sequence_gap":
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.exec_driver_sql(
                "UPDATE uw_company_research_events SET sequence = 2 WHERE id = ?",
                (event_id,),
            )
        elif mutation == "wrong_predecessor":
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.exec_driver_sql(
                "UPDATE uw_company_research_events SET previous_event_hash = ? "
                "WHERE id = ?",
                ("e" * 64, event_id),
            )
        else:
            root_hash = connection.exec_driver_sql(
                "SELECT content_hash FROM uw_company_research_events WHERE id = ?",
                (event_id,),
            ).scalar_one()
            earlier = datetime(2026, 8, 28, tzinfo=UTC)
            payload = {"stage": "second"}
            digest = _company_event_hash(
                version=2,
                preparation_id=UUID(preparation_id),
                sequence=2,
                previous_event_hash=root_hash,
                event_type="test_event",
                payload=payload,
                created_at=earlier,
            )
            connection.execute(
                sa.text(
                    "INSERT INTO uw_company_research_events ("
                    "id, preparation_id, sequence, hash_version, "
                    "previous_event_hash, event_type, payload, content_hash, "
                    "created_at) VALUES (:id, :preparation_id, 2, 2, "
                    ":previous, 'test_event', :payload, :digest, :created_at)"
                ),
                {
                    "id": UUID("77777777-7777-7777-7777-777777777777").hex,
                    "preparation_id": preparation_id,
                    "previous": root_hash,
                    "payload": json.dumps(payload),
                    "digest": digest,
                    "created_at": earlier.replace(tzinfo=None),
                },
            )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
        connection.commit()

    with pytest.raises(
        UnmanagedDatabaseSchemaError,
        match="authenticate or repair company research event schema",
    ):
        upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert "alembic_version" not in sa.inspect(connection).get_table_names()
        assert not {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'uw_company_research_events'"
            )
        }
    engine.dispose()


def test_unmanaged_adoption_rejects_unknown_required_company_event_column(
    tmp_path,
) -> None:
    import app.models  # noqa: F401
    from app.db_migrations import (
        UnmanagedDatabaseSchemaError,
        upgrade_database_to_head,
    )
    from app.models.ledger import Base

    database_url = f"sqlite:///{tmp_path / 'required-extra-event-column.db'}"
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE uw_company_research_events "
            "ADD COLUMN unknown_required TEXT NOT NULL"
        )

    with pytest.raises(
        UnmanagedDatabaseSchemaError,
        match="current company research event schema",
    ):
        upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert "alembic_version" not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_unmanaged_adoption_installs_the_0070_company_worker_index(
    tmp_path,
) -> None:
    import app.models  # noqa: F401
    from app.db_migrations import upgrade_database_to_head
    from app.models.ledger import Base

    database_url = f"sqlite:///{tmp_path / 'legacy-without-worker-index.db'}"
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP INDEX ix_jobs_company_research_worker_candidates"
        )

    upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert connection.scalar(
            sa.text("SELECT version_num FROM alembic_version")
        ) == _expected_head()
        index = {
            row["name"]: row
            for row in sa.inspect(connection).get_indexes("jobs")
        }["ix_jobs_company_research_worker_candidates"]
        assert index["column_names"] == ["status", "created_at", "id"]
        assert "research_case_id IS NULL" in str(
            index["dialect_options"]["sqlite_where"]
        )
        repaired_plan = " ".join(
            row[-1]
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT id FROM jobs "
                "WHERE kind = 'prepare_company_research' "
                "AND target_type = 'company_research_preparation' "
                "AND research_case_id IS NULL AND status = 'queued' "
                "ORDER BY created_at, id LIMIT 100"
            )
        )
        assert "ix_jobs_company_research_worker_candidates" in repaired_plan
        assert "TEMP B-TREE" not in repaired_plan
    engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "extra_predicate",
        "uppercase_literal",
        "cast_suffix_inside_literal",
        "uppercase_target_literal",
        "descending_created_at",
    ),
)
def test_runtime_schema_gate_rejects_a_noncanonical_company_worker_index(
    tmp_path, mutation: str
) -> None:
    from app.db_migrations import (
        UnmanagedDatabaseSchemaError,
        require_company_research_event_schema,
    )

    database_path = tmp_path / f"invalid-worker-index-{mutation}.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP INDEX ix_jobs_company_research_worker_candidates"
        )
        if mutation != "missing":
            kind = {
                "extra_predicate": "prepare_company_research",
                "uppercase_literal": "PREPARE_COMPANY_RESEARCH",
                "cast_suffix_inside_literal": "prepare_company_research::text",
                "uppercase_target_literal": "prepare_company_research",
                "descending_created_at": "prepare_company_research",
            }[mutation]
            target = (
                "COMPANY_RESEARCH_PREPARATION"
                if mutation == "uppercase_target_literal"
                else "company_research_preparation"
            )
            suffix = " AND status = 'never'" if mutation == "extra_predicate" else ""
            columns = (
                "status, created_at DESC, id"
                if mutation == "descending_created_at"
                else "status, created_at, id"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_jobs_company_research_worker_candidates "
                f"ON jobs ({columns}) "
                f"WHERE kind = '{kind}' "
                f"AND target_type = '{target}' "
                f"AND research_case_id IS NULL{suffix}"
            )

    with pytest.raises(
        UnmanagedDatabaseSchemaError,
        match="company research worker candidate index",
    ):
        require_company_research_event_schema(environment["DATABASE_URL"])
    gate = subprocess.run(
        [sys.executable, "-m", "app.scripts.verify_company_research_schema"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert gate.returncode != 0
    assert "company research worker candidate index" in gate.stderr
    engine.dispose()


def test_worker_index_predicate_normalization_preserves_literal_bytes() -> None:
    from app.db_migrations import (
        _COMPANY_WORKER_INDEX_PREDICATE,
        _normalize_company_worker_predicate,
    )

    expected = _normalize_company_worker_predicate(
        _COMPANY_WORKER_INDEX_PREDICATE
    )
    reflected_postgresql = (
        "(((kind)::text = 'prepare_company_research'::text) AND "
        "((target_type)::text = 'company_research_preparation'::text) AND "
        "(research_case_id IS NULL))"
    )
    assert _normalize_company_worker_predicate(reflected_postgresql) == expected
    for invalid in (
        reflected_postgresql.replace(
            "'prepare_company_research'", "'PREPARE_COMPANY_RESEARCH'"
        ),
        reflected_postgresql.replace(
            "'prepare_company_research'", "'prepare_company_research::text'"
        ),
        reflected_postgresql.replace(
            "'company_research_preparation'", "'COMPANY_RESEARCH_PREPARATION'"
        ),
    ):
        assert _normalize_company_worker_predicate(invalid) != expected


@pytest.mark.parametrize(
    "override",
    (
        {"indisvalid": False},
        {"indisready": False},
        {"access_method": "hash"},
        {"key_columns": ["status", "id", "created_at"]},
        {"descending": [False, True, False]},
        {"nulls_first": [False, True, False]},
    ),
)
def test_postgresql_worker_index_catalog_requires_canonical_btree_order(
    override: dict[str, object],
) -> None:
    from app.db_migrations import (
        _postgresql_company_worker_index_is_canonical,
    )

    state = {
        "indisvalid": True,
        "indisready": True,
        "access_method": "btree",
        "indnkeyatts": 3,
        "key_columns": ["status", "created_at", "id"],
        "descending": [False, False, False],
        "nulls_first": [False, False, False],
        **override,
    }
    assert not _postgresql_company_worker_index_is_canonical(state)


def test_postgresql_worker_index_catalog_accepts_canonical_btree_order() -> None:
    from app.db_migrations import (
        _postgresql_company_worker_index_is_canonical,
    )

    assert _postgresql_company_worker_index_is_canonical(
        {
            "indisvalid": True,
            "indisready": True,
            "access_method": "btree",
            "indnkeyatts": 3,
            "key_columns": ["status", "created_at", "id"],
            "descending": [False, False, False],
            "nulls_first": [False, False, False],
        }
    )


def test_postgresql_worker_index_catalog_uses_the_visible_jobs_relation() -> None:
    from app.db_migrations import (
        _POSTGRESQL_COMPANY_WORKER_INDEX_STATE_SQL,
    )

    normalized = " ".join(
        _POSTGRESQL_COMPANY_WORKER_INDEX_STATE_SQL.lower().split()
    )
    assert "i.indrelid = to_regclass(:table_name)" in normalized
    assert "current_schema()" not in normalized


def test_upgrade_from_0051_backfills_source_contract_research_type(tmp_path) -> None:
    database_path = tmp_path / "source-contracts-0051.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    contract_id = "11111111111111111111111111111111"
    document_id = "22222222222222222222222222222222"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO source_contracts (
                    id, document_version_id, source_type, provider_or_tenant,
                    allow_ai_processing, allow_display, allow_export, allow_api,
                    region, retention_policy, deletion_policy,
                    downstream_restrictions, intake_metadata, declared_by, created_at
                ) VALUES (
                    :id, :document_id, 'licensed_provider', 'legacy-provider',
                    1, 1, 0, 0, 'CN', 'case_retained', 'not_recorded',
                    '[]', '{}', 'legacy-user', :created_at
                )
                """
            ),
            {
                "id": contract_id,
                "document_id": document_id,
                "created_at": datetime.now(UTC),
            },
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
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == _expected_head()
        assert connection.execute(
            sa.text(
                "SELECT research_source_type FROM source_contracts WHERE id = :id"
            ),
            {"id": contract_id},
        ).scalar_one() == "licensed_provider"


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


def test_0051_upgrade_requires_an_explicit_preparation_backfill(tmp_path) -> None:
    """Migrations add schema only; the operator explicitly admits old Cases."""
    database_path = tmp_path / "explicit-preparation-backfill.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0054"],
        cwd=backend, env=environment, text=True, capture_output=True, check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    seeded = subprocess.run(
        [sys.executable, "-c", """
from datetime import UTC, datetime
from app.db import SessionLocal
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
from app.models.operational import EventResearchLifecycle
from app.models.source_governance import SourceContract

now = datetime.now(UTC)
with SessionLocal() as session:
    case = ResearchCase(title='legacy eligible case', industry_topic='test', created_by='test', created_at=now)
    document = DocumentVersion(content_sha256='a' * 64, source_url='https://example.test/legacy', available_at=now, acquired_at=now, parser_version='test', source_authority='primary_disclosure')
    session.add_all((case, document)); session.flush()
    session.add_all((
        CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now),
        CaseTenantAdmission(research_case_id=case.id, tenant_id='test', initial_document_version_id=document.id, admitted_by='test', admitted_at=now),
        EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by='test', change_summary='legacy scope', created_at=now),
        EventResearchLifecycle(research_case_id=case.id, status='awaiting_scope', active_run_id=None, current_round=0, status_summary='legacy', current_gap=None, next_human_action=None, updated_at=now),
        SourceContract(document_version_id=document.id, source_type='uploaded_file', provider_or_tenant='test', allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region='CN', effective_from=None, effective_until=None, retention_policy='case_retained', deletion_policy='manual', downstream_restrictions=[], contract_version='test', intake_metadata={}, declared_by='test', created_at=now),
    ))
    session.commit()
"""],
        cwd=backend, env=environment, text=True, capture_output=True, check=False,
    )
    assert seeded.returncode == 0, seeded.stderr
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend, env=environment, text=True, capture_output=True, check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    verified = subprocess.run(
        [sys.executable, "-c", """
from sqlalchemy import select
from app.db import SessionLocal
from app.models.operational import Job
from app.models.research_preparation import ResearchPreparation
from app.services.research_preparation_backfill import ResearchPreparationBackfill

with SessionLocal() as session:
    assert session.scalars(select(ResearchPreparation)).all() == []
    assert session.scalars(select(Job).where(Job.kind == 'prepare_research')).all() == []
    preparations = ResearchPreparationBackfill(session).enqueue_eligible(limit=1)
    assert len(preparations) == 1
    session.commit()
    assert len(session.scalars(select(ResearchPreparation)).all()) == 1
    jobs = session.scalars(select(Job).where(Job.kind == 'prepare_research')).all()
    assert len(jobs) == 1 and jobs[0].correlation_id.endswith(':parse_claims')
"""],
        cwd=backend, env=environment, text=True, capture_output=True, check=False,
    )
    assert verified.returncode == 0, verified.stderr + verified.stdout


def test_unmanaged_adoption_installs_ai_scope_index(tmp_path):
    import app.models  # noqa: F401
    from app.db_migrations import upgrade_database_to_head
    from app.models.ledger import Base
    url = f"sqlite:///{tmp_path / 'unmanaged-ai-scope.db'}"
    engine = sa.create_engine(url)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql('DROP INDEX ix_ai_runs_research_scope')
        upgrade_database_to_head(url)
        with engine.connect() as connection:
            indexes = connection.exec_driver_sql("PRAGMA index_list('ai_runs')").all()
            assert any(row[1] == 'ix_ai_runs_research_scope' for row in indexes)
    finally:
        engine.dispose()
