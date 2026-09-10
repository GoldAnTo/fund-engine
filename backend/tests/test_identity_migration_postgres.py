"""PostgreSQL migration proof for OIDC users and Case grants."""

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
def test_0061_preserves_legacy_tenant_admission_without_inventing_users() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"identity_0061_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    case_id = uuid.uuid4()
    document_id = uuid.uuid4()
    admission_id = uuid.uuid4()
    user_id = uuid.uuid4()

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded_to_0060 = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0060"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded_to_0060.returncode == 0, upgraded_to_0060.stderr

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO document_versions (
                        id, content_sha256, source_url, available_at, acquired_at,
                        parser_version, parse_state, source_authority
                    ) VALUES (
                        :id, :sha, 'https://example.test/legacy.pdf',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'legacy-v1',
                        'success', 'primary_disclosure'
                    )
                    """
                ),
                {"id": document_id, "sha": "a" * 64},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO research_cases (
                        id, title, industry_topic, created_at, created_by
                    ) VALUES (
                        :id, 'Legacy Case', 'legacy', CURRENT_TIMESTAMP,
                        'legacy:opaque-token'
                    )
                    """
                ),
                {"id": case_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO case_tenant_admissions (
                        id, research_case_id, tenant_id,
                        initial_document_version_id, admitted_by, admitted_at
                    ) VALUES (
                        :id, :case_id, 'tenant-a', :document_id,
                        'legacy:opaque-token', CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": admission_id,
                    "case_id": case_id,
                    "document_id": document_id,
                },
            )

        upgraded_to_0061 = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0061"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded_to_0061.returncode == 0, upgraded_to_0061.stderr

        with schema_engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM research_users")
                ).scalar_one()
                == 0
            )

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO research_users (
                        id, issuer, subject, tenant_id, display_name,
                        normalized_email, active, last_seen_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, 'https://issuer.example/realms/research',
                        'subject-a', 'tenant-a', 'Alice',
                        'alice@example.com', true, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"id": user_id},
            )
            connection.execute(
                sa.text(
                    """
                    UPDATE research_users
                    SET display_name = 'Alice Updated',
                        active = false,
                        last_seen_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """
                ),
                {"id": user_id},
            )

        for column_name, replacement in (
            ("issuer", "https://other-issuer.example/realms/research"),
            ("subject", "subject-b"),
        ):
            with pytest.raises(sa.exc.IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            f"UPDATE research_users SET {column_name} = :replacement "
                            "WHERE id = :id"
                        ),
                        {"id": user_id, "replacement": replacement},
                    )

        with schema_engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert {"research_users", "case_access_grants"} <= set(
                inspector.get_table_names()
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT tenant_id FROM case_tenant_admissions "
                        "WHERE research_case_id = :case_id"
                    ),
                    {"case_id": case_id},
                ).scalar_one()
                == "tenant-a"
            )
            assert connection.execute(
                sa.text(
                    """
                    SELECT issuer, subject, display_name, active
                    FROM research_users WHERE id = :id
                    """
                ),
                {"id": user_id},
            ).one() == (
                "https://issuer.example/realms/research",
                "subject-a",
                "Alice Updated",
                False,
            )
            assert (
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM case_access_grants")
                ).scalar_one()
                == 0
            )
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
