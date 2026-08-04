"""Operational store, domain-event outbox, versioned entities, unified proposals.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-03

Adds four new model groups (all on the existing immutable/operational split):

  * Operational store (design §6.3): jobs, job_events, review_assignments,
    task_items, idempotency_keys, projection_checkpoints.
  * Domain-event outbox (design §9.4): domain_events.
  * Versioned entities (design §6.1): research_case_versions,
    thesis_versions, source_statement_versions, causal_step_versions,
    causal_edge_versions, evidence_link_versions.
  * Unified proposals + review decisions (design §5.3/§6.2): proposals,
    proposal_review_decisions.

These are additive: no existing table is altered, so the migration is safe on
a populated database.  The *Version tables initially seed no rows; they fill as
new reviewed/published objects flow through the proposal pipeline.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # Unified proposals + review decisions
    #
    # Created FIRST: review_assignments and every *_versions table carry FKs
    # to proposals / proposal_review_decisions, so these must already exist.
    # ------------------------------------------------------------------ #
    op.create_table(
        "proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("target_context", sa.JSON(), nullable=False),
        sa.Column("proposed_by_type", sa.String(16), nullable=False, server_default="ai"),
        sa.Column("proposed_by_ref", sa.String(128), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("basis_cutoff", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_entity_ids", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_proposals_kind_status", "proposals", ["kind", "status"])
    op.create_index("ix_proposals_case", "proposals", ["research_case_id"])

    op.create_table(
        "proposal_review_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=False
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("replacement_payload", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.String(128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_proposal_version", sa.Integer(), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # Operational store
    # ------------------------------------------------------------------ #
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("step", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=True,
        ),
        sa.Column("ai_run_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "job_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("step", sa.String(128), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_job_events_job_seq", "job_events", ["job_id", "seq"])

    op.create_table(
        "review_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=False
        ),
        sa.Column("assignee", sa.String(128), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "task_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("ref_type", sa.String(64), nullable=True),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=True,
        ),
        sa.Column("assignee", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "projection_checkpoints",
        sa.Column("consumer", sa.String(64), primary_key=True),
        sa.Column("watermark", sa.String(64), nullable=True),
        sa.Column("projection_schema_version", sa.String(32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # Domain-event outbox
    # ------------------------------------------------------------------ #
    op.create_table(
        "domain_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # Monotonic consumer cursor.  Random UUID ids cannot order an outbox,
        # so consumers page on this DB-assigned sequence instead.  The
        # PostgreSQL sequence + DEFAULT is attached right after create_table
        # (op.create_table does not emit CREATE SEQUENCE on its own).
        sa.Column(
            "seq",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
            unique=True,
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("ref_type", sa.String(64), nullable=True),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False, server_default="ledger"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_domain_events_type", "domain_events", ["type"])
    op.create_index("ix_domain_events_aggregate", "domain_events", ["aggregate_type", "aggregate_id"])
    op.create_index("ix_domain_events_seq", "domain_events", ["seq"])
    # PostgreSQL only: back ``seq`` with a real sequence so concurrent writers
    # get gap-free-enough monotonic values without a read-modify-write.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE SEQUENCE domain_events_seq_seq OWNED BY domain_events.seq")
        op.execute(
            "ALTER TABLE domain_events ALTER COLUMN seq "
            "SET DEFAULT nextval('domain_events_seq_seq')"
        )

    # ------------------------------------------------------------------ #
    # Versioned entities
    # ------------------------------------------------------------------ #
    op.create_table(
        "research_case_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("research_case_versions.id"),
            nullable=True,
        ),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=True),
        sa.Column(
            "review_decision_id",
            sa.Uuid(),
            sa.ForeignKey("proposal_review_decisions.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "thesis_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thesis_id", sa.Uuid(), sa.ForeignKey("theses.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("observation_start", sa.Date(), nullable=True),
        sa.Column("observation_end", sa.Date(), nullable=True),
        sa.Column("support_condition", sa.Text(), nullable=True),
        sa.Column("falsification_condition", sa.Text(), nullable=True),
        sa.Column("next_verification_event", sa.Text(), nullable=True),
        sa.Column("applicable_from", sa.Date(), nullable=True),
        sa.Column("applicable_to", sa.Date(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("thesis_versions.id"),
            nullable=True,
        ),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=True),
        sa.Column(
            "review_decision_id",
            sa.Uuid(),
            sa.ForeignKey("proposal_review_decisions.id"),
            nullable=True,
        ),
        sa.Column("creator_type", sa.String(32), nullable=False, server_default="human"),
        sa.Column(
            "review_state", sa.String(32), nullable=False, server_default="confirmed"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "source_statement_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_statement_id",
            sa.Uuid(),
            sa.ForeignKey("source_statements.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("observed_period", sa.Date(), nullable=True),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("metric_definition", sa.JSON(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("source_statement_versions.id"),
            nullable=True,
        ),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=True),
        sa.Column(
            "review_decision_id",
            sa.Uuid(),
            sa.ForeignKey("proposal_review_decisions.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "causal_step_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "causal_step_id",
            sa.Uuid(),
            sa.ForeignKey("causal_steps.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("causal_step_versions.id"),
            nullable=True,
        ),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=True),
        sa.Column(
            "review_decision_id",
            sa.Uuid(),
            sa.ForeignKey("proposal_review_decisions.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "causal_edge_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "causal_edge_id",
            sa.Uuid(),
            sa.ForeignKey("causal_edges.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("applicable_from", sa.Date(), nullable=True),
        sa.Column("applicable_to", sa.Date(), nullable=True),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("causal_edge_versions.id"),
            nullable=True,
        ),
        sa.Column("proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=True),
        sa.Column(
            "review_decision_id",
            sa.Uuid(),
            sa.ForeignKey("proposal_review_decisions.id"),
            nullable=True,
        ),
        sa.Column("creator_type", sa.String(32), nullable=False, server_default="ai"),
        sa.Column(
            "review_state",
            sa.String(32),
            nullable=False,
            server_default="machine_generated",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "evidence_link_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "evidence_link_id",
            sa.Uuid(),
            sa.ForeignKey("evidence_links.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("thesis_id", sa.Uuid(), sa.ForeignKey("theses.id"), nullable=False),
        sa.Column(
            "source_statement_id",
            sa.Uuid(),
            sa.ForeignKey("source_statements.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("applicable_from", sa.Date(), nullable=True),
        sa.Column("applicable_to", sa.Date(), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("evidence_link_versions.id"),
            nullable=True,
        ),
        sa.Column(
            "proposal_id", sa.Uuid(), sa.ForeignKey("proposals.id"), nullable=False
        ),
        sa.Column(
            "review_decision_id",
            sa.Uuid(),
            sa.ForeignKey("proposal_review_decisions.id"),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Strict reverse-dependency order: everything holding an FK to
    # proposals / proposal_review_decisions is dropped before they are.
    op.drop_table("evidence_link_versions")
    op.drop_table("causal_edge_versions")
    op.drop_table("causal_step_versions")
    op.drop_table("source_statement_versions")
    op.drop_table("thesis_versions")
    op.drop_table("research_case_versions")
    op.drop_index("ix_domain_events_seq", table_name="domain_events")
    op.drop_index("ix_domain_events_aggregate", table_name="domain_events")
    op.drop_index("ix_domain_events_type", table_name="domain_events")
    op.drop_table("domain_events")
    op.drop_table("projection_checkpoints")
    op.drop_table("idempotency_keys")
    op.drop_table("task_items")
    op.drop_table("review_assignments")
    op.drop_index("ix_job_events_job_seq", table_name="job_events")
    op.drop_table("job_events")
    op.drop_table("jobs")
    op.drop_table("proposal_review_decisions")
    op.drop_index("ix_proposals_case", table_name="proposals")
    op.drop_index("ix_proposals_kind_status", table_name="proposals")
    op.drop_table("proposals")
