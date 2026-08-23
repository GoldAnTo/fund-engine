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
    "uw_evidence_candidate_dossier_versions",
    "uw_evidence_candidate_review_versions",
}


def _schema_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


def _insert_immutable_records(connection: sa.Connection) -> dict[str, tuple[uuid.UUID, str, object]]:
    """Insert one valid dependency graph so every immutable table can be attacked."""
    ids = {name: uuid.uuid4() for name in (
        "industry", "company", "security", "relation", "mandate", "basis", "source",
        "definition", "observation", "mechanism", "industry_state", "scenario", "exposure",
        "earnings", "forecast", "falsifier", "dossier", "review", "ledger", "research", "answerability",
    )}
    digest = "a" * 64
    connection.execute(sa.text("""
        INSERT INTO uw_research_objects (id, kind, external_key, canonical_name, created_at) VALUES
        (:industry, 'industry', 'POWER_BATTERY:GLOBAL', 'Power battery', CURRENT_TIMESTAMP),
        (:company, 'company', 'CN:300750:COMPANY', 'CATL', CURRENT_TIMESTAMP),
        (:security, 'security', 'SZSE:300750', 'CATL A', CURRENT_TIMESTAMP)
    """), ids)
    connection.execute(sa.text("""
        INSERT INTO uw_object_relations (id, parent_id, child_id, relation_type, created_at)
        VALUES (:relation, :industry, :company, 'industry_member', CURRENT_TIMESTAMP)
    """), ids)
    connection.execute(sa.text("""
        INSERT INTO uw_mandate_versions
          (id, mandate_key, version, horizon_years, base_currency, required_return, permanent_loss_limit, comparison_set, created_at)
        VALUES (:mandate, 'pg-test', 1, 3, 'CNY', 0.10, 0.20, '[]', CURRENT_TIMESTAMP)
    """), ids)
    connection.execute(sa.text("""
        INSERT INTO uw_historical_bases (id, cutoff, price_as_of, source_manifest_hash, created_at)
        VALUES (:basis, CURRENT_TIMESTAMP, NULL, :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_source_manifest_versions
          (id, manifest_key, version, basis_id, manifest, manifest_hash, content_hash, created_at)
        VALUES (:source, 'pg-source', 1, :basis, '{}', :digest, :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_metric_definition_versions
          (id, metric_key, version, basis_id, source_manifest_id, definition, unit, period_semantics, source_role, aggregation, reconciliation_tolerance, content_hash, created_at)
        VALUES (:definition, 'pg.metric', 1, :basis, :source, '{}', 'CNY', 'annual', 'company_filing', 'sum', 0, :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_metric_observations
          (id, metric_key, definition_version, version, basis_id, definition_id, source_manifest_id, source_id, value, unit, observed_start, observed_end, effective_at, available_at, source_locator, dimensions, dimension_hash, content_hash, created_at)
        VALUES (:observation, 'pg.metric', 1, 1, :basis, :definition, :source, 'pg-source', 1, 'CNY', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'pg', '{}', :digest, :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_mechanism_pack_versions
          (id, mechanism_key, version, object_id, basis_id, source_manifest_id, status, payload, source_ids, definition_ids, content_hash, created_at)
        VALUES (:mechanism, 'pg.mechanism', 1, :company, :basis, :source, 'candidate', '{}', '[]', '[]', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_industry_state_versions (id, object_id, version, basis_id, mechanism_id, payload, content_hash, created_at)
        VALUES (:industry_state, :industry, 1, :basis, :mechanism, '{}', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_industry_scenario_versions (id, scenario_key, version, basis_id, industry_state_id, payload, content_hash, created_at)
        VALUES (:scenario, 'base', 1, :basis, :industry_state, '{}', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_company_exposure_versions (id, company_id, industry_state_id, exposure_key, version, basis_id, payload, content_hash, created_at)
        VALUES (:exposure, :company, :industry_state, 'pg.exposure', 1, :basis, '{}', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_earnings_engine_versions (id, company_id, version, basis_id, industry_state_id, payload, content_hash, created_at)
        VALUES (:earnings, :company, 1, :basis, :industry_state, '{}', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_forecast_input_versions (id, company_id, input_key, version, basis_id, earnings_engine_id, payload, content_hash, created_at)
        VALUES (:forecast, :company, 'pg.input', 1, :basis, :earnings, '{}', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_falsifier_versions (id, mechanism_id, falsifier_key, version, basis_id, payload, content_hash, created_at)
        VALUES (:falsifier, :mechanism, 'pg.falsifier', 1, :basis, '{}', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_evidence_candidate_dossier_versions
          (id, dossier_key, version, object_id, basis_id, source_manifest_id, scope_statement, purpose, status, rejected_calculations, payload, source_manifest_hash, content_hash, created_at)
        VALUES (:dossier, 'pg.candidate', 1, :industry, :basis, :source, 'global batteries', 'evidence_candidate', 'candidate', '["output / nominal capacity"]', '{}', :digest, :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_evidence_candidate_review_versions
          (id, dossier_id, dossier_content_hash, reviewer_identity, reviewer_role, decision, rationale, payload, content_hash, reviewed_at, created_at)
        VALUES (:review, :dossier, :digest, 'reviewer:pg', 'provenance', 'approve', 'verified', '{}', :digest, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_ledger_entries
          (id, object_id, basis_id, ledger_kind, family_key, entry_type, version, payload, effective_at, available_at, source_boundary, content_hash, created_at)
        VALUES (:ledger, :company, :basis, 'reality', 'pg', 'metric', 1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'pg', :digest, CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_research_versions (id, object_id, basis_id, version_kind, sequence, content_hash, parent_ids, created_at)
        VALUES (:research, :company, :basis, 'pg', 1, :digest, '[]', CURRENT_TIMESTAMP)
    """), {**ids, "digest": digest})
    connection.execute(sa.text("""
        INSERT INTO uw_answerability_evaluations
          (id, object_id, basis_id, version, state, blockers, research_debt_keys, resolvable_within_mandate, allowed_action, resolution_requirements, created_at)
        VALUES (:answerability, :company, :basis, 1, 'not_answerable', '[]', '[]', true, 'wait_for_validation', '[]', CURRENT_TIMESTAMP)
    """), ids)
    return {
        "uw_research_objects": (ids["company"], "canonical_name", "changed"),
        "uw_object_relations": (ids["relation"], "relation_type", "changed"),
        "uw_mandate_versions": (ids["mandate"], "mandate_key", "changed"),
        "uw_historical_bases": (ids["basis"], "source_manifest_hash", "b" * 64),
        "uw_ledger_entries": (ids["ledger"], "content_hash", "b" * 64),
        "uw_research_versions": (ids["research"], "content_hash", "b" * 64),
        "uw_answerability_evaluations": (ids["answerability"], "state", "partially_answerable"),
        "uw_source_manifest_versions": (ids["source"], "content_hash", "b" * 64),
        "uw_metric_definition_versions": (ids["definition"], "content_hash", "b" * 64),
        "uw_metric_observations": (ids["observation"], "content_hash", "b" * 64),
        "uw_mechanism_pack_versions": (ids["mechanism"], "content_hash", "b" * 64),
        "uw_industry_state_versions": (ids["industry_state"], "content_hash", "b" * 64),
        "uw_industry_scenario_versions": (ids["scenario"], "content_hash", "b" * 64),
        "uw_company_exposure_versions": (ids["exposure"], "content_hash", "b" * 64),
        "uw_earnings_engine_versions": (ids["earnings"], "content_hash", "b" * 64),
        "uw_forecast_input_versions": (ids["forecast"], "content_hash", "b" * 64),
        "uw_falsifier_versions": (ids["falsifier"], "content_hash", "b" * 64),
        "uw_evidence_candidate_dossier_versions": (ids["dossier"], "content_hash", "b" * 64),
        "uw_evidence_candidate_review_versions": (ids["review"], "content_hash", "b" * 64),
    }


@pytest.mark.pg_only
def test_0062_candidate_tables_install_immutable_triggers() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0062_{uuid.uuid4().hex}"
    migration_url = _schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0062"],
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
            rows = _insert_immutable_records(connection)
        assert set(rows) == TABLES
        for table, (row_id, column, value) in rows.items():
            with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
                with isolated.begin() as connection:
                    connection.execute(
                        sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                        {"id": row_id, "value": value},
                    )
            with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
                with isolated.begin() as connection:
                    connection.execute(
                        sa.text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id}
                    )
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
