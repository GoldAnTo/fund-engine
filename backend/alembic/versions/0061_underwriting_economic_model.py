"""Persist append-only underwriting economic research artefacts.

Revision ID: 0061
Revises: 0060
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


IMMUTABLE_TABLES = (
    "uw_source_manifest_versions",
    "uw_metric_definition_versions",
    "uw_metric_observations",
    "uw_mechanism_pack_versions",
    "uw_industry_state_versions",
    "uw_industry_scenario_versions",
    "uw_company_exposure_versions",
    "uw_earnings_engine_versions",
    "uw_forecast_input_versions",
    "uw_falsifier_versions",
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
        "uw_source_manifest_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("manifest_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_source_manifest_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_key", "version", name="uq_uw_source_manifest_version"),
    )
    op.create_index(
        "ix_uw_source_manifest_versions_basis_key_version",
        "uw_source_manifest_versions",
        ["basis_id", "manifest_key", "version"],
    )
    op.create_table(
        "uw_metric_definition_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("metric_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("source_manifest_id", sa.Uuid(), sa.ForeignKey("uw_source_manifest_versions.id"), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("unit", sa.String(length=48), nullable=False),
        sa.Column("period_semantics", sa.String(length=32), nullable=False),
        sa.Column("source_role", sa.String(length=32), nullable=False),
        sa.Column("aggregation", sa.String(length=32), nullable=False),
        sa.Column("reconciliation_tolerance", sa.Numeric(precision=28, scale=8), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_metric_definition_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("metric_key", "version", name="uq_uw_metric_definition_version"),
    )
    op.create_index(
        "ix_uw_metric_definition_versions_basis_key_version",
        "uw_metric_definition_versions",
        ["basis_id", "metric_key", "version"],
    )
    op.create_table(
        "uw_metric_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("metric_key", sa.String(length=160), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("uw_metric_definition_versions.id"), nullable=False),
        sa.Column("source_manifest_id", sa.Uuid(), sa.ForeignKey("uw_source_manifest_versions.id"), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=8), nullable=False),
        sa.Column("unit", sa.String(length=48), nullable=False),
        sa.Column("observed_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("dimension_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_metric_observations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "basis_id", "metric_key", "definition_version", "observed_end", "dimension_hash", "source_id",
            name="uq_uw_metric_observation_identity",
        ),
    )
    op.create_index(
        "ix_uw_metric_observations_basis_metric_available_at",
        "uw_metric_observations",
        ["basis_id", "metric_key", "available_at"],
    )
    op.create_table(
        "uw_mechanism_pack_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mechanism_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("source_manifest_id", sa.Uuid(), sa.ForeignKey("uw_source_manifest_versions.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("definition_ids", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_mechanism_pack_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mechanism_key", "version", name="uq_uw_mechanism_pack_version"),
    )
    op.create_index(
        "ix_uw_mechanism_pack_versions_object_basis_status",
        "uw_mechanism_pack_versions",
        ["object_id", "basis_id", "status"],
    )
    op.create_table(
        "uw_industry_state_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("mechanism_id", sa.Uuid(), sa.ForeignKey("uw_mechanism_pack_versions.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_industry_state_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("object_id", "basis_id", "version", name="uq_uw_industry_state_version"),
    )
    op.create_index(
        "ix_uw_industry_state_versions_object_basis_version",
        "uw_industry_state_versions",
        ["object_id", "basis_id", "version"],
    )
    op.create_table(
        "uw_industry_scenario_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scenario_key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("industry_state_id", sa.Uuid(), sa.ForeignKey("uw_industry_state_versions.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_industry_scenario_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("industry_state_id", "scenario_key", "version", name="uq_uw_industry_scenario_version"),
    )
    op.create_index(
        "ix_uw_industry_scenario_versions_state_key_version",
        "uw_industry_scenario_versions",
        ["industry_state_id", "scenario_key", "version"],
    )
    op.create_table(
        "uw_company_exposure_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
        sa.Column("industry_state_id", sa.Uuid(), sa.ForeignKey("uw_industry_state_versions.id"), nullable=False),
        sa.Column("exposure_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_company_exposure_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "industry_state_id", "exposure_key", "version", name="uq_uw_company_exposure_version"),
    )
    op.create_index(
        "ix_uw_company_exposure_versions_company_basis_key",
        "uw_company_exposure_versions",
        ["company_id", "basis_id", "exposure_key"],
    )
    op.create_table(
        "uw_earnings_engine_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("industry_state_id", sa.Uuid(), sa.ForeignKey("uw_industry_state_versions.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_earnings_engine_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "basis_id", "version", name="uq_uw_earnings_engine_version"),
    )
    op.create_index(
        "ix_uw_earnings_engine_versions_company_basis_version",
        "uw_earnings_engine_versions",
        ["company_id", "basis_id", "version"],
    )
    op.create_table(
        "uw_forecast_input_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("uw_research_objects.id"), nullable=False),
        sa.Column("input_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("earnings_engine_id", sa.Uuid(), sa.ForeignKey("uw_earnings_engine_versions.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_forecast_input_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "basis_id", "input_key", "version", name="uq_uw_forecast_input_version"),
    )
    op.create_index(
        "ix_uw_forecast_input_versions_company_basis_key",
        "uw_forecast_input_versions",
        ["company_id", "basis_id", "input_key"],
    )
    op.create_table(
        "uw_falsifier_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mechanism_id", sa.Uuid(), sa.ForeignKey("uw_mechanism_pack_versions.id"), nullable=False),
        sa.Column("falsifier_key", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_id", sa.Uuid(), sa.ForeignKey("uw_historical_bases.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("uw_falsifier_versions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mechanism_id", "falsifier_key", "version", name="uq_uw_falsifier_version"),
    )
    op.create_index(
        "ix_uw_falsifier_versions_mechanism_key_version",
        "uw_falsifier_versions",
        ["mechanism_id", "falsifier_key", "version"],
    )
    _create_immutable_triggers()


def downgrade() -> None:
    _drop_immutable_triggers()
    for index, table in (
        ("ix_uw_falsifier_versions_mechanism_key_version", "uw_falsifier_versions"),
        ("ix_uw_forecast_input_versions_company_basis_key", "uw_forecast_input_versions"),
        ("ix_uw_earnings_engine_versions_company_basis_version", "uw_earnings_engine_versions"),
        ("ix_uw_company_exposure_versions_company_basis_key", "uw_company_exposure_versions"),
        ("ix_uw_industry_scenario_versions_state_key_version", "uw_industry_scenario_versions"),
        ("ix_uw_industry_state_versions_object_basis_version", "uw_industry_state_versions"),
        ("ix_uw_mechanism_pack_versions_object_basis_status", "uw_mechanism_pack_versions"),
        ("ix_uw_metric_observations_basis_metric_available_at", "uw_metric_observations"),
        ("ix_uw_metric_definition_versions_basis_key_version", "uw_metric_definition_versions"),
        ("ix_uw_source_manifest_versions_basis_key_version", "uw_source_manifest_versions"),
    ):
        op.drop_index(index, table_name=table)
    for table in reversed(IMMUTABLE_TABLES):
        op.drop_table(table)
