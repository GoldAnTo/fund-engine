"""Private conversation control plane over the native automatic research runtime.

The HTTP layer supplies a host-authenticated actor. This service authorizes
private ownership and native Case admission, commits only a short request lease
before intake, and publishes native intake and immutable Gateway facts together.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from unicodedata import category

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor
from app.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun
from app.models.research_gateway import (
    ArtifactReference,
    GatewayCommand,
    GatewayIdempotencyRequest,
    ResearchConversation,
    ResearchIntent,
    ResearchMessage,
    ResearchRunSpec,
    RoleRun,
    SafeGatewayReason,
    SafeRoleEventSummary,
)
from app.repositories.research_gateway import (
    GatewayCommandReceipt,
    GatewayIntentReceipt,
    GatewayReceipt,
    ResearchGatewayRepository,
)
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.case_tenant_access import CaseTenantAccess
from app.services.research_gateway_policy import (
    CAPABILITY_MANIFEST_VERSION,
    ROLE_MANIFEST_VERSION,
    UNSUPPORTED_FOLLOWUP_MESSAGE,
)

if TYPE_CHECKING:
    from app.schemas.v1.research_gateway import ConversationSnapshotDTO
    from app.services.research_gateway_automatic_adapter import NativeRunRef


class _Runtime(Protocol):
    def start(
        self, *, text: str, tenant_id: str, actor_subject_id: str, commit: bool
    ) -> NativeRunRef: ...


_COMMAND_KINDS = frozenset(
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
_NOT_FOUND = "research conversation not found"
_SCOPE_CHANGE_PREFIXES = ("调整范围：", "调整范围:")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fingerprint(payload: dict) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_text(value: str, *, name: str, limit: int, multiline: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(
            category(character) == "Cc" and not (multiline and character in "\n\r\t")
            for character in value
        )
    ):
        raise ValidationFailedError(f"invalid Gateway {name}")
    return value


def _explicit_scope_change(value: str) -> str | None:
    """Return a visibly selected scope change; never infer free-form chat."""
    for prefix in _SCOPE_CHANGE_PREFIXES:
        if value.startswith(prefix):
            scope = value[len(prefix) :].strip()
            return scope or None
    return None


class ResearchGateway:
    def __init__(self, session: Session, runtime: _Runtime | None = None) -> None:
        self._session = session
        self._repository = ResearchGatewayRepository(session)
        self._case_access = CaseTenantAccess(session)
        if runtime is None:
            from app.services.research_gateway_automatic_adapter import (
                AutomaticResearchRuntime,
            )

            runtime = AutomaticResearchRuntime(session)
        self._runtime = runtime

    @staticmethod
    def _require_actor(actor: ResearchActor) -> str:
        if (
            not isinstance(actor, ResearchActor)
            or not isinstance(actor.subject_id, str)
            or not actor.subject_id.strip()
            or len(actor.subject_id) > 128
            or any(category(char) == "Cc" for char in actor.subject_id)
            or not isinstance(actor.tenant_id, str)
            or not actor.tenant_id.strip()
            or len(actor.tenant_id) > 128
            or any(category(char) == "Cc" for char in actor.tenant_id)
        ):
            raise PermissionDeniedError(
                "a stable research subject is required for Gateway access"
            )
        return actor.subject_id

    def create_conversation(
        self, actor: ResearchActor, title: str | None = None
    ) -> ResearchConversation:
        subject = self._require_actor(actor)
        if title is not None:
            _bounded_text(title, name="title", limit=256)
        try:
            conversation = self._repository.create_conversation(
                tenant_id=actor.tenant_id, owner_subject_id=subject, title=title
            )
            self._session.commit()
            return conversation
        except Exception:
            self._session.rollback()
            raise

    def require_read_access(
        self, actor: ResearchActor, conversation_id: uuid.UUID
    ) -> ResearchConversation:
        subject = self._require_actor(actor)
        conversation = self._session.scalar(
            select(ResearchConversation).where(
                ResearchConversation.id == conversation_id,
                ResearchConversation.tenant_id == actor.tenant_id,
                ResearchConversation.owner_subject_id == subject,
                ResearchConversation.visibility == "private",
            )
        )
        if conversation is None:
            raise NotFoundError(_NOT_FOUND)
        for spec in self._session.scalars(
            select(ResearchRunSpec).where(
                ResearchRunSpec.conversation_id == conversation.id
            )
        ):
            self._require_spec_provenance(actor, spec, conversation.id)
        return conversation

    def _require_spec_provenance(
        self, actor: ResearchActor, spec: ResearchRunSpec, conversation_id: uuid.UUID
    ) -> None:
        if (
            spec.conversation_id != conversation_id
            or spec.tenant_id != actor.tenant_id
            or spec.subject_id != actor.subject_id
        ):
            raise NotFoundError(_NOT_FOUND)
        try:
            self._case_access.require_case(spec.native_case_id, actor.tenant_id)
        except NotFoundError as exc:
            raise NotFoundError(_NOT_FOUND) from exc
        native = self._session.get(ResearchRun, spec.native_run_id)
        if native is None or native.research_case_id != spec.native_case_id:
            raise NotFoundError(_NOT_FOUND)

    def _latest_run_spec(
        self, conversation_id: uuid.UUID
    ) -> ResearchRunSpec | None:
        return self._session.scalar(
            select(ResearchRunSpec)
            .where(ResearchRunSpec.conversation_id == conversation_id)
            .order_by(ResearchRunSpec.created_at.desc(), ResearchRunSpec.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    def list_conversations(self, actor: ResearchActor) -> list[ResearchConversation]:
        subject = self._require_actor(actor)
        conversations = list(
            self._session.scalars(
                select(ResearchConversation)
                .where(
                    ResearchConversation.tenant_id == actor.tenant_id,
                    ResearchConversation.owner_subject_id == subject,
                    ResearchConversation.visibility == "private",
                )
                .order_by(
                    ResearchConversation.updated_at.desc(),
                    ResearchConversation.id.desc(),
                )
            )
        )
        authorized = []
        for conversation in conversations:
            try:
                self.require_read_access(actor, conversation.id)
            except NotFoundError:
                continue
            authorized.append(conversation)
        return authorized

    def read_run_spec(
        self, actor: ResearchActor, run_spec_id: uuid.UUID
    ) -> ResearchRunSpec:
        subject = self._require_actor(actor)
        spec = self._session.scalar(
            select(ResearchRunSpec).where(
                ResearchRunSpec.id == run_spec_id,
                ResearchRunSpec.tenant_id == actor.tenant_id,
                ResearchRunSpec.subject_id == subject,
            )
        )
        if spec is None:
            raise NotFoundError(_NOT_FOUND)
        self.require_read_access(actor, spec.conversation_id)
        return spec

    def read_conversation(
        self,
        actor: ResearchActor,
        conversation_id: uuid.UUID,
        *,
        after_sequence: int = 0,
    ) -> ConversationSnapshotDTO:
        self.require_read_access(actor, conversation_id)
        from app.services.research_gateway_projection import project_conversation

        return project_conversation(
            self._session,
            conversation_id=conversation_id,
            after_sequence=after_sequence,
        )

    def _authorize_request_links(
        self, actor: ResearchActor, request_id: uuid.UUID
    ) -> None:
        """Authorize existing provenance before recovery can bootstrap safe events."""
        for spec in self._session.scalars(
            select(ResearchRunSpec).where(
                ResearchRunSpec.gateway_request_id == request_id
            )
        ):
            self.read_run_spec(actor, spec.id)
        for command in self._session.scalars(
            select(GatewayCommand).where(
                GatewayCommand.gateway_request_id == request_id
            )
        ):
            self.require_read_access(actor, command.conversation_id)
            spec = self.read_run_spec(actor, command.run_spec_id)
            if spec.conversation_id != command.conversation_id:
                raise NotFoundError(_NOT_FOUND)
        for intent in self._session.scalars(
            select(ResearchIntent).where(
                ResearchIntent.gateway_request_id == request_id
            )
        ):
            if (
                intent.tenant_id != actor.tenant_id
                or intent.subject_id != actor.subject_id
            ):
                raise NotFoundError(_NOT_FOUND)
            conversation = self.require_read_access(actor, intent.conversation_id)
            message = self._session.get(ResearchMessage, intent.message_id)
            if (
                message is None
                or message.conversation_id != conversation.id
                or message.tenant_id != actor.tenant_id
                or message.subject_id != actor.subject_id
            ):
                raise NotFoundError(_NOT_FOUND)

    def _acquire_request(
        self,
        actor: ResearchActor,
        *,
        operation: str,
        idempotency_key: str,
        payload: dict,
    ) -> tuple[
        uuid.UUID,
        int,
        GatewayReceipt | GatewayCommandReceipt | GatewayIntentReceipt | None,
    ]:
        subject = self._require_actor(actor)
        _bounded_text(idempotency_key, name="idempotency key", limit=512)
        request, acquired = self._repository.acquire_request(
            tenant_id=actor.tenant_id,
            subject_id=subject,
            operation=operation,
            client_key=idempotency_key,
            request_fingerprint=_fingerprint(payload),
        )
        self._authorize_request_links(actor, request.id)
        receipt = self._repository.recover_or_return_receipt(request)
        if receipt is None and not acquired:
            raise ConflictError("gateway_request_in_progress")
        request_id, attempt = request.id, request.attempt
        # This small transaction has no native intake or conversation writes.
        self._session.commit()
        return request_id, attempt, receipt

    def _require_owned_attempt(
        self,
        actor: ResearchActor,
        *,
        request_id: uuid.UUID,
        attempt: int,
        operation: str,
        idempotency_key: str,
        payload: dict,
    ) -> GatewayIdempotencyRequest:
        request = self._repository.require_request_attempt(
            request_id=request_id, attempt=attempt, now=_utcnow()
        )
        if (
            request.tenant_id != actor.tenant_id
            or request.subject_id != actor.subject_id
            or request.operation != operation
            or request.client_key != idempotency_key
            or request.request_fingerprint != _fingerprint(payload)
        ):
            raise ConflictError("gateway_request_scope_conflict")
        return request

    def send_message(
        self,
        actor: ResearchActor,
        conversation_id: uuid.UUID | None = None,
        text: str = "",
        idempotency_key: str = "",
    ) -> GatewayReceipt | GatewayIntentReceipt:
        subject = self._require_actor(actor)
        _bounded_text(text, name="message text", limit=20_000, multiline=True)
        existing_conversation_id = conversation_id
        scope_change_text = None
        payload = {
            "conversation_id": str(conversation_id) if conversation_id else None,
            "text": text,
        }
        try:
            if conversation_id is not None:
                self.require_read_access(actor, conversation_id)
                scope_change_text = _explicit_scope_change(text)
            request_id, attempt, receipt = self._acquire_request(
                actor,
                operation="send_message",
                idempotency_key=idempotency_key,
                payload=payload,
            )
            if receipt is not None:
                if not isinstance(receipt, (GatewayReceipt, GatewayIntentReceipt)):
                    raise ConflictError(
                        "gateway_idempotency_receipt_provenance_conflict"
                    )
                return receipt
            if conversation_id is not None and scope_change_text is None:
                request = self._require_owned_attempt(
                    actor,
                    request_id=request_id,
                    attempt=attempt,
                    operation="send_message",
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                self.require_read_access(actor, conversation_id)
                message = self._repository.append_message(
                    conversation_id=conversation_id,
                    tenant_id=actor.tenant_id,
                    subject_id=subject,
                    text=text,
                )
                self._repository.append_intent(
                    conversation_id=conversation_id,
                    message_id=message.id,
                    tenant_id=actor.tenant_id,
                    subject_id=subject,
                    intent_kind="unsupported",
                    status="rejected",
                    reason_code=SafeGatewayReason.UNSUPPORTED_IN_GATEWAY_P0,
                    input_sha256=message.input_sha256,
                    gateway_request_id=request.id,
                )
                self._repository.append_message(
                    conversation_id=conversation_id,
                    tenant_id=actor.tenant_id,
                    subject_id=subject,
                    text=UNSUPPORTED_FOLLOWUP_MESSAGE,
                    message_kind="system",
                )
                receipt = self._repository.recover_or_return_receipt(request)
                if not isinstance(receipt, GatewayIntentReceipt):
                    raise ConflictError(
                        "gateway_idempotency_receipt_provenance_conflict"
                    )
                self._session.commit()
                return receipt
            parent = None
            if conversation_id is not None:
                self.require_read_access(actor, conversation_id)
                parent = self._latest_run_spec(conversation_id)
            native_input = scope_change_text or text
            native_text = native_input
            if parent is not None:
                native_text = (
                    "Previous frozen research context:\n"
                    + json.dumps(
                        parent.frozen_scope, ensure_ascii=False, sort_keys=True
                    )
                    + "\nRequested scope change:\n"
                    + native_input
                )
            native_ref = self._runtime.start(
                text=native_text,
                tenant_id=actor.tenant_id,
                actor_subject_id=subject,
                commit=False,
            )
            try:
                self._case_access.require_case(native_ref.case_id, actor.tenant_id)
            except NotFoundError as exc:
                raise NotFoundError(_NOT_FOUND) from exc
            native = self._session.get(ResearchRun, native_ref.research_run_id)
            case = self._session.get(ResearchCase, native_ref.case_id)
            if native is None or case is None or native.research_case_id != case.id:
                raise NotFoundError(_NOT_FOUND)
            scope = load_automatic_research_scope(self._session, native)
            cutoff = native.created_at
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                cutoff = cutoff.replace(tzinfo=UTC)
            else:
                cutoff = cutoff.astimezone(UTC)
            # Fence slow or superseded provider attempts immediately before
            # publishing. The fence and all native/Gateway rows commit together.
            request = self._require_owned_attempt(
                actor,
                request_id=request_id,
                attempt=attempt,
                operation="send_message",
                idempotency_key=idempotency_key,
                payload=payload,
            )
            if conversation_id is None:
                conversation = self._repository.create_conversation(
                    tenant_id=actor.tenant_id,
                    owner_subject_id=subject,
                    title=text.strip()[:80],
                )
                conversation_id = conversation.id
            else:
                self.require_read_access(actor, conversation_id)
            message = self._repository.append_message(
                conversation_id=conversation_id,
                tenant_id=actor.tenant_id,
                subject_id=subject,
                text=text,
            )
            if existing_conversation_id is not None:
                current_parent = self._latest_run_spec(existing_conversation_id)
                if (
                    (parent is None) != (current_parent is None)
                    or (
                        parent is not None
                        and current_parent is not None
                        and parent.id != current_parent.id
                    )
                ):
                    raise ConflictError("research_scope_changed_retry")
            intent = self._repository.append_intent(
                conversation_id=conversation_id,
                message_id=message.id,
                tenant_id=actor.tenant_id,
                subject_id=subject,
                intent_kind="scope_change" if parent is not None else "start",
                input_sha256=message.input_sha256,
                parent_intent_id=parent.intent_id if parent is not None else None,
                gateway_request_id=request_id,
            )
            refs = [
                ArtifactReference("research_case", case.id, case_id=case.id),
                ArtifactReference("research_run", native.id, case_id=case.id),
            ]
            if scope.material_document_version_id is not None:
                refs.append(
                    ArtifactReference(
                        "document_version",
                        scope.material_document_version_id,
                        case_id=case.id,
                    )
                )
            run_spec = self._repository.create_run_spec(
                conversation_id=conversation_id,
                intent_id=intent.id,
                tenant_id=actor.tenant_id,
                subject_id=subject,
                native_case_id=case.id,
                native_run_id=native.id,
                frozen_scope=scope.snapshot(),
                frozen_cutoff={
                    "evidence_cutoff": cutoff.isoformat(),
                    "case_evidence_cutoff": case.evidence_cutoff.isoformat()
                    if case.evidence_cutoff
                    else None,
                },
                frozen_source_policy={
                    "allowed_source_types": list(scope.allowed_source_types),
                    "factors": [
                        {
                            "thesis_id": str(factor.thesis_id),
                            "allowed_source_roles": list(factor.allowed_source_roles),
                        }
                        for factor in scope.factors
                    ],
                },
                role_manifest_version=ROLE_MANIFEST_VERSION,
                capability_manifest_version=CAPABILITY_MANIFEST_VERSION,
                correlation_id=str(request_id),
                input_artifact_refs=refs,
                gateway_request_id=request_id,
            )
            from app.services.research_team import ResearchTeamService

            ResearchTeamService(self._session).ensure_team(run_spec)
            receipt = self._repository.recover_or_return_receipt(request)
            if not isinstance(receipt, GatewayReceipt):
                raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
            self._session.commit()
            return receipt
        except Exception:
            self._session.rollback()
            raise

    def issue_command(
        self,
        actor: ResearchActor,
        conversation_id: uuid.UUID,
        run_spec_id: uuid.UUID,
        command: str,
        idempotency_key: str,
    ) -> GatewayCommandReceipt:
        subject = self._require_actor(actor)
        try:
            self.require_read_access(actor, conversation_id)
            spec = self.read_run_spec(actor, run_spec_id)
            if spec.conversation_id != conversation_id:
                raise NotFoundError(_NOT_FOUND)
            if not isinstance(command, str) or command not in _COMMAND_KINDS:
                raise ValidationFailedError("invalid Gateway command")
            target = {
                "conversation_id": str(conversation_id),
                "run_spec_id": str(run_spec_id),
            }
            payload = {**target, "command": command}
            request_id, attempt, receipt = self._acquire_request(
                actor,
                operation="issue_command",
                idempotency_key=idempotency_key,
                payload=payload,
            )
            if receipt is not None:
                if not isinstance(receipt, GatewayCommandReceipt):
                    raise ConflictError(
                        "gateway_idempotency_receipt_provenance_conflict"
                    )
                return receipt
            request = self._require_owned_attempt(
                actor,
                request_id=request_id,
                attempt=attempt,
                operation="issue_command",
                idempotency_key=idempotency_key,
                payload=payload,
            )
            self.require_read_access(actor, conversation_id)
            self._repository.append_command(
                conversation_id=conversation_id,
                run_spec_id=run_spec_id,
                gateway_request_id=request_id,
                tenant_id=actor.tenant_id,
                subject_id=subject,
                actor_subject_id=subject,
                command_kind=command,
                target_hash=_fingerprint(target),
                target_version=None,
                outcome="rejected",
                reason_code=SafeGatewayReason.UNSUPPORTED_IN_GATEWAY_P0,
            )
            role = self._session.scalar(
                select(RoleRun).where(
                    RoleRun.run_spec_id == run_spec_id,
                    RoleRun.role_key == "scope_identity",
                )
            )
            if role is not None:
                self._repository.append_role_event(
                    run_spec_id=run_spec_id,
                    role_key=role.role_key,
                    event_type="command_rejected",
                    source_key=f"gateway:command:{request_id}",
                    safe_summary=SafeRoleEventSummary.COMMAND_REJECTED,
                    reason_code=SafeGatewayReason.UNSUPPORTED_IN_GATEWAY_P0,
                    status=role.status,
                )
            receipt = self._repository.recover_or_return_receipt(request)
            if not isinstance(receipt, GatewayCommandReceipt):
                raise ConflictError("gateway_idempotency_receipt_provenance_conflict")
            self._session.commit()
            return receipt
        except Exception:
            self._session.rollback()
            raise
