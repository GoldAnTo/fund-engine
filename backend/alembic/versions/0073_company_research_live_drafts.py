"""Freeze cited language-model drafts alongside the company research run."""
from alembic import op
import sqlalchemy as sa

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None
TABLE = "uw_company_research_drafts"


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("company research drafts require SQLite or PostgreSQL")
    json_type = "json_type" if dialect == "sqlite" else "json_typeof"
    if not sa.inspect(op.get_bind()).has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("project_id", sa.Uuid(), sa.ForeignKey("uw_research_projects.id"), nullable=False),
            sa.Column("preparation_id", sa.Uuid(), sa.ForeignKey("uw_company_research_preparations.id"), nullable=False),
            sa.Column("ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id"), nullable=False),
            sa.Column("payload", sa.JSON(none_as_null=True), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("preparation_id", name="uq_uw_company_research_draft_preparation"),
            sa.UniqueConstraint("project_id", name="uq_uw_company_research_draft_project"),
            sa.CheckConstraint("length(content_hash) = 64", name="ck_uw_company_research_draft_hash"),
            sa.CheckConstraint(
                f"{json_type}(payload) = 'object'",
                name="ck_uw_company_research_draft_payload",
            ),
        )
    for operation in ("UPDATE", "DELETE"):
        trigger = f"no_{operation.lower()}_{TABLE}"
        if dialect == "sqlite":
            op.execute(f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {TABLE} BEGIN SELECT RAISE(ABORT, 'research draft is append-only'); END")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {TABLE}")
            op.execute(f"CREATE TRIGGER {trigger} BEFORE {operation} ON {TABLE} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()")


def downgrade():
    op.drop_table(TABLE)
