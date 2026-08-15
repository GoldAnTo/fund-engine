"""Persist the independent research-preparation workbench.

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


def upgrade() -> None:
    op.create_index(
        "uq_research_runs_case_id",
        "research_runs",
        ["research_case_id", "id"],
        unique=True,
    )
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
            ["research_case_id", "research_run_id"],
            ["research_runs.research_case_id", "research_runs.id"],
            name="fk_research_preparations_case_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("research_case_id", name="uq_research_preparations_research_case"),
    )
    op.create_table(
        "research_preparation_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_preparation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("preparation_version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=True),
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
    op.create_index(
        "uq_research_preparation_artifacts_current_kind",
        "research_preparation_artifacts",
        ["research_preparation_id", "kind"],
        unique=True,
        sqlite_where=sa.text("state = 'current'"),
        postgresql_where=sa.text("state = 'current'"),
    )
    op.create_table(
        "research_preparation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_preparation_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
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
    _create_event_immutability_triggers()


def downgrade() -> None:
    _drop_event_immutability_triggers()
    op.drop_table("research_preparation_events")
    op.drop_index(
        "uq_research_preparation_artifacts_current_kind",
        table_name="research_preparation_artifacts",
    )
    op.drop_index(
        "ix_research_preparation_artifacts_preparation_kind_state",
        table_name="research_preparation_artifacts",
    )
    op.drop_table("research_preparation_artifacts")
    op.drop_table("research_preparations")
    op.drop_index("uq_research_runs_case_id", table_name="research_runs")


def _create_event_immutability_triggers() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER no_update_research_preparation_events "
            "BEFORE UPDATE ON research_preparation_events FOR EACH ROW "
            "BEGIN SELECT RAISE(ABORT, 'table research_preparation_events is append-only'); END"
        )
        op.execute(
            "CREATE TRIGGER no_delete_research_preparation_events "
            "BEFORE DELETE ON research_preparation_events FOR EACH ROW "
            "BEGIN SELECT RAISE(ABORT, 'table research_preparation_events is append-only'); END"
        )
        return
    if op.get_bind().dialect.name == "postgresql":
        for operation in ("update", "delete"):
            op.execute(
                f"CREATE TRIGGER no_{operation}_research_preparation_events "
                f"BEFORE {operation.upper()} ON research_preparation_events "
                "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
            )


def _drop_event_immutability_triggers() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS no_update_research_preparation_events")
        op.execute("DROP TRIGGER IF EXISTS no_delete_research_preparation_events")
        return
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS no_update_research_preparation_events "
            "ON research_preparation_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS no_delete_research_preparation_events "
            "ON research_preparation_events"
        )
