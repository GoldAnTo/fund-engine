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
from app.models.ledger import Base


class UnmanagedDatabaseSchemaError(RuntimeError):
    """An existing database cannot be safely adopted as the current schema."""


_COMPANY_EVENTS = "uw_company_research_events"
_COMPANY_EVENT_TRIGGERS = frozenset(
    {
        f"no_update_{_COMPANY_EVENTS}",
        f"no_delete_{_COMPANY_EVENTS}",
    }
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
            _install_company_event_immutability(engine)
            _require_company_event_immutability(engine)
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def _require_current_metadata(inspector, actual_tables: set[str]) -> None:
    expected = {table.name: {column.name for column in table.columns} for table in Base.metadata.sorted_tables}
    missing_tables = sorted(set(expected) - actual_tables)
    missing_columns = {
        table: sorted(columns - {column["name"] for column in inspector.get_columns(table)})
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
    if (
        company_event_columns["hash_version"]["nullable"]
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


def _install_company_event_immutability(engine) -> None:
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            connection.execute(
                text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS no_update_{_COMPANY_EVENTS}
                    BEFORE UPDATE ON {_COMPANY_EVENTS}
                    BEGIN
                        SELECT RAISE(ABORT, 'immutable company research table is append-only');
                    END
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS no_delete_{_COMPANY_EVENTS}
                    BEFORE DELETE ON {_COMPANY_EVENTS}
                    BEGIN
                        SELECT RAISE(ABORT, 'immutable company research table is append-only');
                    END
                    """
                )
            )
            return
        if connection.dialect.name != "postgresql":
            raise UnmanagedDatabaseSchemaError(
                "cannot install company research event immutability on this database"
            )
        if connection.scalar(
            text("SELECT to_regprocedure('reject_mutable_ledger()')")
        ) is None:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION reject_mutable_ledger()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION 'immutable ledger rows cannot be changed';
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
            )
        for operation in ("update", "delete"):
            connection.execute(
                text(
                    f"DROP TRIGGER IF EXISTS no_{operation}_{_COMPANY_EVENTS} "
                    f"ON {_COMPANY_EVENTS}"
                )
            )
            connection.execute(
                text(
                    f"CREATE TRIGGER no_{operation}_{_COMPANY_EVENTS} "
                    f"BEFORE {operation.upper()} ON {_COMPANY_EVENTS} "
                    "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
                )
            )


def _require_company_event_immutability(engine) -> None:
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            actual = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                        "AND tbl_name = :table_name"
                    ),
                    {"table_name": _COMPANY_EVENTS},
                )
            }
        elif connection.dialect.name == "postgresql":
            actual = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table = :table_name"
                    ),
                    {"table_name": _COMPANY_EVENTS},
                )
            }
        else:
            actual = set()
    if not _COMPANY_EVENT_TRIGGERS.issubset(actual):
        raise UnmanagedDatabaseSchemaError(
            "existing database has no Alembic version and lacks required "
            "company research event immutability triggers"
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
        _require_company_event_immutability(engine)
    finally:
        engine.dispose()
