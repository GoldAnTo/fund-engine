"""Persistent records owned by the private FundClaw Gateway.

Gateway rows deliberately reference native Case and ResearchRun rows instead
of duplicating their authority. Conversations, role-run state, and request
leases are mutable operational rows. Messages, intents, run specifications,
role events, and commands are append-only records protected by both the
application ledger guard and migration-installed database triggers.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.ledger import Base, _uuid

ROLE_KEYS = (
    "scope_identity",
    "sources_evidence",
    "analysis_counter_evidence",
    "compilation_checks",
)
ARTIFACT_REFERENCE_KINDS = frozenset(
    {
        "evidence_link",
        "document_version",
        "research_case",
        "research_run",
        "draft",
        "validation",
        "decision",
        "source_contract",
        "review",
    }
)
MAX_GATEWAY_ARTIFACT_REFERENCES = 32
MAX_GATEWAY_ARTIFACT_REFS_JSON_BYTES = 4096
SHA256_DIGEST_LENGTH = 64
_SHA256_HEX_ALPHABET = "0123456789abcdef"
_ROLE_KEY_CHECK = "role_key IN (" + ", ".join(f"'{key}'" for key in ROLE_KEYS) + ")"
_ROLE_STATUS_CHECK = (
    "status IN ('queued', 'running', 'blocked', 'completed', 'failed', 'cancelled')"
)
_ROLE_EVENT_SOURCE_KEY_PREFIX = "sha256:"
_ROLE_EVENT_SOURCE_KEY_LENGTH = len(_ROLE_EVENT_SOURCE_KEY_PREFIX) + 64
_MAX_RAW_ROLE_EVENT_SOURCE_KEY_LENGTH = 512
MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH = 96
_ROLE_EVENT_SOURCE_KEY_REMAINDER = "substr(source_key, 8)"
for _hex_character in "0123456789abcdef":
    _ROLE_EVENT_SOURCE_KEY_REMAINDER = (
        f"replace({_ROLE_EVENT_SOURCE_KEY_REMAINDER}, '{_hex_character}', '')"
    )
_ROLE_EVENT_SOURCE_KEY_DIGEST_CHECK = (
    "CAST(source_key AS TEXT) = source_key "
    f"AND length(source_key) = {_ROLE_EVENT_SOURCE_KEY_LENGTH} "
    "AND substr(source_key, 1, 7) = 'sha256:' "
    f"AND length({_ROLE_EVENT_SOURCE_KEY_REMAINDER}) = 0"
)


def _sha256_digest_check(column_name: str) -> str:
    """Portable SQL predicate for a canonical lowercase SHA-256 digest."""
    remainder = column_name
    for hex_character in _SHA256_HEX_ALPHABET:
        remainder = f"replace({remainder}, '{hex_character}', '')"
    return (
        f"CAST({column_name} AS TEXT) = {column_name} "
        f"AND length({column_name}) = {SHA256_DIGEST_LENGTH} "
        f"AND length({remainder}) = 0"
    )


def _sqlite_nul_free_text_check(
    column_name: str,
    *,
    name: str,
) -> CheckConstraint:
    """Mirror SQLite's post-0074 NUL guards in unmanaged metadata DDL.

    PostgreSQL rejects embedded NUL bytes in text values itself, while SQLite's
    ``length`` and JSON1 functions stop at an embedded NUL.  Keep this
    supplementary constraint SQLite-only: deployed Gateway databases receive
    the equivalent trigger through migration 0074, avoiding another table
    rebuild solely for a metadata mirror.
    """
    return CheckConstraint(
        f"typeof({column_name}) = 'text' "
        f"AND instr({column_name}, char(0)) = 0",
        name=name,
    ).ddl_if(dialect="sqlite")


def _sqlite_nul_free_json_object_check(
    column_name: str,
    *,
    name: str,
) -> CheckConstraint:
    """Require a parseable object cache in SQLite-only unmanaged schemas."""
    return CheckConstraint(
        f"{column_name} IS NULL OR (typeof({column_name}) = 'text' "
        f"AND instr({column_name}, char(0)) = 0 "
        f"AND json_valid({column_name}) = 1 "
        f"AND json_type({column_name}) = 'object')",
        name=name,
    ).ddl_if(dialect="sqlite")


def require_canonical_sha256(value: str, *, field_name: str) -> str:
    """Reject payload-like values from fixed Gateway digest fields."""
    if (
        not isinstance(value, str)
        or len(value) != SHA256_DIGEST_LENGTH
        or any(character not in _SHA256_HEX_ALPHABET for character in value)
    ):
        raise ValueError(f"{field_name} must be a canonical SHA-256 digest")
    return value


class SafeRoleEventSummary(str, Enum):
    """A Gateway-owned display category, never provider supplied text."""

    ROLE_QUEUED = "role_queued"
    ROLE_STARTED = "role_started"
    ROLE_PROGRESS = "role_progress"
    ROLE_BLOCKED = "role_blocked"
    ROLE_COMPLETED = "role_completed"
    ROLE_FAILED = "role_failed"
    IDENTITY_DECISION = "identity_decision"
    SOURCE_WAITING = "source_waiting"
    EVIDENCE_AVAILABLE = "evidence_available"
    REJECTED_SOURCE = "rejected_source"
    CANDIDATE = "candidate"
    GAP = "gap"
    DRAFT_REF = "draft_ref"
    VALIDATION = "validation"
    COMMAND_ACCEPTED = "command_accepted"
    COMMAND_REJECTED = "command_rejected"


_SAFE_ROLE_EVENT_DISPLAY_TEXT = {
    SafeRoleEventSummary.ROLE_QUEUED: "Role queued",
    SafeRoleEventSummary.ROLE_STARTED: "Role started",
    SafeRoleEventSummary.ROLE_PROGRESS: "Role progress recorded",
    SafeRoleEventSummary.ROLE_BLOCKED: "Role is blocked",
    SafeRoleEventSummary.ROLE_COMPLETED: "Role completed",
    SafeRoleEventSummary.ROLE_FAILED: "Role failed",
    SafeRoleEventSummary.IDENTITY_DECISION: "Identity decision recorded",
    SafeRoleEventSummary.SOURCE_WAITING: "Waiting for approved source",
    SafeRoleEventSummary.EVIDENCE_AVAILABLE: "Approved evidence available",
    SafeRoleEventSummary.REJECTED_SOURCE: "Source rejected by policy",
    SafeRoleEventSummary.CANDIDATE: "Candidate recorded",
    SafeRoleEventSummary.GAP: "Evidence gap recorded",
    SafeRoleEventSummary.DRAFT_REF: "Draft artifact available",
    SafeRoleEventSummary.VALIDATION: "Validation completed",
    SafeRoleEventSummary.COMMAND_ACCEPTED: "Command accepted",
    SafeRoleEventSummary.COMMAND_REJECTED: "Command rejected",
}
_SAFE_ROLE_EVENT_DISPLAY_TEXTS = frozenset(_SAFE_ROLE_EVENT_DISPLAY_TEXT.values())
_SAFE_ROLE_EVENT_DISPLAY_TEXT_CHECK = (
    "display_text IS NULL OR (CAST(display_text AS TEXT) = display_text "
    "AND display_text IN ("
    + ", ".join(f"'{text}'" for text in sorted(_SAFE_ROLE_EVENT_DISPLAY_TEXTS))
    + "))"
)


class SafeRoleEventReason(str, Enum):
    """Stable, public-safe categories for blocked or failed role events."""

    UNSUPPORTED_IN_GATEWAY_P0 = "unsupported_in_gateway_p0"
    NATIVE_EXECUTION_FAILED = "native_execution_failed"
    AUTHORIZATION_REQUIRED = "authorization_required"
    CUTOFF_UNAVAILABLE = "cutoff_unavailable"
    SOURCE_POLICY_BLOCKED = "source_policy_blocked"
    SOURCE_UNAVAILABLE = "source_unavailable"
    EVIDENCE_GAP = "evidence_gap"
    VALIDATION_FAILED = "validation_failed"
    COMMAND_REJECTED = "command_rejected"


_SAFE_ROLE_EVENT_REASON_CODES = frozenset(
    reason.value for reason in SafeRoleEventReason
)
_SAFE_ROLE_EVENT_REASON_CODE_CHECK = (
    "reason_code IS NULL OR (CAST(reason_code AS TEXT) = reason_code "
    "AND reason_code IN ("
    + ", ".join(f"'{reason}'" for reason in sorted(_SAFE_ROLE_EVENT_REASON_CODES))
    + "))"
)

# Intents, commands, and role events all expose the same small public-safe
# vocabulary.  ``SafeRoleEventReason`` remains the compatibility name for the
# event API; the Gateway persistence boundary uses the broader alias below.
SafeGatewayReason = SafeRoleEventReason
_SAFE_GATEWAY_REASON_CODES = _SAFE_ROLE_EVENT_REASON_CODES
_SAFE_GATEWAY_REASON_CODE_CHECK = _SAFE_ROLE_EVENT_REASON_CODE_CHECK


def require_safe_gateway_reason_code(value: str | None) -> str | None:
    """Accept only a known server-owned reason token on immutable rows."""
    if value is None:
        return None
    if type(value) is SafeGatewayReason:
        return value.value
    if type(value) is not str or value not in _SAFE_GATEWAY_REASON_CODES:
        raise ValueError("Gateway reason code must be a safe reason")
    return value


def safe_gateway_reason_code(reason_code: SafeGatewayReason | None) -> str | None:
    """Convert the repository-only safe enum to its persisted token."""
    if reason_code is None:
        return None
    if type(reason_code) is not SafeGatewayReason:
        raise ValueError("Gateway records require a safe reason code")
    return reason_code.value


def require_absent_gateway_command_target_version(value: str | None) -> None:
    """P0 has no safe command version token, so it stores no version input."""
    if value is not None:
        raise ValueError("Gateway command target version is unavailable in P0")


def safe_role_event_display_text(
    event_type: str, safe_summary: SafeRoleEventSummary | None = None
) -> str:
    """Return the only display text that can be persisted for an event type."""
    if safe_summary is None:
        try:
            safe_summary = SafeRoleEventSummary(event_type)
        except ValueError as exc:
            raise ValueError("unsupported Gateway role event type") from exc
    if type(safe_summary) is not SafeRoleEventSummary:
        raise ValueError("Gateway role events require a server-owned safe summary")
    if safe_summary.value != event_type:
        raise ValueError("Gateway role event summary does not match event type")
    return _SAFE_ROLE_EVENT_DISPLAY_TEXT[safe_summary]


def safe_role_event_reason_code(
    reason_code: SafeRoleEventReason | None,
) -> str | None:
    """Convert a closed safe reason enum to its persisted public token."""
    try:
        return safe_gateway_reason_code(reason_code)
    except ValueError as exc:
        raise ValueError("Gateway role events require a safe reason code") from exc


def canonical_role_event_source_key(source_key: str) -> str:
    """Persist only an opaque digest of a native deduplication identity.

    The input stays in process just long enough to make replay idempotent;
    an append-only Gateway row never stores a provider key, trace, prompt, or
    raw tool identifier. Existing canonical digests are preserved for trusted
    bulk/migration writers.
    """
    if not isinstance(source_key, str) or not source_key.strip():
        raise ValueError("Gateway role event source key must not be empty")
    if len(source_key) > _MAX_RAW_ROLE_EVENT_SOURCE_KEY_LENGTH:
        raise ValueError("Gateway role event source key is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in source_key):
        raise ValueError("Gateway role event source key must not contain controls")
    if (
        len(source_key) == _ROLE_EVENT_SOURCE_KEY_LENGTH
        and source_key.startswith(_ROLE_EVENT_SOURCE_KEY_PREFIX)
        and all(character in "0123456789abcdef" for character in source_key[7:])
    ):
        return source_key
    return _ROLE_EVENT_SOURCE_KEY_PREFIX + hashlib.sha256(
        source_key.encode("utf-8")
    ).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """A safe, typed pointer to an already-authorized native artifact.

    There is intentionally no field for copied source content, prompt, tool
    arguments, or arbitrary payload. Gateway projections resolve the ID under
    normal tenant/case/cutoff authorization before showing an artifact.
    """

    kind: str
    id: uuid.UUID | str
    case_id: uuid.UUID | str | None = None
    locator_available: bool | None = None

    def to_json(self) -> dict[str, str | bool]:
        if self.kind not in ARTIFACT_REFERENCE_KINDS:
            raise ValueError("unsupported Gateway artifact reference kind")
        payload: dict[str, str | bool] = {
            "kind": self.kind,
            "id": str(uuid.UUID(str(self.id))),
        }
        if self.case_id is not None:
            payload["case_id"] = str(uuid.UUID(str(self.case_id)))
        if self.locator_available is not None:
            # ``bool`` subclasses ``int``.  Accept only the actual JSON
            # boolean type, never truthy provider text or numeric sentinels.
            if type(self.locator_available) is not bool:
                raise ValueError(
                    "Gateway artifact locator availability must be a boolean"
                )
            payload["locator_available"] = self.locator_available
        return payload


class ResearchConversation(Base):
    """A private thread owned by one authenticated tenant subject."""

    __tablename__ = "research_conversations"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "tenant_id",
            "owner_subject_id",
            name="uq_research_conversations_provenance",
        ),
        CheckConstraint(
            "visibility = 'private'",
            name="ck_research_conversations_private_visibility",
        ),
        CheckConstraint(
            "event_retention_floor >= 1",
            name="ck_research_conversations_retention_floor",
        ),
        CheckConstraint(
            "next_message_sequence >= 1",
            name="ck_research_conversations_next_message_sequence",
        ),
        CheckConstraint(
            "next_event_sequence >= 1",
            name="ck_research_conversations_next_event_sequence",
        ),
        Index(
            "ix_research_conversations_owner_recent",
            "tenant_id",
            "owner_subject_id",
            "updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owner_subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="private", server_default="private"
    )
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    event_retention_floor: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # Message and safe-event streams have independent cursors. The SSE cursor
    # is ``next_event_sequence``; a user message does not consume a role event.
    next_message_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    next_event_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class GatewayIdempotencyRequest(Base):
    """A mutable, actor-scoped lease and replay receipt for Gateway writes."""

    __tablename__ = "gateway_idempotency_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "subject_id",
            "operation",
            "client_key",
            name="uq_gateway_idempotency_scope",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "subject_id",
            name="uq_gateway_idempotency_request_provenance",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'failed')",
            name="ck_gateway_idempotency_requests_status",
        ),
        CheckConstraint(
            _sha256_digest_check("request_fingerprint"),
            name="ck_gateway_idempotency_requests_request_fingerprint",
        ),
        _sqlite_nul_free_text_check(
            "request_fingerprint",
            name="ck_gateway_idempotency_requests_request_fingerprint_nul_free",
        ),
        _sqlite_nul_free_json_object_check(
            "receipt_json",
            name="ck_gateway_idempotency_requests_receipt_json_safe",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    client_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="in_progress", server_default="in_progress"
    )
    receipt_json: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    @validates("request_fingerprint")
    def _validate_request_fingerprint(self, _key: str, value: str) -> str:
        return require_canonical_sha256(value, field_name="request fingerprint")


class ResearchMessage(Base):
    """Append-only visible input or Gateway-owned safe message."""

    __tablename__ = "research_messages"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            name="uq_research_messages_provenance",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_research_messages_conversation_sequence",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_research_messages_conversation_scope",
        ),
        CheckConstraint(
            "message_kind IN ('user', 'system', 'role')",
            name="ck_research_messages_kind",
        ),
        CheckConstraint(
            "role_key IS NULL OR " + _ROLE_KEY_CHECK,
            name="ck_research_messages_role_key",
        ),
        CheckConstraint(
            _sha256_digest_check("input_sha256"),
            name="ck_research_messages_input_sha256",
        ),
        _sqlite_nul_free_text_check(
            "input_sha256",
            name="ck_research_messages_input_sha256_nul_free",
        ),
        Index(
            "ix_research_messages_conversation_sequence", "conversation_id", "sequence"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    message_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user"
    )
    role_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    @validates("input_sha256")
    def _validate_input_sha256(self, _key: str, value: str) -> str:
        return require_canonical_sha256(value, field_name="message input hash")


class ResearchIntent(Base):
    """Append-only classified interpretation of one submitted message."""

    __tablename__ = "research_intents"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            name="uq_research_intents_provenance",
        ),
        # Nullable requests remain valid for non-idempotent/internal intents;
        # SQL NULL semantics allow more than one of those. A concrete Gateway
        # request, however, is the single receipt lineage for one intent.
        UniqueConstraint(
            "gateway_request_id", name="uq_research_intents_gateway_request"
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_research_intents_conversation_scope",
        ),
        ForeignKeyConstraint(
            ["message_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_messages.id",
                "research_messages.conversation_id",
                "research_messages.tenant_id",
                "research_messages.subject_id",
            ],
            name="fk_research_intents_message_scope",
        ),
        ForeignKeyConstraint(
            ["parent_intent_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_intents.id",
                "research_intents.conversation_id",
                "research_intents.tenant_id",
                "research_intents.subject_id",
            ],
            name="fk_research_intents_parent_scope",
        ),
        ForeignKeyConstraint(
            ["gateway_request_id", "tenant_id", "subject_id"],
            [
                "gateway_idempotency_requests.id",
                "gateway_idempotency_requests.tenant_id",
                "gateway_idempotency_requests.subject_id",
            ],
            name="fk_research_intents_gateway_request_scope",
        ),
        CheckConstraint(
            "intent_kind IN ('start', 'scope_change', 'grounded_question', "
            "'command', 'unsupported')",
            name="ck_research_intents_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'accepted', 'rejected', 'completed')",
            name="ck_research_intents_status",
        ),
        CheckConstraint(
            _sha256_digest_check("input_sha256"),
            name="ck_research_intents_input_sha256",
        ),
        _sqlite_nul_free_text_check(
            "input_sha256",
            name="ck_research_intents_input_sha256_nul_free",
        ),
        CheckConstraint(
            _SAFE_GATEWAY_REASON_CODE_CHECK,
            name="ck_research_intents_reason_code_safe",
        ),
        Index(
            "ix_research_intents_conversation_created", "conversation_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_messages.id"), nullable=False, index=True
    )
    parent_intent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_intents.id"), nullable=True, index=True
    )
    gateway_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    @validates("input_sha256")
    def _validate_input_sha256(self, _key: str, value: str) -> str:
        return require_canonical_sha256(value, field_name="intent input hash")

    @validates("reason_code")
    def _validate_reason_code(self, _key: str, value: str | None) -> str | None:
        return require_safe_gateway_reason_code(value)


class ResearchRunSpec(Base):
    """Immutable execution contract connected to native Case and Run IDs."""

    __tablename__ = "research_run_specs"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            name="uq_research_run_specs_provenance",
        ),
        UniqueConstraint("native_run_id", name="uq_research_run_specs_native_run"),
        UniqueConstraint(
            "gateway_request_id", name="uq_research_run_specs_gateway_request"
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_research_run_specs_conversation_scope",
        ),
        ForeignKeyConstraint(
            ["intent_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_intents.id",
                "research_intents.conversation_id",
                "research_intents.tenant_id",
                "research_intents.subject_id",
            ],
            name="fk_research_run_specs_intent_scope",
        ),
        ForeignKeyConstraint(
            ["native_case_id", "native_run_id"],
            ["research_runs.research_case_id", "research_runs.id"],
            name="fk_research_run_specs_native_case_run",
        ),
        ForeignKeyConstraint(
            ["gateway_request_id", "tenant_id", "subject_id"],
            [
                "gateway_idempotency_requests.id",
                "gateway_idempotency_requests.tenant_id",
                "gateway_idempotency_requests.subject_id",
            ],
            name="fk_research_run_specs_gateway_request_scope",
        ),
        _sqlite_nul_free_text_check(
            "input_artifact_refs_json",
            name="ck_research_run_specs_input_artifact_refs_nul_free",
        ),
        Index(
            "ix_research_run_specs_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index("ix_research_run_specs_native_case", "native_case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False, index=True
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_intents.id"), nullable=False, index=True
    )
    gateway_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    native_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False, index=True
    )
    native_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    frozen_scope: Mapped[dict] = mapped_column(
        "frozen_scope_json", JSON, nullable=False
    )
    frozen_cutoff: Mapped[dict] = mapped_column(
        "frozen_cutoff_json", JSON, nullable=False
    )
    frozen_source_policy: Mapped[dict] = mapped_column(
        "frozen_source_policy_json", JSON, nullable=False
    )
    role_manifest_version: Mapped[str] = mapped_column(String(128), nullable=False)
    capability_manifest_version: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    input_artifact_refs: Mapped[list] = mapped_column(
        "input_artifact_refs_json", JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    @property
    def scope(self) -> dict:
        return self.frozen_scope

    @property
    def cutoff(self) -> dict:
        return self.frozen_cutoff

    @property
    def source_policy(self) -> dict:
        return self.frozen_source_policy


class RoleRun(Base):
    """Mutable operational state for exactly one fixed role in a run spec."""

    __tablename__ = "role_runs"
    __table_args__ = (
        UniqueConstraint("run_spec_id", "role_key", name="uq_role_runs_spec_role"),
        UniqueConstraint(
            "id",
            "run_spec_id",
            "conversation_id",
            "tenant_id",
            "subject_id",
            "role_key",
            name="uq_role_runs_provenance",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_role_runs_conversation_scope",
        ),
        ForeignKeyConstraint(
            ["run_spec_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_run_specs.id",
                "research_run_specs.conversation_id",
                "research_run_specs.tenant_id",
                "research_run_specs.subject_id",
            ],
            name="fk_role_runs_spec_scope",
        ),
        CheckConstraint(_ROLE_KEY_CHECK, name="ck_role_runs_role_key"),
        CheckConstraint(_ROLE_STATUS_CHECK, name="ck_role_runs_status"),
        Index("ix_role_runs_run_spec_status", "run_spec_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False, index=True
    )
    run_spec_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_run_specs.id"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    claim_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_event_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class RoleEvent(Base):
    """Append-only, safe-to-display event with dual replay sequences."""

    __tablename__ = "role_events"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_role_events_conversation_sequence"
        ),
        UniqueConstraint(
            "run_spec_id", "run_sequence", name="uq_role_events_run_sequence"
        ),
        UniqueConstraint(
            "run_spec_id", "source_key", name="uq_role_events_native_source"
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_role_events_conversation_scope",
        ),
        ForeignKeyConstraint(
            ["run_spec_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_run_specs.id",
                "research_run_specs.conversation_id",
                "research_run_specs.tenant_id",
                "research_run_specs.subject_id",
            ],
            name="fk_role_events_spec_scope",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(_ROLE_KEY_CHECK, name="ck_role_events_role_key"),
        CheckConstraint(_ROLE_STATUS_CHECK, name="ck_role_events_status"),
        CheckConstraint(
            "display_text IS NULL OR (CAST(display_text AS TEXT) = display_text "
            f"AND length(display_text) <= {MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH})",
            name="ck_role_events_display_text_bounded",
        ),
        CheckConstraint(
            _SAFE_ROLE_EVENT_DISPLAY_TEXT_CHECK,
            name="ck_role_events_display_text_safe",
        ),
        CheckConstraint(
            _SAFE_ROLE_EVENT_REASON_CODE_CHECK,
            name="ck_role_events_reason_code_safe",
        ),
        CheckConstraint(
            _ROLE_EVENT_SOURCE_KEY_DIGEST_CHECK,
            name="ck_role_events_source_key_digest",
        ),
        _sqlite_nul_free_text_check(
            "source_key",
            name="ck_role_events_source_key_nul_free",
        ),
        _sqlite_nul_free_text_check(
            "artifact_refs_json",
            name="ck_role_events_artifact_refs_nul_free",
        ),
        CheckConstraint(
            "event_type IN ('role_queued', 'role_started', 'role_progress', "
            "'role_blocked', 'role_completed', 'role_failed', 'identity_decision', "
            "'source_waiting', 'evidence_available', 'rejected_source', "
            "'candidate', 'gap', 'draft_ref', 'validation', 'command_accepted', "
            "'command_rejected')",
            name="ck_role_events_type",
        ),
        Index("ix_role_events_conversation_after", "conversation_id", "sequence"),
        Index("ix_role_events_run_after", "run_spec_id", "run_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False, index=True
    )
    run_spec_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_run_specs.id"), nullable=False, index=True
    )
    role_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("role_runs.id"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    run_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_text: Mapped[str | None] = mapped_column(
        String(MAX_SAFE_ROLE_EVENT_DISPLAY_TEXT_LENGTH), nullable=True
    )
    artifact_refs: Mapped[list] = mapped_column(
        "artifact_refs_json", JSON, nullable=False, default=list
    )
    source_key: Mapped[str] = mapped_column(
        String(_ROLE_EVENT_SOURCE_KEY_LENGTH), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    @validates("display_text")
    def _validate_display_text(self, _key: str, value: str | None) -> str | None:
        if value is not None and value not in _SAFE_ROLE_EVENT_DISPLAY_TEXTS:
            raise ValueError("Gateway role event display text must be server-owned safe summary")
        return value

    @validates("reason_code")
    def _validate_reason_code(self, _key: str, value: str | None) -> str | None:
        if value is not None and value not in _SAFE_ROLE_EVENT_REASON_CODES:
            raise ValueError("Gateway role event reason code must be safe")
        return value

    @validates("source_key")
    def _validate_source_key(self, _key: str, value: str) -> str:
        return canonical_role_event_source_key(value)


class GatewayCommand(Base):
    """Append-only Gateway control receipt; it stores no raw command payload."""

    __tablename__ = "gateway_commands"
    __table_args__ = (
        UniqueConstraint(
            "gateway_request_id", name="uq_gateway_commands_gateway_request"
        ),
        ForeignKeyConstraint(
            ["gateway_request_id", "tenant_id", "subject_id"],
            [
                "gateway_idempotency_requests.id",
                "gateway_idempotency_requests.tenant_id",
                "gateway_idempotency_requests.subject_id",
            ],
            name="fk_gateway_commands_gateway_request_scope",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "subject_id"],
            [
                "research_conversations.id",
                "research_conversations.tenant_id",
                "research_conversations.owner_subject_id",
            ],
            name="fk_gateway_commands_conversation_scope",
        ),
        ForeignKeyConstraint(
            ["run_spec_id", "conversation_id", "tenant_id", "subject_id"],
            [
                "research_run_specs.id",
                "research_run_specs.conversation_id",
                "research_run_specs.tenant_id",
                "research_run_specs.subject_id",
            ],
            name="fk_gateway_commands_run_spec_scope",
        ),
        CheckConstraint(
            "command_kind IN ('cancel', 'retry', 'request_counter_evidence', "
            "'scope_change', 'pause', 'resume', 'grounded_question')",
            name="ck_gateway_commands_kind",
        ),
        CheckConstraint(
            "outcome IN ('accepted', 'rejected', 'completed')",
            name="ck_gateway_commands_outcome",
        ),
        CheckConstraint(
            _sha256_digest_check("target_hash"),
            name="ck_gateway_commands_target_hash",
        ),
        _sqlite_nul_free_text_check(
            "target_hash",
            name="ck_gateway_commands_target_hash_nul_free",
        ),
        CheckConstraint(
            "target_version IS NULL",
            name="ck_gateway_commands_target_version_absent",
        ),
        CheckConstraint(
            _SAFE_GATEWAY_REASON_CODE_CHECK,
            name="ck_gateway_commands_reason_code_safe",
        ),
        CheckConstraint(
            "actor_subject_id = subject_id",
            name="ck_gateway_commands_actor_subject",
        ),
        Index(
            "ix_gateway_commands_conversation_created", "conversation_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_conversations.id"), nullable=False, index=True
    )
    run_spec_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_run_specs.id"), nullable=False, index=True
    )
    gateway_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    command_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    @validates("target_hash")
    def _validate_target_hash(self, _key: str, value: str) -> str:
        return require_canonical_sha256(value, field_name="command target hash")

    @validates("target_version")
    def _validate_target_version(self, _key: str, value: str | None) -> None:
        return require_absent_gateway_command_target_version(value)

    @validates("reason_code")
    def _validate_reason_code(self, _key: str, value: str | None) -> str | None:
        return require_safe_gateway_reason_code(value)
