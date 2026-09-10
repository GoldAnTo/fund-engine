"""Move acquisition query-plan lineage onto stable goal series.

Revision ID: 0056
Revises: 0055
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_empty_legacy_plan_table() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM acquisition_query_plans")
    ).scalar_one()
    if count:
        raise RuntimeError(
            "0056 cannot infer real run/scope goal-series identity for legacy "
            "query plans; migrate those rows explicitly before upgrading"
        )


def upgrade() -> None:
    _require_empty_legacy_plan_table()
    op.create_table(
        "acquisition_series",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=256), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("scope_version_id", sa.Uuid(), nullable=False),
        sa.Column("thesis_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trim(tenant_id) <> ''", name="ck_acquisition_series_tenant_nonblank"
        ),
        sa.CheckConstraint(
            "trim(goal_id) <> ''", name="ck_acquisition_series_goal_nonblank"
        ),
        sa.ForeignKeyConstraint(
            ["research_case_id"],
            ["research_cases.id"],
            name="fk_acquisition_series_research_case_id",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_acquisition_series_research_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["scope_version_id"],
            ["event_research_scope_versions.id"],
            name="fk_acquisition_series_scope_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.id"],
            name="fk_acquisition_series_thesis_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "research_case_id",
            "research_run_id",
            "scope_version_id",
            "thesis_id",
            "goal_id",
            name="uq_acquisition_series_goal_identity",
        ),
    )

    with op.batch_alter_table("acquisition_query_plans") as batch:
        batch.drop_constraint(
            "fk_acquisition_query_plans_previous_plan_lineage",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "uq_acquisition_query_plans_lineage_target", type_="unique"
        )
        batch.drop_constraint(
            "uq_acquisition_query_plans_job_round", type_="unique"
        )
        batch.add_column(sa.Column("series_id", sa.Uuid(), nullable=False))
        batch.create_foreign_key(
            "fk_acquisition_query_plans_series_id",
            "acquisition_series",
            ["series_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_acquisition_query_plans_job", ["acquisition_job_id"]
        )
        batch.create_unique_constraint(
            "uq_acquisition_query_plans_series_round",
            ["series_id", "acquisition_round"],
        )
        batch.create_unique_constraint(
            "uq_acquisition_query_plans_lineage_target",
            ["id", "series_id", "acquisition_round"],
        )
        batch.create_foreign_key(
            "fk_acquisition_query_plans_previous_plan_lineage",
            "acquisition_query_plans",
            [
                "previous_query_plan_id",
                "series_id",
                "previous_acquisition_round",
            ],
            ["id", "series_id", "acquisition_round"],
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER no_update_acquisition_series "
            "BEFORE UPDATE ON acquisition_series FOR EACH ROW "
            "EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            "CREATE TRIGGER no_delete_acquisition_series "
            "BEFORE DELETE ON acquisition_series FOR EACH ROW "
            "EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    incompatible = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM acquisition_query_plans current_plan "
            "JOIN acquisition_query_plans previous_plan "
            "ON previous_plan.id = current_plan.previous_query_plan_id "
            "WHERE current_plan.previous_query_plan_id IS NOT NULL "
            "AND current_plan.acquisition_job_id <> previous_plan.acquisition_job_id"
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError(
            "cannot downgrade 0056 while cross-job acquisition plan lineage exists"
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS no_delete_acquisition_series "
            "ON acquisition_series;"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS no_update_acquisition_series "
            "ON acquisition_series;"
        )

    with op.batch_alter_table("acquisition_query_plans") as batch:
        batch.drop_constraint(
            "fk_acquisition_query_plans_previous_plan_lineage",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_acquisition_query_plans_series_id", type_="foreignkey"
        )
        batch.drop_constraint(
            "uq_acquisition_query_plans_lineage_target", type_="unique"
        )
        batch.drop_constraint(
            "uq_acquisition_query_plans_series_round", type_="unique"
        )
        batch.drop_constraint(
            "uq_acquisition_query_plans_job", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_acquisition_query_plans_job_round",
            ["acquisition_job_id", "acquisition_round"],
        )
        batch.create_unique_constraint(
            "uq_acquisition_query_plans_lineage_target",
            ["id", "acquisition_job_id", "acquisition_round"],
        )
        batch.create_foreign_key(
            "fk_acquisition_query_plans_previous_plan_lineage",
            "acquisition_query_plans",
            [
                "previous_query_plan_id",
                "acquisition_job_id",
                "previous_acquisition_round",
            ],
            ["id", "acquisition_job_id", "acquisition_round"],
        )
        batch.drop_column("series_id")

    op.drop_table("acquisition_series")
