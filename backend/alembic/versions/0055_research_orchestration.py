"""Persist event-research orchestration state and history.

Revision ID: 0055
Revises: 0054
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = (
    "research_orchestration_events",
    "acquisition_query_plans",
)


def _create_immutable_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _IMMUTABLE_TABLES:
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
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "research_orchestrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=256), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("current_scope_version_id", sa.Uuid(), nullable=True),
        sa.Column("current_research_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state",
            sa.String(length=64),
            server_default="intake",
            nullable=False,
        ),
        sa.Column(
            "user_stage",
            sa.String(length=64),
            server_default="intake",
            nullable=False,
        ),
        sa.Column("current_system_action", sa.Text(), nullable=True),
        sa.Column("system_action_reason", sa.Text(), nullable=True),
        sa.Column(
            "checkpoint_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("next_action_kind", sa.String(length=64), nullable=True),
        sa.Column("next_action_label", sa.Text(), nullable=True),
        sa.Column("next_action_payload", sa.JSON(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_status", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trim(tenant_id) <> ''",
            name="ck_research_orchestrations_tenant_nonblank",
        ),
        sa.CheckConstraint(
            "state IN ('intake', 'awaiting_scope_confirmation', "
            "'planning_acquisition', 'acquiring', 'freezing_sources', "
            "'assessing_coverage', 'synthesizing_evidence', "
            "'adjudicating_thesis', 'generating_report', 'monitoring', "
            "'retry_wait', 'recovering', 'needs_scope_decision', 'exhausted', "
            "'cancelled', 'failed')",
            name="ck_research_orchestrations_state",
        ),
        sa.CheckConstraint(
            "user_stage IN ('intake', 'scope_confirmation', 'acquisition', "
            "'evidence_synthesis', 'thesis_adjudication', 'report_monitoring')",
            name="ck_research_orchestrations_user_stage",
        ),
        sa.CheckConstraint(
            "(next_action_kind IS NULL AND next_action_label IS NULL) OR "
            "(next_action_kind IS NOT NULL AND next_action_label IS NOT NULL)",
            name="ck_research_orchestrations_next_action",
        ),
        sa.CheckConstraint(
            "recovery_status IS NULL OR recovery_status IN "
            "('healthy', 'stale', 'recovering', 'failed')",
            name="ck_research_orchestrations_recovery_status",
        ),
        sa.CheckConstraint(
            "version >= 0",
            name="ck_research_orchestrations_version_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["research_case_id"],
            ["research_cases.id"],
            name="fk_research_orchestrations_research_case_id",
        ),
        sa.ForeignKeyConstraint(
            ["current_scope_version_id"],
            ["event_research_scope_versions.id"],
            name="fk_research_orchestrations_current_scope_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["current_research_run_id"],
            ["research_runs.id"],
            name="fk_research_orchestrations_current_research_run_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "research_case_id",
            name="uq_research_orchestrations_tenant_case",
        ),
    )
    op.create_index(
        "ix_research_orchestrations_current_scope_version",
        "research_orchestrations",
        ["current_scope_version_id"],
    )
    op.create_index(
        "ix_research_orchestrations_current_research_run",
        "research_orchestrations",
        ["current_research_run_id"],
    )

    op.create_table(
        "research_orchestration_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("orchestration_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("transition", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sequence >= 1",
            name="ck_research_orchestration_events_sequence_positive",
        ),
        sa.CheckConstraint(
            "trim(transition) <> ''",
            name="ck_research_orchestration_events_transition_nonblank",
        ),
        sa.CheckConstraint(
            "trim(actor) <> ''",
            name="ck_research_orchestration_events_actor_nonblank",
        ),
        sa.CheckConstraint(
            "trim(message) <> ''",
            name="ck_research_orchestration_events_message_nonblank",
        ),
        sa.CheckConstraint(
            "trim(idempotency_key) <> ''",
            name="ck_research_orchestration_events_idempotency_key_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["orchestration_id"],
            ["research_orchestrations.id"],
            name="fk_research_orchestration_events_orchestration_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "orchestration_id",
            "sequence",
            name="uq_research_orchestration_events_sequence",
        ),
        sa.UniqueConstraint(
            "orchestration_id",
            "idempotency_key",
            name="uq_research_orchestration_events_idempotency",
        ),
    )

    op.create_table(
        "acquisition_query_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("acquisition_job_id", sa.Uuid(), nullable=False),
        sa.Column("acquisition_round", sa.Integer(), nullable=False),
        sa.Column("goal_id", sa.String(length=256), nullable=False),
        sa.Column("planner_version", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("frozen_inputs_json", sa.JSON(), nullable=False),
        sa.Column("ordered_queries_json", sa.JSON(), nullable=False),
        sa.Column("previous_query_plan_id", sa.Uuid(), nullable=True),
        sa.Column("previous_acquisition_round", sa.Integer(), nullable=True),
        sa.Column("expansion_trigger", sa.Text(), nullable=True),
        sa.Column("diff_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "acquisition_round >= 1",
            name="ck_acquisition_query_plans_round_positive",
        ),
        sa.CheckConstraint(
            "trim(goal_id) <> ''",
            name="ck_acquisition_query_plans_goal_id_nonblank",
        ),
        sa.CheckConstraint(
            "trim(planner_version) <> ''",
            name="ck_acquisition_query_plans_planner_version_nonblank",
        ),
        sa.CheckConstraint(
            "trim(policy_version) <> ''",
            name="ck_acquisition_query_plans_policy_version_nonblank",
        ),
        sa.CheckConstraint(
            "(acquisition_round = 1 AND previous_query_plan_id IS NULL "
            "AND previous_acquisition_round IS NULL) OR "
            "(acquisition_round > 1 AND previous_query_plan_id IS NOT NULL "
            "AND previous_acquisition_round IS NOT NULL "
            "AND previous_acquisition_round = acquisition_round - 1 "
            "AND expansion_trigger IS NOT NULL "
            "AND trim(expansion_trigger) <> '')",
            name="ck_acquisition_query_plans_round_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["acquisition_job_id"],
            ["acquisition_jobs.id"],
            name="fk_acquisition_query_plans_acquisition_job_id",
        ),
        sa.ForeignKeyConstraint(
            [
                "previous_query_plan_id",
                "acquisition_job_id",
                "previous_acquisition_round",
            ],
            [
                "acquisition_query_plans.id",
                "acquisition_query_plans.acquisition_job_id",
                "acquisition_query_plans.acquisition_round",
            ],
            name="fk_acquisition_query_plans_previous_plan_lineage",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "acquisition_job_id",
            "acquisition_round",
            name="uq_acquisition_query_plans_job_round",
        ),
        sa.UniqueConstraint(
            "id",
            "acquisition_job_id",
            "acquisition_round",
            name="uq_acquisition_query_plans_lineage_target",
        ),
    )
    op.create_index(
        "ix_acquisition_query_plans_previous_query_plan",
        "acquisition_query_plans",
        ["previous_query_plan_id"],
    )

    op.create_table(
        "acquisition_goal_coverages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("scope_version_id", sa.Uuid(), nullable=False),
        sa.Column("thesis_id", sa.Uuid(), nullable=True),
        sa.Column("goal_id", sa.String(length=256), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="unmet",
            nullable=False,
        ),
        sa.Column(
            "required_authority_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "observed_authority_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "required_independent_source_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "observed_independent_source_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "contrary_search_completed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("evidence_link_ids_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trim(goal_id) <> ''",
            name="ck_acquisition_goal_coverages_goal_id_nonblank",
        ),
        sa.CheckConstraint(
            "trim(objective) <> ''",
            name="ck_acquisition_goal_coverages_objective_nonblank",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'unmet', 'exhausted')",
            name="ck_acquisition_goal_coverages_status",
        ),
        sa.CheckConstraint(
            "required_authority_count >= 0 AND observed_authority_count >= 0 "
            "AND required_independent_source_count >= 0 "
            "AND observed_independent_source_count >= 0",
            name="ck_acquisition_goal_coverages_counts_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_acquisition_goal_coverages_research_run_id",
        ),
        sa.ForeignKeyConstraint(
            ["scope_version_id"],
            ["event_research_scope_versions.id"],
            name="fk_acquisition_goal_coverages_scope_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id"],
            ["theses.id"],
            name="fk_acquisition_goal_coverages_thesis_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_run_id",
            "scope_version_id",
            "goal_id",
            name="uq_acquisition_goal_coverages_run_scope_goal",
        ),
    )
    op.create_index(
        "ix_acquisition_goal_coverages_thesis",
        "acquisition_goal_coverages",
        ["thesis_id"],
    )

    _create_immutable_triggers()


def downgrade() -> None:
    _drop_immutable_triggers()

    op.drop_index(
        "ix_acquisition_goal_coverages_thesis",
        table_name="acquisition_goal_coverages",
    )
    op.drop_table("acquisition_goal_coverages")
    op.drop_index(
        "ix_acquisition_query_plans_previous_query_plan",
        table_name="acquisition_query_plans",
    )
    op.drop_table("acquisition_query_plans")
    op.drop_table("research_orchestration_events")
    op.drop_index(
        "ix_research_orchestrations_current_research_run",
        table_name="research_orchestrations",
    )
    op.drop_index(
        "ix_research_orchestrations_current_scope_version",
        table_name="research_orchestrations",
    )
    op.drop_table("research_orchestrations")
