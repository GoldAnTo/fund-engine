"""Keep conditional financial model drafts separate from published research."""

import sqlalchemy as sa

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None
TABLE = "uw_company_research_financial_drafts"


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("financial drafts require SQLite or PostgreSQL")
    json_type = "json_type" if dialect == "sqlite" else "json_typeof"
    if not sa.inspect(op.get_bind()).has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "project_id",
                sa.Uuid(),
                sa.ForeignKey("uw_research_projects.id"),
                nullable=False,
            ),
            sa.Column(
                "parent_revision_id",
                sa.Uuid(),
                sa.ForeignKey("uw_research_versions.id"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column(
                "supersedes_id", sa.Uuid(), sa.ForeignKey(f"{TABLE}.id"), nullable=True
            ),
            sa.Column("parent_content_hash", sa.String(64), nullable=True),
            sa.Column("idempotency_key", sa.String(255), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(none_as_null=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "project_id", "sequence", name="uq_uw_financial_draft_sequence"
            ),
            sa.UniqueConstraint(
                "idempotency_key", name="uq_uw_financial_draft_idempotency"
            ),
            sa.CheckConstraint("sequence >= 1", name="ck_uw_financial_draft_sequence"),
            sa.CheckConstraint(
                "length(input_hash) = 64 AND length(content_hash) = 64 AND length(request_hash) = 64",
                name="ck_uw_financial_draft_hashes",
            ),
            sa.CheckConstraint(
                "(supersedes_id IS NULL AND parent_content_hash IS NULL) OR (supersedes_id IS NOT NULL AND length(parent_content_hash) = 64)",
                name="ck_uw_financial_draft_parent",
            ),
            sa.CheckConstraint(
                f"{json_type}(payload) = 'object'", name="ck_uw_financial_draft_payload"
            ),
        )
    for operation in ("UPDATE", "DELETE"):
        trigger = f"no_{operation.lower()}_{TABLE}"
        if dialect == "sqlite":
            op.execute(
                f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {TABLE} BEGIN SELECT RAISE(ABORT, 'financial draft is append-only'); END"
            )
        else:
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {TABLE}")
            op.execute(
                f"CREATE TRIGGER {trigger} BEFORE {operation} ON {TABLE} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger()"
            )


def downgrade():
    op.drop_table(TABLE)
