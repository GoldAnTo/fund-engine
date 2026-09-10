"""Persist immutable evidence-component impact assessments.

Revision ID: 0023
Revises: 0022
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLE = "event_impact_hypothesis_assessments"


def upgrade() -> None:
    op.create_table(
        _IMMUTABLE_TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hypothesis_id",
            sa.Uuid(),
            sa.ForeignKey("event_impact_hypotheses.id"),
            nullable=False,
        ),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("event_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column("classification", sa.String(16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "classification IN ('key', 'alternative', 'background', 'unresolved')",
            name="ck_event_impact_hypothesis_assessments_classification",
        ),
    )
    op.create_index(
        "ix_event_impact_assessments_case_scope_rank",
        _IMMUTABLE_TABLE,
        ["research_case_id", "scope_version_id", "rank"],
    )
    op.create_index(
        "ix_event_impact_assessments_hypothesis_created",
        _IMMUTABLE_TABLE,
        ["hypothesis_id", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        for action in ("update", "delete"):
            op.execute(
                f"CREATE TRIGGER no_{action}_{_IMMUTABLE_TABLE} BEFORE {action.upper()} "
                f"ON {_IMMUTABLE_TABLE} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for action in ("delete", "update"):
            op.execute(
                f"DROP TRIGGER IF EXISTS no_{action}_{_IMMUTABLE_TABLE} ON {_IMMUTABLE_TABLE};"
            )
    op.drop_index("ix_event_impact_assessments_hypothesis_created", table_name=_IMMUTABLE_TABLE)
    op.drop_index("ix_event_impact_assessments_case_scope_rank", table_name=_IMMUTABLE_TABLE)
    op.drop_table(_IMMUTABLE_TABLE)
