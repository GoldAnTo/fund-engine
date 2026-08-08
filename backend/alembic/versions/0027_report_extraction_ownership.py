"""Bind report spans to their case and claim extraction exactly once.

Revision ID: 0027
Revises: 0026
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = ("report_case_source_spans", "report_extraction_claims")


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "report_case_source_spans",
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
        sa.Column("source_span_id", sa.Uuid(), sa.ForeignKey("source_spans.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "research_case_id", "source_span_id", name="uq_report_case_source_span"
        ),
    )
    op.create_index(
        "ix_report_case_source_spans_case_document",
        "report_case_source_spans",
        ["research_case_id", "document_version_id"],
    )

    op.create_table(
        "report_extraction_claims",
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
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "research_case_id",
            "document_version_id",
            "extractor_version",
            "input_fingerprint",
            name="uq_report_extraction_claim",
        ),
    )
    op.create_index(
        "ix_report_extraction_claims_case_document",
        "report_extraction_claims",
        ["research_case_id", "document_version_id"],
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
    op.drop_index(
        "ix_report_extraction_claims_case_document",
        table_name="report_extraction_claims",
    )
    op.drop_table("report_extraction_claims")
    op.drop_index(
        "ix_report_case_source_spans_case_document",
        table_name="report_case_source_spans",
    )
    op.drop_table("report_case_source_spans")
