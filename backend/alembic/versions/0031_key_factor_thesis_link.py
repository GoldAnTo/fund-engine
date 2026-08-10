"""Link reviewed market key factors to the Case research scope."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("key_factors") as batch:
            batch.add_column(sa.Column("thesis_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_key_factors_thesis_id", "theses", ["thesis_id"], ["id"]
            )
    else:
        op.add_column(
            "key_factors",
            sa.Column("thesis_id", sa.Uuid(), sa.ForeignKey("theses.id"), nullable=True),
        )
    op.create_index("ix_key_factors_thesis", "key_factors", ["thesis_id"])


def downgrade() -> None:
    op.drop_index("ix_key_factors_thesis", table_name="key_factors")
    op.drop_column("key_factors", "thesis_id")
