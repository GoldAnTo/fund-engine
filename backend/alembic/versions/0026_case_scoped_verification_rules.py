"""Scope immutable verification rule versions to one research case.

Revision ID: 0026
Revises: 0025
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable preserves historical append-only rows created before Case scope.
    # Those rows are intentionally not considered effective by the new reader.
    op.add_column(
        "verification_rule_versions",
        sa.Column("research_case_id", sa.Uuid(), nullable=True),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_verification_rule_versions_research_case_id",
            "verification_rule_versions",
            "research_cases",
            ["research_case_id"],
            ["id"],
        )
    op.create_index(
        "ix_verification_rule_versions_research_case_id",
        "verification_rule_versions",
        ["research_case_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_verification_rule_versions_research_case_id", table_name="verification_rule_versions")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "fk_verification_rule_versions_research_case_id",
            "verification_rule_versions",
            type_="foreignkey",
        )
    op.drop_column("verification_rule_versions", "research_case_id")
