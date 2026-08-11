from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0044_reconcile_retired_schema.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("schema_reconciliation", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retired_schema_cleanup_refuses_to_drop_nonempty_tables() -> None:
    migration = _migration_module()

    with pytest.raises(RuntimeError, match="report_relations"):
        migration.assert_retired_tables_empty(
            migration.RETIRED_TABLES,
            lambda table_name: 1 if table_name == "report_relations" else 0,
        )


def test_retired_schema_cleanup_drops_children_before_retired_parents() -> None:
    migration = _migration_module()
    outgoing_foreign_keys = {
        "company_impact_observations": {"company_impact_relations"},
        "company_impact_relations": {"event_impact_hypotheses"},
        "event_impact_hypotheses": set(),
    }

    order = migration.retired_drop_order(outgoing_foreign_keys)

    assert order.index("company_impact_observations") < order.index("company_impact_relations")
    assert order.index("company_impact_relations") < order.index("event_impact_hypotheses")


def test_index_reconciliation_keeps_active_model_index_names() -> None:
    migration_path = MIGRATION_PATH.with_name("0045_reconcile_active_index_names.py")
    spec = importlib.util.spec_from_file_location("active_index_reconciliation", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert (
        "case_monitor_versions",
        "ix_case_monitor_versions_case",
        "ix_case_monitor_versions_research_case_id",
    ) in migration.ACTIVE_INDEX_RENAMES
    assert (
        "research_runs",
        "ix_research_runs_case_created",
    ) in migration.RETIRED_ACTIVE_INDEXES


def test_task_type_constraint_reconciliation_tracks_the_retired_constraint() -> None:
    migration_path = MIGRATION_PATH.with_name("0046_drop_retired_task_type_constraint.py")
    spec = importlib.util.spec_from_file_location("task_type_constraint_reconciliation", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.RETIRED_TASK_TYPE_CONSTRAINT == (
        "research_tasks",
        "ck_research_task_type",
    )
