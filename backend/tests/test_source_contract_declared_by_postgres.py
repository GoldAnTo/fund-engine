"""PostgreSQL migration coverage for long source-contract declarers."""
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
def test_0071_postgres_accepts_long_declarer_and_preserves_immutability() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"source_contract_0071_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    document_id = str(uuid.uuid4())
    contract_id = str(uuid.uuid4())
    long_document_id = str(uuid.uuid4())
    long_contract_id = str(uuid.uuid4())
    declared_by = f"tenant:{'t' * 256}"

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded_to_0070 = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0070"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded_to_0070.returncode == 0, upgraded_to_0070.stderr

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO document_versions (
                        id, content_sha256, source_url, available_at, acquired_at,
                        parser_version, parse_state, source_authority
                    ) VALUES (
                        :id, :content_sha256, 'https://provider.example/original',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'fixture-v1', 'partial',
                        'licensed_research'
                    )
                    """
                ),
                {"id": document_id, "content_sha256": "a" * 64},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO source_contracts (
                        id, document_version_id, source_type, research_source_type,
                        provider_or_tenant, allow_ai_processing, allow_display,
                        allow_export, allow_api, region, retention_policy,
                        deletion_policy, downstream_restrictions, intake_metadata,
                        declared_by, created_at
                    ) VALUES (
                        :id, :document_id, 'licensed_provider', 'licensed_provider',
                        'gildata', true, true, false, false, 'CN', 'case_retained',
                        'not_recorded', '[]'::json, '{}'::json, 'tenant:legacy',
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"id": contract_id, "document_id": document_id},
            )

        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0071"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded.returncode == 0, upgraded.stderr

        with schema_engine.begin() as connection:
            declared_type = {
                column["name"]: column["type"]
                for column in sa.inspect(connection).get_columns("source_contracts")
            }["declared_by"]
            assert declared_type.length == 512
            connection.execute(
                sa.text(
                    """
                    INSERT INTO document_versions (
                        id, content_sha256, source_url, available_at, acquired_at,
                        parser_version, parse_state, source_authority
                    ) VALUES (
                        :id, :content_sha256, 'https://provider.example/long',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'fixture-v1', 'partial',
                        'licensed_research'
                    )
                    """
                ),
                {"id": long_document_id, "content_sha256": "b" * 64},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO source_contracts (
                        id, document_version_id, source_type, research_source_type,
                        provider_or_tenant, allow_ai_processing, allow_display,
                        allow_export, allow_api, region, retention_policy,
                        deletion_policy, downstream_restrictions, intake_metadata,
                        declared_by, created_at
                    ) VALUES (
                        :id, :document_id, 'licensed_provider', 'licensed_provider',
                        'gildata', true, true, false, false, 'CN', 'case_retained',
                        'not_recorded', '[]'::json, '{}'::json, :declared_by,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": long_contract_id,
                    "document_id": long_document_id,
                    "declared_by": declared_by,
                },
            )
            assert connection.scalar(
                sa.text("SELECT declared_by FROM source_contracts WHERE id = :id"),
                {"id": long_contract_id},
            ) == declared_by

        with pytest.raises(sa.exc.DBAPIError, match="append-only"):
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE source_contracts SET region = 'US' WHERE id = :id"
                    ),
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
