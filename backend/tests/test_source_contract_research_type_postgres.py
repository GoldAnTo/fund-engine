"""PostgreSQL-only migration coverage for source-contract research categories."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa


def _schema_database_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


@pytest.mark.pg_only
def test_0051_postgres_backfill_restores_source_contract_immutability() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"source_contract_0051_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    document_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded_to_0050 = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0050"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded_to_0050.returncode == 0, upgraded_to_0050.stderr

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO document_versions (
                        id, content_sha256, source_url, available_at, acquired_at,
                        parser_version, parse_state, source_authority
                    ) VALUES (
                        :id, :content_sha256, 'https://provider.example/report',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'fixture-v1', 'partial',
                        'unknown'
                    )
                    """
                ),
                {"id": document_id, "content_sha256": "a" * 64},
            )
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
                        true, true, false, false, 'CN', 'case_retained', 'not_recorded',
                        '[]'::json, '{}'::json, 'legacy-user', CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"id": contract_id, "document_id": document_id},
            )

        upgraded_to_0051 = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0051"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded_to_0051.returncode == 0, upgraded_to_0051.stderr

        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT research_source_type FROM source_contracts WHERE id = :id"
                ),
                {"id": contract_id},
            ).scalar_one() == "licensed_provider"

        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text("UPDATE source_contracts SET region = 'US' WHERE id = :id"),
                    {"id": contract_id},
                )
        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text("DELETE FROM source_contracts WHERE id = :id"),
                    {"id": contract_id},
                )
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
