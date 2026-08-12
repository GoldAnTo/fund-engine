"""PostgreSQL migration and database immutability checks for acquisition."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.test_acquisition_schema import (
    ACQUISITION_TABLES,
    APPEND_ONLY_TABLES,
    assert_migrated_acquisition_schema,
)


def _schema_database_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


@pytest.mark.pg_only
def test_0052_postgres_constraints_and_database_immutability() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"acquisition_0052_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        previous = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0051"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert previous.returncode == 0, previous.stderr

        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0052"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded.returncode == 0, upgraded.stderr

        with schema_engine.connect() as connection:
            inspector = sa.inspect(connection)
            assert ACQUISITION_TABLES <= set(inspector.get_table_names())
            assert_migrated_acquisition_schema(inspector)
            job_checks = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints("acquisition_jobs")
            )
            assert all(
                value in job_checks
                for value in (
                    "queued", "running", "retry_wait", "succeeded", "partial",
                    "failed", "cancelled", "searching", "fetching", "freezing",
                    "extracting", "admitting", "attempt >= 0",
                    "reference_count >= 0", "exception_count >= 0",
                )
            )
            attempt_checks = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints(
                    "acquisition_attempts"
                )
            )
            artifact_checks = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints(
                    "retrieval_artifacts"
                )
            )
            decision_checks = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints(
                    "automatic_admission_decisions"
                )
            )
            evidence_link_checks = " ".join(
                constraint["sqltext"]
                for constraint in inspector.get_check_constraints("evidence_links")
            )
            assert "attempt_no >= 1" in attempt_checks
            assert "byte_size > 0" in artifact_checks
            assert "length(content_sha256) = 64" in artifact_checks
            assert "byte_size = length(raw_bytes)" in artifact_checks
            assert "admitted" in decision_checks
            assert "quarantined" in decision_checks
            assert "automatically_admitted" in evidence_link_checks
            assert "automatic_admission_decision_id IS NOT NULL" in evidence_link_checks
            external_version = {
                column["name"]: column
                for column in inspector.get_columns("source_references")
            }["external_version"]
            assert external_version["nullable"] is False
            assert external_version["default"] is not None

        ids = {name: str(uuid.uuid4()) for name in (
            "case", "thesis", "job", "event", "attempt", "reference", "artifact",
            "document", "binding", "span", "candidate", "decision", "exception",
        )}
        now = datetime.now(UTC)
        with schema_engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO research_cases (id, title, industry_topic, created_at, created_by) "
                "VALUES (:id, 'case', 'topic', :now, 'test')"
            ), {"id": ids["case"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO theses (id, research_case_id, statement, created_at, created_by) "
                "VALUES (:id, :case_id, 'thesis', :now, 'test')"
            ), {"id": ids["thesis"], "case_id": ids["case"], "now": now})
            connection.execute(sa.text(
                """
                INSERT INTO acquisition_jobs (
                    id, tenant_id, research_case_id, thesis_id, idempotency_key,
                    request_snapshot, policy_snapshot, status, stage, attempt,
                    reference_count, fetched_count, frozen_count, admitted_count,
                    exception_count, created_at, updated_at
                ) VALUES (
                    :id, 'tenant', :case_id, :thesis_id, 'key', '{}'::json, '{}'::json,
                    'queued', 'queued', 0, 0, 0, 0, 0, 0, :now, :now
                )
                """
            ), {"id": ids["job"], "case_id": ids["case"], "thesis_id": ids["thesis"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO acquisition_job_events "
                "(id, job_id, seq, status, stage, message, payload_json, created_at) "
                "VALUES (:id, :job_id, 1, 'queued', 'queued', 'created', '{}'::json, :now)"
            ), {"id": ids["event"], "job_id": ids["job"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO acquisition_attempts "
                "(id, job_id, adapter_key, operation, attempt_no, started_at, outcome, retryable, safe_metadata) "
                "VALUES (:id, :job_id, 'sse', 'search', 1, :now, 'succeeded', false, '{}'::json)"
            ), {"id": ids["attempt"], "job_id": ids["job"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO source_references "
                "(id, job_id, adapter_key, external_record_id, external_version, canonical_url, title, source_role, metadata_json, created_at) "
                "VALUES (:id, :job_id, 'sse', 'record', '', 'https://example.test/a', 'title', 'company_disclosure', '{}'::json, :now)"
            ), {"id": ids["reference"], "job_id": ids["job"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO retrieval_artifacts "
                "(id, source_reference_id, attempt_id, content_sha256, raw_bytes, mime_type, byte_size, final_url, retrieved_at) "
                "VALUES (:id, :reference_id, :attempt_id, :sha, :raw, 'text/plain', 1, 'https://example.test/a', :now)"
            ), {"id": ids["artifact"], "reference_id": ids["reference"], "attempt_id": ids["attempt"], "sha": "a" * 64, "raw": b"a", "now": now})
            connection.execute(sa.text(
                """
                INSERT INTO document_versions (
                    id, content_sha256, source_url, available_at, acquired_at,
                    parser_version, parse_state, source_authority
                ) VALUES (
                    :id, :sha, 'https://example.test/a', :now, :now, 'test-v1',
                    'success', 'company_disclosure'
                )
                """
            ), {"id": ids["document"], "sha": "b" * 64, "now": now})
            connection.execute(sa.text(
                "INSERT INTO retrieval_artifact_documents "
                "(id, retrieval_artifact_id, document_version_id, relation, publication_key, created_at) "
                "VALUES (:id, :artifact_id, :document_id, 'created', 'publication', :now)"
            ), {"id": ids["binding"], "artifact_id": ids["artifact"], "document_id": ids["document"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO source_spans (id, document_version_id, locator, verbatim_text) "
                "VALUES (:id, :document_id, '{}'::json, 'claim')"
            ), {"id": ids["span"], "document_id": ids["document"]})
            connection.execute(sa.text(
                """
                INSERT INTO atomic_claim_candidates (
                    id, source_span_id, canonical_key, quote, quote_start, quote_end,
                    quote_sha256, normalized_text, claim_type, authority_level,
                    structured_fields, validation_result, created_at
                ) VALUES (
                    :id, :span_id, :key, 'claim', 0, 5, :sha, 'claim', 'reported_claim',
                    'primary', '{}'::json, '{}'::json, :now
                )
                """
            ), {"id": ids["candidate"], "span_id": ids["span"], "key": uuid.uuid4().hex, "sha": "c" * 64, "now": now})
            connection.execute(sa.text(
                "INSERT INTO automatic_admission_decisions "
                "(id, job_id, candidate_id, retrieval_artifact_id, outcome, gate_version, policy_version, gate_results, created_at) "
                "VALUES (:id, :job_id, :candidate_id, :artifact_id, 'admitted', 'gate-v1', 'policy-v1', '{}'::json, :now)"
            ), {"id": ids["decision"], "job_id": ids["job"], "candidate_id": ids["candidate"], "artifact_id": ids["artifact"], "now": now})
            connection.execute(sa.text(
                "INSERT INTO acquisition_exceptions "
                "(id, job_id, reason_code, detail_json, created_at) "
                "VALUES (:id, :job_id, 'test_exception', '{}'::json, :now)"
            ), {"id": ids["exception"], "job_id": ids["job"], "now": now})

        rows = {
            "acquisition_job_events": ids["event"],
            "acquisition_attempts": ids["attempt"],
            "source_references": ids["reference"],
            "retrieval_artifacts": ids["artifact"],
            "retrieval_artifact_documents": ids["binding"],
            "automatic_admission_decisions": ids["decision"],
            "acquisition_exceptions": ids["exception"],
        }
        assert set(rows) == APPEND_ONLY_TABLES
        for table_name, row_id in rows.items():
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f'UPDATE "{table_name}" SET id = id WHERE id = :id'),
                        {"id": row_id},
                    )
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f'DELETE FROM "{table_name}" WHERE id = :id'),
                        {"id": row_id},
                    )

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE acquisition_jobs SET status = 'running' WHERE id = :id"),
                {"id": ids["job"]},
            )
        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT status FROM acquisition_jobs WHERE id = :id"),
                {"id": ids["job"]},
            ).scalar_one() == "running"
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
