"""Store reported provider usage on immutable AI operation records."""
from alembic import op
import sqlalchemy as sa

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade():
    # ORM-created legacy databases may already contain this nullable column.
    if "usage" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ai_runs")}:
        op.add_column("ai_runs", sa.Column("usage", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("ai_runs", "usage")
