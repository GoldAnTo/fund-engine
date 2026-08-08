"""Migration contracts for immutable report embed grants."""
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
    / "0036_report_embed_grants.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("report_embed_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_migration_creates_case_scoped_grants_and_revocation_audit() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE research_cases (id VARCHAR(36) PRIMARY KEY)"))
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"embed_grants", "embed_grant_revocations"} <= set(
            inspector.get_table_names()
        )
        assert {column["name"] for column in inspector.get_columns("embed_grants")} == {
            "id",
            "research_case_id",
            "token_sha256",
            "expires_at",
            "allowed_origins",
            "issued_by",
            "created_at",
        }
        assert {index["name"] for index in inspector.get_indexes("embed_grants")} == {
            "ix_embed_grants_case_expiry"
        }
        assert {index["name"] for index in inspector.get_indexes("embed_grant_revocations")} == {
            "ix_embed_grant_revocations_grant"
        }

        migration.downgrade()

        assert not {"embed_grants", "embed_grant_revocations"} & set(
            sa.inspect(connection).get_table_names()
        )


def test_postgres_migration_installs_append_only_grant_and_revocation_triggers(
    monkeypatch,
) -> None:
    migration = _migration()
    executed: list[str] = []
    fake_op = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        create_table=lambda *_args: None,
        create_index=lambda *_args: None,
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
