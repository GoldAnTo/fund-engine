"""Persist the independent research-preparation workbench.

Revision ID: 0052
Revises: 0051
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_preparations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("parse_claims_state", sa.String(length=16), nullable=False),
        sa.Column("draft_protocol_state", sa.String(length=16), nullable=False),
        sa.Column("draft_evidence_plan_state", sa.String(length=16), nullable=False),
        sa.Column("claim_review_state", sa.String(length=16), nullable=False),
        sa.Column("protocol_review_state", sa.String(length=16), nullable=False),
        sa.Column("plan_review_state", sa.String(length=16), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('preparing', 'awaiting_claim_review', "
            "'awaiting_protocol_confirmation', 'awaiting_plan_authorization', "
            "'recoverable_failure', 'authorized')",
            name="ck_research_preparations_status",
        ),
        sa.CheckConstraint(
            "parse_claims_state IN ('queued', 'running', 'succeeded', 'retrying', 'failed', 'stale')",
            name="ck_research_preparations_parse_claims_state",
        ),
        sa.CheckConstraint(
            "draft_protocol_state IN ('queued', 'running', 'succeeded', 'retrying', 'failed', 'stale')",
            name="ck_research_preparations_draft_protocol_state",
        ),
        sa.CheckConstraint(
            "draft_evidence_plan_state IN ('queued', 'running', 'succeeded', 'retrying', 'failed', 'stale')",
            name="ck_research_preparations_draft_evidence_plan_state",
        ),
        sa.CheckConstraint(
            "claim_review_state IN ('locked', 'awaiting_review', 'confirmed', 'stale')",
            name="ck_research_preparations_claim_review_state",
        ),
        sa.CheckConstraint(
            "protocol_review_state IN ('locked', 'awaiting_review', 'confirmed', 'stale')",
            name="ck_research_preparations_protocol_review_state",
        ),
        sa.CheckConstraint(
            "plan_review_state IN ('locked', 'awaiting_review', 'confirmed', 'stale')",
            name="ck_research_preparations_plan_review_state",
        ),
        sa.CheckConstraint(
            "(status = 'authorized' AND research_run_id IS NOT NULL) OR "
            "(status <> 'authorized' AND research_run_id IS NULL)",
            name="ck_research_preparations_authorized_run",
        ),
        sa.ForeignKeyConstraint(
            ["research_case_id"],
            ["research_cases.id"],
            name="fk_research_preparations_research_case_id",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_research_preparations_research_run_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("research_case_id", name="uq_research_preparations_research_case"),
    )
    op.create_index(
        "ix_research_preparations_research_case_id",
        "research_preparations",
        ["research_case_id"],
    )
    op.create_table(
        "research_preparation_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_preparation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("invalidated_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('atomic_claim_candidates', 'research_protocol_draft', 'evidence_acquisition_plan')",
            name="ck_research_preparation_artifacts_kind",
        ),
        sa.CheckConstraint(
            "state IN ('current', 'stale', 'superseded')",
            name="ck_research_preparation_artifacts_state",
        ),
        sa.ForeignKeyConstraint(
            ["research_preparation_id"],
            ["research_preparations.id"],
            name="fk_research_preparation_artifacts_preparation_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_preparation_id",
            "sequence",
            name="uq_research_preparation_artifacts_preparation_sequence",
        ),
    )
    op.create_index(
        "ix_research_preparation_artifacts_preparation_kind_state",
        "research_preparation_artifacts",
        ["research_preparation_id", "kind", "state"],
    )
    op.create_table(
        "research_preparation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_preparation_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "step IS NULL OR step IN ('parse_claims', 'draft_protocol', 'draft_evidence_plan')",
            name="ck_research_preparation_events_step",
        ),
        sa.ForeignKeyConstraint(
            ["research_preparation_id"],
            ["research_preparations.id"],
            name="fk_research_preparation_events_preparation_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_preparation_id",
            "seq",
            name="uq_research_preparation_events_preparation_seq",
        ),
    )
    op.create_index(
        "ix_research_preparation_events_preparation_seq",
        "research_preparation_events",
        ["research_preparation_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_preparation_events_preparation_seq",
        table_name="research_preparation_events",
    )
    op.drop_table("research_preparation_events")
    op.drop_index(
        "ix_research_preparation_artifacts_preparation_kind_state",
        table_name="research_preparation_artifacts",
    )
    op.drop_table("research_preparation_artifacts")
    op.drop_index(
        "ix_research_preparations_research_case_id",
        table_name="research_preparations",
    )
    op.drop_table("research_preparations")
