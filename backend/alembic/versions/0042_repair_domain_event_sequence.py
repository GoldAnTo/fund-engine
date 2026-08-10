"""Repair the PostgreSQL outbox sequence for stamped legacy databases.

Revision ID: 0042
Revises: 0041
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE SEQUENCE IF NOT EXISTS domain_events_seq_seq")
    op.execute("ALTER SEQUENCE domain_events_seq_seq OWNED BY domain_events.seq")
    op.execute(
        "ALTER TABLE domain_events ALTER COLUMN seq "
        "SET DEFAULT nextval('domain_events_seq_seq')"
    )
    op.execute(
        "SELECT setval('domain_events_seq_seq', "
        "COALESCE((SELECT MAX(seq) FROM domain_events), 0) + 1, false)"
    )


def downgrade() -> None:
    # 0041's runtime model already requires this sequence.  Preserve the
    # repair when stepping back one revision instead of reintroducing a broken
    # outbox cursor.
    return
