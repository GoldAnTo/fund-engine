"""PostgreSQL contract for report-scope visibility migration 0035."""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0035_report_scope_visibility_cutoff.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("report_scope_visibility_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.pg_only
def test_postgres_0035_migrates_legacy_scope_and_restores_update_trigger(engine) -> None:
    """A 0034 scope can be backfilled without weakening append-only writes."""
    schema = f"scope_visibility_{uuid.uuid4().hex}"
    scope_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        connection.execute(
            sa.text(
                "CREATE FUNCTION reject_mutable_ledger() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'immutable ledger'; END; $$ LANGUAGE plpgsql"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE report_research_scope_versions ("
                "id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TRIGGER no_update_report_research_scope_versions "
                "BEFORE UPDATE ON report_research_scope_versions "
                "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TRIGGER no_delete_report_research_scope_versions "
                "BEFORE DELETE ON report_research_scope_versions "
                "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO report_research_scope_versions (id, created_at) "
                "VALUES (:id, '2026-08-01 08:00:00+00')"
            ),
            {"id": scope_id},
        )

        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        cutoff = connection.execute(
            sa.text(
                "SELECT visibility_cutoff_at FROM report_research_scope_versions "
                "WHERE id = :id"
            ),
            {"id": scope_id},
        ).scalar_one()
        assert cutoff is not None
        with connection.begin_nested():
            with pytest.raises(sa.exc.DatabaseError, match="immutable ledger"):
                connection.execute(
                    sa.text(
                        "UPDATE report_research_scope_versions "
                        "SET visibility_cutoff_at = visibility_cutoff_at WHERE id = :id"
                    ),
                    {"id": scope_id},
                )
        with connection.begin_nested():
            with pytest.raises(sa.exc.DatabaseError, match="immutable ledger"):
                connection.execute(
                    sa.text("DELETE FROM report_research_scope_versions WHERE id = :id"),
                    {"id": scope_id},
                )
        connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
