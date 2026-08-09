"""Add versioned metric definitions and fixed outcome bindings.

Revision ID: 0024
Revises: 0023
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _immutable(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def upgrade() -> None:
    op.add_column("theses", sa.Column("research_protocol_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "metric_definition_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("metric_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("canonical_definition", sa.Text(), nullable=False),
        sa.Column("entity_scope", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("frequency", sa.String(length=32), nullable=False),
        sa.Column("period_semantics", sa.String(length=32), nullable=False),
        sa.Column("allowed_source_roles", sa.JSON(), nullable=False),
        sa.Column("role_eligibility", sa.JSON(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("metric_definition_versions.id"), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("metric_id", "version", name="uq_metric_definition_versions_metric_version"),
    )
    op.create_index("ix_metric_definition_versions_metric_id", "metric_definition_versions", ["metric_id"])
    op.create_table(
        "outcome_binding_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thesis_id", sa.Uuid(), sa.ForeignKey("theses.id"), nullable=False),
        sa.Column("metric_definition_id", sa.Uuid(), sa.ForeignKey("metric_definition_versions.id"), nullable=False),
        sa.Column("entity_scope", sa.JSON(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("baseline", sa.JSON(), nullable=False),
        sa.Column("horizon_start", sa.Date(), nullable=False),
        sa.Column("horizon_end", sa.Date(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("outcome_binding_versions.id"), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outcome_binding_versions_thesis_id", "outcome_binding_versions", ["thesis_id"])
    for table in ("metric_definition_versions", "outcome_binding_versions"):
        _immutable(table)


def downgrade() -> None:
    for table in ("outcome_binding_versions", "metric_definition_versions"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.drop_table(table)
    op.drop_column("theses", "research_protocol_required")
