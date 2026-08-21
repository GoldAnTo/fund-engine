"""Add the append-only underwriting research kernel schema.

Revision ID: 0060
Revises: 0059
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


IMMUTABLE_TABLES = (
    "uw_research_objects",
    "uw_object_relations",
    "uw_mandate_versions",
    "uw_historical_bases",
    "uw_ledger_entries",
    "uw_research_versions",
    "uw_answerability_evaluations",
)


def upgrade() -> None:
    op.create_table(
        "uw_research_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("external_key", sa.String(length=160), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('industry', 'company', 'security')",
            name="ck_uw_object_kind",
        ),
        sa.UniqueConstraint(
            "kind", "external_key", name="uq_uw_object_external_key"
        ),
    )
    op.create_table(
        "uw_object_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "child_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=48), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "parent_id",
            "child_id",
            "relation_type",
            name="uq_uw_object_relation",
        ),
    )
    op.create_table(
        "uw_mandate_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mandate_key", sa.String(length=120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("horizon_years", sa.Integer(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("required_return", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column(
            "permanent_loss_limit", sa.Numeric(precision=12, scale=8), nullable=False
        ),
        sa.Column("comparison_set", sa.JSON(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_mandate_versions.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "horizon_years BETWEEN 3 AND 5",
            name="ck_uw_mandate_horizon",
        ),
        sa.CheckConstraint(
            "required_return >= 0 AND required_return < 1",
            name="ck_uw_required_return",
        ),
        sa.CheckConstraint(
            "permanent_loss_limit >= 0 AND permanent_loss_limit <= 1",
            name="ck_uw_loss_limit",
        ),
        sa.UniqueConstraint(
            "mandate_key", "version", name="uq_uw_mandate_version"
        ),
    )
    op.create_table(
        "uw_historical_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "uw_ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "basis_id",
            sa.Uuid(),
            sa.ForeignKey("uw_historical_bases.id"),
            nullable=False,
        ),
        sa.Column("ledger_kind", sa.String(length=16), nullable=False),
        sa.Column("family_key", sa.String(length=160), nullable=False),
        sa.Column("entry_type", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_boundary", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_ledger_entries.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ledger_kind IN ('reality', 'belief', 'decision', 'calibration')",
            name="ck_uw_ledger_kind",
        ),
        sa.UniqueConstraint(
            "object_id",
            "ledger_kind",
            "family_key",
            "version",
            name="uq_uw_ledger_family_version",
        ),
    )
    op.create_index(
        "ix_uw_ledger_entries_object_kind_available_at",
        "uw_ledger_entries",
        ["object_id", "ledger_kind", "available_at"],
    )
    op.create_table(
        "uw_research_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "basis_id",
            sa.Uuid(),
            sa.ForeignKey("uw_historical_bases.id"),
            nullable=False,
        ),
        sa.Column("version_kind", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_ids", sa.JSON(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_versions.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "object_id",
            "version_kind",
            "sequence",
            name="uq_uw_research_version_sequence",
        ),
    )
    op.create_index(
        "ix_uw_research_versions_object_kind_sequence",
        "uw_research_versions",
        ["object_id", "version_kind", "sequence"],
    )
    op.create_table(
        "uw_answerability_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column(
            "basis_id",
            sa.Uuid(),
            sa.ForeignKey("uw_historical_bases.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("research_debt_keys", sa.JSON(), nullable=False),
        sa.Column("resolvable_within_mandate", sa.Boolean(), nullable=False),
        sa.Column("allowed_action", sa.String(length=48), nullable=False),
        sa.Column("resolution_requirements", sa.JSON(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_answerability_evaluations.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('answerable', 'partially_answerable', 'not_answerable')",
            name="ck_uw_answerability_state",
        ),
        sa.CheckConstraint(
            "allowed_action IN ('observe', 'wait_for_validation', "
            "'eligible_for_probe_entry', 'eligible_for_staged_entry', 'do_not_enter')",
            name="ck_uw_answerability_action",
        ),
        sa.UniqueConstraint(
            "object_id",
            "basis_id",
            "version",
            name="uq_uw_answerability_version",
        ),
    )
    op.create_index(
        "ix_uw_answerability_evaluations_object_basis_version",
        "uw_answerability_evaluations",
        ["object_id", "basis_id", "version"],
    )

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


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")

    op.drop_index(
        "ix_uw_answerability_evaluations_object_basis_version",
        table_name="uw_answerability_evaluations",
    )
    op.drop_table("uw_answerability_evaluations")
    op.drop_index(
        "ix_uw_research_versions_object_kind_sequence",
        table_name="uw_research_versions",
    )
    op.drop_table("uw_research_versions")
    op.drop_index(
        "ix_uw_ledger_entries_object_kind_available_at",
        table_name="uw_ledger_entries",
    )
    op.drop_table("uw_ledger_entries")
    op.drop_table("uw_object_relations")
    op.drop_table("uw_historical_bases")
    op.drop_table("uw_mandate_versions")
    op.drop_table("uw_research_objects")
