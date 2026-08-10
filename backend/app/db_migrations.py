"""Explicit schema bootstrap for runnable research environments.

Application services never create tables implicitly. A runnable database is
always brought to the Alembic head first so its version is inspectable and a
subsequent release has a well-defined upgrade path.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401 - ensure the complete metadata is registered
from app.models.ledger import Base


class UnmanagedDatabaseSchemaError(RuntimeError):
    """An existing database cannot be safely adopted as the current schema."""


def upgrade_database_to_head(database_url: str) -> None:
    """Upgrade ``database_url`` using this repository's Alembic history."""
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
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
