"""research commands: falsifiable theses, framed cases, link-level reviews

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-01

Prototype-driven enrichment (新建研究 / 审核工作区):

- research_cases: research-question framing fields (research_object,
  phenomenon, core_question, period_start, period_end, evidence_cutoff)
- theses: falsifiable-proposition fields (title, observation window,
  support/falsification conditions, next verification event) plus
  creator_type / review_state so AI-drafted theses start as drafts
- evidence_reviews: append-only link-level human review (四要素审核)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMMUTABLE_TABLES = ("evidence_reviews",)


def upgrade() -> None:
    op.add_column("research_cases", sa.Column("research_object", sa.Text(), nullable=True))
    op.add_column("research_cases", sa.Column("phenomenon", sa.Text(), nullable=True))
    op.add_column("research_cases", sa.Column("core_question", sa.Text(), nullable=True))
    op.add_column("research_cases", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("research_cases", sa.Column("period_end", sa.Date(), nullable=True))
    op.add_column("research_cases", sa.Column("evidence_cutoff", sa.Date(), nullable=True))

    op.add_column("theses", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("theses", sa.Column("observation_start", sa.Date(), nullable=True))
    op.add_column("theses", sa.Column("observation_end", sa.Date(), nullable=True))
    op.add_column("theses", sa.Column("support_condition", sa.Text(), nullable=True))
    op.add_column("theses", sa.Column("falsification_condition", sa.Text(), nullable=True))
    op.add_column("theses", sa.Column("next_verification_event", sa.Text(), nullable=True))
    op.add_column(
        "theses",
        sa.Column("creator_type", sa.String(32), nullable=False, server_default="human"),
    )
    op.add_column(
        "theses",
        sa.Column("review_state", sa.String(32), nullable=False, server_default="confirmed"),
    )

    op.create_table(
        "evidence_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "evidence_link_id",
            sa.Uuid(),
            sa.ForeignKey("evidence_links.id"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("relation", sa.String(32), nullable=True),
        sa.Column("factor_role", sa.Text(), nullable=False),
        sa.Column("scope_boundary", sa.Text(), nullable=False),
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
    op.drop_table("evidence_reviews")

    for column in (
        "review_state",
        "creator_type",
        "next_verification_event",
        "falsification_condition",
        "support_condition",
        "observation_end",
        "observation_start",
        "title",
    ):
        op.drop_column("theses", column)
    for column in (
        "evidence_cutoff",
        "period_end",
        "period_start",
        "core_question",
        "phenomenon",
        "research_object",
    ):
        op.drop_column("research_cases", column)
