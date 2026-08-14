"""freeze human-authorized preparation plans."""
from alembic import op
import sqlalchemy as sa

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("research_preparations") as batch:
        batch.add_column(
            sa.Column("authorized_evidence_plan", sa.JSON(none_as_null=True), nullable=True)
        )
        batch.drop_constraint("ck_research_preparations_authorized_run", type_="check")
        batch.create_check_constraint(
            "ck_research_preparations_authorized_run",
            "(status = 'authorized' AND research_run_id IS NOT NULL AND "
            "authorized_evidence_plan IS NOT NULL) OR (status <> 'authorized' "
            "AND research_run_id IS NULL AND authorized_evidence_plan IS NULL)",
        )

def downgrade():
    with op.batch_alter_table("research_preparations") as batch:
        batch.drop_constraint("ck_research_preparations_authorized_run", type_="check")
        batch.drop_column("authorized_evidence_plan")
        batch.create_check_constraint(
            "ck_research_preparations_authorized_run",
            "(status = 'authorized' AND research_run_id IS NOT NULL) OR "
            "(status <> 'authorized' AND research_run_id IS NULL)",
        )
