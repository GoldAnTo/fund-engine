"""PostgreSQL migration and immutable-trigger coverage for underwriting."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa


TABLES = {
    "uw_research_objects",
    "uw_object_relations",
    "uw_mandate_versions",
    "uw_historical_bases",
    "uw_ledger_entries",
    "uw_research_versions",
    "uw_answerability_evaluations",
}


def _schema_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


@pytest.mark.pg_only
def test_0060_installs_kernel_tables_and_immutable_triggers() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0060_{uuid.uuid4().hex}"
    migration_url = _schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    object_id = uuid.uuid4()
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0060"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr
        with isolated.begin() as connection:
            assert TABLES <= set(sa.inspect(connection).get_table_names())
            triggered = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT c.relname
                        FROM pg_trigger t
                        JOIN pg_class c ON c.oid = t.tgrelid
                        WHERE NOT t.tgisinternal AND c.relname LIKE 'uw_%'
                        """
                    )
                ).scalars()
            )
            assert triggered == TABLES
            connection.execute(
                sa.text(
                    """
                    INSERT INTO uw_research_objects
                        (id, kind, external_key, canonical_name, created_at)
                    VALUES (:id, 'company', 'CN:300750:COMPANY', '宁德时代', CURRENT_TIMESTAMP)
                    """
                ),
                {"id": object_id},
            )
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with isolated.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE uw_research_objects SET canonical_name = 'changed' WHERE id = :id"
                    ),
                    {"id": object_id},
                )
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with isolated.begin() as connection:
                connection.execute(
                    sa.text("DELETE FROM uw_research_objects WHERE id = :id"),
                    {"id": object_id},
                )
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
