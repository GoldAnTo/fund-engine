"""freeze human-authorized preparation plans."""
from alembic import op
import sqlalchemy as sa

revision = "0055_authorized_evidence_plan"
down_revision = "0054_preparation_job_claim_tokens"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("research_preparations", sa.Column("authorized_evidence_plan", sa.JSON(none_as_null=True), nullable=True))
    op.drop_constraint("ck_research_preparations_authorized_run", "research_preparations", type_="check")
    op.create_check_constraint("ck_research_preparations_authorized_run", "research_preparations", "(status = 'authorized' AND research_run_id IS NOT NULL AND authorized_evidence_plan IS NOT NULL) OR (status <> 'authorized' AND research_run_id IS NULL AND authorized_evidence_plan IS NULL)")

def downgrade():
    op.drop_constraint("ck_research_preparations_authorized_run", "research_preparations", type_="check")
    op.drop_column("research_preparations", "authorized_evidence_plan")
    op.create_check_constraint("ck_research_preparations_authorized_run", "research_preparations", "(status = 'authorized' AND research_run_id IS NOT NULL) OR (status <> 'authorized' AND research_run_id IS NULL)")
