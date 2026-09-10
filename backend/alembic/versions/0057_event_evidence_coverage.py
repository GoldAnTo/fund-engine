"""Persist goal-bound automatic mappings and versioned coverage details.

Revision ID: 0057
Revises: 0056
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("event_research_scope_evidence_assignments") as batch:
        batch.add_column(
            sa.Column(
                "assignment_kind",
                sa.String(length=16),
                nullable=False,
                server_default="reviewed",
            )
        )
        batch.add_column(sa.Column("research_run_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("acquisition_goal_id", sa.String(length=256), nullable=True)
        )
        batch.add_column(
            sa.Column("automatic_admission_decision_id", sa.Uuid(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "automatic_provenance_json",
                sa.JSON(none_as_null=True),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_event_scope_evidence_assignment_research_run",
            "research_runs",
            ["research_run_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_event_scope_evidence_assignment_admission_decision",
            "automatic_admission_decisions",
            ["automatic_admission_decision_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_event_research_scope_evidence_assignment_kind",
            "assignment_kind IN ('reviewed', 'automatic')",
        )
        batch.create_check_constraint(
            "ck_event_research_scope_evidence_assignment_lineage",
            "(assignment_kind = 'reviewed' AND research_run_id IS NULL "
            "AND acquisition_goal_id IS NULL "
            "AND automatic_admission_decision_id IS NULL "
            "AND automatic_provenance_json IS NULL) OR "
            "(assignment_kind = 'automatic' AND research_run_id IS NOT NULL "
            "AND acquisition_goal_id IS NOT NULL "
            "AND trim(acquisition_goal_id) <> '' "
            "AND automatic_admission_decision_id IS NOT NULL "
            "AND automatic_provenance_json IS NOT NULL)",
        )

    with op.batch_alter_table("acquisition_goal_coverages") as batch:
        batch.add_column(
            sa.Column(
                "policy_version",
                sa.String(length=128),
                nullable=False,
                server_default="event-goal-coverage-v1",
            )
        )
        batch.add_column(
            sa.Column(
                "required_authority_levels_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "observed_authority_levels_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "independent_source_identities_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "conflict_details_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column(
                "unknown_details_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(
            sa.Column("evaluation_round", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column(
                "zero_new_independent_source_rounds",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_check_constraint(
            "ck_acquisition_goal_coverages_policy_version_nonblank",
            "trim(policy_version) <> ''",
        )
        batch.create_check_constraint(
            "ck_acquisition_goal_coverages_rounds_non_negative",
            "evaluation_round >= 1 AND zero_new_independent_source_rounds >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("acquisition_goal_coverages") as batch:
        batch.drop_constraint(
            "ck_acquisition_goal_coverages_rounds_non_negative", type_="check"
        )
        batch.drop_constraint(
            "ck_acquisition_goal_coverages_policy_version_nonblank", type_="check"
        )
        batch.drop_column("zero_new_independent_source_rounds")
        batch.drop_column("evaluation_round")
        batch.drop_column("unknown_details_json")
        batch.drop_column("conflict_details_json")
        batch.drop_column("independent_source_identities_json")
        batch.drop_column("policy_version")
        batch.drop_column("observed_authority_levels_json")
        batch.drop_column("required_authority_levels_json")

    with op.batch_alter_table("event_research_scope_evidence_assignments") as batch:
        batch.drop_constraint(
            "ck_event_research_scope_evidence_assignment_lineage", type_="check"
        )
        batch.drop_constraint(
            "ck_event_research_scope_evidence_assignment_kind", type_="check"
        )
        batch.drop_constraint(
            "fk_event_scope_evidence_assignment_admission_decision",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_event_scope_evidence_assignment_research_run", type_="foreignkey"
        )
        batch.drop_column("automatic_provenance_json")
        batch.drop_column("automatic_admission_decision_id")
        batch.drop_column("acquisition_goal_id")
        batch.drop_column("research_run_id")
        batch.drop_column("assignment_kind")
