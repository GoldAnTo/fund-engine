"""case theme tag events (横切主题标签事件表)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMMUTABLE_TABLES = ("case_theme_tag_events",)


def upgrade() -> None:
    op.create_table(
        "case_theme_tag_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("op", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Defence-in-depth, same convention as migration 0001.
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
    op.drop_table("case_theme_tag_events")
