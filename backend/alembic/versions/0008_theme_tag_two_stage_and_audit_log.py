"""theme-tag two-stage review + audit_logs table

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-02

Two schema changes that share a migration because they together implement
the SPEC §"AI/人工边界" hardening:

1. ``case_theme_tag_events`` gains ``proposed_by`` / ``status`` /
   ``proposal_id`` so AI-initiated PATCH /research-cases/{id}/theme-tags
   calls land as ``status='pending'`` events that do not change the
   effective tag set until a human PATCH with the same desired set
   promotes them to ``status='confirmed'``.

2. New ``audit_logs`` table captures who/when/what for every command
   endpoint. This is distinct from the per-entity ledger tables
   (which carry the data) and from ``ai_runs`` (which audits AI model
   invocations). The whole write surface is now reviewable from a
   single timeline.

Both are append-only / immutable-ledger compliant (postgreSQL triggers
installed the same way as migration 0001).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Only tables CREATED by this migration belong here.  ``case_theme_tag_events``
# already had its triggers installed by 0007, so re-creating them raised
# DuplicateObject and blocked any fresh database from migrating to head.
IMMUTABLE_TABLES = ("audit_logs",)


def upgrade() -> None:
    # 1) case_theme_tag_events: two-stage review columns.
    op.add_column(
        "case_theme_tag_events",
        sa.Column(
            "proposed_by",
            sa.String(16),
            nullable=False,
            server_default="human",
        ),
    )
    op.add_column(
        "case_theme_tag_events",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="confirmed",
        ),
    )
    op.add_column(
        "case_theme_tag_events",
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_case_theme_tag_events_proposal_id",
        "case_theme_tag_events",
        ["proposal_id"],
    )
    op.create_index(
        "ix_case_theme_tag_events_status",
        "case_theme_tag_events",
        ["status"],
    )

    # 2) audit_logs: who/when/what timeline for every command endpoint.
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # Defence-in-depth, same convention as migration 0001.
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_case_theme_tag_events_status", table_name="case_theme_tag_events")
    op.drop_index(
        "ix_case_theme_tag_events_proposal_id", table_name="case_theme_tag_events"
    )
    op.drop_column("case_theme_tag_events", "proposal_id")
    op.drop_column("case_theme_tag_events", "status")
    op.drop_column("case_theme_tag_events", "proposed_by")
