"""Record why a legacy Case was explicitly admitted to a tenant.

Revision ID: 0039
Revises: 0038
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing admissions were created before an explicit-reason field
    # existed.  They remain immutable historical records, so this stays NULL
    # for them; all legacy-admission commands require a non-empty reason.
    op.add_column(
        "case_tenant_admissions",
        sa.Column("admission_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("case_tenant_admissions", "admission_reason")
