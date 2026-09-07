"""Explicit schema bootstrap for runnable research environments.

Application services never create tables implicitly. A runnable database is
always brought to the Alembic head first so its version is inspectable and a
subsequent release has a well-defined upgrade path.
"""
from __future__ import annotations

from collections.abc import Mapping
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
_POSTGRESQL_COMPANY_WORKER_INDEX_STATE_SQL = (
    "SELECT i.indisvalid, i.indisready, am.amname AS access_method, "
    "i.indnkeyatts, "
    "ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY "
    "AS key(attnum, ordinal) JOIN pg_attribute a "
    "ON a.attrelid = table_rel.oid AND a.attnum = key.attnum "
    "WHERE key.ordinal <= i.indnkeyatts ORDER BY key.ordinal) "
    "AS key_columns, "
    "ARRAY(SELECT (i.indoption[ordinal - 1] & 1) <> 0 "
    "FROM generate_series(1, i.indnkeyatts) AS ordinal "
    "ORDER BY ordinal) AS descending, "
    "ARRAY(SELECT (i.indoption[ordinal - 1] & 2) <> 0 "
    "FROM generate_series(1, i.indnkeyatts) AS ordinal "
    "ORDER BY ordinal) AS nulls_first "
    "FROM pg_index i JOIN pg_class index_rel "
    "ON index_rel.oid = i.indexrelid JOIN pg_class table_rel "
    "ON table_rel.oid = i.indrelid JOIN pg_am am "
    "ON am.oid = index_rel.relam WHERE i.indrelid = to_regclass(:table_name) "
    "AND index_rel.relname = :index_name"
)


def _normalize_company_worker_predicate(value: object) -> str:
    """Normalize SQL syntax without changing case-sensitive literals."""
    source = str(value)
    normalized: list[str] = []
    cursor = 0
    while cursor < len(source):
        if source[cursor] == "'":
            end = cursor + 1
            while end < len(source):
                if source[end] != "'":
                    end += 1
                    continue
                if end + 1 < len(source) and source[end + 1] == "'":
                    end += 2
                    continue
                end += 1
                break
            if end > len(source) or source[end - 1] != "'":
                return "invalid-unclosed-literal"
            normalized.append(source[cursor:end])
            cursor = end
            continue
        end = source.find("'", cursor)
        if end < 0:
            end = len(source)
        syntax = (
            source[cursor:end]
            .lower()
            .replace('"', "")
            .replace("::text", "")
        )
        normalized.append(
            "".join(
                character
                for character in syntax
                if not character.isspace() and character not in "()"
            )
        )
        cursor = end
    return "".join(normalized)


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
            _install_ai_scope_index(engine)
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


def _install_ai_scope_index(engine) -> None:
    """Install the current audit index before stamping an unmanaged database."""
    index = next(index for index in Base.metadata.tables['ai_runs'].indexes
                 if index.name == 'ix_ai_runs_research_scope')
    with engine.begin() as connection:
        if connection.dialect.name == 'sqlite':
            if any(row[1] == index.name for row in connection.exec_driver_sql("PRAGMA index_list('ai_runs')")):
                return
            index.create(connection)
        else:
            index.create(connection, checkfirst=True)


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


def _postgresql_company_worker_index_is_canonical(
    state: Mapping[str, object] | None,
) -> bool:
    return bool(
        state is not None
        and state.get("indisvalid") is True
        and state.get("indisready") is True
        and state.get("access_method") == "btree"
        and state.get("indnkeyatts") == len(_COMPANY_WORKER_INDEX_COLUMNS)
        and tuple(state.get("key_columns") or ())
        == _COMPANY_WORKER_INDEX_COLUMNS
        and tuple(state.get("descending") or ()) == (False, False, False)
        and tuple(state.get("nulls_first") or ()) == (False, False, False)
    )


def _company_worker_index_physical_definition_is_canonical(engine) -> bool:
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            index_row = next(
                (
                    row
                    for row in connection.exec_driver_sql(
                        "PRAGMA index_list('jobs')"
                    )
                    if row[1] == _COMPANY_WORKER_INDEX
                ),
                None,
            )
            if (
                index_row is None
                or index_row[2] != 0
                or index_row[3] != "c"
                or index_row[4] != 1
            ):
                return False
            key_rows = tuple(
                row
                for row in connection.exec_driver_sql(
                    f"PRAGMA index_xinfo('{_COMPANY_WORKER_INDEX}')"
                )
                if row[5] == 1
            )
            return tuple(
                (row[2], row[3], row[4]) for row in key_rows
            ) == tuple(
                (column, 0, "BINARY")
                for column in _COMPANY_WORKER_INDEX_COLUMNS
            )
        if connection.dialect.name != "postgresql":
            return False
        state = connection.execute(
            text(_POSTGRESQL_COMPANY_WORKER_INDEX_STATE_SQL),
            {"table_name": "jobs", "index_name": _COMPANY_WORKER_INDEX},
        ).mappings().one_or_none()
        return _postgresql_company_worker_index_is_canonical(state)


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
    if _normalize_company_worker_predicate(
        predicate
    ) != _normalize_company_worker_predicate(
        _COMPANY_WORKER_INDEX_PREDICATE
    ):
        raise UnmanagedDatabaseSchemaError(
            "database has an invalid company research worker candidate index"
        )
    if not _company_worker_index_physical_definition_is_canonical(engine):
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
        _require_company_worker_index(engine)
    finally:
        engine.dispose()
