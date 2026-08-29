"""Explicit schema bootstrap for runnable research environments.

Application services never create tables implicitly. A runnable database is
always brought to the Alembic head first so its version is inspectable and a
subsequent release has a well-defined upgrade path.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401 - ensure the complete metadata is registered
from app.company_research_event_schema import (
    CompanyResearchEventSchemaError,
    repair_company_event_schema,
)
from app.models.ledger import Base


class UnmanagedDatabaseSchemaError(RuntimeError):
    """An existing database cannot be safely adopted as the current schema."""


_COMPANY_EVENTS = "uw_company_research_events"
_COMPANY_WORKER_INDEX = "ix_jobs_company_research_worker_candidates"
_COMPANY_WORKER_INDEX_COLUMNS = ("status", "created_at", "id")
_COMPANY_WORKER_INDEX_PREDICATE = (
    "kind = 'prepare_company_research' AND "
    "target_type = 'company_research_preparation' AND research_case_id IS NULL"
)


def upgrade_database_to_head(database_url: str) -> None:
    """Upgrade ``database_url`` using this repository's Alembic history."""
    backend_root = Path(__file__).parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    # ``script_location`` in alembic.ini is relative to the caller's CWD.
    # Bootstrap is invoked by scripts and tests from outside ``backend/``, so
    # bind it to this module's repository location instead.
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names())
        if actual_tables and "alembic_version" not in actual_tables:
            _require_current_metadata(inspector, actual_tables)
            # Direct ORM-created demo databases predate migration management.
            # Only a structurally complete one is adopted; unknown schemas are
            # rejected instead of being falsely marked current.
            _repair_company_event_schema(engine)
            _install_company_worker_index(engine)
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
        # Reinstall and behavior-check the canonical append-only boundary even
        # when Alembic was already at head.  This repairs later trigger loss or
        # a trigger with a trusted name but untrusted body.
        _repair_company_event_schema(engine)
        _require_company_worker_index(engine)
    finally:
        engine.dispose()


def _require_current_metadata(inspector, actual_tables: set[str]) -> None:
    expected = {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }
    missing_tables = sorted(set(expected) - actual_tables)
    missing_columns = {
        table: sorted(
            columns
            - {column["name"] for column in inspector.get_columns(table)}
        )
        for table, columns in expected.items()
        if table in actual_tables
        and columns - {column["name"] for column in inspector.get_columns(table)}
    }
    if missing_tables or missing_columns:
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and is not compatible with "
            f"the current schema; missing_tables={missing_tables}, "
            f"missing_columns={missing_columns}"
        )
    _require_company_event_relational_contract(inspector)


def _require_company_event_relational_contract(inspector) -> None:
    company_event_columns = {
        column["name"]: column
        for column in inspector.get_columns(_COMPANY_EVENTS)
    }
    company_event_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(_COMPANY_EVENTS)
    }
    company_event_indexes = {
        index["name"] for index in inspector.get_indexes(_COMPANY_EVENTS)
    }
    company_event_uniques = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(_COMPANY_EVENTS)
    }
    company_event_foreign_keys = {
        (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
        )
        for constraint in inspector.get_foreign_keys(_COMPANY_EVENTS)
    }
    required_checks = {
        "ck_uw_company_research_event_content_hash",
        "ck_uw_company_research_event_sequence",
        "ck_uw_company_research_event_hash_version",
        "ck_uw_company_research_event_predecessor_hash",
        "ck_uw_company_research_event_payload_shape",
    }
    required_indexes = {"ix_uw_company_research_event_preparation"}
    expected_columns = {
        "id",
        "preparation_id",
        "sequence",
        "hash_version",
        "previous_event_hash",
        "event_type",
        "payload",
        "content_hash",
        "created_at",
    }
    incompatible_extra_columns = {
        name
        for name, column in company_event_columns.items()
        if name not in expected_columns
        and (
            column.get("computed") is not None
            or column.get("identity") is not None
            or (
                not column.get("nullable", True)
                and column.get("default") is None
            )
        )
    }
    if (
        set(company_event_columns) < expected_columns
        or incompatible_extra_columns
        or company_event_columns["hash_version"]["nullable"]
        or company_event_columns["hash_version"].get("default") is not None
        or not required_checks.issubset(company_event_checks)
        or not required_indexes.issubset(company_event_indexes)
        or "uq_uw_company_research_event_sequence" not in company_event_uniques
        or (
            ("preparation_id",),
            "uw_company_research_preparations",
            ("id",),
        )
        not in company_event_foreign_keys
    ):
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and is not compatible with "
            "the current company research event schema"
        )


def _repair_company_event_schema(engine) -> None:
    with engine.begin() as connection:
        try:
            repair_company_event_schema(connection)
        except (CompanyResearchEventSchemaError, ValueError, TypeError) as exc:
            raise UnmanagedDatabaseSchemaError(
                "cannot authenticate or repair company research event schema"
            ) from exc


def _install_company_worker_index(engine) -> None:
    """Install the 0070 worker index for ORM-created unmanaged databases."""
    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX IF EXISTS {_COMPANY_WORKER_INDEX}"))
        connection.execute(
            text(
                f"CREATE INDEX {_COMPANY_WORKER_INDEX} ON jobs "
                f"(status, created_at, id) WHERE {_COMPANY_WORKER_INDEX_PREDICATE}"
            )
        )


def _require_company_worker_index(engine) -> None:
    inspector = inspect(engine)
    indexes = {
        index["name"]: index for index in inspector.get_indexes("jobs")
    }
    index = indexes.get(_COMPANY_WORKER_INDEX)
    if (
        index is None
        or tuple(index.get("column_names") or ())
        != _COMPANY_WORKER_INDEX_COLUMNS
        or index.get("unique", False)
    ):
        raise UnmanagedDatabaseSchemaError(
            "database lacks the company research worker candidate index"
        )
    dialect_options = index.get("dialect_options") or {}
    predicate = dialect_options.get(
        "sqlite_where"
        if engine.dialect.name == "sqlite"
        else "postgresql_where"
    )
    if predicate is None:
        raise UnmanagedDatabaseSchemaError(
            "database lacks the company research worker candidate index"
        )
    normalized = " ".join(str(predicate).lower().split())
    if not all(
        fragment in normalized
        for fragment in (
            "kind",
            "prepare_company_research",
            "target_type",
            "company_research_preparation",
            "research_case_id",
            "is null",
        )
    ):
        raise UnmanagedDatabaseSchemaError(
            "database has an invalid company research worker candidate index"
        )


def require_company_research_event_schema(database_url: str) -> None:
    """Fail startup closed unless the timestamp-bound event schema is active."""
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if "alembic_version" not in tables or _COMPANY_EVENTS not in tables:
            raise UnmanagedDatabaseSchemaError(
                "database is not at the company research event schema"
            )
        columns = {
            column["name"]: column
            for column in inspector.get_columns(_COMPANY_EVENTS)
        }
        if (
            "hash_version" not in columns
            or columns["hash_version"]["nullable"]
            or columns["hash_version"].get("default") is not None
        ):
            raise UnmanagedDatabaseSchemaError(
                "database is not at the company research event schema"
            )
        _require_company_event_relational_contract(inspector)
        _repair_company_event_schema(engine)
    finally:
        engine.dispose()
