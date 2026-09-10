"""research ledger: cases, theses, causal steps/edges, statements, links, snapshots, assessments, reviews

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMMUTABLE_TABLES = (
    "research_cases",
    "theses",
    "causal_steps",
    "causal_edges",
    "source_statements",
    "evidence_links",
    "evidence_snapshots",
    "ai_assessments",
    "review_decisions",
)


def upgrade() -> None:
    op.create_table(
        "research_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("industry_topic", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
    )

    op.create_table(
        "theses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
    )

    op.create_table(
        "causal_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "causal_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_step_id",
            sa.Uuid(),
            sa.ForeignKey("causal_steps.id"),
            nullable=False,
        ),
        sa.Column(
            "target_step_id",
            sa.Uuid(),
            sa.ForeignKey("causal_steps.id"),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("creator_type", sa.String(32), nullable=False),
        sa.Column("review_state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "source_statements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_span_id",
            sa.Uuid(),
            sa.ForeignKey("source_spans.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("observed_period", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "evidence_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id"),
            nullable=False,
        ),
        sa.Column(
            "source_statement_id",
            sa.Uuid(),
            sa.ForeignKey("source_statements.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creator_type", sa.String(32), nullable=False),
        sa.Column("review_state", sa.String(32), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "evidence_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "thesis_id",
            sa.Uuid(),
            sa.ForeignKey("theses.id"),
            nullable=False,
        ),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_link_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ai_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("evidence_snapshots.id"),
            nullable=False,
        ),
        sa.Column("conclusion", sa.String(32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("displayed_as_provisional", sa.Boolean(), nullable=False),
        sa.Column("creator_type", sa.String(32), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ai_assessment_id",
            sa.Uuid(),
            sa.ForeignKey("ai_assessments.id"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("conclusion", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Defence-in-depth: reject UPDATE/DELETE for every new ledger table.
    # reject_mutable_ledger() was created in migration 0001.
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
    op.drop_table("review_decisions")
    op.drop_table("ai_assessments")
    op.drop_table("evidence_snapshots")
    op.drop_table("evidence_links")
    op.drop_table("source_statements")
    op.drop_table("causal_edges")
    op.drop_table("causal_steps")
    op.drop_table("theses")
    op.drop_table("research_cases")
