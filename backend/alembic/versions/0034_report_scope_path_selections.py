"""Freeze selected claim and relation paths for each report scope.

Revision ID: 0034
Revises: 0033
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = (
    "report_research_scope_claims",
    "report_research_scope_relations",
)


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "report_research_scope_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("report_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "report_claim_id",
            sa.Uuid(),
            sa.ForeignKey("report_claims.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_version_id", "report_claim_id", name="uq_report_scope_claim"),
    )
    op.create_index(
        "ix_report_scope_claim_claim",
        "report_research_scope_claims",
        ["report_claim_id"],
    )
    op.create_table(
        "report_research_scope_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("report_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "report_relation_id",
            sa.Uuid(),
            sa.ForeignKey("report_relations.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scope_version_id", "report_relation_id", name="uq_report_scope_relation"
        ),
    )
    op.create_index(
        "ix_report_scope_relation_relation",
        "report_research_scope_relations",
        ["report_relation_id"],
    )

    # Existing scopes historically meant "all extracted paths in this report
    # document". Materialize that interpretation before making selections
    # mandatory so upgrades never hide previously visible report evidence.
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                "SELECT scope.id AS scope_version_id, claim.id AS report_claim_id, "
                "scope.created_at AS created_at "
                "FROM report_research_scope_versions AS scope "
                "JOIN report_claims AS claim "
                "ON claim.research_case_id = scope.research_case_id "
                "JOIN source_spans AS span ON span.id = claim.source_span_id "
                "JOIN report_case_source_spans AS selected "
                "ON selected.research_case_id = scope.research_case_id "
                "AND selected.document_version_id = scope.document_version_id "
                "AND selected.source_span_id = claim.source_span_id "
                "WHERE span.document_version_id = scope.document_version_id"
            )
        ).mappings()
    )
    claim_rows = [
        {
            "id": uuid4(),
            "scope_version_id": row["scope_version_id"],
            "report_claim_id": row["report_claim_id"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    if claim_rows:
        op.bulk_insert(
            sa.table(
                "report_research_scope_claims",
                sa.column("id", sa.Uuid()),
                sa.column("scope_version_id", sa.Uuid()),
                sa.column("report_claim_id", sa.Uuid()),
                sa.column("created_at", sa.DateTime(timezone=True)),
            ),
            claim_rows,
        )
    relation_rows = list(
        bind.execute(
            sa.text(
                "SELECT scope.id AS scope_version_id, relation.id AS report_relation_id, "
                "scope.created_at AS created_at "
                "FROM report_research_scope_versions AS scope "
                "JOIN report_claims AS claim "
                "ON claim.research_case_id = scope.research_case_id "
                "JOIN source_spans AS span ON span.id = claim.source_span_id "
                "JOIN report_case_source_spans AS selected "
                "ON selected.research_case_id = scope.research_case_id "
                "AND selected.document_version_id = scope.document_version_id "
                "AND selected.source_span_id = claim.source_span_id "
                "JOIN report_relations AS relation ON relation.claim_id = claim.id "
                "WHERE span.document_version_id = scope.document_version_id"
            )
        ).mappings()
    )
    if relation_rows:
        op.bulk_insert(
            sa.table(
                "report_research_scope_relations",
                sa.column("id", sa.Uuid()),
                sa.column("scope_version_id", sa.Uuid()),
                sa.column("report_relation_id", sa.Uuid()),
                sa.column("created_at", sa.DateTime(timezone=True)),
            ),
            [
                {
                    "id": uuid4(),
                    "scope_version_id": row["scope_version_id"],
                    "report_relation_id": row["report_relation_id"],
                    "created_at": row["created_at"],
                }
                for row in relation_rows
            ],
        )
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")


def downgrade() -> None:
    bind = op.get_bind()
    for table in _IMMUTABLE_TABLES:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(f"refusing to downgrade: {table} contains {count} immutable records")
    if bind.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    op.drop_index(
        "ix_report_scope_relation_relation",
        table_name="report_research_scope_relations",
    )
    op.drop_table("report_research_scope_relations")
    op.drop_index("ix_report_scope_claim_claim", table_name="report_research_scope_claims")
    op.drop_table("report_research_scope_claims")
