"""Persist append-only, versioned event-research factor scope snapshots.

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = (
    "event_research_scope_versions",
    "event_research_scope_factors",
)


def _add_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def _drop_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "event_research_scope_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(length=128), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "research_case_id",
            "version",
            name="uq_event_research_scope_versions_case_version",
        ),
    )
    op.create_index(
        "ix_event_research_scope_versions_case",
        "event_research_scope_versions",
        ["research_case_id"],
    )

    op.create_table(
        "event_research_scope_factors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("event_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "scope_version_id",
            "position",
            name="uq_event_research_scope_factors_position",
        ),
    )
    op.create_index(
        "ix_event_research_scope_factors_scope_version",
        "event_research_scope_factors",
        ["scope_version_id"],
    )

    for table in _IMMUTABLE_TABLES:
        _add_immutable_triggers(table)


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        _drop_immutable_triggers(table)
    op.drop_index(
        "ix_event_research_scope_factors_scope_version",
        table_name="event_research_scope_factors",
    )
    op.drop_table("event_research_scope_factors")
    op.drop_index(
        "ix_event_research_scope_versions_case",
        table_name="event_research_scope_versions",
    )
    op.drop_table("event_research_scope_versions")
