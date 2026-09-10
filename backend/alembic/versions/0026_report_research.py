"""Persist immutable report claims and report-asserted relations.

Revision ID: 0026
Revises: 0025
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = ("report_claims", "report_relations")


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "report_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "source_span_id", sa.Uuid(), sa.ForeignKey("source_spans.id"), nullable=False
        ),
        sa.Column(
            "source_statement_id",
            sa.Uuid(),
            sa.ForeignKey("source_statements.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('report_opinion', 'report_forecast', 'report_assumption', 'report_risk')",
            name="ck_report_claims_kind",
        ),
    )
    op.create_index(
        "ix_report_claims_case_span", "report_claims", ["research_case_id", "source_span_id"]
    )
    op.create_index("ix_report_claims_statement", "report_claims", ["source_statement_id"])

    op.create_table(
        "report_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=False),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "source_span_id", sa.Uuid(), sa.ForeignKey("source_spans.id"), nullable=False
        ),
        sa.Column(
            "source_statement_id",
            sa.Uuid(),
            sa.ForeignKey("source_statements.id"),
            nullable=False,
        ),
        sa.Column("subject_company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("object_company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("subject_name", sa.Text(), nullable=True),
        sa.Column("object_name", sa.Text(), nullable=True),
        sa.Column("relation_kind", sa.String(length=64), nullable=False),
        sa.Column("mechanism", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'report_claim'", name="ck_report_relations_status"),
        sa.CheckConstraint(
            "subject_company_id IS NOT NULL OR subject_name IS NOT NULL",
            name="ck_report_relations_subject_node",
        ),
        sa.CheckConstraint(
            "object_company_id IS NOT NULL OR object_name IS NOT NULL",
            name="ck_report_relations_object_node",
        ),
    )
    op.create_index(
        "ix_report_relations_case_claim", "report_relations", ["research_case_id", "claim_id"]
    )
    op.create_index(
        "ix_report_relations_subject_company",
        "report_relations",
        ["research_case_id", "subject_company_id"],
    )
    op.create_index(
        "ix_report_relations_object_company",
        "report_relations",
        ["research_case_id", "object_company_id"],
    )
    op.create_index(
        "ix_report_relations_statement", "report_relations", ["source_statement_id"]
    )
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")


def downgrade() -> None:
    for table in _IMMUTABLE_TABLES:
        count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing to downgrade: {table} contains {count} immutable records; "
                "export or purge them first"
            )
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    op.drop_index("ix_report_relations_statement", table_name="report_relations")
    op.drop_index("ix_report_relations_object_company", table_name="report_relations")
    op.drop_index("ix_report_relations_subject_company", table_name="report_relations")
    op.drop_index("ix_report_relations_case_claim", table_name="report_relations")
    op.drop_table("report_relations")
    op.drop_index("ix_report_claims_statement", table_name="report_claims")
    op.drop_index("ix_report_claims_case_span", table_name="report_claims")
    op.drop_table("report_claims")
