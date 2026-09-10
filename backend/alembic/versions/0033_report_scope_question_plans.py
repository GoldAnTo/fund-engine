"""Allow multiple research scopes for one report document.

Revision ID: 0033
Revises: 0032
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A document is immutable source material; it can legitimately support
    # multiple questions and factor/evidence plans.  Version remains unique
    # within a case, but (case, document) intentionally is not.
    with op.batch_alter_table("report_research_scope_versions") as batch:
        batch.drop_constraint("uq_report_scope_case_document", type_="unique")
        batch.add_column(
            sa.Column(
                "research_question",
                sa.Text(),
                nullable=False,
                server_default="验证研报观点与市场影响。",
            )
        )
        batch.add_column(
            sa.Column("factor_selection", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(
            sa.Column("evidence_plan", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT count(*) FROM report_research_scope_versions")
    ).scalar_one()
    if count:
        raise RuntimeError(
            "refusing to downgrade: report_research_scope_versions contains immutable records"
        )
    with op.batch_alter_table("report_research_scope_versions") as batch:
        batch.drop_column("evidence_plan")
        batch.drop_column("factor_selection")
        batch.drop_column("research_question")
        batch.create_unique_constraint(
            "uq_report_scope_case_document",
            ["research_case_id", "document_version_id"],
        )
