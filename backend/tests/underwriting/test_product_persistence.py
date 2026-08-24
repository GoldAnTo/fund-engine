from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, delete, inspect, update

from app.models import Base
import app.underwriting.persistence as persistence
from app.models.ledger import (
    DELETE_PROTECTED_TABLES,
    IMMUTABLE_TABLES,
    ImmutableLedgerError,
)


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

PRODUCT_MODEL_NAMES = {
    "UnderwritingObjectIdentityVersion",
    "UnderwritingResearchProject",
    "UnderwritingResearchProjectSecurity",
    "UnderwritingResearchScopeVersion",
    "UnderwritingResearchAgendaVersion",
    "UnderwritingPriceSnapshot",
    "UnderwritingFXSnapshot",
    "UnderwritingCapitalStructureSnapshot",
    "UnderwritingSecurityRightsVersion",
    "UnderwritingResearchAssessmentVersion",
    "UnderwritingWorkspaceDraft",
    "UnderwritingRevisionBoundary",
    "UnderwritingRevisionManifest",
}


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


def _foreign_key_targets(table_name: str) -> set[str]:
    return {
        foreign_key.target_fullname
        for foreign_key in Base.metadata.tables[table_name].foreign_keys
    }


def test_exact_product_table_set_is_registered_with_metadata() -> None:
    registered = {
        name
        for name in Base.metadata.tables
        if name in PRODUCT_TABLES or name.startswith("uw_workspace_")
    }
    assert registered == PRODUCT_TABLES


def test_product_orm_models_are_exported_and_mapped() -> None:
    for model_name in PRODUCT_MODEL_NAMES:
        model = getattr(persistence, model_name, None)
        assert model is not None, model_name
        assert model.__table__.metadata is Base.metadata


def test_product_tables_have_the_required_guard_registration() -> None:
    assert IMMUTABLE_PRODUCT_TABLES <= IMMUTABLE_TABLES
    assert "uw_workspace_drafts" not in IMMUTABLE_TABLES
    assert DELETE_PROTECTED_TABLES == frozenset({"uw_workspace_drafts"})


def test_product_immutable_tables_reject_update_and_delete() -> None:
    engine = create_engine("sqlite://")
    for table_name in IMMUTABLE_PRODUCT_TABLES:
        table = Base.metadata.tables[table_name]
        with pytest.raises(ImmutableLedgerError):
            with engine.begin() as connection:
                connection.execute(update(table).values(id=table.c.id))
        with pytest.raises(ImmutableLedgerError):
            with engine.begin() as connection:
                connection.execute(delete(table))


def test_workspace_draft_allows_update_but_rejects_delete() -> None:
    engine = create_engine("sqlite://")
    table = Base.metadata.tables["uw_workspace_drafts"]
    Base.metadata.create_all(engine, tables=[table])
    try:
        with engine.begin() as connection:
            connection.execute(update(table).values(lock_version=table.c.lock_version + 1))
        with pytest.raises(ImmutableLedgerError, match="DELETE"):
            with engine.begin() as connection:
                connection.execute(delete(table))
    finally:
        Base.metadata.drop_all(engine, tables=[table])


def test_versioned_product_families_prevent_duplicate_versions_and_successors() -> None:
    expected_uniques = {
        "uw_object_identity_versions": {
            ("object_id", "version"),
            ("supersedes_id",),
        },
        "uw_research_scope_versions": {
            ("project_id", "version"),
            ("supersedes_id",),
        },
        "uw_research_agenda_versions": {
            ("project_id", "version"),
            ("supersedes_id",),
        },
        "uw_security_rights_versions": {
            ("security_identity_id", "version"),
            ("supersedes_id",),
        },
        "uw_research_assessment_versions": {
            ("project_id", "version"),
            ("supersedes_id",),
        },
    }
    for table_name, required in expected_uniques.items():
        assert required <= _unique_columns(table_name)


def test_product_natural_identities_and_revision_retry_identity_are_unique() -> None:
    assert ("project_id", "security_id") in _unique_columns(
        "uw_research_project_securities"
    )
    assert ("project_id",) in _unique_columns("uw_workspace_drafts")
    assert ("project_id", "idempotency_key") in _unique_columns(
        "uw_revision_manifests"
    )
    assert ("boundary_id",) in _unique_columns("uw_revision_manifests")
    for table_name in (
        "uw_price_snapshots",
        "uw_fx_snapshots",
        "uw_capital_structure_snapshots",
    ):
        assert _unique_columns(table_name), table_name


def test_product_tables_expose_content_hash_except_mutable_draft() -> None:
    for table_name in IMMUTABLE_PRODUCT_TABLES:
        column = Base.metadata.tables[table_name].c.content_hash
        assert column.nullable is False
        assert column.type.length == 64
    assert "content_hash" not in Base.metadata.tables["uw_workspace_drafts"].c


def test_product_constraints_cover_intervals_currencies_numbers_and_states() -> None:
    checks = {
        table_name: " ".join(
            str(constraint.sqltext)
            for constraint in Base.metadata.tables[table_name].constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        )
        for table_name in PRODUCT_TABLES
    }
    assert "effective_to" in checks["uw_object_identity_versions"]
    assert "trading_currency" in checks["uw_object_identity_versions"]
    assert "price > 0" in checks["uw_price_snapshots"]
    assert "base_currency <> quote_currency" in checks["uw_fx_snapshots"]
    assert "rate > 0" in checks["uw_fx_snapshots"]
    assert "diluted_shares >= basic_shares" in checks["uw_capital_structure_snapshots"]
    assert "basic_shares > 0" in checks["uw_capital_structure_snapshots"]
    assert "votes_per_unit >= 0" in checks["uw_security_rights_versions"]
    assert "economic_units > 0" in checks["uw_security_rights_versions"]
    assert "publication_status IN ('user_frozen', 'superseded')" in checks[
        "uw_research_assessment_versions"
    ]
    assert "not_answerable" in checks["uw_research_assessment_versions"]
    assert "lock_version >= 1" in checks["uw_workspace_drafts"]
    assert "length(trim(idempotency_key)) > 0" in checks["uw_revision_manifests"]


def test_compatibility_columns_are_nullable_and_have_required_foreign_keys() -> None:
    expected_columns = {
        "uw_mandate_versions": {
            "project_id",
            "benchmark_key",
            "required_excess_return",
            "effective_at",
            "expires_at",
            "content_hash",
        },
        "uw_historical_bases": {
            "definition_bundle_hash",
            "parser_bundle_hash",
            "boundary_schema_version",
            "content_hash",
        },
        "uw_research_versions": {
            "project_id",
            "boundary_id",
            "manifest_id",
            "manifest_schema",
            "publication_status",
        },
    }
    for table_name, columns in expected_columns.items():
        table = Base.metadata.tables[table_name]
        assert columns <= set(table.c.keys())
        assert all(table.c[column].nullable for column in columns)

    assert "uw_research_projects.id" in _foreign_key_targets("uw_mandate_versions")
    assert {
        "uw_research_projects.id",
        "uw_revision_boundaries.id",
        "uw_revision_manifests.id",
    } <= _foreign_key_targets("uw_research_versions")


def test_product_dependency_graph_can_create_and_drop_in_sqlite() -> None:
    engine = create_engine("sqlite://")
    dependency_tables = {
        "uw_research_objects",
        "uw_mandate_versions",
        "uw_historical_bases",
        "uw_research_versions",
    }
    tables = [
        Base.metadata.tables[name] for name in PRODUCT_TABLES | dependency_tables
    ]

    Base.metadata.create_all(engine, tables=tables)
    assert PRODUCT_TABLES <= set(inspect(engine).get_table_names())
    Base.metadata.drop_all(engine, tables=tables)
    assert not PRODUCT_TABLES & set(inspect(engine).get_table_names())


def test_0065_migration_source_is_additive_and_has_no_legacy_backfill() -> None:
    migration = Path(__file__).parents[2] / "alembic/versions/0065_investment_research_product_foundation.py"
    source = migration.read_text()
    normalized = " ".join(source.lower().split())

    assert 'revision: str = "0065"' in source
    assert 'down_revision: union[str, none] = "0064"' in source.lower()
    assert "delete from uw_research_versions" not in normalized
    assert "update uw_mandate_versions" not in normalized
    assert "update uw_historical_bases" not in normalized
    assert "update uw_research_versions" not in normalized
    assert "backfill" not in normalized


def test_0064_to_0065_sqlite_upgrade_preserves_legacy_rows_and_nulls_new_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "product-foundation.db"
    backend = Path(__file__).parents[2]
    database_url = f"sqlite:///{database_path}"
    environment = {**os.environ, "DATABASE_URL": database_url}

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0064"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr

    ids = {name: uuid.uuid4().hex for name in ("company", "mandate", "basis", "revision")}
    digest = "a" * 64
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_objects "
                "(id, kind, external_key, canonical_name, created_at) "
                "VALUES (:company, 'company', 'legacy-company', 'Legacy Company', CURRENT_TIMESTAMP)"
            ),
            ids,
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_mandate_versions "
                "(id, mandate_key, version, horizon_years, base_currency, required_return, "
                "permanent_loss_limit, comparison_set, created_at) "
                "VALUES (:mandate, 'legacy-mandate', 1, 3, 'CNY', 0.10, 0.20, '[]', CURRENT_TIMESTAMP)"
            ),
            ids,
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_historical_bases "
                "(id, cutoff, price_as_of, source_manifest_hash, created_at) "
                "VALUES (:basis, CURRENT_TIMESTAMP, NULL, :digest, CURRENT_TIMESTAMP)"
            ),
            {**ids, "digest": digest},
        )
        connection.execute(
            sa.text(
                "INSERT INTO uw_research_versions "
                "(id, object_id, basis_id, version_kind, sequence, content_hash, parent_ids, created_at) "
                "VALUES (:revision, :company, :basis, 'legacy', 1, :digest, '[]', CURRENT_TIMESTAMP)"
            ),
            {**ids, "digest": digest},
        )

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0065"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert migrated.returncode == 0, migrated.stderr

    with engine.connect() as connection:
        mandate = connection.execute(
            sa.text(
                "SELECT mandate_key, project_id, benchmark_key, required_excess_return, "
                "effective_at, expires_at, content_hash FROM uw_mandate_versions "
                "WHERE id = :id"
            ),
            {"id": ids["mandate"]},
        ).one()
        assert mandate[0] == "legacy-mandate"
        assert mandate[1:] == (None, None, None, None, None, None)

        basis = connection.execute(
            sa.text(
                "SELECT source_manifest_hash, definition_bundle_hash, parser_bundle_hash, "
                "boundary_schema_version, content_hash FROM uw_historical_bases WHERE id = :id"
            ),
            {"id": ids["basis"]},
        ).one()
        assert basis == (digest, None, None, None, None)

        revision = connection.execute(
            sa.text(
                "SELECT content_hash, parent_ids, project_id, boundary_id, manifest_id, "
                "manifest_schema, publication_status FROM uw_research_versions WHERE id = :id"
            ),
            {"id": ids["revision"]},
        ).one()
        assert revision[0] == digest
        assert revision[1] == "[]"
        assert revision[2:] == (None, None, None, None, None)

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0064"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert not PRODUCT_TABLES & set(inspector.get_table_names())
        assert "project_id" not in {
            column["name"] for column in inspector.get_columns("uw_mandate_versions")
        }
        assert connection.execute(
            sa.text("SELECT content_hash FROM uw_research_versions WHERE id = :id"),
            {"id": ids["revision"]},
        ).scalar_one() == digest
    engine.dispose()
