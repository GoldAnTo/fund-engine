"""Own report Wiki scopes and relation-specific confounder outcomes.

Revision ID: 0032
Revises: 0031
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = (
    "report_research_scope_versions",
    "report_confounder_assessments",
)


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "report_research_scope_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(length=128), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_case_id", "version", name="uq_report_scope_case_version"),
        sa.UniqueConstraint(
            "research_case_id", "document_version_id", name="uq_report_scope_case_document"
        ),
    )
    op.create_index(
        "ix_report_scope_case_version",
        "report_research_scope_versions",
        ["research_case_id", "version"],
    )
    # Reports created before this migration already have case-owned source
    # spans.  Promote each historical document into an ordered immutable
    # scope instead of making old Wiki graphs disappear on upgrade.
    rows = op.get_bind().execute(
        sa.text(
            "SELECT research_case_id, document_version_id, MIN(created_at) AS created_at "
            "FROM report_case_source_spans "
            "GROUP BY research_case_id, document_version_id "
            "ORDER BY research_case_id, MIN(created_at), document_version_id"
        )
    ).mappings()
    scopes: list[dict] = []
    versions: dict[object, int] = {}
    for row in rows:
        case_id = row["research_case_id"]
        versions[case_id] = versions.get(case_id, 0) + 1
        scopes.append(
            {
                "id": uuid4(),
                "research_case_id": case_id,
                "document_version_id": row["document_version_id"],
                "version": versions[case_id],
                "changed_by": "migration-0032",
                "change_summary": "迁移既有研报范围",
                "created_at": row["created_at"],
            }
        )
    if scopes:
        scope_table = sa.table(
            "report_research_scope_versions",
            sa.column("id", sa.Uuid()),
            sa.column("research_case_id", sa.Uuid()),
            sa.column("document_version_id", sa.Uuid()),
            sa.column("version", sa.Integer()),
            sa.column("changed_by", sa.String()),
            sa.column("change_summary", sa.Text()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(scope_table, scopes)
    op.create_table(
        "report_confounder_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "report_claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=False
        ),
        sa.Column(
            "report_relation_id", sa.Uuid(), sa.ForeignKey("report_relations.id"), nullable=False
        ),
        sa.Column(
            "report_confounder_id",
            sa.Uuid(),
            sa.ForeignKey("report_market_confounders.id"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('material', 'not_material', 'unresolved')",
            name="ck_report_confounder_assessment_outcome",
        ),
    )
    op.create_index(
        "ix_report_confounder_assessment_relation_created",
        "report_confounder_assessments",
        ["report_relation_id", "created_at"],
    )
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")


def downgrade() -> None:
    bind = op.get_bind()
    for table in _IMMUTABLE_TABLES:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing to downgrade: {table} contains {count} immutable records"
            )
    if bind.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    op.drop_index(
        "ix_report_confounder_assessment_relation_created",
        table_name="report_confounder_assessments",
    )
    op.drop_table("report_confounder_assessments")
    op.drop_index("ix_report_scope_case_version", table_name="report_research_scope_versions")
    op.drop_table("report_research_scope_versions")
