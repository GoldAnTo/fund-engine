"""Add immutable mechanism templates, selections, and verification rules.

Revision ID: 0025
Revises: 0024
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "mechanism_template_versions",
    "mechanism_node_versions",
    "mechanism_edge_versions",
    "case_mechanism_selection_versions",
    "verification_rule_versions",
)


def _immutable(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def upgrade() -> None:
    op.create_table(
        "mechanism_template_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("industry_scope", sa.String(length=128), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("mechanism_template_versions.id"), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_key", "version", name="uq_mechanism_template_versions_key_version"),
    )
    op.create_index("ix_mechanism_template_versions_template_key", "mechanism_template_versions", ["template_key"])
    op.create_table(
        "mechanism_node_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_version_id", sa.Uuid(), sa.ForeignKey("mechanism_template_versions.id"), nullable=False),
        sa.Column("node_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_version_id", "node_key", name="uq_mechanism_node_versions_template_key"),
    )
    op.create_index("ix_mechanism_node_versions_template_version_id", "mechanism_node_versions", ["template_version_id"])
    op.create_table(
        "mechanism_edge_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_version_id", sa.Uuid(), sa.ForeignKey("mechanism_template_versions.id"), nullable=False),
        sa.Column("edge_key", sa.String(length=128), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), sa.ForeignKey("mechanism_node_versions.id"), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), sa.ForeignKey("mechanism_node_versions.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_version_id", "edge_key", name="uq_mechanism_edge_versions_template_key"),
    )
    op.create_index("ix_mechanism_edge_versions_template_version_id", "mechanism_edge_versions", ["template_version_id"])
    op.create_table(
        "case_mechanism_selection_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), sa.ForeignKey("mechanism_template_versions.id"), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("case_mechanism_selection_versions.id"), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_case_mechanism_selection_versions_research_case_id", "case_mechanism_selection_versions", ["research_case_id"])
    op.create_table(
        "verification_rule_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mechanism_edge_id", sa.Uuid(), sa.ForeignKey("mechanism_edge_versions.id"), nullable=False),
        sa.Column("metric_definition_id", sa.Uuid(), sa.ForeignKey("metric_definition_versions.id"), nullable=False),
        sa.Column("expected_direction", sa.String(length=16), nullable=False),
        sa.Column("support_predicate", sa.Text(), nullable=False),
        sa.Column("contradiction_predicate", sa.Text(), nullable=False),
        sa.Column("allowed_source_roles", sa.JSON(), nullable=False),
        sa.Column("observed_period_start", sa.Date(), nullable=False),
        sa.Column("observed_period_end", sa.Date(), nullable=False),
        sa.Column("available_at_deadline", sa.Date(), nullable=False),
        sa.Column("next_verification_event", sa.String(length=256), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("verification_rule_versions.id"), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_verification_rule_versions_mechanism_edge_id", "verification_rule_versions", ["mechanism_edge_id"])
    for table in _TABLES:
        _immutable(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.drop_table(table)

