"""SQLite contract for the industry-index ledger migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0031_report_industry_index_ledger.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("report_industry_index_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_migration_creates_auditable_index_ledger_and_observation_link():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE companies (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(
            sa.text(
                "CREATE TABLE report_market_observations ("
                "id VARCHAR(36) PRIMARY KEY, stock_id VARCHAR(36), "
                "valuation_snapshot_id VARCHAR(36), kind VARCHAR(32) NOT NULL, "
                "status VARCHAR(16) NOT NULL)"
            )
        )
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert set(inspector.get_table_names()) >= {
            "china_industry_indexes",
            "china_industry_index_memberships",
            "china_industry_index_snapshots",
        }
        assert "industry_index_snapshot_id" in {
            column["name"] for column in inspector.get_columns("report_market_observations")
        }
        assert {
            index["name"]
            for table in (
                "china_industry_index_memberships",
                "china_industry_index_snapshots",
            )
            for index in inspector.get_indexes(table)
        } == {
            "ix_china_industry_memberships_company_available",
            "ix_china_industry_memberships_index_available",
            "ix_china_industry_index_snapshots_metric_as_of",
            "ix_china_industry_index_snapshots_availability",
        }

        migration.downgrade()

        assert not {
            "china_industry_indexes",
            "china_industry_index_memberships",
            "china_industry_index_snapshots",
        } & set(sa.inspect(connection).get_table_names())
        assert "industry_index_snapshot_id" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("report_market_observations")
        }


def test_postgres_migration_installs_append_only_triggers_for_all_index_facts(monkeypatch):
    migration = _migration()
    executed: list[str] = []

    class Batch:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_column(self, *_args):
            return None

        def create_foreign_key(self, *_args):
            return None

        def create_check_constraint(self, *_args):
            return None

    fake_op = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        create_table=lambda *_args: None,
        create_index=lambda *_args: None,
        batch_alter_table=lambda *_args: Batch(),
        execute=executed.append,
    )
    monkeypatch.setattr(migration, "op", fake_op)

    migration.upgrade()

    assert executed == [
        f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        for table in migration._IMMUTABLE_TABLES
        for action in ("update", "delete")
    ]
