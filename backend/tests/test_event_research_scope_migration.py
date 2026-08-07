from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0015_event_research_scope_versions.py"
)
RUN_SCOPE_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0016_research_run_scope_theses.py"
)
CONCLUSION_SCOPE_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0017_event_research_conclusion_scope.py"
)


class _OperationsRecorder:
    def __init__(self, dialect: str) -> None:
        self._dialect = dialect
        self.tables: list[tuple] = []
        self.indexes: list[tuple] = []
        self.columns: list[tuple] = []
        self.foreign_keys: list[tuple] = []
        self.executed: list[str] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect))

    def create_table(self, *args) -> None:
        self.tables.append(args)

    def create_index(self, *args) -> None:
        self.indexes.append(args)

    def create_foreign_key(self, *args) -> None:
        self.foreign_keys.append(args)

    def add_column(self, *args) -> None:
        self.columns.append(args)

    def drop_constraint(self, *args, **kwargs) -> None:
        pass

    def drop_column(self, *args) -> None:
        pass

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def drop_index(self, *args) -> None:
        pass

    def drop_table(self, *args) -> None:
        pass


def _migration_module():
    return _load_migration("scope_migration", MIGRATION_PATH)


def _load_migration(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_columns(table_args: tuple) -> set[str]:
    return {item.name for item in table_args[1:] if isinstance(item, sa.Column)}


def _unique_constraints(table_args: tuple) -> set[tuple[str, ...]]:
    return {
        tuple(constraint._pending_colargs)
        for constraint in table_args[1:]
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _check_constraints(table_args: tuple) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table_args[1:]
        if isinstance(constraint, sa.CheckConstraint)
    }


def test_scope_migration_creates_versioned_tables_indexes_and_pg_immutability() -> None:
    migration = _migration_module()
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.upgrade()

    tables = {args[0]: args for args in operations.tables}
    assert migration.revision == "0015"
    assert migration.down_revision == "0014"
    assert _table_columns(tables["event_research_scope_versions"]) == {
        "id",
        "research_case_id",
        "version",
        "changed_by",
        "change_summary",
        "created_at",
    }
    assert _unique_constraints(tables["event_research_scope_versions"]) == {
        ("research_case_id", "version")
    }
    assert _table_columns(tables["event_research_scope_factors"]) == {
        "id",
        "scope_version_id",
        "statement",
        "position",
    }
    assert _unique_constraints(tables["event_research_scope_factors"]) == {
        ("scope_version_id", "position")
    }
    assert _table_columns(tables["event_research_scope_evidence_assignments"]) == {
        "id",
        "scope_version_id",
        "evidence_link_id",
        "factor_statement",
        "disposition",
        "created_at",
    }
    assert _unique_constraints(tables["event_research_scope_evidence_assignments"]) == {
        ("scope_version_id", "evidence_link_id")
    }
    assert _check_constraints(tables["event_research_scope_evidence_assignments"]) == {
        "disposition IN ('mapped', 'unmapped')"
    }
    assert {index[0] for index in operations.indexes} == {
        "ix_event_research_scope_versions_case",
        "ix_event_research_scope_factors_scope_version",
        "ix_event_research_scope_evidence_assignments_scope_version",
    }
    assert "INSERT INTO event_research_scope_versions" in operations.executed[0]
    assert "MIN(created_by)" in operations.executed[0]
    assert "MIN(created_at)" in operations.executed[0]
    assert "INSERT INTO event_research_scope_factors" in operations.executed[1]
    assert "event_research_factor_drafts" in operations.executed[1]
    assert "INSERT INTO event_research_scope_evidence_assignments" in operations.executed[2]
    assert "evidence_links.review_state = 'reviewed'" in operations.executed[2]
    assert "THEN 'mapped'" in operations.executed[2]
    assert "ELSE 'unmapped'" in operations.executed[2]
    assert operations.executed[3:] == [
        "CREATE TRIGGER no_update_event_research_scope_versions BEFORE UPDATE ON "
        "event_research_scope_versions FOR EACH ROW EXECUTE FUNCTION "
        "reject_mutable_ledger();",
        "CREATE TRIGGER no_delete_event_research_scope_versions BEFORE DELETE ON "
        "event_research_scope_versions FOR EACH ROW EXECUTE FUNCTION "
        "reject_mutable_ledger();",
        "CREATE TRIGGER no_update_event_research_scope_factors BEFORE UPDATE ON "
        "event_research_scope_factors FOR EACH ROW EXECUTE FUNCTION "
        "reject_mutable_ledger();",
        "CREATE TRIGGER no_delete_event_research_scope_factors BEFORE DELETE ON "
        "event_research_scope_factors FOR EACH ROW EXECUTE FUNCTION "
        "reject_mutable_ledger();",
        "CREATE TRIGGER no_update_event_research_scope_evidence_assignments BEFORE "
        "UPDATE ON event_research_scope_evidence_assignments FOR EACH ROW EXECUTE "
        "FUNCTION reject_mutable_ledger();",
        "CREATE TRIGGER no_delete_event_research_scope_evidence_assignments BEFORE "
        "DELETE ON event_research_scope_evidence_assignments FOR EACH ROW EXECUTE "
        "FUNCTION reject_mutable_ledger();",
    ]


def test_scope_migration_skips_postgres_triggers_on_sqlite() -> None:
    migration = _migration_module()
    operations = _OperationsRecorder("sqlite")
    migration.op = operations

    migration.upgrade()

    assert len(operations.tables) == 3
    assert len(operations.executed) == 3


def test_research_run_scope_migration_persists_selected_thesis_ids() -> None:
    migration = _load_migration("run_scope_migration", RUN_SCOPE_MIGRATION_PATH)
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0016"
    assert migration.down_revision == "0015"
    assert operations.columns[0][0] == "research_runs"
    column = operations.columns[0][1]
    assert column.name == "scope_thesis_ids"
    assert isinstance(column.type, sa.JSON)
    assert column.nullable is True
    assert len(operations.executed) == 1
    backfill_sql = operations.executed[0]
    assert "UPDATE research_runs" in backfill_sql
    assert "json_agg(DISTINCT research_tasks.thesis_id)" in backfill_sql
    assert "GROUP BY research_tasks.run_id" in backfill_sql


def test_conclusion_scope_migration_preserves_legacy_rows_as_unpublishable() -> None:
    migration = _load_migration(
        "conclusion_scope_migration", CONCLUSION_SCOPE_MIGRATION_PATH
    )
    operations = _OperationsRecorder("postgresql")
    migration.op = operations

    migration.upgrade()

    assert migration.revision == "0017"
    assert migration.down_revision == "0016"
    assert operations.columns[0][0] == "event_research_conclusions"
    column = operations.columns[0][1]
    assert column.name == "scope_version_id"
    assert column.nullable is True
    assert operations.foreign_keys == [
        (
            "fk_event_research_conclusions_scope_version",
            "event_research_conclusions",
            "event_research_scope_versions",
            ["scope_version_id"],
            ["id"],
        )
    ]
    assert operations.indexes[0][0] == "ix_event_research_conclusions_scope_version"
    # Existing draft rows have no reliable historical scope.  They remain for
    # audit with NULL and the service refuses to publish them until regenerated.
    assert operations.executed == []
