"""Authorized Gateway snapshot, built atomically with durable native catch-up.

The service must establish private conversation ownership before calling this
module. All artifacts are independently reauthorized on every read.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.operational import ResearchRun
from app.models.research_gateway import (
    ResearchConversation,
    ResearchIntent,
    ResearchMessage,
    ResearchRunSpec,
    RoleEvent,
    RoleRun,
)
from app.schemas.v1.research_gateway import (
    ConversationSnapshotDTO,
    GatewayRoleDTO,
    GatewayRunDTO,
    GatewayRunScopeDTO,
    GatewayRunScopeSourcePolicyDTO,
    ResearchMessageDTO,
    SafeArtifactRef,
    SafeRoleEventDTO,
)
from app.services.research_gateway_artifacts import native_artifacts
from app.services.research_gateway_automatic_adapter import (
    GatewayAutomaticResearchAdapter,
)
from app.services.research_gateway_execution import project_execution


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _safe_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _frozen_subject_label(protocol: dict) -> str | None:
    subject = protocol.get("subject")
    if isinstance(subject, dict):
        return _safe_text(subject.get("value")) or _safe_text(subject.get("label"))
    return _safe_text(subject) or _safe_text(protocol.get("value"))


def _parse_frozen_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return utc(parsed)


def _parse_frozen_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat()


def _run_scope(spec: ResearchRunSpec) -> GatewayRunScopeDTO | None:
    frozen_scope = spec.frozen_scope
    frozen_cutoff = spec.frozen_cutoff
    frozen_policy = spec.frozen_source_policy
    if not isinstance(frozen_scope, dict) or not isinstance(frozen_cutoff, dict) or not isinstance(frozen_policy, dict):
        return None
    protocol = frozen_scope.get("automatic_protocol")
    if not isinstance(protocol, dict):
        return None
    topic = _safe_text(protocol.get("research_question"))
    evidence_cutoff_at = _parse_frozen_datetime(frozen_cutoff.get("evidence_cutoff"))
    input_kind = frozen_scope.get("input_kind")
    source_types = frozen_policy.get("allowed_source_types")
    factors = frozen_policy.get("factors")
    if (
        topic is None
        or evidence_cutoff_at is None
        or input_kind not in {"topic", "material"}
        or not isinstance(source_types, list)
        or any(not _safe_text(value) for value in source_types)
        or len(source_types) != len(set(source_types))
        or not isinstance(factors, list)
    ):
        return None
    source_roles: set[str] = set()
    for factor in factors:
        if not isinstance(factor, dict):
            return None
        roles = factor.get("allowed_source_roles")
        if not isinstance(roles, list):
            return None
        for role in roles:
            if role not in {"company_disclosure", "licensed_provider"}:
                return None
            source_roles.add(role)
    if not source_roles:
        return None
    has_intake_material = input_kind == "material"
    if has_intake_material and _safe_text(frozen_scope.get("intake_material_document_version_id")) is None:
        return None
    return GatewayRunScopeDTO(
        topic=topic,
        subject_label=_frozen_subject_label(protocol),
        evidence_cutoff_at=evidence_cutoff_at,
        case_evidence_cutoff_date=_parse_frozen_date(frozen_cutoff.get("case_evidence_cutoff")),
        source_policy=GatewayRunScopeSourcePolicyDTO(
            input_kind=input_kind,
            allowed_source_types=[str(value) for value in source_types],
            allowed_source_roles=sorted(source_roles),
            has_intake_material=has_intake_material,
        ),
    )


def authorized_artifacts(session: Session, spec: ResearchRunSpec) -> dict[tuple[str, str], SafeArtifactRef]:
    """Native identity is safe only after service and adapter provenance checks."""
    return {(ref.kind, str(ref.id)): SafeArtifactRef.model_validate(ref.to_json())
            for ref in native_artifacts(session, spec)}


def to_safe_role_event(event: RoleEvent, allowed: dict[tuple[str, str], SafeArtifactRef]) -> SafeRoleEventDTO:
    # Reconstruct references from authorized native rows, not persisted case_id
    # or locator booleans. Unknown/outdated/cross-case identifiers disappear.
    refs = []
    for ref in event.artifact_refs:
        key = (ref.get("kind"), ref.get("id")) if isinstance(ref, dict) else None
        if key in allowed and allowed[key] not in refs:
            refs.append(allowed[key])
    return SafeRoleEventDTO(
        sequence=event.sequence, run_spec_id=str(event.run_spec_id),
        role=event.role_key, type=event.event_type, status=event.status,
        summary=event.display_text, reason_code=event.reason_code,
        artifacts=refs, occurred_at=utc(event.created_at),
    )


def project_conversation(session: Session, *, conversation_id: uuid.UUID,
                         after_sequence: int = 0) -> ConversationSnapshotDTO:
    from app.services.research_gateway_policy import UNSUPPORTED_FOLLOWUP_MESSAGE

    if type(after_sequence) is not int or after_sequence < 0:
        raise ValidationFailedError("invalid event cursor")
    try:
        GatewayAutomaticResearchAdapter(session).sync_conversation(conversation_id)
        conversation = session.get(ResearchConversation, conversation_id, populate_existing=True)
        if conversation is None:
            raise NotFoundError("research conversation not found")
        specs = list(session.scalars(select(ResearchRunSpec).where(
            ResearchRunSpec.conversation_id == conversation_id,
        ).order_by(ResearchRunSpec.created_at, ResearchRunSpec.id)))
        high_watermark = conversation.next_event_sequence - 1
        all_events = list(session.scalars(select(RoleEvent).where(
            RoleEvent.conversation_id == conversation_id,
            RoleEvent.sequence <= high_watermark,
        ).order_by(RoleEvent.sequence)))
        allowed = {spec.id: authorized_artifacts(session, spec) for spec in specs}
        last_event = {(event.run_spec_id, event.role_key): event.sequence for event in all_events}
        parent_by_intent = {spec.intent_id: str(spec.id) for spec in specs}
        runs = []
        for spec in specs:
            native = session.get(ResearchRun, spec.native_run_id)
            intent = session.get(ResearchIntent, spec.intent_id)
            runs.append(GatewayRunDTO(
                run_spec_id=str(spec.id), native_case_id=str(spec.native_case_id),
                native_run_id=str(spec.native_run_id), status=native.status,
                parent_run_spec_id=parent_by_intent.get(intent.parent_intent_id),
                created_at=utc(spec.created_at), scope=_run_scope(spec),
                execution=project_execution(session, spec, native,
                    authorized_evidence_ids=frozenset(ref.id for ref in allowed[spec.id].values()
                                                      if ref.kind == "evidence_link"),
                    has_authorized_draft=any(ref.kind == "draft" for ref in allowed[spec.id].values())),
            ))
        result = ConversationSnapshotDTO(
            conversation_id=str(conversation.id), title=conversation.title,
            created_at=utc(conversation.created_at), updated_at=utc(conversation.updated_at),
            latest_sequence=high_watermark, event_retention_floor=conversation.event_retention_floor,
            messages=[ResearchMessageDTO(id=str(m.id), sequence=m.sequence, text=m.content,
                                         created_at=utc(m.created_at), kind=m.message_kind)
                      for m in session.scalars(select(ResearchMessage).where(
                          ResearchMessage.conversation_id == conversation_id,
                      ).order_by(ResearchMessage.sequence))
                      if m.message_kind == "user" or (m.message_kind == "system" and m.content == UNSUPPORTED_FOLLOWUP_MESSAGE)],
            runs=runs,
            roles=[GatewayRoleDTO(run_spec_id=str(role.run_spec_id), role=role.role_key,
                                  status=role.status, latest_sequence=last_event.get((role.run_spec_id, role.role_key), 0))
                   for role in session.scalars(select(RoleRun).where(RoleRun.conversation_id == conversation_id)
                                               .order_by(RoleRun.created_at, RoleRun.role_key)
                                               .execution_options(populate_existing=True))],
            events=[to_safe_role_event(e, allowed[e.run_spec_id]) for e in all_events
                    if e.sequence > after_sequence and e.sequence >= conversation.event_retention_floor],
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
