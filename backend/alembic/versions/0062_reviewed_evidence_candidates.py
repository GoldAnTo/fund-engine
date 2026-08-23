"""Persist append-only reviewed evidence candidates.

Revision ID: 0062
Revises: 0061
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


IMMUTABLE_TABLES = (
    "uw_evidence_candidate_dossier_versions",
    "uw_evidence_candidate_review_versions",
)


def _create_immutable_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def _drop_immutable_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "uw_evidence_candidate_dossier_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dossier_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("source_manifest_id", sa.Uuid(), sa.ForeignKey("uw_source_manifest_versions.id"), nullable=False),
        sa.Column("scope_statement", sa.Text(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rejected_calculations", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_evidence_candidate_dossier_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purpose = 'evidence_candidate'", name="ck_uw_evidence_candidate_dossier_purpose"),
        sa.UniqueConstraint(
            "object_id", "basis_id", "dossier_key", "version",
            name="uq_uw_evidence_candidate_dossier_version",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_uw_evidence_candidate_dossier_successor"),
    )
    op.create_index(
        "ix_uw_evidence_candidate_dossiers_object_basis_status",
        "uw_evidence_candidate_dossier_versions",
        ["object_id", "basis_id", "status"],
    )
    op.create_table(
        "uw_evidence_candidate_review_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dossier_id", sa.Uuid(), sa.ForeignKey("uw_evidence_candidate_dossier_versions.id"), nullable=False),
        sa.Column("dossier_content_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewer_identity", sa.String(length=320), nullable=False),
        sa.Column("reviewer_role", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reviewer_role IN ('provenance', 'methodology')",
            name="ck_uw_evidence_candidate_review_role",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'request_changes')",
            name="ck_uw_evidence_candidate_review_decision",
        ),
        sa.UniqueConstraint(
            "dossier_id", "reviewer_identity", "reviewer_role",
            name="uq_uw_evidence_candidate_review_identity",
        ),
    )
    op.create_index(
        "ix_uw_evidence_candidate_reviews_dossier_role",
        "uw_evidence_candidate_review_versions",
        ["dossier_id", "reviewer_role"],
    )
    _create_immutable_triggers()


def downgrade() -> None:
    _drop_immutable_triggers()
    op.drop_index("ix_uw_evidence_candidate_reviews_dossier_role", table_name="uw_evidence_candidate_review_versions")
    op.drop_table("uw_evidence_candidate_review_versions")
    op.drop_index("ix_uw_evidence_candidate_dossiers_object_basis_status", table_name="uw_evidence_candidate_dossier_versions")
    op.drop_table("uw_evidence_candidate_dossier_versions")
