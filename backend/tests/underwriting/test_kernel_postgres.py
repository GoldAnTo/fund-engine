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

PRODUCT_TABLES = {
    "uw_object_identity_versions",
    "uw_research_projects",
    "uw_research_project_securities",
    "uw_research_scope_versions",
    "uw_research_agenda_versions",
    "uw_price_snapshots",
    "uw_fx_snapshots",
    "uw_capital_structure_snapshots",
    "uw_security_rights_versions",
    "uw_research_assessment_versions",
    "uw_workspace_drafts",
    "uw_revision_boundaries",
    "uw_revision_manifests",
}

IMMUTABLE_PRODUCT_TABLES = PRODUCT_TABLES - {"uw_workspace_drafts"}
MANIFEST_SCHEMA = "underwriting.research-revision-manifest.v1"


def _schema_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


def _assert_0065_product_uniqueness(
    engine: sa.Engine,
    ids: dict[str, uuid.UUID],
) -> None:
    digest = "c" * 64
    successor_inserts = {
        "uw_object_identity_versions": sa.text("""
            INSERT INTO uw_object_identity_versions
              (id, object_id, version, canonical_name, symbol, exchange, share_class,
               trading_currency, effective_from, effective_to, supersedes_id,
               content_hash, created_at)
            SELECT :new_id, object_id, :version, canonical_name, symbol, exchange,
                   share_class, trading_currency, effective_from, effective_to, id,
                   :digest, created_at
            FROM uw_object_identity_versions WHERE id = :parent_id
        """),
        "uw_research_scope_versions": sa.text("""
            INSERT INTO uw_research_scope_versions
              (id, project_id, version, payload, supersedes_id, content_hash, created_at)
            SELECT :new_id, project_id, :version, payload, id, :digest, created_at
            FROM uw_research_scope_versions WHERE id = :parent_id
        """),
        "uw_research_agenda_versions": sa.text("""
            INSERT INTO uw_research_agenda_versions
              (id, project_id, version, scope_id, payload, generator_provenance,
               supersedes_id, content_hash, created_at)
            SELECT :new_id, project_id, :version, scope_id, payload,
                   generator_provenance, id, :digest, created_at
            FROM uw_research_agenda_versions WHERE id = :parent_id
        """),
        "uw_security_rights_versions": sa.text("""
            INSERT INTO uw_security_rights_versions
              (id, security_identity_id, version, economic_units, votes_per_unit,
               conversion_ratio, adr_ratio, dividend_rights_per_unit, effective_from,
               effective_to, source_id, raw_hash, supersedes_id, content_hash, created_at)
            SELECT :new_id, security_identity_id, :version, economic_units,
                   votes_per_unit, conversion_ratio, adr_ratio,
                   dividend_rights_per_unit, effective_from, effective_to, source_id,
                   raw_hash, id, :digest, created_at
            FROM uw_security_rights_versions WHERE id = :parent_id
        """),
        "uw_research_assessment_versions": sa.text("""
            INSERT INTO uw_research_assessment_versions
              (id, project_id, version, supersedes_id, answerability, direction,
               confidence, publication_status, blockers, resolution_requirements,
               next_review_at, content_hash, created_at)
            SELECT :new_id, project_id, :version, id, answerability, direction,
                   confidence, publication_status, blockers, resolution_requirements,
                   next_review_at, :digest, created_at
            FROM uw_research_assessment_versions WHERE id = :parent_id
        """),
    }
    parent_ids = {
        "uw_object_identity_versions": ids["identity"],
        "uw_research_scope_versions": ids["scope"],
        "uw_research_agenda_versions": ids["agenda"],
        "uw_security_rights_versions": ids["rights"],
        "uw_research_assessment_versions": ids["assessment"],
    }
    for table_name, statement in successor_inserts.items():
        parameters = {
            "new_id": uuid.uuid4(),
            "parent_id": parent_ids[table_name],
            "version": 2,
            "digest": digest,
        }
        with engine.begin() as connection:
            connection.execute(statement, parameters)
        with pytest.raises(sa.exc.DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    statement,
                    {**parameters, "new_id": uuid.uuid4(), "version": 3},
                )


def _assert_0065_revision_and_constraint_contracts(
    engine: sa.Engine,
    ids: dict[str, uuid.UUID],
) -> None:
    digest = "a" * 64
    first_revision = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO uw_research_versions
              (id, object_id, basis_id, version_kind, sequence, content_hash, parent_ids,
               project_id, boundary_id, manifest_id, manifest_schema,
               publication_status, created_at)
            VALUES (:id, :company, :basis, 'independent_research', 1, :digest, '[]',
                    :project, :boundary, :manifest, :manifest_schema, 'user_frozen',
                    CURRENT_TIMESTAMP)
        """), {
            **ids,
            "id": first_revision,
            "digest": digest,
            "manifest_schema": MANIFEST_SCHEMA,
        })
        assert connection.execute(
            sa.text("SELECT manifest_schema FROM uw_research_versions WHERE id = :id"),
            {"id": first_revision},
        ).scalar_one() == MANIFEST_SCHEMA

    second_project = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO uw_research_projects
              (id, primary_company_id, content_hash, created_at)
            VALUES (:id, :company, :digest, CURRENT_TIMESTAMP)
        """), {"id": second_project, "company": ids["company"], "digest": digest})
        connection.execute(sa.text("""
            INSERT INTO uw_research_versions
              (id, object_id, basis_id, version_kind, sequence, content_hash, parent_ids,
               project_id, manifest_schema, publication_status, created_at)
            VALUES (:id, :company, :basis, 'independent_research', 1, :digest, '[]',
                    :project, :schema, 'user_frozen', CURRENT_TIMESTAMP)
        """), {
            "id": uuid.uuid4(), "company": ids["company"], "basis": ids["basis"],
            "digest": digest, "project": second_project, "schema": MANIFEST_SCHEMA,
        })
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_research_versions
                  (id, object_id, basis_id, version_kind, sequence, content_hash,
                   parent_ids, project_id, created_at)
                VALUES (:id, :company, :basis, 'independent_research', 1, :digest,
                        '[]', :project, CURRENT_TIMESTAMP)
            """), {
                "id": uuid.uuid4(), "company": ids["company"], "basis": ids["basis"],
                "digest": digest, "project": second_project,
            })

    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO uw_research_versions
              (id, object_id, basis_id, version_kind, sequence, content_hash, parent_ids,
               created_at)
            VALUES (:id, :company, :basis, 'legacy-guard', 1, :digest, '[]',
                    CURRENT_TIMESTAMP)
        """), {"id": uuid.uuid4(), "company": ids["company"], "basis": ids["basis"], "digest": digest})
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_research_versions
                  (id, object_id, basis_id, version_kind, sequence, content_hash,
                   parent_ids, created_at)
                VALUES (:id, :company, :basis, 'legacy-guard', 1, :digest, '[]',
                        CURRENT_TIMESTAMP)
            """), {"id": uuid.uuid4(), "company": ids["company"], "basis": ids["basis"], "digest": digest})

    for invalid in (None, "null", "1", "{}"):
        with pytest.raises(sa.exc.DBAPIError):
            with engine.begin() as connection:
                connection.execute(sa.text("""
                    INSERT INTO uw_revision_boundaries
                      (id, project_id, historical_basis_id, mandate_id, scope_id,
                       agenda_id, price_snapshot_ids, fx_snapshot_ids,
                       capital_structure_snapshot_id, security_rights_ids,
                       schema_version, content_hash, created_at)
                    SELECT :id, project_id, historical_basis_id, mandate_id, scope_id,
                           agenda_id, CAST(:invalid AS JSON), fx_snapshot_ids,
                           capital_structure_snapshot_id, security_rights_ids,
                           schema_version, :digest, created_at
                    FROM uw_revision_boundaries WHERE id = :boundary
                """), {
                    "id": uuid.uuid4(), "invalid": invalid, "digest": "b" * 64,
                    "boundary": ids["boundary"],
                })

    cloned_boundary = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO uw_revision_boundaries
              (id, project_id, historical_basis_id, mandate_id, scope_id, agenda_id,
               price_snapshot_ids, fx_snapshot_ids, capital_structure_snapshot_id,
               security_rights_ids, schema_version, content_hash, created_at)
            SELECT :id, project_id, historical_basis_id, mandate_id, scope_id, agenda_id,
                   price_snapshot_ids, fx_snapshot_ids, capital_structure_snapshot_id,
                   security_rights_ids, schema_version, :digest, created_at
            FROM uw_revision_boundaries WHERE id = :boundary
        """), {"id": cloned_boundary, "digest": "b" * 64, "boundary": ids["boundary"]})
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_revision_manifests
                  (id, project_id, boundary_id, idempotency_key, manifest, content_hash,
                   created_at)
                VALUES (:id, :project, :boundary, :key, CAST('[]' AS JSON), :digest,
                        CURRENT_TIMESTAMP)
            """), {
                "id": uuid.uuid4(), "project": ids["project"],
                "boundary": cloned_boundary, "key": uuid.uuid4().hex, "digest": digest,
            })

    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_research_projects
                  (id, primary_company_id, content_hash, created_at)
                VALUES (:id, :company, 'short', CURRENT_TIMESTAMP)
            """), {"id": uuid.uuid4(), "company": ids["company"]})

    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_object_identity_versions
                  (id, object_id, version, canonical_name, effective_from, effective_to,
                   content_hash, created_at)
                VALUES (:id, :security, 99, 'bad', CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP - INTERVAL '1 day', :digest, CURRENT_TIMESTAMP)
            """), {"id": uuid.uuid4(), "security": ids["security"], "digest": digest})
    with pytest.raises(sa.exc.DBAPIError):
        with engine.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_research_assessment_versions
                  (id, project_id, version, answerability, direction, confidence,
                   publication_status, blockers, resolution_requirements, content_hash,
                   created_at)
                VALUES (:id, :project, 99, 'not_answerable', 'provisional_bullish',
                        'high', 'user_frozen', '[]', '[]', :digest, CURRENT_TIMESTAMP)
            """), {"id": uuid.uuid4(), "project": ids["project"], "digest": digest})

    snapshot_duplicates = {
        "uw_price_snapshots": sa.text("""
            INSERT INTO uw_price_snapshots
              (id, security_identity_id, price, currency, price_type, adjustment_basis,
               market_at, available_at, source_id, raw_hash, content_hash, created_at)
            SELECT :new_id, security_identity_id, price + 1, currency, price_type,
                   adjustment_basis, market_at, available_at, source_id, raw_hash,
                   :digest, created_at
            FROM uw_price_snapshots WHERE id = :existing_id
        """),
        "uw_fx_snapshots": sa.text("""
            INSERT INTO uw_fx_snapshots
              (id, base_currency, quote_currency, rate, quote_direction, market_at,
               available_at, source_id, raw_hash, content_hash, created_at)
            SELECT :new_id, base_currency, quote_currency, rate + 1, quote_direction,
                   market_at, available_at, source_id, raw_hash, :digest, created_at
            FROM uw_fx_snapshots WHERE id = :existing_id
        """),
        "uw_capital_structure_snapshots": sa.text("""
            INSERT INTO uw_capital_structure_snapshots
              (id, company_id, currency, cash, debt, minority_interest, investments,
               pension_liabilities, other_adjustments, basic_shares, diluted_shares,
               potential_dilution_descriptors, report_period_start, report_period_end,
               market_at, available_at, source_id, raw_hash, content_hash, created_at)
            SELECT :new_id, company_id, currency, cash + 1, debt, minority_interest,
                   investments, pension_liabilities, other_adjustments, basic_shares,
                   diluted_shares, potential_dilution_descriptors, report_period_start,
                   report_period_end, market_at, available_at, source_id, raw_hash,
                   :digest, created_at
            FROM uw_capital_structure_snapshots WHERE id = :existing_id
        """),
    }
    snapshot_ids = {
        "uw_price_snapshots": ids["price"],
        "uw_fx_snapshots": ids["fx"],
        "uw_capital_structure_snapshots": ids["capital"],
    }
    for table_name, statement in snapshot_duplicates.items():
        with pytest.raises(sa.exc.DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    statement,
                    {
                        "new_id": uuid.uuid4(),
                        "existing_id": snapshot_ids[table_name],
                        "digest": digest,
                    },
                )


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
def test_0063_candidate_tables_install_immutable_triggers() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0063_{uuid.uuid4().hex}"
    migration_url = _schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0063"],
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


@pytest.mark.pg_only
def test_0064_candidate_review_trigger_rejects_only_new_stale_inserts() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0064_{uuid.uuid4().hex}"
    migration_url = _schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0064"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True, capture_output=True, check=False,
        )
        assert migrated.returncode == 0, migrated.stderr
        with isolated.connect() as connection:
            trigger_rows = connection.execute(sa.text("""
                SELECT tgname, pg_get_triggerdef(t.oid)
                FROM pg_trigger AS t
                JOIN pg_class AS c ON c.oid = t.tgrelid
                WHERE NOT t.tgisinternal
                  AND c.relname = 'uw_evidence_candidate_review_versions'
            """)).all()
            trigger_definitions = dict(trigger_rows)
            assert {"no_update_uw_evidence_candidate_review_versions", "no_delete_uw_evidence_candidate_review_versions", "trg_uw_candidate_review_reject_superseded"}.issubset(trigger_definitions)
            assert "BEFORE INSERT" in trigger_definitions["trg_uw_candidate_review_reject_superseded"]
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.pg_only
def test_0065_product_tables_install_precise_immutable_and_draft_delete_triggers() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0065_{uuid.uuid4().hex}"
    migration_url = _schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    ids = {
        name: uuid.uuid4()
        for name in (
            "company",
            "security",
            "identity",
            "project",
            "project_security",
            "scope",
            "agenda",
            "price",
            "fx",
            "capital",
            "rights",
            "assessment",
            "draft",
            "mandate",
            "basis",
            "boundary",
            "manifest",
        )
    }
    digest = "a" * 64
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0065"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr

        with isolated.begin() as connection:
            assert PRODUCT_TABLES <= set(sa.inspect(connection).get_table_names())
            trigger_rows = connection.execute(
                sa.text(
                    """
                    SELECT c.relname, t.tgname
                    FROM pg_trigger AS t
                    JOIN pg_class AS c ON c.oid = t.tgrelid
                    WHERE NOT t.tgisinternal AND c.relname LIKE 'uw_%'
                    """
                )
            ).all()
            actual_triggers: dict[str, set[str]] = {
                table_name: set() for table_name in PRODUCT_TABLES
            }
            for table_name, trigger_name in trigger_rows:
                if table_name in actual_triggers:
                    actual_triggers[table_name].add(trigger_name)
            for table_name in IMMUTABLE_PRODUCT_TABLES:
                assert actual_triggers[table_name] == {
                    f"no_update_{table_name}",
                    f"no_delete_{table_name}",
                }
            assert actual_triggers["uw_workspace_drafts"] == {
                "no_delete_uw_workspace_drafts"
            }

            connection.execute(sa.text("""
                INSERT INTO uw_research_objects
                  (id, kind, external_key, canonical_name, created_at) VALUES
                  (:company, 'company', '0065-company', 'Company', CURRENT_TIMESTAMP),
                  (:security, 'security', '0065-security', 'Security', CURRENT_TIMESTAMP)
            """), ids)
            connection.execute(sa.text("""
                INSERT INTO uw_object_identity_versions
                  (id, object_id, version, canonical_name, symbol, exchange, share_class,
                   trading_currency, effective_from, content_hash, created_at)
                VALUES (:identity, :security, 1, 'Security', 'SEC', 'TEST', 'ordinary',
                        'CNY', CURRENT_TIMESTAMP, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_research_projects
                  (id, primary_company_id, content_hash, created_at)
                VALUES (:project, :company, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_research_project_securities
                  (id, project_id, security_id, content_hash, created_at)
                VALUES (:project_security, :project, :security, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_research_scope_versions
                  (id, project_id, version, payload, content_hash, created_at)
                VALUES (:scope, :project, 1, '{}', :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_research_agenda_versions
                  (id, project_id, version, scope_id, payload, generator_provenance,
                   content_hash, created_at)
                VALUES (:agenda, :project, 1, :scope, '{}', '{}', :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_price_snapshots
                  (id, security_identity_id, price, currency, price_type, adjustment_basis,
                   market_at, available_at, source_id, raw_hash, content_hash, created_at)
                VALUES (:price, :security, 10, 'CNY', 'close', 'unadjusted',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test', :digest, :digest,
                        CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_fx_snapshots
                  (id, base_currency, quote_currency, rate, quote_direction, market_at,
                   available_at, source_id, raw_hash, content_hash, created_at)
                VALUES (:fx, 'USD', 'CNY', 7, 'quote_per_base', CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, 'test', :digest, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_capital_structure_snapshots
                  (id, company_id, currency, cash, debt, minority_interest, investments,
                   pension_liabilities, other_adjustments, basic_shares, diluted_shares,
                   potential_dilution_descriptors, report_period_start, report_period_end,
                   market_at, available_at, source_id, raw_hash, content_hash, created_at)
                VALUES (:capital, :company, 'CNY', 1, 1, 0, 0, 0, 0, 10, 11, '[]',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, 'test', :digest, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_security_rights_versions
                  (id, security_identity_id, version, economic_units, votes_per_unit,
                   conversion_ratio, adr_ratio, dividend_rights_per_unit, effective_from,
                   source_id, raw_hash, content_hash, created_at)
                VALUES (:rights, :security, 1, 1, 0, 1, 1, 0, CURRENT_TIMESTAMP,
                        'test', :digest, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_research_assessment_versions
                  (id, project_id, version, answerability, direction, confidence,
                   publication_status, blockers, resolution_requirements, content_hash,
                   created_at)
                VALUES (:assessment, :project, 1, 'not_answerable', NULL, NULL,
                        'user_frozen', '[]', '[]', :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_workspace_drafts
                  (id, project_id, lock_version, content, created_at, updated_at)
                VALUES (:draft, :project, 1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), ids)
            connection.execute(sa.text("""
                INSERT INTO uw_mandate_versions
                  (id, mandate_key, version, horizon_years, base_currency, required_return,
                   permanent_loss_limit, comparison_set, created_at)
                VALUES (:mandate, '0065', 1, 3, 'CNY', 0.1, 0.2, '[]', CURRENT_TIMESTAMP)
            """), ids)
            connection.execute(sa.text("""
                INSERT INTO uw_historical_bases
                  (id, cutoff, source_manifest_hash, created_at)
                VALUES (:basis, CURRENT_TIMESTAMP, :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_revision_boundaries
                  (id, project_id, historical_basis_id, mandate_id, scope_id, agenda_id,
                   price_snapshot_ids, fx_snapshot_ids, capital_structure_snapshot_id,
                   security_rights_ids, schema_version, content_hash, created_at)
                VALUES (:boundary, :project, :basis, :mandate, :scope, :agenda, '[]', '[]',
                        :capital, '[]', '1', :digest, CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_revision_manifests
                  (id, project_id, boundary_id, idempotency_key, manifest, content_hash,
                   created_at)
                VALUES (:manifest, :project, :boundary, '0065-retry', '{}', :digest,
                        CURRENT_TIMESTAMP)
            """), {**ids, "digest": digest})

        _assert_0065_product_uniqueness(isolated, ids)
        _assert_0065_revision_and_constraint_contracts(isolated, ids)

        immutable_ids = {
            "uw_object_identity_versions": ids["identity"],
            "uw_research_projects": ids["project"],
            "uw_research_project_securities": ids["project_security"],
            "uw_research_scope_versions": ids["scope"],
            "uw_research_agenda_versions": ids["agenda"],
            "uw_price_snapshots": ids["price"],
            "uw_fx_snapshots": ids["fx"],
            "uw_capital_structure_snapshots": ids["capital"],
            "uw_security_rights_versions": ids["rights"],
            "uw_research_assessment_versions": ids["assessment"],
            "uw_revision_boundaries": ids["boundary"],
            "uw_revision_manifests": ids["manifest"],
        }
        assert set(immutable_ids) == IMMUTABLE_PRODUCT_TABLES
        for table_name, row_id in immutable_ids.items():
            with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
                with isolated.begin() as connection:
                    connection.execute(
                        sa.text(
                            f"UPDATE {table_name} SET content_hash = :digest WHERE id = :id"
                        ),
                        {"digest": "b" * 64, "id": row_id},
                    )
            with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
                with isolated.begin() as connection:
                    connection.execute(
                        sa.text(f"DELETE FROM {table_name} WHERE id = :id"),
                        {"id": row_id},
                    )

        with isolated.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE uw_workspace_drafts SET lock_version = 2 WHERE id = :id"
                ),
                {"id": ids["draft"]},
            )
            assert connection.execute(
                sa.text("SELECT lock_version FROM uw_workspace_drafts WHERE id = :id"),
                {"id": ids["draft"]},
            ).scalar_one() == 2
        with pytest.raises(sa.exc.DBAPIError, match="append-only|immutable"):
            with isolated.begin() as connection:
                connection.execute(
                    sa.text("DELETE FROM uw_workspace_drafts WHERE id = :id"),
                    {"id": ids["draft"]},
                )
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.pg_only
def test_0065_downgrade_removes_only_product_foundation_and_restores_legacy_unique() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"underwriting_0065_down_{uuid.uuid4().hex}"
    migration_url = _schema_url(database_url, schema)
    admin = sa.create_engine(database_url, future=True)
    isolated = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[2]
    company, basis, revision = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    digest = "a" * 64
    try:
        with admin.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0065"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr
        with isolated.begin() as connection:
            connection.execute(sa.text("""
                INSERT INTO uw_research_objects
                  (id, kind, external_key, canonical_name, created_at)
                VALUES (:company, 'company', 'downgrade-company', 'Company',
                        CURRENT_TIMESTAMP)
            """), {"company": company})
            connection.execute(sa.text("""
                INSERT INTO uw_historical_bases
                  (id, cutoff, source_manifest_hash, created_at)
                VALUES (:basis, CURRENT_TIMESTAMP, :digest, CURRENT_TIMESTAMP)
            """), {"basis": basis, "digest": digest})
            connection.execute(sa.text("""
                INSERT INTO uw_research_versions
                  (id, object_id, basis_id, version_kind, sequence, content_hash,
                   parent_ids, created_at)
                VALUES (:revision, :company, :basis, 'legacy', 1, :digest, '[]',
                        CURRENT_TIMESTAMP)
            """), {
                "revision": revision, "company": company, "basis": basis, "digest": digest,
            })
        downgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0064"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert downgraded.returncode == 0, downgraded.stderr
        with isolated.connect() as connection:
            inspector = sa.inspect(connection)
            assert not PRODUCT_TABLES & set(inspector.get_table_names())
            assert "project_id" not in {
                column["name"]
                for column in inspector.get_columns("uw_research_versions")
            }
            assert "uq_uw_research_version_sequence" in {
                item["name"]
                for item in inspector.get_unique_constraints("uw_research_versions")
            }
            assert not {
                "uq_uw_research_version_legacy_sequence",
                "uq_uw_research_version_project_sequence",
                "ix_uw_research_versions_project",
            } & {item["name"] for item in inspector.get_indexes("uw_research_versions")}
            assert connection.execute(
                sa.text("SELECT content_hash FROM uw_research_versions WHERE id = :id"),
                {"id": revision},
            ).scalar_one() == digest
        with pytest.raises(sa.exc.DBAPIError):
            with isolated.begin() as connection:
                connection.execute(sa.text("""
                    INSERT INTO uw_research_versions
                      (id, object_id, basis_id, version_kind, sequence, content_hash,
                       parent_ids, created_at)
                    VALUES (:id, :company, :basis, 'legacy', 1, :digest, '[]',
                            CURRENT_TIMESTAMP)
                """), {
                    "id": uuid.uuid4(), "company": company, "basis": basis, "digest": digest,
                })
    finally:
        isolated.dispose()
        with admin.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
