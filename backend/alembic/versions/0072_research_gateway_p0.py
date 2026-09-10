"""Persist the private FundClaw Gateway P0 records.

Revision ID: 0072
Revises: 0071

The schema is written explicitly rather than generated from metadata because
the append-only trigger contract is database behavior, not ORM metadata.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROLE_KEYS = (
    "scope_identity",
    "sources_evidence",
    "analysis_counter_evidence",
    "compilation_checks",
)
_ROLE_KEY_CHECK = "role_key IN (" + ", ".join(f"'{key}'" for key in _ROLE_KEYS) + ")"
_ROLE_STATUS_CHECK = (
    "status IN ('queued', 'running', 'blocked', 'completed', 'failed', 'cancelled')"
)
_IMMUTABLE_TABLES = (
    "research_messages",
    "research_intents",
    "research_run_specs",
    "role_events",
    "gateway_commands",
)
_TRIGGER_ERROR = "immutable gateway table is append-only"


def _drop_immutable_triggers(dialect_name: str) -> None:
    for table_name in _IMMUTABLE_TABLES:
        suffix = "" if dialect_name == "sqlite" else f" ON {table_name}"
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table_name}{suffix}")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table_name}{suffix}")


def _install_immutable_triggers(dialect_name: str) -> None:
    _drop_immutable_triggers(dialect_name)
    for table_name in _IMMUTABLE_TABLES:
        if dialect_name == "sqlite":
            op.execute(
                f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
            )
            op.execute(
                f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
                f"BEGIN SELECT RAISE(ABORT, '{_TRIGGER_ERROR}'); END"
            )
            continue
        # ``reject_mutable_ledger`` is the shared PostgreSQL guard installed
        # by the base ledger migration. This revision must not own or drop it.
        op.execute(
            f"CREATE TRIGGER no_update_{table_name} BEFORE UPDATE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table_name} BEFORE DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "research_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("owner_subject_id", sa.String(length=128), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="private",
        ),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column(
            "event_retention_floor",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "next_message_sequence",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "next_event_sequence",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "visibility = 'private'",
            name="ck_research_conversations_private_visibility",
        ),
        sa.CheckConstraint(
            "event_retention_floor >= 1",
            name="ck_research_conversations_retention_floor",
        ),
        sa.CheckConstraint(
            "next_message_sequence >= 1",
            name="ck_research_conversations_next_message_sequence",
        ),
        sa.CheckConstraint(
            "next_event_sequence >= 1",
            name="ck_research_conversations_next_event_sequence",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "owner_subject_id",
            name="uq_research_conversations_provenance",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_conversations_tenant_id",
        "research_conversations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_research_conversations_owner_recent",
        "research_conversations",
        ["tenant_id", "owner_subject_id", "updated_at"],
    )

    op.create_table(
        "gateway_idempotency_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("client_key", sa.String(length=512), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column("receipt_json", sa.JSON(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name="ck_gateway_idempotency_requests_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_id",
            "operation",
            "client_key",
            name="uq_gateway_idempotency_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "tenant_id",
            "subject_id",
            name="uq_gateway_idempotency_request_provenance",
        ),
    )
    op.create_index(
        "ix_gateway_idempotency_requests_tenant_id",
        "gateway_idempotency_requests",
        ["tenant_id"],
    )
    op.create_index(
        "ix_gateway_idempotency_requests_subject_id",
        "gateway_idempotency_requests",
        ["subject_id"],
    )

    op.create_table(
        "research_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("message_kind", sa.String(length=16), nullable=False),
        sa.Column("role_key", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "message_kind IN ('user', 'system', 'role')",
            name="ck_research_messages_kind",
        ),
        sa.CheckConstraint(
            "role_key IS NULL OR " + _ROLE_KEY_CHECK,
            name="ck_research_messages_role_key",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64", name="ck_research_messages_input_sha256"
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["research_conversations.id"]),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_research_messages_conversation_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            name="uq_research_messages_provenance",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_research_messages_conversation_sequence",
        ),
    )
    op.create_index(
        "ix_research_messages_conversation_id",
        "research_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_research_messages_tenant_id", "research_messages", ["tenant_id"]
    )
    op.create_index(
        "ix_research_messages_conversation_sequence",
        "research_messages",
        ["conversation_id", "sequence"],
    )

    op.create_table(
        "research_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("parent_intent_id", sa.Uuid(), nullable=True),
        sa.Column("gateway_request_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("intent_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "intent_kind IN ('start', 'scope_change', 'grounded_question', "
            "'command', 'unsupported')",
            name="ck_research_intents_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'accepted', 'rejected', 'completed')",
            name="ck_research_intents_status",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64", name="ck_research_intents_input_sha256"
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["research_conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["research_messages.id"]),
        sa.ForeignKeyConstraint(["parent_intent_id"], ["research_intents.id"]),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_research_intents_conversation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_messages.id",
                "research_messages.conversation_id",
                "research_messages.tenant_id",
                "research_messages.subject_id",
            ],
            name="fk_research_intents_message_scope",
        ),
        sa.ForeignKeyConstraint(
            ["parent_intent_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_intents.id",
                "research_intents.conversation_id",
                "research_intents.tenant_id",
                "research_intents.subject_id",
            ],
            name="fk_research_intents_parent_scope",
        ),
        sa.ForeignKeyConstraint(
            ["gateway_request_id", "tenant_id", "subject_id"],
            [
                "gateway_idempotency_requests.id",
                "gateway_idempotency_requests.tenant_id",
                "gateway_idempotency_requests.subject_id",
            ],
            name="fk_research_intents_gateway_request_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            name="uq_research_intents_provenance",
        ),
    )
    for index_name, columns in (
        ("ix_research_intents_conversation_id", ["conversation_id"]),
        ("ix_research_intents_message_id", ["message_id"]),
        ("ix_research_intents_parent_intent_id", ["parent_intent_id"]),
        ("ix_research_intents_gateway_request_id", ["gateway_request_id"]),
        ("ix_research_intents_tenant_id", ["tenant_id"]),
        ("ix_research_intents_conversation_created", ["conversation_id", "created_at"]),
    ):
        op.create_index(index_name, "research_intents", columns)

    op.create_table(
        "research_run_specs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("gateway_request_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("native_case_id", sa.Uuid(), nullable=False),
        sa.Column("native_run_id", sa.Uuid(), nullable=False),
        sa.Column("frozen_scope_json", sa.JSON(), nullable=False),
        sa.Column("frozen_cutoff_json", sa.JSON(), nullable=False),
        sa.Column("frozen_source_policy_json", sa.JSON(), nullable=False),
        sa.Column("role_manifest_version", sa.String(length=128), nullable=False),
        sa.Column("capability_manifest_version", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("input_artifact_refs_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["research_conversations.id"]),
        sa.ForeignKeyConstraint(["intent_id"], ["research_intents.id"]),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_research_run_specs_conversation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["intent_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_intents.id",
                "research_intents.conversation_id",
                "research_intents.tenant_id",
                "research_intents.subject_id",
            ],
            name="fk_research_run_specs_intent_scope",
        ),
        sa.ForeignKeyConstraint(
            ["gateway_request_id", "tenant_id", "subject_id"],
            [
                "gateway_idempotency_requests.id",
                "gateway_idempotency_requests.tenant_id",
                "gateway_idempotency_requests.subject_id",
            ],
            name="fk_research_run_specs_gateway_request_scope",
        ),
        sa.ForeignKeyConstraint(["native_case_id"], ["research_cases.id"]),
        sa.ForeignKeyConstraint(
            ["native_case_id", "native_run_id"],
            ["research_runs.research_case_id", "research_runs.id"],
            name="fk_research_run_specs_native_case_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            name="uq_research_run_specs_provenance",
        ),
        sa.UniqueConstraint("native_run_id", name="uq_research_run_specs_native_run"),
        sa.UniqueConstraint(
            "gateway_request_id", name="uq_research_run_specs_gateway_request"
        ),
    )
    for index_name, columns in (
        ("ix_research_run_specs_conversation_id", ["conversation_id"]),
        ("ix_research_run_specs_intent_id", ["intent_id"]),
        ("ix_research_run_specs_gateway_request_id", ["gateway_request_id"]),
        ("ix_research_run_specs_tenant_id", ["tenant_id"]),
        ("ix_research_run_specs_native_case_id", ["native_case_id"]),
        ("ix_research_run_specs_native_run_id", ["native_run_id"]),
        ("ix_research_run_specs_correlation_id", ["correlation_id"]),
        (
            "ix_research_run_specs_conversation_created",
            ["conversation_id", "created_at"],
        ),
        ("ix_research_run_specs_native_case", ["native_case_id"]),
    ):
        op.create_index(index_name, "research_run_specs", columns)

    op.create_table(
        "role_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_spec_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("role_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="queued"
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_event_sequence", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ROLE_KEY_CHECK, name="ck_role_runs_role_key"),
        sa.CheckConstraint(_ROLE_STATUS_CHECK, name="ck_role_runs_status"),
        sa.CheckConstraint(
            "next_event_sequence >= 1", name="ck_role_runs_next_event_sequence"
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["research_conversations.id"]),
        sa.ForeignKeyConstraint(["run_spec_id"], ["research_run_specs.id"]),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_role_runs_conversation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["run_spec_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_run_specs.id",
                "research_run_specs.conversation_id",
                "research_run_specs.tenant_id",
                "research_run_specs.subject_id",
            ],
            name="fk_role_runs_spec_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_spec_id", "role_key", name="uq_role_runs_spec_role"),
        sa.UniqueConstraint(
            "id",
            "run_spec_id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            "role_key",
            name="uq_role_runs_provenance",
        ),
    )
    for index_name, columns in (
        ("ix_role_runs_conversation_id", ["conversation_id"]),
        ("ix_role_runs_run_spec_id", ["run_spec_id"]),
        ("ix_role_runs_tenant_id", ["tenant_id"]),
        ("ix_role_runs_run_spec_status", ["run_spec_id", "status"]),
    ):
        op.create_index(index_name, "role_runs", columns)

    op.create_table(
        "role_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_spec_id", sa.Uuid(), nullable=False),
        sa.Column("role_run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("run_sequence", sa.Integer(), nullable=False),
        sa.Column("role_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("display_text", sa.Text(), nullable=True),
        sa.Column("artifact_refs_json", sa.JSON(), nullable=False),
        sa.Column("source_key", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_ROLE_KEY_CHECK, name="ck_role_events_role_key"),
        sa.CheckConstraint(_ROLE_STATUS_CHECK, name="ck_role_events_status"),
        sa.CheckConstraint(
            "event_type IN ('role_queued', 'role_started', 'role_progress', "
            "'role_blocked', 'role_completed', 'role_failed', 'identity_decision', "
            "'source_waiting', 'evidence_available', 'rejected_source', "
            "'candidate', 'gap', 'draft_ref', 'validation', 'command_accepted', "
            "'command_rejected')",
            name="ck_role_events_type",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["research_conversations.id"]),
        sa.ForeignKeyConstraint(["run_spec_id"], ["research_run_specs.id"]),
        sa.ForeignKeyConstraint(["role_run_id"], ["role_runs.id"]),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_role_events_conversation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["run_spec_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_run_specs.id",
                "research_run_specs.conversation_id",
                "research_run_specs.tenant_id",
                "research_run_specs.subject_id",
            ],
            name="fk_role_events_spec_scope",
        ),
        sa.ForeignKeyConstraint(
            [
                "role_run_id",
                "run_spec_id",
                "conversation_id",
                "tenant_id",
                "subject_id",
                "role_key",
            ],
            [
                "role_runs.id",
                "role_runs.run_spec_id",
                "role_runs.conversation_id",
                "role_runs.tenant_id",
                "role_runs.subject_id",
                "role_runs.role_key",
            ],
            name="fk_role_events_role_run_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_role_events_conversation_sequence"
        ),
        sa.UniqueConstraint(
            "run_spec_id", "run_sequence", name="uq_role_events_run_sequence"
        ),
        sa.UniqueConstraint(
            "run_spec_id", "source_key", name="uq_role_events_native_source"
        ),
    )
    for index_name, columns in (
        ("ix_role_events_conversation_id", ["conversation_id"]),
        ("ix_role_events_run_spec_id", ["run_spec_id"]),
        ("ix_role_events_role_run_id", ["role_run_id"]),
        ("ix_role_events_tenant_id", ["tenant_id"]),
        ("ix_role_events_conversation_after", ["conversation_id", "sequence"]),
        ("ix_role_events_run_after", ["run_spec_id", "run_sequence"]),
    ):
        op.create_index(index_name, "role_events", columns)

    op.create_table(
        "gateway_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_spec_id", sa.Uuid(), nullable=False),
        sa.Column("gateway_request_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("actor_subject_id", sa.String(length=128), nullable=False),
        sa.Column("command_kind", sa.String(length=64), nullable=False),
        sa.Column("target_hash", sa.String(length=64), nullable=False),
        sa.Column("target_version", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "command_kind IN ('cancel', 'retry', 'request_counter_evidence', "
            "'scope_change', 'pause', 'resume', 'grounded_question')",
            name="ck_gateway_commands_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'rejected', 'completed')",
            name="ck_gateway_commands_outcome",
        ),
        sa.CheckConstraint(
            "length(target_hash) = 64", name="ck_gateway_commands_target_hash"
        ),
        sa.CheckConstraint(
            "actor_subject_id = subject_id",
            name="ck_gateway_commands_actor_subject",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["research_conversations.id"]),
        sa.ForeignKeyConstraint(["run_spec_id"], ["research_run_specs.id"]),
        sa.ForeignKeyConstraint(
            ["gateway_request_id", "tenant_id", "subject_id"],
            [
                "gateway_idempotency_requests.id",
                "gateway_idempotency_requests.tenant_id",
                "gateway_idempotency_requests.subject_id",
            ],
            name="fk_gateway_commands_gateway_request_scope",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_gateway_commands_conversation_scope",
        ),
        sa.ForeignKeyConstraint(
            ["run_spec_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_run_specs.id",
                "research_run_specs.conversation_id",
                "research_run_specs.tenant_id",
                "research_run_specs.subject_id",
            ],
            name="fk_gateway_commands_run_spec_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "gateway_request_id", name="uq_gateway_commands_gateway_request"
        ),
    )
    for index_name, columns in (
        ("ix_gateway_commands_conversation_id", ["conversation_id"]),
        ("ix_gateway_commands_run_spec_id", ["run_spec_id"]),
        ("ix_gateway_commands_gateway_request_id", ["gateway_request_id"]),
        ("ix_gateway_commands_tenant_id", ["tenant_id"]),
        ("ix_gateway_commands_conversation_created", ["conversation_id", "created_at"]),
    ):
        op.create_index(index_name, "gateway_commands", columns)

    _install_immutable_triggers(op.get_bind().dialect.name)


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    _drop_immutable_triggers(dialect_name)

    for index_name in (
        "ix_gateway_commands_conversation_created",
        "ix_gateway_commands_tenant_id",
        "ix_gateway_commands_gateway_request_id",
        "ix_gateway_commands_run_spec_id",
        "ix_gateway_commands_conversation_id",
    ):
        op.drop_index(index_name, table_name="gateway_commands")
    op.drop_table("gateway_commands")

    for index_name in (
        "ix_role_events_run_after",
        "ix_role_events_conversation_after",
        "ix_role_events_tenant_id",
        "ix_role_events_role_run_id",
        "ix_role_events_run_spec_id",
        "ix_role_events_conversation_id",
    ):
        op.drop_index(index_name, table_name="role_events")
    op.drop_table("role_events")

    for index_name in (
        "ix_role_runs_run_spec_status",
        "ix_role_runs_tenant_id",
        "ix_role_runs_run_spec_id",
        "ix_role_runs_conversation_id",
    ):
        op.drop_index(index_name, table_name="role_runs")
    op.drop_table("role_runs")

    for index_name in (
        "ix_research_run_specs_native_case",
        "ix_research_run_specs_conversation_created",
        "ix_research_run_specs_correlation_id",
        "ix_research_run_specs_native_run_id",
        "ix_research_run_specs_native_case_id",
        "ix_research_run_specs_tenant_id",
        "ix_research_run_specs_gateway_request_id",
        "ix_research_run_specs_intent_id",
        "ix_research_run_specs_conversation_id",
    ):
        op.drop_index(index_name, table_name="research_run_specs")
    op.drop_table("research_run_specs")

    for index_name in (
        "ix_research_intents_conversation_created",
        "ix_research_intents_tenant_id",
        "ix_research_intents_gateway_request_id",
        "ix_research_intents_parent_intent_id",
        "ix_research_intents_message_id",
        "ix_research_intents_conversation_id",
    ):
        op.drop_index(index_name, table_name="research_intents")
    op.drop_table("research_intents")

    for index_name in (
        "ix_research_messages_conversation_sequence",
        "ix_research_messages_tenant_id",
        "ix_research_messages_conversation_id",
    ):
        op.drop_index(index_name, table_name="research_messages")
    op.drop_table("research_messages")

    for index_name in (
        "ix_gateway_idempotency_requests_subject_id",
        "ix_gateway_idempotency_requests_tenant_id",
    ):
        op.drop_index(index_name, table_name="gateway_idempotency_requests")
    op.drop_table("gateway_idempotency_requests")

    op.drop_index(
        "ix_research_conversations_owner_recent", table_name="research_conversations"
    )
    op.drop_index(
        "ix_research_conversations_tenant_id", table_name="research_conversations"
    )
    op.drop_table("research_conversations")
