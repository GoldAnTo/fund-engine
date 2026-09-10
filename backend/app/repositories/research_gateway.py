"""Repository for Gateway-private persistence and scoped idempotency.

The repository owns allocation of both message and safe-event cursors.  It
never invokes a provider: a later service layer composes its short lease and
its caller-owned native/Gateway write transaction around these methods.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.errors import ConflictError
from app.models.operational import ResearchRun
from app.models.research_gateway import (
    ROLE_KEYS,
    ArtifactReference,
    GatewayCommand,
    GatewayIdempotencyRequest,
    ResearchConversation,
    ResearchIntent,
    ResearchMessage,
    ResearchRunSpec,
    RoleEvent,
    RoleRun,
    SafeGatewayReason,
    SafeRoleEventReason,
    SafeRoleEventSummary,
    canonical_role_event_source_key,
    require_absent_gateway_command_target_version,
    require_canonical_sha256,
    require_safe_gateway_reason_code,
    safe_gateway_reason_code,
    safe_role_event_display_text,
    safe_role_event_reason_code,
)
from app.services.research_gateway_policy import UNSUPPORTED_FOLLOWUP_MESSAGE


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _message_request_fingerprint(conversation_id: uuid.UUID, text: str) -> str:
    """Match the service-owned fingerprint for an existing-message request."""
    payload = {
        "conversation_id": str(conversation_id),
        "text": text,
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_EVENT_STATUS = {
    "role_queued": "queued",
    "role_started": "running",
    "role_progress": "running",
    "role_blocked": "blocked",
    "role_completed": "completed",
    "role_failed": "failed",
    "identity_decision": "completed",
    "source_waiting": "blocked",
    "evidence_available": "running",
    "rejected_source": "blocked",
    "candidate": "running",
    "gap": "blocked",
    "draft_ref": "running",
    "validation": "completed",
    "command_accepted": "running",
    "command_rejected": "blocked",
}
_MESSAGE_OPERATION = "send_message"
_COMMAND_OPERATION = "issue_command"
_EVENT_SEQUENCE_RETRY_ATTEMPTS = 3
_SQLITE_BUSY_RETRY_ATTEMPTS = 6
_SQLITE_BUSY_RETRY_DELAY_SECONDS = 0.025
_INITIAL_ROLE_EVENT_SOURCE_PREFIX = "gateway:run-spec"
_GATEWAY_RECEIPT_PAYLOAD_KEYS = frozenset(
    {
        "conversation_id",
        "intent_id",
        "run_spec_id",
        "native_case_id",
        "native_run_id",
        "status",
        "latest_sequence",
    }
)
_GATEWAY_COMMAND_RECEIPT_PAYLOAD_KEYS = frozenset(
    {
        "receipt_kind",
        "command_id",
        "conversation_id",
        "run_spec_id",
        "command_kind",
        "outcome",
        "reason_code",
    }
)
_GATEWAY_INTENT_RECEIPT_PAYLOAD_KEYS = frozenset(
    {
        "receipt_kind",
        "conversation_id",
        "intent_id",
        "intent_kind",
        "outcome",
        "reason_code",
    }
)
_GATEWAY_COMMAND_KINDS = frozenset(
    {
        "cancel",
        "retry",
        "request_counter_evidence",
        "scope_change",
        "pause",
        "resume",
        "grounded_question",
    }
)
_GATEWAY_COMMAND_OUTCOMES = frozenset({"accepted", "rejected", "completed"})


@dataclass(frozen=True, slots=True)
class GatewayReceipt:
    """The durable response reconstructed without re-running native intake."""

    conversation_id: uuid.UUID
    intent_id: uuid.UUID
    run_spec_id: uuid.UUID
    native_case_id: uuid.UUID
    native_run_id: uuid.UUID
    status: str
    latest_sequence: int

    def __post_init__(self) -> None:
        if self.status != "queued":
            raise ValueError("Gateway run receipt status must be queued")
        if type(self.latest_sequence) is not int or self.latest_sequence < 0:
            raise ValueError("Gateway run receipt sequence must be a nonnegative integer")

    def to_payload(self) -> dict[str, str | int]:
        return {
            "conversation_id": str(self.conversation_id),
            "intent_id": str(self.intent_id),
            "run_spec_id": str(self.run_spec_id),
            "native_case_id": str(self.native_case_id),
            "native_run_id": str(self.native_run_id),
            "status": self.status,
            "latest_sequence": self.latest_sequence,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> GatewayReceipt:
        if type(payload) is not dict or set(payload) != _GATEWAY_RECEIPT_PAYLOAD_KEYS:
            raise ValueError("Gateway run receipt payload has an invalid schema")
        uuid_fields = (
            "conversation_id",
            "intent_id",
            "run_spec_id",
            "native_case_id",
            "native_run_id",
        )
        if any(type(payload[field]) is not str for field in uuid_fields):
            raise ValueError("Gateway run receipt IDs must be strings")
        if type(payload["status"]) is not str:
            raise ValueError("Gateway run receipt status must be a string")
        if type(payload["latest_sequence"]) is not int:
            raise ValueError("Gateway run receipt sequence must be an integer")
        return cls(
            conversation_id=uuid.UUID(payload["conversation_id"]),
            intent_id=uuid.UUID(payload["intent_id"]),
            run_spec_id=uuid.UUID(payload["run_spec_id"]),
            native_case_id=uuid.UUID(payload["native_case_id"]),
            native_run_id=uuid.UUID(payload["native_run_id"]),
            status=payload["status"],
            latest_sequence=payload["latest_sequence"],
        )


@dataclass(frozen=True, slots=True)
class GatewayCommandReceipt:
    """The durable result of one idempotent Gateway command."""

    command_id: uuid.UUID
    conversation_id: uuid.UUID
    run_spec_id: uuid.UUID
    command_kind: str
    outcome: str
    reason_code: str | None

    def __post_init__(self) -> None:
        if self.command_kind not in _GATEWAY_COMMAND_KINDS:
            raise ValueError("Gateway command receipt has an unsupported command kind")
        if self.outcome not in _GATEWAY_COMMAND_OUTCOMES:
            raise ValueError("Gateway command receipt has an unsupported outcome")
        object.__setattr__(
            self,
            "reason_code",
            require_safe_gateway_reason_code(self.reason_code),
        )

    def to_payload(self) -> dict[str, str | None]:
        return {
            "receipt_kind": "command",
            "command_id": str(self.command_id),
            "conversation_id": str(self.conversation_id),
            "run_spec_id": str(self.run_spec_id),
            "command_kind": self.command_kind,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> GatewayCommandReceipt:
        if (
            type(payload) is not dict
            or set(payload) != _GATEWAY_COMMAND_RECEIPT_PAYLOAD_KEYS
            or payload["receipt_kind"] != "command"
        ):
            raise ValueError("Gateway command receipt payload has an invalid schema")
        for field in (
            "command_id",
            "conversation_id",
            "run_spec_id",
            "command_kind",
            "outcome",
        ):
            if type(payload[field]) is not str:
                raise ValueError("Gateway command receipt fields must be strings")
        if payload["reason_code"] is not None and type(payload["reason_code"]) is not str:
            raise ValueError("Gateway command receipt reason must be a string")
        return cls(
            command_id=uuid.UUID(payload["command_id"]),
            conversation_id=uuid.UUID(payload["conversation_id"]),
            run_spec_id=uuid.UUID(payload["run_spec_id"]),
            command_kind=payload["command_kind"],
            outcome=payload["outcome"],
            reason_code=payload["reason_code"],
        )


@dataclass(frozen=True, slots=True)
class GatewayIntentReceipt:
    """The durable, no-native-work outcome of a rejected message intent."""

    conversation_id: uuid.UUID
    intent_id: uuid.UUID
    intent_kind: str
    outcome: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.intent_kind != "unsupported":
            raise ValueError("Gateway intent receipt must be unsupported")
        if self.outcome != "rejected":
            raise ValueError("Gateway intent receipt must be rejected")
        if self.reason_code != SafeGatewayReason.UNSUPPORTED_IN_GATEWAY_P0.value:
            raise ValueError("Gateway intent receipt has an invalid reason")

    def to_payload(self) -> dict[str, str]:
        return {
            "receipt_kind": "intent",
            "conversation_id": str(self.conversation_id),
            "intent_id": str(self.intent_id),
            "intent_kind": self.intent_kind,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> GatewayIntentReceipt:
        if (
            type(payload) is not dict
            or set(payload) != _GATEWAY_INTENT_RECEIPT_PAYLOAD_KEYS
            or payload["receipt_kind"] != "intent"
        ):
            raise ValueError("Gateway intent receipt payload has an invalid schema")
        for field in (
            "conversation_id",
            "intent_id",
            "intent_kind",
            "outcome",
            "reason_code",
        ):
            if type(payload[field]) is not str:
                raise ValueError("Gateway intent receipt fields must be strings")
        return cls(
            conversation_id=uuid.UUID(payload["conversation_id"]),
            intent_id=uuid.UUID(payload["intent_id"]),
            intent_kind=payload["intent_kind"],
            outcome=payload["outcome"],
            reason_code=payload["reason_code"],
        )


class _ExistingRoleEvent(Exception):
    """Internal signal that rolls back a SQLite cursor reservation."""

    def __init__(self, event: RoleEvent) -> None:
        self.event = event


class ResearchGatewayRepository:
    """Own Gateway row locks, scoped request slots, and ordered appends."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def acquire_request(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        operation: str,
        client_key: str,
        request_fingerprint: str,
        lease_seconds: int = 300,
    ) -> tuple[GatewayIdempotencyRequest, bool]:
        """Atomically acquire an actor-scoped Gateway request slot.

        The scope includes tenant, subject, and operation, so a string that is
        valid for one private action cannot replay an unrelated action or a
        different subject's request.
        """
        require_canonical_sha256(
            request_fingerprint,
            field_name="gateway request fingerprint",
        )
        now = _utcnow()
        row = GatewayIdempotencyRequest(
            tenant_id=tenant_id,
            subject_id=subject_id,
            operation=operation,
            client_key=client_key,
            request_fingerprint=request_fingerprint,
            status="in_progress",
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            created_at=now,
            updated_at=now,
        )
        try:
            # Keep only the duplicate-key write in a savepoint: callers can
            # retain their own transaction after a concurrent winner appears.
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            existing = self._scoped_request(
                tenant_id=tenant_id,
                subject_id=subject_id,
                operation=operation,
                client_key=client_key,
                lock=True,
            )
            if existing is None:
                raise ConflictError("gateway_idempotency_conflict")
            if existing.request_fingerprint != request_fingerprint:
                raise ConflictError("gateway_idempotency_fingerprint_conflict")
            if self._can_take_over_expired_request(existing, now=now):
                existing.attempt += 1
                existing.lease_expires_at = now + timedelta(seconds=lease_seconds)
                existing.updated_at = now
                self._session.flush()
                return existing, True
            return existing, False
        return row, True

    def create_conversation(
        self,
        *,
        tenant_id: str,
        owner_subject_id: str,
        title: str | None = None,
    ) -> ResearchConversation:
        if not tenant_id.strip() or not owner_subject_id.strip():
            raise ValueError("Gateway conversation requires tenant and subject")
        conversation = ResearchConversation(
            tenant_id=tenant_id,
            owner_subject_id=owner_subject_id,
            visibility="private",
            title=title,
            event_retention_floor=1,
            next_message_sequence=1,
            next_event_sequence=1,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        self._session.add(conversation)
        self._session.flush()
        return conversation

    def require_request_attempt(
        self, *, request_id: uuid.UUID, attempt: int, now: datetime
    ) -> GatewayIdempotencyRequest:
        """Fence an expired/superseded owner in the caller's publish transaction.

        A conditional write is necessary on SQLite, where SELECT FOR UPDATE
        cannot exclude another lease owner. It also keeps PostgreSQL's request
        lock until native rows and the immutable receipt provenance commit.
        The caller validates the returned actor/operation/fingerprint against
        its original request. The attempt is captured before the short lease
        commits, and the returned row remains locked for this transaction.
        """
        request_id = self._session.scalar(
            update(GatewayIdempotencyRequest)
            .where(
                GatewayIdempotencyRequest.id == request_id,
                GatewayIdempotencyRequest.attempt == attempt,
                GatewayIdempotencyRequest.status == "in_progress",
                GatewayIdempotencyRequest.lease_expires_at > now,
            )
            .values(updated_at=now)
            .returning(GatewayIdempotencyRequest.id)
            .execution_options(synchronize_session=False)
        )
        if request_id is None:
            raise ConflictError("gateway_request_lease_lost")
        return self._session.scalar(
            select(GatewayIdempotencyRequest)
            .where(GatewayIdempotencyRequest.id == request_id)
            .execution_options(populate_existing=True)
        )

    def recover_or_return_receipt(
        self, request: GatewayIdempotencyRequest
    ) -> GatewayReceipt | GatewayCommandReceipt | GatewayIntentReceipt | None:
        """Recover a receipt only from its linked immutable Gateway record.

        A process can fail after writing native intake and Gateway records but
        before it records the HTTP receipt.  A RunSpec or command request FK
        makes that state reconstructible without a second provider/native call.
        ``receipt_json`` is a mutable cache, never recovery authority: a replay
        always computes the current safe cursor from the immutable lineage.
        """
        if self._session.get_bind().dialect.name == "sqlite":
            # Establish the real outer transaction before any recovery read.
            # Role bootstrap and receipt completion must commit or roll back
            # together, never through a top-level pysqlite SAVEPOINT.
            self._ensure_sqlite_outer_transaction()
        run_specs = list(
            self._session.scalars(
                select(ResearchRunSpec)
                .where(ResearchRunSpec.gateway_request_id == request.id)
                .order_by(ResearchRunSpec.created_at)
            )
        )
        commands = list(
            self._session.scalars(
                select(GatewayCommand)
                .where(GatewayCommand.gateway_request_id == request.id)
                .order_by(GatewayCommand.created_at)
            )
        )
        intents = list(
            self._session.scalars(
                select(ResearchIntent)
                .where(ResearchIntent.gateway_request_id == request.id)
                .order_by(ResearchIntent.created_at)
            )
        )
        if len(run_specs) + len(commands) > 1:
            raise ConflictError("gateway_idempotency_recovery_ambiguous")
        if run_specs:
            run_spec = run_specs[0]
            self._require_run_spec_request_provenance(request, run_spec)
            self._ensure_run_spec_role_bootstrap(run_spec)
            receipt = self._run_spec_receipt(run_spec)
            self._record_recovered_receipt(request, receipt)
            return receipt
        if commands:
            command = commands[0]
            self._require_command_request_provenance(request, command)
            receipt = self._command_receipt(command)
            self._record_recovered_receipt(request, receipt)
            return receipt
        if len(intents) > 1:
            raise ConflictError("gateway_idempotency_recovery_ambiguous")
        if intents:
            intent = intents[0]
            self._require_intent_request_provenance(request, intent)
            receipt = self._intent_receipt(intent)
            self._record_recovered_receipt(request, receipt)
            return receipt
        if request.status == "completed":
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
        return None

    def _record_recovered_receipt(
        self,
        request: GatewayIdempotencyRequest,
        receipt: GatewayReceipt | GatewayCommandReceipt | GatewayIntentReceipt,
    ) -> None:
        """Refresh the mutable receipt cache from verified immutable facts."""
        self._require_receipt_provenance(request, receipt)
        request.status = "completed"
        request.receipt_json = receipt.to_payload()
        request.lease_expires_at = None
        request.updated_at = _utcnow()
        self._session.flush()

    def complete_request(
        self,
        request: GatewayIdempotencyRequest,
        *,
        receipt: GatewayReceipt | GatewayCommandReceipt | GatewayIntentReceipt,
    ) -> None:
        self._require_receipt_provenance(request, receipt)
        payload = receipt.to_payload()
        if request.status == "completed" and request.receipt_json not in (
            None,
            payload,
        ):
            raise ConflictError("gateway_idempotency_receipt_conflict")
        request.status = "completed"
        request.receipt_json = payload
        request.lease_expires_at = None
        request.updated_at = _utcnow()
        self._session.flush()

    def _require_receipt_provenance(
        self,
        request: GatewayIdempotencyRequest,
        receipt: GatewayReceipt | GatewayCommandReceipt | GatewayIntentReceipt,
    ) -> None:
        if isinstance(receipt, GatewayIntentReceipt):
            intent = self._session.get(ResearchIntent, receipt.intent_id)
            if intent is None:
                raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
            self._require_intent_request_provenance(request, intent)
            if receipt != self._intent_receipt(intent):
                raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
            return
        if isinstance(receipt, GatewayCommandReceipt):
            command = self._session.get(GatewayCommand, receipt.command_id)
            if command is None:
                raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
            self._require_command_request_provenance(request, command)
            if receipt != self._command_receipt(command):
                raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
            return
        run_spec = self._session.get(ResearchRunSpec, receipt.run_spec_id)
        if run_spec is None:
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
        self._require_run_spec_request_provenance(request, run_spec)
        if (
            receipt != self._run_spec_receipt(run_spec)
        ):
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")

    def _run_spec_receipt(self, run_spec: ResearchRunSpec) -> GatewayReceipt:
        latest_sequence = self._session.scalar(
            select(func.max(RoleEvent.sequence)).where(
                RoleEvent.conversation_id == run_spec.conversation_id
            )
        )
        return GatewayReceipt(
            conversation_id=run_spec.conversation_id,
            intent_id=run_spec.intent_id,
            run_spec_id=run_spec.id,
            native_case_id=run_spec.native_case_id,
            native_run_id=run_spec.native_run_id,
            status="queued",
            latest_sequence=int(latest_sequence or 0),
        )

    @staticmethod
    def _command_receipt(command: GatewayCommand) -> GatewayCommandReceipt:
        try:
            return GatewayCommandReceipt(
                command_id=command.id,
                conversation_id=command.conversation_id,
                run_spec_id=command.run_spec_id,
                command_kind=command.command_kind,
                outcome=command.outcome,
                reason_code=command.reason_code,
            )
        except ValueError as exc:
            raise ConflictError(
                "gateway_idempotency_receipt_provenance_conflict"
            ) from exc

    @staticmethod
    def _intent_receipt(intent: ResearchIntent) -> GatewayIntentReceipt:
        try:
            return GatewayIntentReceipt(
                conversation_id=intent.conversation_id,
                intent_id=intent.id,
                intent_kind=intent.intent_kind,
                outcome=intent.status,
                reason_code=intent.reason_code,
            )
        except (TypeError, ValueError) as exc:
            raise ConflictError(
                "gateway_idempotency_receipt_provenance_conflict"
            ) from exc

    def _require_run_spec_request_provenance(
        self,
        request: GatewayIdempotencyRequest,
        run_spec: ResearchRunSpec,
    ) -> None:
        conversation = self._session.get(ResearchConversation, run_spec.conversation_id)
        intent = self._session.get(ResearchIntent, run_spec.intent_id)
        if (
            request.operation != _MESSAGE_OPERATION
            or run_spec.gateway_request_id != request.id
            or run_spec.tenant_id != request.tenant_id
            or run_spec.subject_id != request.subject_id
            or conversation is None
            or conversation.tenant_id != request.tenant_id
            or conversation.owner_subject_id != request.subject_id
            or intent is None
            or intent.gateway_request_id != request.id
            or intent.tenant_id != request.tenant_id
            or intent.subject_id != request.subject_id
        ):
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")

    def _require_command_request_provenance(
        self,
        request: GatewayIdempotencyRequest,
        command: GatewayCommand,
    ) -> None:
        conversation = self._session.get(ResearchConversation, command.conversation_id)
        if (
            request.operation != _COMMAND_OPERATION
            or command.gateway_request_id != request.id
            or command.tenant_id != request.tenant_id
            or command.subject_id != request.subject_id
            or command.actor_subject_id != request.subject_id
            or conversation is None
            or conversation.tenant_id != request.tenant_id
            or conversation.owner_subject_id != request.subject_id
        ):
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
        if command.run_spec_id is None:
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
        run_spec = self._session.get(ResearchRunSpec, command.run_spec_id)
        if (
            run_spec is None
            or run_spec.conversation_id != command.conversation_id
            or run_spec.tenant_id != request.tenant_id
            or run_spec.subject_id != request.subject_id
        ):
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")

    def _require_intent_request_provenance(
        self,
        request: GatewayIdempotencyRequest,
        intent: ResearchIntent,
    ) -> None:
        conversation = self._session.get(ResearchConversation, intent.conversation_id)
        message = self._session.get(ResearchMessage, intent.message_id)
        safe_reply = None
        if message is not None:
            safe_reply = self._session.scalar(
                select(ResearchMessage).where(
                    ResearchMessage.conversation_id == message.conversation_id,
                    ResearchMessage.sequence == message.sequence + 1,
                )
            )
        has_run_spec = (
            self._session.scalar(
                select(ResearchRunSpec.id)
                .where(ResearchRunSpec.gateway_request_id == request.id)
                .limit(1)
            )
            is not None
        )
        has_command = (
            self._session.scalar(
                select(GatewayCommand.id)
                .where(GatewayCommand.gateway_request_id == request.id)
                .limit(1)
            )
            is not None
        )
        if (
            request.operation != _MESSAGE_OPERATION
            or intent.gateway_request_id != request.id
            or intent.tenant_id != request.tenant_id
            or intent.subject_id != request.subject_id
            or intent.intent_kind != "unsupported"
            or intent.status != "rejected"
            or intent.reason_code != SafeGatewayReason.UNSUPPORTED_IN_GATEWAY_P0.value
            or conversation is None
            or conversation.tenant_id != request.tenant_id
            or conversation.owner_subject_id != request.subject_id
            or conversation.visibility != "private"
            or message is None
            or message.conversation_id != conversation.id
            or message.tenant_id != request.tenant_id
            or message.subject_id != request.subject_id
            or message.message_kind != "user"
            or message.role_key is not None
            or message.input_sha256 != intent.input_sha256
            or intent.parent_intent_id is not None
            or request.request_fingerprint
            != _message_request_fingerprint(conversation.id, message.content)
            or safe_reply is None
            or safe_reply.tenant_id != request.tenant_id
            or safe_reply.subject_id != request.subject_id
            or safe_reply.message_kind != "system"
            or safe_reply.role_key is not None
            or safe_reply.content != UNSUPPORTED_FOLLOWUP_MESSAGE
            or has_run_spec
            or has_command
        ):
            raise ConflictError("gateway_idempotency_receipt_provenance_conflict")

    def append_message(
        self,
        *,
        conversation_id: uuid.UUID,
        tenant_id: str,
        subject_id: str,
        text: str,
        message_kind: str = "user",
        role_key: str | None = None,
    ) -> ResearchMessage:
        conversation = self._locked_conversation(conversation_id)
        self._require_conversation_provenance(conversation, tenant_id, subject_id)
        if not text:
            raise ValueError("Gateway message text must not be empty")
        if role_key is not None and role_key not in ROLE_KEYS:
            raise ValueError("unsupported Gateway role")
        if message_kind == "role" and role_key is None:
            raise ValueError("Gateway role message requires a manifest role")
        now = _utcnow()
        message = ResearchMessage(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            sequence=conversation.next_message_sequence,
            message_kind=message_kind,
            role_key=role_key,
            content=text,
            input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            created_at=now,
        )
        # The mutable cursor makes concurrent PostgreSQL writers serialize on
        # the conversation row rather than computing MAX(sequence).
        conversation.next_message_sequence += 1
        conversation.updated_at = now
        self._session.add(message)
        self._session.flush()
        return message

    def append_intent(
        self,
        *,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        tenant_id: str,
        subject_id: str,
        intent_kind: str,
        input_sha256: str,
        parent_intent_id: uuid.UUID | None = None,
        gateway_request_id: uuid.UUID | None = None,
        status: str = "queued",
        reason_code: SafeGatewayReason | None = None,
    ) -> ResearchIntent:
        require_canonical_sha256(input_sha256, field_name="Gateway intent input hash")
        stored_reason_code = safe_gateway_reason_code(reason_code)
        conversation = self._locked_conversation(conversation_id)
        self._require_conversation_provenance(conversation, tenant_id, subject_id)
        message = self._session.get(ResearchMessage, message_id)
        if message is None or message.conversation_id != conversation.id:
            raise ValueError("Gateway intent message does not belong to conversation")
        if message.input_sha256 != input_sha256:
            raise ValueError("Gateway intent input hash does not match message")
        if parent_intent_id is not None:
            parent = self._session.get(ResearchIntent, parent_intent_id)
            if parent is None or parent.conversation_id != conversation.id:
                raise ValueError(
                    "Gateway parent intent does not belong to conversation"
                )
        if gateway_request_id is not None:
            self._require_request_provenance(
                gateway_request_id,
                tenant_id=tenant_id,
                subject_id=subject_id,
                operation=(
                    _COMMAND_OPERATION
                    if intent_kind == "command"
                    else _MESSAGE_OPERATION
                ),
            )
            existing_intent = self._session.scalar(
                select(ResearchIntent.id)
                .where(ResearchIntent.gateway_request_id == gateway_request_id)
                .limit(1)
            )
            if existing_intent is not None:
                raise ConflictError("gateway_request_intent_conflict")
        intent = ResearchIntent(
            conversation_id=conversation.id,
            message_id=message.id,
            parent_intent_id=parent_intent_id,
            gateway_request_id=gateway_request_id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            intent_kind=intent_kind,
            status=status,
            input_sha256=input_sha256,
            reason_code=stored_reason_code,
            created_at=_utcnow(),
        )
        self._session.add(intent)
        self._session.flush()
        return intent

    def create_run_spec(
        self,
        *,
        conversation_id: uuid.UUID,
        intent_id: uuid.UUID,
        tenant_id: str,
        subject_id: str,
        native_case_id: uuid.UUID,
        frozen_scope: dict,
        frozen_cutoff: dict,
        frozen_source_policy: dict,
        role_manifest_version: str,
        capability_manifest_version: str,
        correlation_id: str,
        input_artifact_refs: list[ArtifactReference],
        native_run_id: uuid.UUID | None = None,
        native_research_run_id: uuid.UUID | None = None,
        gateway_request_id: uuid.UUID | None = None,
    ) -> ResearchRunSpec:
        """Persist the immutable Gateway contract after native intake succeeds."""
        resolved_native_run_id = native_run_id or native_research_run_id
        if resolved_native_run_id is None:
            raise ValueError("Gateway run spec requires a native run ID")
        if (
            native_run_id is not None
            and native_research_run_id is not None
            and native_run_id != native_research_run_id
        ):
            raise ValueError("conflicting native run IDs")
        native_run = self._session.get(ResearchRun, resolved_native_run_id)
        if native_run is None or native_run.research_case_id != native_case_id:
            raise ValueError("Gateway native run does not belong to native case")
        conversation = self._locked_conversation(conversation_id)
        self._require_conversation_provenance(conversation, tenant_id, subject_id)
        intent = self._session.get(ResearchIntent, intent_id)
        if intent is None or intent.conversation_id != conversation.id:
            raise ValueError("Gateway run spec intent does not belong to conversation")
        if gateway_request_id is not None:
            self._require_request_provenance(
                gateway_request_id,
                tenant_id=tenant_id,
                subject_id=subject_id,
                operation=_MESSAGE_OPERATION,
            )
            existing_run_spec = self._session.scalar(
                select(ResearchRunSpec.id)
                .where(ResearchRunSpec.gateway_request_id == gateway_request_id)
                .limit(1)
            )
            if existing_run_spec is not None:
                raise ConflictError("gateway_request_run_spec_conflict")
        if intent.gateway_request_id != gateway_request_id:
            raise ConflictError("gateway_run_spec_request_link_conflict")
        run_spec = ResearchRunSpec(
            conversation_id=conversation.id,
            intent_id=intent.id,
            gateway_request_id=gateway_request_id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            native_case_id=native_case_id,
            native_run_id=resolved_native_run_id,
            frozen_scope=dict(frozen_scope),
            frozen_cutoff=dict(frozen_cutoff),
            frozen_source_policy=dict(frozen_source_policy),
            role_manifest_version=role_manifest_version,
            capability_manifest_version=capability_manifest_version,
            correlation_id=correlation_id,
            input_artifact_refs=self._typed_artifact_refs(input_artifact_refs),
            created_at=_utcnow(),
        )
        self._session.add(run_spec)
        self._session.flush()
        return run_spec

    def initialize_role_runs(self, *, run_spec_id: uuid.UUID) -> list[RoleRun]:
        if self._session.get_bind().dialect.name == "sqlite":
            return self._initialize_role_runs_sqlite(run_spec_id=run_spec_id)
        # Maintain the same global PostgreSQL lock order as append_role_event:
        # conversation → immutable run spec → mutable role rows. In
        # particular, recovery must not hold a RunSpec lock while an event
        # appender holds the conversation lock and waits for that RunSpec.
        conversation = self._locked_conversation_for_run_spec(run_spec_id)
        run_spec = self._session.scalar(
            select(ResearchRunSpec)
            .where(ResearchRunSpec.id == run_spec_id)
            .with_for_update()
        )
        if run_spec is None or run_spec.conversation_id != conversation.id:
            raise ValueError("Gateway run spec not found")
        existing = {
            row.role_key: row
            for row in self._session.scalars(
                select(RoleRun)
                .where(RoleRun.run_spec_id == run_spec.id)
                .with_for_update()
            )
        }
        now = _utcnow()
        for role_key in ROLE_KEYS:
            if role_key not in existing:
                row = RoleRun(
                    conversation_id=run_spec.conversation_id,
                    run_spec_id=run_spec.id,
                    tenant_id=run_spec.tenant_id,
                    subject_id=run_spec.subject_id,
                    role_key=role_key,
                    status="queued",
                    attempt=0,
                    next_event_sequence=1,
                    created_at=now,
                    updated_at=now,
                )
                self._session.add(row)
                existing[role_key] = row
        self._session.flush()
        return [existing[role_key] for role_key in ROLE_KEYS]

    def _initialize_role_runs_sqlite(
        self, *, run_spec_id: uuid.UUID
    ) -> list[RoleRun]:
        """Initialize fixed roles after acquiring SQLite's writer lease first."""
        self._ensure_sqlite_outer_transaction()
        conversation_for_run = (
            select(ResearchRunSpec.conversation_id)
            .where(ResearchRunSpec.id == run_spec_id)
            .scalar_subquery()
        )
        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                now = _utcnow()
                with self._session.begin_nested():
                    # SQLite ignores FOR UPDATE. This first DML statement
                    # serializes two recovery workers before either observes
                    # a partial role set.
                    conversation_id = self._session.scalar(
                        update(ResearchConversation)
                        .where(ResearchConversation.id == conversation_for_run)
                        .values(updated_at=now)
                        .returning(ResearchConversation.id)
                    )
                    if conversation_id is None:
                        raise ValueError("Gateway run spec not found")
                    run_spec = self._session.scalar(
                        select(ResearchRunSpec)
                        .where(ResearchRunSpec.id == run_spec_id)
                        .with_for_update()
                    )
                    if run_spec is None:
                        raise ValueError("Gateway run spec not found")
                    existing = {
                        row.role_key: row
                        for row in self._session.scalars(
                            select(RoleRun).where(RoleRun.run_spec_id == run_spec.id)
                        )
                    }
                    for role_key in ROLE_KEYS:
                        if role_key not in existing:
                            row = RoleRun(
                                conversation_id=run_spec.conversation_id,
                                run_spec_id=run_spec.id,
                                tenant_id=run_spec.tenant_id,
                                subject_id=run_spec.subject_id,
                                role_key=role_key,
                                status="queued",
                                attempt=0,
                                next_event_sequence=1,
                                created_at=now,
                                updated_at=now,
                            )
                            self._session.add(row)
                            existing[role_key] = row
                    self._session.flush()
                return [existing[role_key] for role_key in ROLE_KEYS]
            except IntegrityError:
                self._session.expire_all()
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc):
                    raise
                self._session.expire_all()
                if attempt + 1 < _SQLITE_BUSY_RETRY_ATTEMPTS:
                    time.sleep(_SQLITE_BUSY_RETRY_DELAY_SECONDS * (2**attempt))
        raise ConflictError("gateway_role_run_initialization_conflict")

    def _ensure_run_spec_role_bootstrap(self, run_spec: ResearchRunSpec) -> None:
        """Repair the recoverable RunSpec→roles/events crash boundary in one UoW."""
        self.initialize_role_runs(run_spec_id=run_spec.id)
        for role_key in ROLE_KEYS:
            self._append_historical_role_queued_event(
                run_spec_id=run_spec.id,
                role_key=role_key,
            )

    def _append_historical_role_queued_event(
        self,
        *,
        run_spec_id: uuid.UUID,
        role_key: str,
    ) -> RoleEvent:
        """Backfill one initial event without replaying its mutable transition.

        The event is historical evidence that a role entered the queue. It may
        be missing after a process crash even though a later event has already
        advanced the mutable RoleRun. Replaying that old event must never move
        a running, blocked, or completed role back to ``queued``.
        """
        return self._append_role_event(
            run_spec_id=run_spec_id,
            role_key=role_key,
            event_type="role_queued",
            source_key=(
                f"{_INITIAL_ROLE_EVENT_SOURCE_PREFIX}:{run_spec_id}:role:"
                f"{role_key}:queued"
            ),
            _transition_role_status=False,
        )

    def append_command(
        self,
        *,
        conversation_id: uuid.UUID,
        run_spec_id: uuid.UUID,
        gateway_request_id: uuid.UUID,
        tenant_id: str,
        subject_id: str,
        actor_subject_id: str,
        command_kind: str,
        target_hash: str,
        outcome: str,
        target_version: str | None = None,
        reason_code: SafeGatewayReason | None = None,
    ) -> GatewayCommand:
        """Append an auditable command receipt without retaining raw input."""
        require_canonical_sha256(target_hash, field_name="Gateway command target hash")
        require_absent_gateway_command_target_version(target_version)
        stored_reason_code = safe_gateway_reason_code(reason_code)
        conversation = self._locked_conversation(conversation_id)
        self._require_conversation_provenance(conversation, tenant_id, subject_id)
        request = self._require_request_provenance(
            gateway_request_id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            operation=_COMMAND_OPERATION,
        )
        if actor_subject_id != subject_id:
            raise ConflictError("gateway_command_actor_provenance_conflict")
        existing_command = self._session.scalar(
            select(GatewayCommand.id)
            .where(GatewayCommand.gateway_request_id == request.id)
            .limit(1)
        )
        if existing_command is not None:
            raise ConflictError("gateway_request_command_conflict")
        if run_spec_id is None:
            raise ValueError("Gateway command requires a run spec")
        run_spec = self._session.get(ResearchRunSpec, run_spec_id)
        if (
            run_spec is None
            or run_spec.conversation_id != conversation.id
            or run_spec.tenant_id != tenant_id
            or run_spec.subject_id != subject_id
        ):
            raise ValueError("Gateway command run spec does not belong to conversation")
        command = GatewayCommand(
            conversation_id=conversation.id,
            run_spec_id=run_spec_id,
            gateway_request_id=request.id,
            tenant_id=tenant_id,
            subject_id=subject_id,
            actor_subject_id=actor_subject_id,
            command_kind=command_kind,
            target_hash=target_hash,
            target_version=target_version,
            outcome=outcome,
            reason_code=stored_reason_code,
            created_at=_utcnow(),
        )
        self._session.add(command)
        self._session.flush()
        return command

    def append_role_event(
        self,
        *,
        run_spec_id: uuid.UUID,
        role_key: str,
        event_type: str,
        source_key: str,
        display_text: str | None = None,
        safe_summary: SafeRoleEventSummary | None = None,
        status: str | None = None,
        reason_code: SafeRoleEventReason | None = None,
        artifact_refs: list[ArtifactReference] | None = None,
    ) -> RoleEvent:
        """Append one deduplicated safe event with conversation/run cursors.

        A database uniqueness constraint remains the final source-dedup guard.
        If a concurrent projector won the source key, its persisted event is
        returned instead of emitting another UI event.
        """
        return self._append_role_event(
            run_spec_id=run_spec_id,
            role_key=role_key,
            event_type=event_type,
            source_key=source_key,
            display_text=display_text,
            safe_summary=safe_summary,
            status=status,
            reason_code=reason_code,
            artifact_refs=artifact_refs,
            _transition_role_status=True,
        )

    def _append_role_event(
        self,
        *,
        run_spec_id: uuid.UUID,
        role_key: str,
        event_type: str,
        source_key: str,
        display_text: str | None = None,
        safe_summary: SafeRoleEventSummary | None = None,
        status: str | None = None,
        reason_code: SafeRoleEventReason | None = None,
        artifact_refs: list[ArtifactReference] | None = None,
        _transition_role_status: bool,
    ) -> RoleEvent:
        """Append a safe event with an explicitly internal state-transition mode."""
        if role_key not in ROLE_KEYS:
            raise ValueError("unsupported Gateway role")
        event_status = status or _EVENT_STATUS.get(event_type)
        if event_status is None:
            raise ValueError("unsupported Gateway role event type")
        if display_text is not None:
            raise ValueError("Gateway role events require a server-owned safe summary")
        stored_display_text = safe_role_event_display_text(event_type, safe_summary)
        stored_reason_code = safe_role_event_reason_code(reason_code)
        stored_source_key = canonical_role_event_source_key(source_key)
        if self._session.get_bind().dialect.name == "sqlite":
            return self._append_role_event_sqlite(
                run_spec_id=run_spec_id,
                role_key=role_key,
                event_type=event_type,
                source_key=stored_source_key,
                event_status=event_status,
                display_text=stored_display_text,
                reason_code=stored_reason_code,
                artifact_refs=artifact_refs,
                transition_role_status=_transition_role_status,
            )

        run_spec_pointer = self._session.get(ResearchRunSpec, run_spec_id)
        if run_spec_pointer is None:
            raise ValueError("Gateway run spec not found")
        conversation_id = run_spec_pointer.conversation_id
        # Fixed lock order: conversation first, then immutable spec and the
        # mutable role row. This serializes cursor allocation on PostgreSQL.
        for _attempt in range(_EVENT_SEQUENCE_RETRY_ATTEMPTS):
            try:
                conversation = self._locked_conversation(conversation_id)
                run_spec = self._session.scalar(
                    select(ResearchRunSpec)
                    .where(ResearchRunSpec.id == run_spec_id)
                    .with_for_update()
                )
                if run_spec is None or run_spec.conversation_id != conversation.id:
                    raise ValueError("Gateway run spec does not belong to conversation")
                existing = self._source_event(run_spec.id, stored_source_key)
                if existing is not None:
                    return existing
                role_run = self._session.scalar(
                    select(RoleRun)
                    .where(RoleRun.run_spec_id == run_spec.id)
                    .where(RoleRun.role_key == role_key)
                    .with_for_update()
                )
                if role_run is None:
                    raise ValueError("Gateway role run has not been initialized")
                now = _utcnow()
                # Include cursor changes in the savepoint. A source-key race
                # rolls back the reserved sequence before the winner is read.
                with self._session.begin_nested():
                    event = RoleEvent(
                        conversation_id=conversation.id,
                        run_spec_id=run_spec.id,
                        role_run_id=role_run.id,
                        tenant_id=run_spec.tenant_id,
                        subject_id=run_spec.subject_id,
                        sequence=conversation.next_event_sequence,
                        # A run sequence is global to the immutable RunSpec,
                        # not local to a role. The RunSpec row lock serializes
                        # this MAX query on PostgreSQL; the unique constraint
                        # plus retry is the SQLite/concurrent fallback.
                        run_sequence=self._next_run_sequence(run_spec.id),
                        role_key=role_key,
                        event_type=event_type,
                        status=event_status,
                        reason_code=stored_reason_code,
                        display_text=stored_display_text,
                        artifact_refs=self._typed_artifact_refs(artifact_refs or []),
                        source_key=stored_source_key,
                        created_at=now,
                    )
                    conversation.next_event_sequence += 1
                    conversation.updated_at = now
                    if _transition_role_status:
                        role_run.status = event_status
                        role_run.updated_at = now
                    self._session.add(event)
                    self._session.flush()
                return event
            except IntegrityError:
                duplicate = self._source_event(run_spec.id, stored_source_key)
                if duplicate is not None:
                    return duplicate
                self._session.expire_all()
        raise ConflictError("gateway_role_event_sequence_conflict")

    def _append_role_event_sqlite(
        self,
        *,
        run_spec_id: uuid.UUID,
        role_key: str,
        event_type: str,
        source_key: str,
        event_status: str,
        display_text: str | None,
        reason_code: str | None,
        artifact_refs: list[ArtifactReference] | None,
        transition_role_status: bool,
    ) -> RoleEvent:
        """Append under an UPDATE-first SQLite cursor reservation.

        SQLite's deferred transactions cannot safely upgrade two overlapping
        readers to writers. The first statement inside each savepoint is
        therefore the conversation cursor UPDATE. It wins the SQLite writer
        lease before loading the RunSpec or checking source de-duplication.
        A source-key winner leaves the UPDATE predicate false; an unexpected
        winner observed after a reservation raises an internal signal so the
        savepoint rolls the cursor back before returning that winner.
        """
        self._ensure_sqlite_outer_transaction()
        source_exists = (
            select(RoleEvent.id)
            .where(RoleEvent.run_spec_id == run_spec_id)
            .where(RoleEvent.source_key == source_key)
            .exists()
        )
        conversation_for_run = (
            select(ResearchRunSpec.conversation_id)
            .where(ResearchRunSpec.id == run_spec_id)
            .scalar_subquery()
        )

        for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
            try:
                now = _utcnow()
                with self._session.begin_nested():
                    # This must precede every read of RunSpec/RoleEvent. On a
                    # duplicate source key it affects zero rows, so no
                    # conversation sequence is consumed by replay.
                    reservation = self._session.execute(
                        update(ResearchConversation)
                        .where(ResearchConversation.id == conversation_for_run)
                        .where(~source_exists)
                        .values(
                            next_event_sequence=(
                                ResearchConversation.next_event_sequence + 1
                            ),
                            updated_at=now,
                        )
                        .returning(
                            ResearchConversation.id,
                            ResearchConversation.next_event_sequence,
                        )
                    ).one_or_none()

                    run_spec = self._session.scalar(
                        select(ResearchRunSpec)
                        .where(ResearchRunSpec.id == run_spec_id)
                        .with_for_update()
                    )
                    if run_spec is None:
                        raise ValueError("Gateway run spec not found")
                    existing = self._source_event(run_spec.id, source_key)
                    if existing is not None:
                        if reservation is not None:
                            raise _ExistingRoleEvent(existing)
                        return existing
                    if reservation is None:
                        raise ConflictError("gateway_role_event_source_state_conflict")

                    role_run = self._session.scalar(
                        select(RoleRun)
                        .where(RoleRun.run_spec_id == run_spec.id)
                        .where(RoleRun.role_key == role_key)
                        .with_for_update()
                    )
                    if role_run is None:
                        raise ValueError("Gateway role run has not been initialized")

                    event = RoleEvent(
                        conversation_id=reservation[0],
                        run_spec_id=run_spec.id,
                        role_run_id=role_run.id,
                        tenant_id=run_spec.tenant_id,
                        subject_id=run_spec.subject_id,
                        sequence=int(reservation[1]) - 1,
                        run_sequence=self._next_run_sequence(run_spec.id),
                        role_key=role_key,
                        event_type=event_type,
                        status=event_status,
                        reason_code=reason_code,
                        display_text=display_text,
                        artifact_refs=self._typed_artifact_refs(artifact_refs or []),
                        source_key=source_key,
                        created_at=now,
                    )
                    if transition_role_status:
                        role_run.status = event_status
                        role_run.updated_at = now
                    self._session.add(event)
                    self._session.flush()
                return event
            except _ExistingRoleEvent as winner:
                return winner.event
            except IntegrityError:
                duplicate = self._source_event(run_spec_id, source_key)
                if duplicate is not None:
                    return duplicate
                self._session.expire_all()
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc):
                    raise
                # A failed savepoint retains the caller-owned outer UoW. The
                # next iteration starts with a fresh source/cursor read; do
                # not call Session.rollback(), which would discard that UoW.
                self._session.expire_all()
                if attempt + 1 < _SQLITE_BUSY_RETRY_ATTEMPTS:
                    time.sleep(_SQLITE_BUSY_RETRY_DELAY_SECONDS * (2**attempt))
        raise ConflictError("gateway_role_event_sequence_conflict")

    def _ensure_sqlite_outer_transaction(self) -> None:
        """Make pysqlite's caller-owned outer transaction real before SAVEPOINT.

        pysqlite treats a top-level SAVEPOINT as its transaction and commits
        it at RELEASE. Sending a deferred ``BEGIN`` first keeps the nested
        cursor reservation inside the Session's outer transaction, so callers
        retain normal commit/rollback atomicity. Existing DBAPI transactions
        (including a caller's flushed native write) are left untouched.
        """
        connection = self._session.connection()
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")

    def events_after(
        self, *, conversation_id: uuid.UUID, after_sequence: int
    ) -> list[RoleEvent]:
        return list(
            self._session.scalars(
                select(RoleEvent)
                .where(RoleEvent.conversation_id == conversation_id)
                .where(RoleEvent.sequence > after_sequence)
                .order_by(RoleEvent.sequence)
            )
        )

    def _scoped_request(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        operation: str,
        client_key: str,
        lock: bool,
    ) -> GatewayIdempotencyRequest | None:
        statement = (
            select(GatewayIdempotencyRequest)
            .where(GatewayIdempotencyRequest.tenant_id == tenant_id)
            .where(GatewayIdempotencyRequest.subject_id == subject_id)
            .where(GatewayIdempotencyRequest.operation == operation)
            .where(GatewayIdempotencyRequest.client_key == client_key)
        )
        if lock:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        return self._session.scalar(statement)

    def _locked_conversation(self, conversation_id: uuid.UUID) -> ResearchConversation:
        conversation = self._session.scalar(
            select(ResearchConversation)
            .where(ResearchConversation.id == conversation_id)
            .with_for_update()
        )
        if conversation is None:
            raise ValueError("Gateway conversation not found")
        return conversation

    def _locked_conversation_for_run_spec(
        self, run_spec_id: uuid.UUID
    ) -> ResearchConversation:
        """Lock a RunSpec's conversation without first locking the RunSpec."""
        conversation = self._session.scalar(
            select(ResearchConversation)
            .join(
                ResearchRunSpec,
                ResearchRunSpec.conversation_id == ResearchConversation.id,
            )
            .where(ResearchRunSpec.id == run_spec_id)
            .with_for_update(of=ResearchConversation)
        )
        if conversation is None:
            raise ValueError("Gateway run spec not found")
        return conversation

    def _require_request_provenance(
        self,
        gateway_request_id: uuid.UUID,
        *,
        tenant_id: str,
        subject_id: str,
        operation: str | None = None,
    ) -> GatewayIdempotencyRequest:
        request = self._session.get(GatewayIdempotencyRequest, gateway_request_id)
        if (
            request is None
            or request.tenant_id != tenant_id
            or request.subject_id != subject_id
        ):
            raise ConflictError("gateway_request_provenance_conflict")
        if operation is not None and request.operation != operation:
            raise ConflictError("gateway_request_operation_conflict")
        return request

    @staticmethod
    def _require_conversation_provenance(
        conversation: ResearchConversation, tenant_id: str, subject_id: str
    ) -> None:
        if (
            conversation.tenant_id != tenant_id
            or conversation.owner_subject_id != subject_id
        ):
            raise ConflictError("gateway_conversation_provenance_conflict")

    def _source_event(
        self, run_spec_id: uuid.UUID, source_key: str
    ) -> RoleEvent | None:
        return self._session.scalar(
            select(RoleEvent)
            .where(RoleEvent.run_spec_id == run_spec_id)
            .where(RoleEvent.source_key == source_key)
        )

    def _next_run_sequence(self, run_spec_id: uuid.UUID) -> int:
        last_sequence = self._session.scalar(
            select(func.max(RoleEvent.run_sequence)).where(
                RoleEvent.run_spec_id == run_spec_id
            )
        )
        return int(last_sequence or 0) + 1

    def _is_retryable_sqlite_lock(self, exc: OperationalError) -> bool:
        """Keep PostgreSQL errors unchanged; retry only known SQLite locks."""
        if self._session.get_bind().dialect.name != "sqlite":
            return False
        detail = str(exc.orig).lower()
        return any(
            marker in detail
            for marker in (
                "database is locked",
                "database table is locked",
                "database schema is locked",
                "database is busy",
            )
        )

    def _can_take_over_expired_request(
        self, request: GatewayIdempotencyRequest, *, now: datetime
    ) -> bool:
        if request.status != "in_progress" or request.lease_expires_at is None:
            return False
        expires_at = request.lease_expires_at
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at.astimezone(UTC) > now:
            return False
        has_run_spec = (
            self._session.scalar(
                select(ResearchRunSpec.id)
                .where(ResearchRunSpec.gateway_request_id == request.id)
                .limit(1)
            )
            is not None
        )
        has_command = (
            self._session.scalar(
                select(GatewayCommand.id)
                .where(GatewayCommand.gateway_request_id == request.id)
                .limit(1)
            )
            is not None
        )
        has_intent = (
            self._session.scalar(
                select(ResearchIntent.id)
                .where(ResearchIntent.gateway_request_id == request.id)
                .limit(1)
            )
            is not None
        )
        return not has_run_spec and not has_command and not has_intent

    @staticmethod
    def _typed_artifact_refs(
        refs: list[ArtifactReference],
    ) -> list[dict[str, str | bool]]:
        if not all(type(reference) is ArtifactReference for reference in refs):
            raise ValueError("Gateway records require typed artifact references")
        try:
            return [reference.to_json() for reference in refs]
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid typed artifact reference") from exc
