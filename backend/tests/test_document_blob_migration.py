from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


MIGRATION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions" / "0025_document_blobs.py"
)


class _Bind:
    def __init__(self, count: int, dialect: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self._count = count

    def execute(self, _statement):
        return SimpleNamespace(scalar_one=lambda: self._count)


class _Operations:
    def __init__(self, count: int = 0, dialect: str = "postgresql") -> None:
        self._bind = _Bind(count, dialect)
        self.executed: list[str] = []
        self.dropped_tables: list[str] = []

    def get_bind(self):
        return self._bind

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def create_table(self, *args) -> None:
        pass

    def create_index(self, *args, **kwargs) -> None:
        pass

    def drop_index(self, *args, **kwargs) -> None:
        pass

    def drop_table(self, table: str) -> None:
        self.dropped_tables.append(table)


def _migration_module():
    spec = importlib.util.spec_from_file_location("document_blob_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_document_blob_downgrade_refuses_to_destroy_existing_references() -> None:
    migration = _migration_module()
    operations = _Operations(count=1)
    migration.op = operations

    with pytest.raises(RuntimeError, match="document_blobs contains 1"):
        migration.downgrade()

    assert operations.dropped_tables == []


def test_document_blob_migration_keeps_pg_immutability_contract() -> None:
    migration = _migration_module()
    operations = _Operations()
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0025"
    assert migration.down_revision == "0024"
    assert any("no_update_document_blobs" in statement for statement in operations.executed)
    assert any("no_delete_document_blobs" in statement for statement in operations.executed)
