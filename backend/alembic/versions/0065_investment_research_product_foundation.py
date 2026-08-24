"""Add the investment research product persistence foundation.

Revision ID: 0065
Revises: 0064
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


IMMUTABLE_PRODUCT_TABLES = (
    "uw_object_identity_versions",
    "uw_research_projects",
    "uw_research_project_securities",
    "uw_research_scope_versions",
    "uw_research_agenda_versions",
    "uw_price_snapshots",
    "uw_fx_snapshots",
    "uw_capital_structure_snapshots",
    "uw_security_rights_versions",
    "uw_research_assessment_versions",
    "uw_revision_boundaries",
    "uw_revision_manifests",
)

LEGACY_REVISION_INDEX = "uq_uw_research_version_legacy_sequence"
PRODUCT_REVISION_INDEX = "uq_uw_research_version_project_sequence"


def _add_compatibility_columns() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "uw_mandate_versions", recreate="always"
        ) as batch:
            batch.add_column(sa.Column("project_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("benchmark_key", sa.String(120), nullable=True))
            batch.add_column(
                sa.Column(
                    "required_excess_return", sa.Numeric(precision=12, scale=8), nullable=True
                )
            )
            batch.add_column(
                sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch.add_column(
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
            batch.create_foreign_key(
                "fk_uw_mandate_project",
                "uw_research_projects",
                ["project_id"],
                ["id"],
            )
        with op.batch_alter_table(
            "uw_historical_bases", recreate="always"
        ) as batch:
            batch.add_column(
                sa.Column("definition_bundle_hash", sa.String(64), nullable=True)
            )
            batch.add_column(
                sa.Column("parser_bundle_hash", sa.String(64), nullable=True)
            )
            batch.add_column(
                sa.Column("boundary_schema_version", sa.String(32), nullable=True)
            )
            batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        with op.batch_alter_table(
            "uw_research_versions", recreate="always"
        ) as batch:
            batch.drop_constraint("uq_uw_research_version_sequence", type_="unique")
            batch.add_column(sa.Column("project_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("boundary_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("manifest_id", sa.Uuid(), nullable=True))
            batch.add_column(sa.Column("manifest_schema", sa.String(64), nullable=True))
            batch.add_column(
                sa.Column("publication_status", sa.String(16), nullable=True)
            )
            batch.create_foreign_key(
                "fk_uw_research_version_project",
                "uw_research_projects",
                ["project_id"],
                ["id"],
            )
            batch.create_foreign_key(
                "fk_uw_research_version_boundary",
                "uw_revision_boundaries",
                ["boundary_id"],
                ["id"],
            )
            batch.create_foreign_key(
                "fk_uw_research_version_manifest",
                "uw_revision_manifests",
                ["manifest_id"],
                ["id"],
            )
        return

    op.add_column("uw_mandate_versions", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column(
        "uw_mandate_versions", sa.Column("benchmark_key", sa.String(120), nullable=True)
    )
    op.add_column(
        "uw_mandate_versions",
        sa.Column(
            "required_excess_return", sa.Numeric(precision=12, scale=8), nullable=True
        ),
    )
    op.add_column(
        "uw_mandate_versions",
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "uw_mandate_versions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "uw_mandate_versions", sa.Column("content_hash", sa.String(64), nullable=True)
    )
    op.create_foreign_key(
        "fk_uw_mandate_project",
        "uw_mandate_versions",
        "uw_research_projects",
        ["project_id"],
        ["id"],
    )
    op.add_column(
        "uw_historical_bases",
        sa.Column("definition_bundle_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "uw_historical_bases",
        sa.Column("parser_bundle_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "uw_historical_bases",
        sa.Column("boundary_schema_version", sa.String(32), nullable=True),
    )
    op.add_column(
        "uw_historical_bases", sa.Column("content_hash", sa.String(64), nullable=True)
    )
    op.add_column("uw_research_versions", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("uw_research_versions", sa.Column("boundary_id", sa.Uuid(), nullable=True))
    op.add_column("uw_research_versions", sa.Column("manifest_id", sa.Uuid(), nullable=True))
    op.add_column(
        "uw_research_versions",
        sa.Column("manifest_schema", sa.String(64), nullable=True),
    )
    op.add_column(
        "uw_research_versions",
        sa.Column("publication_status", sa.String(16), nullable=True),
    )
    op.create_foreign_key(
        "fk_uw_research_version_project",
        "uw_research_versions",
        "uw_research_projects",
        ["project_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_uw_research_version_boundary",
        "uw_research_versions",
        "uw_revision_boundaries",
        ["boundary_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_uw_research_version_manifest",
        "uw_research_versions",
        "uw_revision_manifests",
        ["manifest_id"],
        ["id"],
    )
    op.drop_constraint(
        "uq_uw_research_version_sequence", "uw_research_versions", type_="unique"
    )


def _drop_compatibility_columns() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "uw_research_versions", recreate="always"
        ) as batch:
            batch.drop_constraint("fk_uw_research_version_manifest", type_="foreignkey")
            batch.drop_constraint("fk_uw_research_version_boundary", type_="foreignkey")
            batch.drop_constraint("fk_uw_research_version_project", type_="foreignkey")
            batch.drop_column("publication_status")
            batch.drop_column("manifest_schema")
            batch.drop_column("manifest_id")
            batch.drop_column("boundary_id")
            batch.drop_column("project_id")
            batch.create_unique_constraint(
                "uq_uw_research_version_sequence",
                ["object_id", "version_kind", "sequence"],
            )
        with op.batch_alter_table(
            "uw_historical_bases", recreate="always"
        ) as batch:
            batch.drop_column("content_hash")
            batch.drop_column("boundary_schema_version")
            batch.drop_column("parser_bundle_hash")
            batch.drop_column("definition_bundle_hash")
        with op.batch_alter_table(
            "uw_mandate_versions", recreate="always"
        ) as batch:
            batch.drop_constraint("fk_uw_mandate_project", type_="foreignkey")
            batch.drop_column("content_hash")
            batch.drop_column("expires_at")
            batch.drop_column("effective_at")
            batch.drop_column("required_excess_return")
            batch.drop_column("benchmark_key")
            batch.drop_column("project_id")
        return

    op.drop_constraint(
        "fk_uw_research_version_manifest", "uw_research_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_uw_research_version_boundary", "uw_research_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_uw_research_version_project", "uw_research_versions", type_="foreignkey"
    )
    op.create_unique_constraint(
        "uq_uw_research_version_sequence",
        "uw_research_versions",
        ["object_id", "version_kind", "sequence"],
    )
    for column in (
        "publication_status",
        "manifest_schema",
        "manifest_id",
        "boundary_id",
        "project_id",
    ):
        op.drop_column("uw_research_versions", column)
    for column in (
        "content_hash",
        "boundary_schema_version",
        "parser_bundle_hash",
        "definition_bundle_hash",
    ):
        op.drop_column("uw_historical_bases", column)
    op.drop_constraint(
        "fk_uw_mandate_project", "uw_mandate_versions", type_="foreignkey"
    )
    for column in (
        "content_hash",
        "expires_at",
        "effective_at",
        "required_excess_return",
        "benchmark_key",
        "project_id",
    ):
        op.drop_column("uw_mandate_versions", column)


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    json_type = "json_type" if dialect_name == "sqlite" else "json_typeof"
    op.create_table(
        "uw_object_identity_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "object_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("symbol", sa.String(48), nullable=True),
        sa.Column("exchange", sa.String(32), nullable=True),
        sa.Column("share_class", sa.String(64), nullable=True),
        sa.Column("trading_currency", sa.String(3), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_object_identity_versions.id"),
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_uw_object_identity_version"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_uw_object_identity_effective_interval",
        ),
        sa.CheckConstraint(
            "trading_currency IS NULL OR length(trading_currency) = 3",
            name="ck_uw_object_identity_currency",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_object_identity_content_hash"
        ),
        sa.UniqueConstraint(
            "object_id", "version", name="uq_uw_object_identity_version"
        ),
        sa.UniqueConstraint(
            "supersedes_id", name="uq_uw_object_identity_successor"
        ),
    )
    op.create_index(
        "ix_uw_object_identity_object_effective_from",
        "uw_object_identity_versions",
        ["object_id", "effective_from"],
    )
    op.create_table(
        "uw_research_projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "primary_company_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_research_project_content_hash"
        ),
    )
    op.create_index(
        "ix_uw_research_projects_company",
        "uw_research_projects",
        ["primary_company_id"],
    )
    op.create_table(
        "uw_research_project_securities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "security_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_uw_research_project_security_content_hash",
        ),
        sa.UniqueConstraint(
            "project_id",
            "security_id",
            name="uq_uw_project_security",
        ),
    )
    op.create_index(
        "ix_uw_project_securities_security",
        "uw_research_project_securities",
        ["security_id"],
    )
    op.create_table(
        "uw_research_scope_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(none_as_null=True), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_scope_versions.id"),
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_uw_research_scope_version"),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_research_scope_content_hash"
        ),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_uw_research_scope_version"
        ),
        sa.UniqueConstraint(
            "supersedes_id", name="uq_uw_research_scope_successor"
        ),
    )
    op.create_table(
        "uw_research_agenda_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "scope_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("generator_provenance", sa.JSON(none_as_null=True), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_agenda_versions.id"),
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_uw_research_agenda_version"),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_research_agenda_content_hash"
        ),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_uw_research_agenda_version"
        ),
        sa.UniqueConstraint(
            "supersedes_id", name="uq_uw_research_agenda_successor"
        ),
    )
    op.create_index(
        "ix_uw_research_agenda_scope", "uw_research_agenda_versions", ["scope_id"]
    )
    op.create_table(
        "uw_price_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "security_identity_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(28, 10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_type", sa.String(64), nullable=False),
        sa.Column("adjustment_basis", sa.String(64), nullable=False),
        sa.Column("market_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price > 0", name="ck_uw_price_snapshot_positive"),
        sa.CheckConstraint(
            "length(currency) = 3", name="ck_uw_price_snapshot_currency"
        ),
        sa.CheckConstraint(
            "market_at <= available_at", name="ck_uw_price_snapshot_available"
        ),
        sa.CheckConstraint(
            "length(raw_hash) = 64", name="ck_uw_price_snapshot_raw_hash"
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_price_snapshot_content_hash"
        ),
        sa.UniqueConstraint(
            "security_identity_id",
            "price_type",
            "adjustment_basis",
            "market_at",
            "source_id",
            "raw_hash",
            name="uq_uw_price_snapshot_identity",
        ),
    )
    op.create_index(
        "ix_uw_price_snapshot_security_market",
        "uw_price_snapshots",
        ["security_identity_id", "market_at"],
    )
    op.create_table(
        "uw_fx_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(28, 12), nullable=False),
        sa.Column("quote_direction", sa.String(32), nullable=False),
        sa.Column("market_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(base_currency) = 3 AND length(quote_currency) = 3",
            name="ck_uw_fx_snapshot_currencies",
        ),
        sa.CheckConstraint(
            "base_currency <> quote_currency", name="ck_uw_fx_snapshot_pair"
        ),
        sa.CheckConstraint("rate > 0", name="ck_uw_fx_snapshot_positive"),
        sa.CheckConstraint(
            "quote_direction = 'quote_per_base'",
            name="ck_uw_fx_snapshot_quote_direction",
        ),
        sa.CheckConstraint(
            "market_at <= available_at", name="ck_uw_fx_snapshot_available"
        ),
        sa.CheckConstraint("length(raw_hash) = 64", name="ck_uw_fx_snapshot_raw_hash"),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_fx_snapshot_content_hash"
        ),
        sa.UniqueConstraint(
            "base_currency",
            "quote_currency",
            "quote_direction",
            "market_at",
            "source_id",
            "raw_hash",
            name="uq_uw_fx_snapshot_identity",
        ),
    )
    op.create_index(
        "ix_uw_fx_snapshot_pair_market",
        "uw_fx_snapshots",
        ["base_currency", "quote_currency", "market_at"],
    )
    op.create_table(
        "uw_capital_structure_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cash", sa.Numeric(28, 10), nullable=False),
        sa.Column("debt", sa.Numeric(28, 10), nullable=False),
        sa.Column("minority_interest", sa.Numeric(28, 10), nullable=False),
        sa.Column("investments", sa.Numeric(28, 10), nullable=False),
        sa.Column("pension_liabilities", sa.Numeric(28, 10), nullable=False),
        sa.Column("other_adjustments", sa.Numeric(28, 10), nullable=False),
        sa.Column("basic_shares", sa.Numeric(28, 10), nullable=False),
        sa.Column("diluted_shares", sa.Numeric(28, 10), nullable=False),
        sa.Column(
            "potential_dilution_descriptors",
            sa.JSON(none_as_null=True),
            nullable=False,
        ),
        sa.Column("report_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(currency) = 3", name="ck_uw_capital_structure_currency"
        ),
        sa.CheckConstraint(
            "basic_shares > 0 AND diluted_shares >= basic_shares",
            name="ck_uw_capital_structure_shares",
        ),
        sa.CheckConstraint(
            "report_period_start <= report_period_end",
            name="ck_uw_capital_structure_report_period",
        ),
        sa.CheckConstraint(
            "market_at <= available_at", name="ck_uw_capital_structure_available"
        ),
        sa.CheckConstraint(
            "length(raw_hash) = 64", name="ck_uw_capital_structure_raw_hash"
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_uw_capital_structure_content_hash",
        ),
        sa.UniqueConstraint(
            "company_id",
            "report_period_start",
            "report_period_end",
            "market_at",
            "source_id",
            "raw_hash",
            name="uq_uw_capital_structure_identity",
        ),
    )
    op.create_index(
        "ix_uw_capital_structure_company_market",
        "uw_capital_structure_snapshots",
        ["company_id", "market_at"],
    )
    op.create_table(
        "uw_security_rights_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "security_identity_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_objects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("economic_units", sa.Numeric(28, 10), nullable=False),
        sa.Column("votes_per_unit", sa.Numeric(28, 10), nullable=False),
        sa.Column("conversion_ratio", sa.Numeric(28, 10), nullable=False),
        sa.Column("adr_ratio", sa.Numeric(28, 10), nullable=False),
        sa.Column("dividend_rights_per_unit", sa.Numeric(28, 10), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_security_rights_versions.id"),
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_uw_security_rights_version"),
        sa.CheckConstraint(
            "economic_units > 0 AND conversion_ratio > 0 AND adr_ratio > 0",
            name="ck_uw_security_rights_positive",
        ),
        sa.CheckConstraint(
            "votes_per_unit >= 0 AND dividend_rights_per_unit >= 0",
            name="ck_uw_security_rights_nonnegative",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_uw_security_rights_effective_interval",
        ),
        sa.CheckConstraint(
            "length(raw_hash) = 64", name="ck_uw_security_rights_raw_hash"
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_security_rights_content_hash"
        ),
        sa.UniqueConstraint(
            "security_identity_id", "version", name="uq_uw_security_rights_version"
        ),
        sa.UniqueConstraint(
            "supersedes_id", name="uq_uw_security_rights_successor"
        ),
    )
    op.create_index(
        "ix_uw_security_rights_security_effective",
        "uw_security_rights_versions",
        ["security_identity_id", "effective_from"],
    )
    op.create_table(
        "uw_research_assessment_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_assessment_versions.id"),
            nullable=True,
        ),
        sa.Column("answerability", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(32), nullable=True),
        sa.Column("confidence", sa.String(16), nullable=True),
        sa.Column("publication_status", sa.String(24), nullable=False),
        sa.Column("blockers", sa.JSON(none_as_null=True), nullable=False),
        sa.Column(
            "resolution_requirements", sa.JSON(none_as_null=True), nullable=False
        ),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_uw_research_assessment_version"),
        sa.CheckConstraint(
            "answerability IN ('answerable', 'partially_answerable', 'not_answerable')",
            name="ck_uw_research_assessment_answerability",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN "
            "('provisional_bullish', 'provisional_neutral', 'provisional_cautious')",
            name="ck_uw_research_assessment_direction",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name="ck_uw_research_assessment_confidence",
        ),
        sa.CheckConstraint(
            "publication_status IN ('user_frozen', 'superseded')",
            name="ck_uw_research_assessment_publication",
        ),
        sa.CheckConstraint(
            "answerability <> 'not_answerable' OR "
            "(direction IS NULL AND confidence IS NULL)",
            name="ck_uw_research_assessment_not_answerable",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_uw_research_assessment_content_hash",
        ),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_uw_research_assessment_version"
        ),
        sa.UniqueConstraint(
            "supersedes_id", name="uq_uw_research_assessment_successor"
        ),
    )
    op.create_table(
        "uw_workspace_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "base_revision_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_versions.id"),
            nullable=True,
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_uw_workspace_draft_lock_version"
        ),
        sa.UniqueConstraint("project_id", name="uq_uw_workspace_draft_project"),
    )
    op.create_table(
        "uw_revision_boundaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "historical_basis_id",
            sa.Uuid(),
            sa.ForeignKey("uw_historical_bases.id"),
            nullable=False,
        ),
        sa.Column(
            "mandate_id",
            sa.Uuid(),
            sa.ForeignKey("uw_mandate_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "scope_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "agenda_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_agenda_versions.id"),
            nullable=False,
        ),
        sa.Column("price_snapshot_ids", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("fx_snapshot_ids", sa.JSON(none_as_null=True), nullable=False),
        sa.Column(
            "capital_structure_snapshot_id",
            sa.Uuid(),
            sa.ForeignKey("uw_capital_structure_snapshots.id"),
            nullable=False,
        ),
        sa.Column("security_rights_ids", sa.JSON(none_as_null=True), nullable=False),
        sa.Column(
            "parent_revision_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_versions.id"),
            nullable=True,
        ),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(schema_version)) > 0",
            name="ck_uw_revision_boundary_schema_version",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_revision_boundary_content_hash"
        ),
        sa.CheckConstraint(
            f"{json_type}(price_snapshot_ids) = 'array'",
            name="ck_uw_revision_boundary_price_refs_array",
        ),
        sa.CheckConstraint(
            f"{json_type}(fx_snapshot_ids) = 'array'",
            name="ck_uw_revision_boundary_fx_refs_array",
        ),
        sa.CheckConstraint(
            f"{json_type}(security_rights_ids) = 'array'",
            name="ck_uw_revision_boundary_rights_refs_array",
        ),
    )
    op.create_index(
        "ix_uw_revision_boundary_project_created",
        "uw_revision_boundaries",
        ["project_id", "created_at"],
    )
    op.create_table(
        "uw_revision_manifests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("uw_research_projects.id"),
            nullable=False,
        ),
        sa.Column(
            "boundary_id",
            sa.Uuid(),
            sa.ForeignKey("uw_revision_boundaries.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("manifest", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name="ck_uw_revision_manifest_idempotency_key",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name="ck_uw_revision_manifest_content_hash"
        ),
        sa.CheckConstraint(
            f"{json_type}(manifest) = 'object'",
            name="ck_uw_revision_manifest_object",
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_uw_revision_manifest_idempotency",
        ),
        sa.UniqueConstraint("boundary_id", name="uq_uw_revision_manifest_boundary"),
    )

    _add_compatibility_columns()

    op.create_index(
        LEGACY_REVISION_INDEX,
        "uw_research_versions",
        ["object_id", "version_kind", "sequence"],
        unique=True,
        sqlite_where=sa.text("project_id IS NULL"),
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        PRODUCT_REVISION_INDEX,
        "uw_research_versions",
        ["project_id", "version_kind", "sequence"],
        unique=True,
        sqlite_where=sa.text("project_id IS NOT NULL"),
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ix_uw_research_versions_project",
        "uw_research_versions",
        ["project_id"],
    )
    op.create_index(
        "ix_uw_mandate_versions_project",
        "uw_mandate_versions",
        ["project_id"],
    )

    if dialect_name == "sqlite":
        for table_name in IMMUTABLE_PRODUCT_TABLES:
            op.execute(f"""
                CREATE TRIGGER no_update_{table_name}
                BEFORE UPDATE ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'immutable product table is append-only');
                END;
            """)
            op.execute(f"""
                CREATE TRIGGER no_delete_{table_name}
                BEFORE DELETE ON {table_name}
                BEGIN
                    SELECT RAISE(ABORT, 'immutable product table is append-only');
                END;
            """)
        op.execute("""
            CREATE TRIGGER no_delete_uw_workspace_drafts
            BEFORE DELETE ON uw_workspace_drafts
            BEGIN
                SELECT RAISE(ABORT, 'immutable product table is append-only');
            END;
        """)
        return
    for table_name in IMMUTABLE_PRODUCT_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
    op.execute(
        "CREATE TRIGGER no_delete_uw_workspace_drafts "
        "BEFORE DELETE ON uw_workspace_drafts "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS no_delete_uw_workspace_drafts "
            "ON uw_workspace_drafts;"
        )
        for table_name in reversed(IMMUTABLE_PRODUCT_TABLES):
            op.execute(
                f"DROP TRIGGER IF EXISTS no_delete_{table_name} ON {table_name};"
            )
            op.execute(
                f"DROP TRIGGER IF EXISTS no_update_{table_name} ON {table_name};"
            )
    elif dialect_name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS no_delete_uw_workspace_drafts;")
        for table_name in reversed(IMMUTABLE_PRODUCT_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name};")
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name};")

    op.drop_index("ix_uw_mandate_versions_project", table_name="uw_mandate_versions")
    op.drop_index("ix_uw_research_versions_project", table_name="uw_research_versions")
    op.drop_index(PRODUCT_REVISION_INDEX, table_name="uw_research_versions")
    op.drop_index(LEGACY_REVISION_INDEX, table_name="uw_research_versions")

    _drop_compatibility_columns()

    op.drop_table("uw_revision_manifests")
    op.drop_index(
        "ix_uw_revision_boundary_project_created",
        table_name="uw_revision_boundaries",
    )
    op.drop_table("uw_revision_boundaries")
    op.drop_table("uw_workspace_drafts")
    op.drop_table("uw_research_assessment_versions")
    op.drop_index(
        "ix_uw_security_rights_security_effective",
        table_name="uw_security_rights_versions",
    )
    op.drop_table("uw_security_rights_versions")
    op.drop_index(
        "ix_uw_capital_structure_company_market",
        table_name="uw_capital_structure_snapshots",
    )
    op.drop_table("uw_capital_structure_snapshots")
    op.drop_index("ix_uw_fx_snapshot_pair_market", table_name="uw_fx_snapshots")
    op.drop_table("uw_fx_snapshots")
    op.drop_index(
        "ix_uw_price_snapshot_security_market", table_name="uw_price_snapshots"
    )
    op.drop_table("uw_price_snapshots")
    op.drop_index(
        "ix_uw_research_agenda_scope", table_name="uw_research_agenda_versions"
    )
    op.drop_table("uw_research_agenda_versions")
    op.drop_table("uw_research_scope_versions")
    op.drop_index(
        "ix_uw_project_securities_security",
        table_name="uw_research_project_securities",
    )
    op.drop_table("uw_research_project_securities")
    op.drop_index(
        "ix_uw_research_projects_company", table_name="uw_research_projects"
    )
    op.drop_table("uw_research_projects")
    op.drop_index(
        "ix_uw_object_identity_object_effective_from",
        table_name="uw_object_identity_versions",
    )
    op.drop_table("uw_object_identity_versions")
