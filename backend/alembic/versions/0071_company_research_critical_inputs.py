"""Allow append-only critical-input artifacts.

Revision ID: 0071
Revises: 0070
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


_ARTIFACTS = "uw_company_research_artifact_versions"
_CHECK = "ck_uw_company_research_artifact_kind"
_OLD_KINDS = (
    "evidence_index",
    "business_map",
    "driver_map",
    "financial_bridge",
    "scenario_set",
    "valuation_set",
    "research_gaps",
    "judgment_context",
    "memo",
)
_NEW_KINDS = (*_OLD_KINDS, "critical_inputs")
_TRIGGER_ERROR = "immutable company research table is append-only"
_PRESERVATION_ERROR = (
    "cannot downgrade 0071 while critical_inputs artifacts require preservation"
)


def _condition(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def _install_sqlite_triggers() -> None:
    for operation in ("update", "delete"):
        trigger = f"no_{operation}_{_ARTIFACTS}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE {operation.upper()} ON {_ARTIFACTS} "
            f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
        )


def _replace_constraint(kinds: tuple[str, ...]) -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "sqlite":
        with op.batch_alter_table(_ARTIFACTS, recreate="always") as batch:
            batch.drop_constraint(_CHECK, type_="check")
            batch.create_check_constraint(_CHECK, _condition(kinds))
        _install_sqlite_triggers()
        return
    if dialect_name == "postgresql":
        op.drop_constraint(_CHECK, _ARTIFACTS, type_="check")
        op.create_check_constraint(_CHECK, _ARTIFACTS, _condition(kinds))
        return
    raise RuntimeError("company research artifacts require SQLite or PostgreSQL")


def _critical_inputs_exist() -> bool:
    return bool(
        op.get_bind().scalar(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {_ARTIFACTS} "
                "WHERE kind = :kind)"
            ),
            {"kind": "critical_inputs"},
        )
    )


def upgrade() -> None:
    _replace_constraint(_NEW_KINDS)


def downgrade() -> None:
    if _critical_inputs_exist():
        raise RuntimeError(_PRESERVATION_ERROR)
    _replace_constraint(_OLD_KINDS)
