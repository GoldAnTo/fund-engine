"""Transactional persistence for the event-research orchestration owner."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.coverage_decision import (
    BOUNDARY_DECISION_ALTERNATIVES,
    BOUNDARY_DECISION_REASON,
    BOUNDARY_DECISION_RECOMMENDATION,
    BOUNDARY_REASON_CODES,
    COVERAGE_REASON_CODES,
)
from app.errors import ConflictError, NotFoundError, ValidationFailedError
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import CaseTenantAdmission, ResearchCase
from app.models.operational import EventResearchLifecycle, Job, ResearchRun
from app.models.research_orchestration import (
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.repositories.outbox import emit_event
from app.services.event_research_scope_evidence import (
    lock_event_scope_case,
    lock_event_research_lifecycle,
)


ORCHESTRATION_STATES = frozenset(
    {
        "intake",
        "awaiting_scope_confirmation",
        "planning_acquisition",
        "acquiring",
        "freezing_sources",
        "assessing_coverage",
        "synthesizing_evidence",
        "adjudicating_thesis",
        "generating_report",
        "monitoring",
        "retry_wait",
        "recovering",
        "needs_scope_decision",
        "exhausted",
        "cancelled",
        "failed",
    }
)

USER_STAGES = frozenset(
    {
        "intake",
        "scope_confirmation",
        "acquisition",
        "evidence_synthesis",
        "thesis_adjudication",
        "report_monitoring",
    }
)

LEGAL_TRANSITIONS = {
    "intake": frozenset({"awaiting_scope_confirmation", "cancelled"}),
    "awaiting_scope_confirmation": frozenset(
        {"planning_acquisition", "cancelled"}
    ),
    "planning_acquisition": frozenset(
        {"acquiring", "recovering", "retry_wait", "failed", "cancelled"}
    ),
    "acquiring": frozenset(
        {
            "freezing_sources",
            "assessing_coverage",
            "recovering",
            "retry_wait",
            "failed",
            "cancelled",
        }
    ),
    "freezing_sources": frozenset(
        {"assessing_coverage", "retry_wait", "failed", "cancelled"}
    ),
    "assessing_coverage": frozenset(
        {
            "planning_acquisition",
            "synthesizing_evidence",
            "needs_scope_decision",
            "exhausted",
            "failed",
            "cancelled",
        }
    ),
    "synthesizing_evidence": frozenset(
        {"adjudicating_thesis", "retry_wait", "failed", "cancelled"}
    ),
    "adjudicating_thesis": frozenset(
        {"generating_report", "needs_scope_decision", "failed", "cancelled"}
    ),
    "generating_report": frozenset(
        {"monitoring", "retry_wait", "failed", "cancelled"}
    ),
    "monitoring": frozenset({"planning_acquisition", "cancelled"}),
    "retry_wait": frozenset(
        {
            "recovering",
            "planning_acquisition",
            "acquiring",
            "synthesizing_evidence",
            "generating_report",
            "failed",
            "cancelled",
        }
    ),
    "recovering": frozenset(
        {
            "planning_acquisition",
            "acquiring",
            "synthesizing_evidence",
            "generating_report",
            "failed",
            "cancelled",
        }
    ),
    "needs_scope_decision": frozenset(
        {"planning_acquisition", "synthesizing_evidence", "exhausted", "cancelled"}
    ),
    "exhausted": frozenset({"planning_acquisition", "cancelled"}),
    "failed": frozenset({"recovering", "cancelled"}),
    "cancelled": frozenset(),
}

TERMINAL_STATES = frozenset({"exhausted", "failed", "cancelled"})
ACTIONABLE_STATES = frozenset(
    {
        "planning_acquisition",
        "acquiring",
        "retry_wait",
        "recovering",
        "needs_scope_decision",
    }
)


def _acquisition_recovery_start_event_clause():
    return or_(
        ResearchOrchestrationEvent.transition
        == "acquisition_recovery_started",
        and_(
            ResearchOrchestrationEvent.transition == "recovery_started",
            ResearchOrchestrationEvent.idempotency_key.like(
                "acquisition-recovery:%:started"
            ),
        ),
    )


def _acquisition_recovery_terminal_event_clause():
    return or_(
        ResearchOrchestrationEvent.idempotency_key.like(
            "acquisition-recovery:%:completed"
        ),
        ResearchOrchestrationEvent.idempotency_key.like(
            "acquisition-recovery-failed:%"
        ),
    )
SCOPE_REVISION_SOURCE_STATES = frozenset(
    {
        "planning_acquisition",
        "acquiring",
        "freezing_sources",
        "assessing_coverage",
        "synthesizing_evidence",
        "adjudicating_thesis",
        "generating_report",
        "retry_wait",
        "recovering",
    }
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailedError(f"{field} must not be blank")
    return value.strip()


def _canonical_json(value: Any, field: str = "JSON") -> Any:
    """Return the one persisted representation accepted at this boundary."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValidationFailedError(f"{field} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValidationFailedError(f"{field} object keys must be strings")
        return {
            key: _canonical_json(value[key], f"{field}.{key}")
            for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [
            _canonical_json(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValidationFailedError(
        f"{field} contains unsupported {type(value).__name__} value"
    )


def _canonical_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationFailedError(f"{field} must be a JSON object")
    return _canonical_json(value, field)


_COMPATIBILITY_BY_STATE = {
    "intake": ("extracting", "正在建立事件研究"),
    "awaiting_scope_confirmation": (
        "awaiting_key_review",
        "等待确认可验证命题与研究范围",
    ),
    "planning_acquisition": (
        "researching",
        "正在根据已确认范围规划资料获取",
    ),
    "acquiring": ("researching", "正在搜索并获取外部资料"),
    "freezing_sources": ("researching", "正在冻结并去重已获取资料"),
    "assessing_coverage": ("researching", "正在评估各研究目标的证据覆盖"),
    "synthesizing_evidence": ("continuing", "正在归并当前范围内的证据"),
    "adjudicating_thesis": ("continuing", "正在判定命题与竞争性解释"),
    "generating_report": ("continuing", "正在生成带来源的系统研究报告"),
    "monitoring": (
        "draft_ready",
        "系统报告已生成并进入持续监测；未经人工审核",
    ),
    "retry_wait": ("continuing", "系统正在等待自动重试，无需人工操作"),
    "recovering": ("continuing", "系统正在从最近检查点恢复，无需人工操作"),
    "needs_scope_decision": ("awaiting_scope", "研究边界需要你的决定"),
    "exhausted": ("exhausted", "限定检索范围已穷尽，未解决项保持未知"),
    "cancelled": ("exhausted", "研究流程已取消"),
    "failed": ("exhausted", "研究流程失败，已保留最近检查点"),
}


@dataclass(frozen=True, slots=True)
class _DerivedCompatibility:
    status: str
    status_summary: str
    active_run_id: uuid.UUID | None
    current_round: int
    current_gap: str | None
    next_human_action: str | None


@dataclass(frozen=True, slots=True)
class OrchestrationTransitionCommand:
    """Frozen input for one atomic orchestration transition and projections."""

    expected_version: int
    target_state: str
    user_stage: str
    action: str
    reason: str
    checkpoint: dict
    next_action_kind: str | None
    next_action_label: str | None
    next_action_payload: dict | None
    recovery_status: str | None
    heartbeat: datetime | None
    current_scope_version_id: uuid.UUID | None
    current_research_run_id: uuid.UUID | None
    actor: str
    event_transition: str
    event_message: str
    event_payload: dict
    idempotency_key: str

    def fingerprint(self) -> dict[str, Any]:
        """Canonical replay identity for every behavior-visible input."""
        if self.event_transition in {
            "scope_confirmed",
            "protocol_completed",
        }:
            return {
                "actor": self.actor,
                "event_transition": self.event_transition,
                "current_scope_version_id": (
                    None
                    if self.current_scope_version_id is None
                    else str(self.current_scope_version_id)
                ),
            }
        return {
            "target_state": self.target_state,
            "user_stage": self.user_stage,
            "action": self.action,
            "reason": self.reason,
            "checkpoint": self.checkpoint,
            "recovery_status": self.recovery_status,
            "next_action_kind": self.next_action_kind,
            "next_action_label": self.next_action_label,
            "next_action_payload": self.next_action_payload,
            "current_scope_version_id": (
                None
                if self.current_scope_version_id is None
                else str(self.current_scope_version_id)
            ),
            "current_research_run_id": (
                None
                if self.current_research_run_id is None
                else str(self.current_research_run_id)
            ),
            "actor": self.actor,
            "event_transition": self.event_transition,
            "event_message": self.event_message,
            "event_payload": self.event_payload,
        }


class ResearchOrchestrationRepository:
    """Owns all mutable orchestration and append-only transition writes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _lock_case_for_tenant(
        self, case_id: uuid.UUID, tenant_id: str
    ) -> ResearchCase | None:
        admission = self._session.scalar(
            select(CaseTenantAdmission.id)
            .where(
                CaseTenantAdmission.research_case_id == case_id,
                CaseTenantAdmission.tenant_id == tenant_id,
            )
        )
        if admission is None:
            return None
        # One physical ResearchCase row is the shared lock root for protocol
        # mutations, orchestration commands, scope changes, and worker
        # persistence. Tenant admission is immutable, so checking it before
        # the lock preserves not-found secrecy without adding another lock
        # class to the global Case -> orchestration -> run/job order.
        return lock_event_scope_case(self._session, case_id)

    def get_for_update(
        self, case_id: uuid.UUID, tenant_id: str
    ) -> ResearchOrchestration | None:
        """Take the stable Case lock before reading a tenant projection."""
        case = self._lock_case_for_tenant(case_id, tenant_id)
        if case is None:
            return None
        return self._session.scalar(
            select(ResearchOrchestration)
            .where(
                ResearchOrchestration.research_case_id == case_id,
                ResearchOrchestration.tenant_id == tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def create_if_absent(
        self,
        case_id: uuid.UUID,
        tenant_id: str,
        current_scope_version_id: uuid.UUID | None,
        *,
        initial_state: str,
        initial_user_stage: str,
        initial_action: str,
        initial_reason: str,
    ) -> ResearchOrchestration:
        """Create under the caller's Case lock without owning its transaction."""
        tenant_id = _nonblank(tenant_id, "tenant_id")
        if self._lock_case_for_tenant(case_id, tenant_id) is None:
            raise NotFoundError("event research orchestration not found")
        initial_action = _nonblank(initial_action, "initial_action")
        initial_reason = _nonblank(initial_reason, "initial_reason")
        self._validate_state_and_stage(initial_state, initial_user_stage)
        self._validate_scope_owner(case_id, current_scope_version_id)
        existing = self._session.scalar(
            select(ResearchOrchestration)
            .where(
                ResearchOrchestration.research_case_id == case_id,
                ResearchOrchestration.tenant_id == tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            return existing

        now = _utcnow()
        candidate = ResearchOrchestration(
            tenant_id=tenant_id,
            research_case_id=case_id,
            current_scope_version_id=current_scope_version_id,
            current_research_run_id=None,
            state=initial_state,
            user_stage=initial_user_stage,
            current_system_action=initial_action,
            system_action_reason=initial_reason,
            checkpoint_json={},
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            last_heartbeat_at=now,
            recovery_status="healthy",
            state_started_at=now,
            version=0,
            created_at=now,
            updated_at=now,
        )
        # SQLite's pysqlite driver can release a first SAVEPOINT as the outer
        # transaction, defeating caller rollback.  Local tests are
        # single-writer, so insert directly there.  PostgreSQL keeps the
        # savepoint needed to recover an honest uniqueness race without
        # rolling back the route-owned transaction.
        if self._session.get_bind().dialect.name == "sqlite":
            self._session.add(candidate)
            self._session.flush()
            return candidate
        try:
            with self._session.begin_nested():
                self._session.add(candidate)
                self._session.flush()
            return candidate
        except IntegrityError:
            winner = self._session.scalar(
                select(ResearchOrchestration)
                .where(
                    ResearchOrchestration.research_case_id == case_id,
                    ResearchOrchestration.tenant_id == tenant_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if winner is None:
                raise
            return winner

    def transition(
        self,
        orchestration: ResearchOrchestration,
        command: OrchestrationTransitionCommand,
    ) -> ResearchOrchestration:
        """Atomically mutate state, append events, and update compatibility."""
        return self._transition(orchestration, command)

    def record_checkpoint_event(
        self,
        orchestration: ResearchOrchestration,
        command: OrchestrationTransitionCommand,
    ) -> ResearchOrchestration:
        """Record a durable checkpoint while deliberately retaining state.

        This narrow seam is for completed assessments whose successor stage is
        owned by a later worker task.  Generic same-state transitions remain
        forbidden.
        """
        if (
            orchestration.state != "assessing_coverage"
            or command.target_state != "assessing_coverage"
            or command.event_transition != "coverage_ready"
        ):
            raise ValidationFailedError(
                "checkpoint event is reserved for a ready coverage assessment"
            )
        return self._transition(
            orchestration,
            command,
            allow_same_state_checkpoint=True,
        )

    def transition_scope_revision(
        self,
        orchestration: ResearchOrchestration,
        command: OrchestrationTransitionCommand,
        *,
        superseded_run_id: uuid.UUID,
    ) -> ResearchOrchestration:
        """Apply the one narrow restart allowed after a confirmed scope edit."""
        return self._transition(
            orchestration,
            command,
            scope_revision_superseded_run_id=superseded_run_id,
        )

    def _transition(
        self,
        orchestration: ResearchOrchestration,
        command: OrchestrationTransitionCommand,
        *,
        scope_revision_superseded_run_id: uuid.UUID | None = None,
        allow_same_state_checkpoint: bool = False,
    ) -> ResearchOrchestration:
        locked = self.get_for_update(
            orchestration.research_case_id, orchestration.tenant_id
        )
        if locked is None:
            raise NotFoundError("event research orchestration not found")
        _nonblank(command.action, "action")
        _nonblank(command.reason, "reason")
        _nonblank(command.actor, "actor")
        _nonblank(command.event_transition, "event_transition")
        _nonblank(command.event_message, "event_message")
        _nonblank(command.idempotency_key, "idempotency_key")
        normalized_command = replace(
            command,
            checkpoint=_canonical_object(
                command.checkpoint, "checkpoint JSON"
            ),
            next_action_payload=(
                None
                if command.next_action_payload is None
                else _canonical_object(
                    command.next_action_payload,
                    "next action payload JSON",
                )
            ),
            event_payload=_canonical_object(
                command.event_payload, "event payload JSON"
            ),
        )
        fingerprint = _canonical_object(
            {
                **normalized_command.fingerprint(),
                "tenant_id": locked.tenant_id,
                "research_case_id": str(locked.research_case_id),
            },
            "command fingerprint JSON",
        )
        replay = self._event_for_key(
            locked.id, normalized_command.idempotency_key
        )
        if replay is not None:
            replay_fingerprint = _canonical_json(
                replay.payload_json.get("command_fingerprint"),
                "stored command fingerprint JSON",
            )
            if replay_fingerprint != fingerprint:
                raise ConflictError(
                    "idempotency key was used with different input"
                )
            return locked

        if locked.version != normalized_command.expected_version:
            raise ConflictError("stale orchestration version")
        self._validate_state_and_stage(
            normalized_command.target_state, normalized_command.user_stage
        )
        self._validate_next_action(
            normalized_command.target_state,
            normalized_command.next_action_kind,
            normalized_command.next_action_label,
            normalized_command.next_action_payload,
        )
        self._validate_case_pointers(
            locked,
            normalized_command.current_scope_version_id,
            normalized_command.current_research_run_id,
        )
        if scope_revision_superseded_run_id is None:
            if (
                normalized_command.target_state == locked.state
                and not allow_same_state_checkpoint
            ):
                raise ValidationFailedError(
                    "same-state transition requires an existing idempotent replay"
                )
            if (
                normalized_command.target_state != locked.state
                and
                normalized_command.target_state
                not in LEGAL_TRANSITIONS[locked.state]
            ):
                raise ValidationFailedError(
                    "illegal transition: "
                    f"{locked.state} -> {normalized_command.target_state}"
                )
        else:
            self._validate_scope_revision_transition(
                locked,
                normalized_command,
                superseded_run_id=scope_revision_superseded_run_id,
            )

        from_state = locked.state
        self._apply_transition(locked, normalized_command)
        event_payload = _canonical_object(
            {
                **normalized_command.event_payload,
                "tenant_id": locked.tenant_id,
                "research_case_id": str(locked.research_case_id),
                "orchestration_id": str(locked.id),
                "state": locked.state,
                "from_state": from_state,
                "to_state": locked.state,
                "version": locked.version,
                "command_fingerprint": fingerprint,
            },
            "persisted event payload JSON",
        )
        event = self._append_event_locked(
            locked,
            transition=normalized_command.event_transition,
            actor=normalized_command.actor,
            message=normalized_command.event_message,
            payload=event_payload,
            idempotency_key=normalized_command.idempotency_key,
        )
        emit_event(
            self._session,
            type=(
                "research.orchestration."
                f"{normalized_command.event_transition}"
            ),
            aggregate_type="research_orchestration",
            aggregate_id=locked.id,
            payload=_canonical_object(
                {
                    **event_payload,
                    "orchestration_event_id": str(event.id),
                },
                "outbox payload JSON",
            ),
            origin="operational",
            ref_type="research_case",
            ref_id=locked.research_case_id,
            actor=normalized_command.actor,
            correlation_id=normalized_command.idempotency_key[:128],
        )
        self._sync_compatibility_lifecycle(locked)
        self._session.flush()
        return locked

    def _apply_transition(
        self,
        orchestration: ResearchOrchestration,
        command: OrchestrationTransitionCommand,
    ) -> None:
        """Raw row mutation; callers must use :meth:`transition`."""
        state_changed = orchestration.state != command.target_state
        orchestration.current_scope_version_id = command.current_scope_version_id
        orchestration.current_research_run_id = command.current_research_run_id
        orchestration.state = command.target_state
        orchestration.user_stage = command.user_stage
        orchestration.current_system_action = command.action.strip()
        orchestration.system_action_reason = command.reason.strip()
        orchestration.checkpoint_json = dict(command.checkpoint)
        orchestration.next_action_kind = command.next_action_kind
        orchestration.next_action_label = command.next_action_label
        orchestration.next_action_payload = (
            dict(command.next_action_payload)
            if command.next_action_payload is not None
            else None
        )
        orchestration.recovery_status = command.recovery_status
        orchestration.last_heartbeat_at = command.heartbeat
        orchestration.version = command.expected_version + 1
        now = _utcnow()
        if state_changed:
            orchestration.state_started_at = now
        orchestration.updated_at = now

    def append_event(
        self,
        case_id: uuid.UUID,
        tenant_id: str,
        *,
        transition: str,
        actor: str,
        message: str,
        payload: dict,
        idempotency_key: str,
    ) -> ResearchOrchestrationEvent:
        """Atomically append a workflow event and its matching outbox row."""
        tenant_id = _nonblank(tenant_id, "tenant_id")
        orchestration = self.get_for_update(case_id, tenant_id)
        if orchestration is None:
            raise NotFoundError("event research orchestration not found")
        transition = _nonblank(transition, "transition")
        actor = _nonblank(actor, "actor")
        message = _nonblank(message, "message")
        idempotency_key = _nonblank(idempotency_key, "idempotency_key")
        normalized_payload = _canonical_object(payload, "event payload JSON")
        fingerprint = _canonical_object(
            {
                "tenant_id": tenant_id,
                "research_case_id": str(case_id),
                "transition": transition,
                "actor": actor,
                "message": message,
                "payload": normalized_payload,
            },
            "append command fingerprint JSON",
        )
        existing = self._event_for_key(orchestration.id, idempotency_key)
        if existing is not None:
            replay_fingerprint = _canonical_json(
                existing.payload_json.get("command_fingerprint"),
                "stored append fingerprint JSON",
            )
            if replay_fingerprint != fingerprint:
                raise ConflictError(
                    "idempotency key was used with different input"
                )
            return existing
        event_payload = _canonical_object(
            {
                **normalized_payload,
                "tenant_id": orchestration.tenant_id,
                "research_case_id": str(orchestration.research_case_id),
                "orchestration_id": str(orchestration.id),
                "command_fingerprint": fingerprint,
            },
            "persisted event payload JSON",
        )
        event = self._append_event_locked(
            orchestration,
            transition=transition,
            actor=actor,
            message=message,
            payload=event_payload,
            idempotency_key=idempotency_key,
        )
        emit_event(
            self._session,
            type=f"research.orchestration.{transition}",
            aggregate_type="research_orchestration",
            aggregate_id=orchestration.id,
            payload=_canonical_object(
                {
                    **event_payload,
                    "orchestration_event_id": str(event.id),
                },
                "outbox payload JSON",
            ),
            origin="operational",
            ref_type="research_case",
            ref_id=orchestration.research_case_id,
            actor=actor,
            correlation_id=idempotency_key[:128],
        )
        self._session.flush()
        return event

    def _append_event_locked(
        self,
        orchestration: ResearchOrchestration,
        *,
        transition: str,
        actor: str,
        message: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> ResearchOrchestrationEvent:
        existing = self._event_for_key(orchestration.id, idempotency_key)
        if existing is not None:
            return existing
        last_sequence = self._session.scalar(
            select(func.max(ResearchOrchestrationEvent.sequence)).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration.id
            )
        )
        event = ResearchOrchestrationEvent(
            orchestration_id=orchestration.id,
            sequence=int(last_sequence or 0) + 1,
            transition=transition,
            actor=actor,
            message=message,
            payload_json=payload,
            idempotency_key=idempotency_key,
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def replay_command_fingerprint(
        self,
        orchestration_id: uuid.UUID,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Return the canonical replay identity without exposing event rows."""
        event = self._event_for_key(
            orchestration_id,
            _nonblank(idempotency_key, "idempotency_key"),
        )
        if event is None:
            return None
        return _canonical_object(
            event.payload_json.get("command_fingerprint"),
            "stored command fingerprint JSON",
        )

    def _event_for_key(
        self, orchestration_id: uuid.UUID, idempotency_key: str
    ) -> ResearchOrchestrationEvent | None:
        return self._session.scalar(
            select(ResearchOrchestrationEvent).where(
                ResearchOrchestrationEvent.orchestration_id == orchestration_id,
                ResearchOrchestrationEvent.idempotency_key == idempotency_key,
            )
        )

    def record_heartbeat(
        self,
        orchestration: ResearchOrchestration,
        expected_version: int,
        heartbeat: datetime,
    ) -> ResearchOrchestration:
        locked = self.get_for_update(
            orchestration.research_case_id, orchestration.tenant_id
        )
        if locked is None:
            raise NotFoundError("event research orchestration not found")
        if locked.version != expected_version:
            raise ConflictError("stale orchestration version")
        locked.last_heartbeat_at = heartbeat
        locked.updated_at = _utcnow()
        locked.version = expected_version + 1
        self._session.flush()
        return locked

    def find_reconcilable(
        self,
        now: datetime,
        stale_before: datetime,
        limit: int,
        *,
        states: frozenset[str] | None = None,
    ) -> list[ResearchOrchestration]:
        if limit < 1:
            raise ValidationFailedError("limit must be positive")
        if states is not None and not states <= ORCHESTRATION_STATES:
            raise ValidationFailedError("reconciliation states are invalid")
        future_synthesis_retry = (
            select(Job.id)
            .where(
                Job.kind == "research_run",
                Job.target_type == "research_run",
                Job.target_id
                == ResearchOrchestration.current_research_run_id,
                Job.research_case_id == ResearchOrchestration.research_case_id,
                Job.status == "failed",
                Job.next_retry_at.is_not(None),
                Job.next_retry_at > now,
            )
            .correlate(ResearchOrchestration)
            .exists()
        )
        eligibility = [
            ResearchOrchestration.state.not_in(TERMINAL_STATES),
            ResearchOrchestration.updated_at <= now,
            or_(
                ResearchOrchestration.state != "retry_wait",
                ~future_synthesis_retry,
            ),
            or_(
                ResearchOrchestration.state.in_(ACTIONABLE_STATES),
                ResearchOrchestration.last_heartbeat_at.is_(None),
                ResearchOrchestration.last_heartbeat_at < stale_before,
            ),
        ]
        if states is not None:
            eligibility.append(ResearchOrchestration.state.in_(states))
        return self._find_locked_candidates(
            eligibility=eligibility,
            limit=limit,
        )

    def find_stale_acquisition_recovery_candidates(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        limit: int,
    ) -> list[ResearchOrchestration]:
        """Lock only stale acquisition phases, never fresh actionable rows."""
        if limit < 1:
            raise ValidationFailedError("limit must be positive")
        return self._find_locked_candidates(
            eligibility=[
                ResearchOrchestration.state.in_(
                    ("planning_acquisition", "acquiring")
                ),
                ResearchOrchestration.updated_at <= now,
                or_(
                    ResearchOrchestration.last_heartbeat_at.is_(None),
                    ResearchOrchestration.last_heartbeat_at < stale_before,
                ),
            ],
            limit=limit,
        )

    def find_incomplete_acquisition_recoveries(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[ResearchOrchestration]:
        """Lock open acquisition recoveries from immutable event history."""
        if limit < 1:
            raise ValidationFailedError("limit must be positive")
        latest_start = (
            select(func.max(ResearchOrchestrationEvent.sequence))
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == ResearchOrchestration.id,
                _acquisition_recovery_start_event_clause(),
            )
            .correlate(ResearchOrchestration)
            .scalar_subquery()
        )
        latest_terminal = (
            select(func.max(ResearchOrchestrationEvent.sequence))
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == ResearchOrchestration.id,
                _acquisition_recovery_terminal_event_clause(),
            )
            .correlate(ResearchOrchestration)
            .scalar_subquery()
        )
        return self._find_locked_candidates(
            eligibility=[
                ResearchOrchestration.state == "recovering",
                latest_start.is_not(None),
                or_(
                    latest_terminal.is_(None),
                    latest_start > latest_terminal,
                ),
            ],
            limit=limit,
        )

    def latest_acquisition_recovery_start(
        self,
        orchestration_id: uuid.UUID,
    ) -> ResearchOrchestrationEvent | None:
        """Read the latest immutable acquisition recovery identity event."""
        return self._session.scalar(
            select(ResearchOrchestrationEvent)
            .where(
                ResearchOrchestrationEvent.orchestration_id
                == orchestration_id,
                _acquisition_recovery_start_event_clause(),
            )
            .order_by(ResearchOrchestrationEvent.sequence.desc())
            .limit(1)
        )

    def _find_locked_candidates(
        self,
        *,
        eligibility: list,
        limit: int,
    ) -> list[ResearchOrchestration]:
        ordering = (
            ResearchOrchestration.last_heartbeat_at.asc().nullsfirst(),
            ResearchOrchestration.updated_at,
            ResearchOrchestration.id,
        )
        if self._session.get_bind().dialect.name == "postgresql":
            # The Case is the stable lock root for every workflow command.
            # Lock candidates there before touching orchestration rows.
            case_statement = (
                select(ResearchCase.id, ResearchOrchestration.tenant_id)
                .join(
                    ResearchOrchestration,
                    ResearchOrchestration.research_case_id == ResearchCase.id,
                )
                .where(*eligibility)
                .order_by(*ordering)
                .limit(limit)
                .with_for_update(of=ResearchCase, skip_locked=True)
            )
            candidates = self._session.execute(case_statement).all()
            locked_orchestrations = []
            for case_id, tenant_id in candidates:
                orchestration = self.get_for_update(case_id, tenant_id)
                if orchestration is not None:
                    locked_orchestrations.append(orchestration)
            return locked_orchestrations

        # SQLite has no row-lock proof; retain only deterministic behavior.
        statement = (
            select(ResearchOrchestration)
            .where(*eligibility)
            .order_by(*ordering)
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return list(self._session.scalars(statement))

    def _sync_compatibility_lifecycle(
        self,
        orchestration: ResearchOrchestration,
    ) -> EventResearchLifecycle:
        update = self._derive_compatibility(orchestration)
        lifecycle = lock_event_research_lifecycle(
            self._session, orchestration.research_case_id
        )
        if lifecycle is None:
            lifecycle = EventResearchLifecycle(
                research_case_id=orchestration.research_case_id,
                status=update.status,
                active_run_id=update.active_run_id,
                current_round=update.current_round,
                status_summary=update.status_summary,
                current_gap=update.current_gap,
                next_human_action=update.next_human_action,
                updated_at=_utcnow(),
            )
            self._session.add(lifecycle)
        else:
            lifecycle.status = update.status
            lifecycle.active_run_id = update.active_run_id
            lifecycle.current_round = update.current_round
            lifecycle.status_summary = update.status_summary
            lifecycle.current_gap = update.current_gap
            lifecycle.next_human_action = update.next_human_action
            lifecycle.updated_at = _utcnow()
        return lifecycle

    @staticmethod
    def _derive_compatibility(
        orchestration: ResearchOrchestration,
    ) -> _DerivedCompatibility:
        status, summary = _COMPATIBILITY_BY_STATE[orchestration.state]
        checkpoint = orchestration.checkpoint_json
        round_value = (
            checkpoint.get("acquisition_round")
            if isinstance(checkpoint, dict)
            else None
        )
        current_round = (
            round_value
            if isinstance(round_value, int)
            and not isinstance(round_value, bool)
            and round_value >= 0
            else 0
        )
        gap_states = {"needs_scope_decision", "exhausted", "failed"}
        return _DerivedCompatibility(
            status=status,
            status_summary=summary,
            active_run_id=orchestration.current_research_run_id,
            current_round=current_round,
            current_gap=(
                orchestration.system_action_reason
                if orchestration.state in gap_states
                else None
            ),
            next_human_action=(
                orchestration.next_action_label
                if orchestration.state == "needs_scope_decision"
                else None
            ),
        )

    def _validate_scope_owner(
        self,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID | None,
    ) -> None:
        if scope_version_id is None:
            return
        owner_id = self._session.scalar(
            select(EventResearchScopeVersion.research_case_id).where(
                EventResearchScopeVersion.id == scope_version_id
            )
        )
        if owner_id != case_id:
            raise ValidationFailedError(
                "scope version does not belong to the orchestration Case"
            )

    def _validate_case_pointers(
        self,
        orchestration: ResearchOrchestration,
        scope_version_id: uuid.UUID | None,
        research_run_id: uuid.UUID | None,
    ) -> None:
        self._validate_scope_owner(
            orchestration.research_case_id, scope_version_id
        )
        if research_run_id is None:
            return
        owner_id = self._session.scalar(
            select(ResearchRun.research_case_id).where(
                ResearchRun.id == research_run_id
            )
        )
        if owner_id != orchestration.research_case_id:
            raise ValidationFailedError(
                "research run does not belong to the orchestration Case"
            )

    @staticmethod
    def _validate_state_and_stage(state: str, user_stage: str) -> None:
        if state not in ORCHESTRATION_STATES:
            raise ValidationFailedError(f"unsupported orchestration state: {state}")
        if user_stage not in USER_STAGES:
            raise ValidationFailedError(f"unsupported user stage: {user_stage}")

    @staticmethod
    def _validate_next_action(
        target_state: str,
        kind: str | None,
        label: str | None,
        payload: dict | None,
    ) -> None:
        values = (kind, label, payload)
        if target_state == "needs_scope_decision":
            if any(value is None for value in values):
                raise ValidationFailedError(
                    "needs_scope_decision requires one complete next action"
                )
            _nonblank(kind, "next_action_kind")
            _nonblank(label, "next_action_label")
            if not isinstance(payload, dict):
                raise ValidationFailedError("next action payload must be an object")
            if kind == "complete_research_protocol":
                ResearchOrchestrationRepository._validate_protocol_action(
                    payload
                )
            else:
                ResearchOrchestrationRepository._validate_decision_context(
                    payload
                )
            return
        if any(value is not None for value in values):
            raise ValidationFailedError(
                "automatic states must not carry a next action"
            )

    @staticmethod
    def _validate_protocol_action(payload: dict) -> None:
        required = {
            "action",
            "scope_version_id",
            "scope_version",
            "blocked_thesis_ids",
            "reason_codes",
            "blocked_theses",
            "missing",
            "attempted",
            "cannot_continue_reason",
            "recommendation",
            "alternatives",
        }
        if set(payload) != required:
            raise ValidationFailedError(
                "protocol action must use the closed typed context"
            )
        if payload.get("action") != "complete_research_protocol":
            raise ValidationFailedError(
                "protocol action has an invalid action code"
            )
        scope_version_id = payload.get("scope_version_id")
        scope_version = payload.get("scope_version")
        blocked_thesis_ids = payload.get("blocked_thesis_ids")
        if not isinstance(scope_version_id, str):
            raise ValidationFailedError(
                "protocol action requires scope_version_id"
            )
        try:
            uuid.UUID(scope_version_id)
        except ValueError as exc:
            raise ValidationFailedError(
                "protocol action scope_version_id must be a UUID"
            ) from exc
        if (
            not isinstance(scope_version, int)
            or isinstance(scope_version, bool)
            or scope_version < 1
        ):
            raise ValidationFailedError(
                "protocol action scope_version must be positive"
            )
        if not isinstance(blocked_thesis_ids, list) or not blocked_thesis_ids:
            raise ValidationFailedError(
                "protocol action requires blocked_thesis_ids"
            )
        for thesis_id in blocked_thesis_ids:
            if not isinstance(thesis_id, str):
                raise ValidationFailedError(
                    "protocol action blocked_thesis_ids must be UUID strings"
                )
            try:
                uuid.UUID(thesis_id)
            except ValueError as exc:
                raise ValidationFailedError(
                    "protocol action blocked_thesis_ids must be UUID strings"
                ) from exc
        if len(set(blocked_thesis_ids)) != len(blocked_thesis_ids):
            raise ValidationFailedError(
                "protocol action blocked_thesis_ids must be unique"
            )
        reason_codes = payload.get("reason_codes")
        if not isinstance(reason_codes, dict) or set(reason_codes) != set(
            blocked_thesis_ids
        ):
            raise ValidationFailedError(
                "protocol action reason_codes must cover blocked theses"
            )
        for thesis_id, codes in reason_codes.items():
            if not isinstance(codes, list) or not codes or not all(
                isinstance(code, str) and code.strip() for code in codes
            ):
                raise ValidationFailedError(
                    f"protocol action reason codes are invalid for {thesis_id}"
                )
            if codes != sorted(set(codes)):
                raise ValidationFailedError(
                    f"protocol action reason codes are not canonical for {thesis_id}"
                )

        blocked_theses = payload.get("blocked_theses")
        missing = payload.get("missing")
        attempted = payload.get("attempted")
        for field, records in (
            ("blocked_theses", blocked_theses),
            ("missing", missing),
            ("attempted", attempted),
        ):
            if not isinstance(records, list) or len(records) != len(
                blocked_thesis_ids
            ):
                raise ValidationFailedError(
                    f"protocol action {field} must cover blocked theses"
                )
            if {record.get("thesis_id") for record in records if isinstance(record, dict)} != set(blocked_thesis_ids):
                raise ValidationFailedError(
                    f"protocol action {field} thesis IDs are invalid"
                )
            for record in records:
                if not isinstance(record, dict):
                    raise ValidationFailedError(
                        f"protocol action {field} items must be objects"
                    )
                codes = record.get("reason_codes")
                if codes != reason_codes[record["thesis_id"]]:
                    raise ValidationFailedError(
                        f"protocol action {field} reason codes are inconsistent"
                    )
                expected_fields = (
                    {"thesis_id", "protocol_check", "reason_codes"}
                    if field == "attempted"
                    else {"thesis_id", "statement", "reason_codes"}
                )
                if set(record) != expected_fields:
                    raise ValidationFailedError(
                        f"protocol action {field} items use an open schema"
                    )
                if field == "attempted":
                    if record["protocol_check"] != "blocked":
                        raise ValidationFailedError(
                            "protocol action attempted check must be blocked"
                        )
                else:
                    _nonblank(
                        record.get("statement"),
                        f"protocol action {field}.statement",
                    )
        _nonblank(
            payload.get("cannot_continue_reason"),
            "protocol action cannot_continue_reason",
        )
        recommendation = payload.get("recommendation")
        alternatives = payload.get("alternatives")
        ResearchOrchestrationRepository._validate_action_option(
            recommendation, "protocol action recommendation"
        )
        if not isinstance(alternatives, list) or len(alternatives) < 2:
            raise ValidationFailedError(
                "protocol action requires at least two alternatives"
            )
        for index, option in enumerate(alternatives):
            ResearchOrchestrationRepository._validate_action_option(
                option, f"protocol action alternatives[{index}]"
            )

    @staticmethod
    def _validate_action_option(value: object, field: str) -> None:
        if not isinstance(value, dict) or set(value) != {
            "kind",
            "label",
            "impact",
        }:
            raise ValidationFailedError(
                f"{field} must contain kind, label, and impact"
            )
        for key in ("kind", "label", "impact"):
            _nonblank(value.get(key), f"{field}.{key}")

    def _validate_scope_revision_transition(
        self,
        orchestration: ResearchOrchestration,
        command: OrchestrationTransitionCommand,
        *,
        superseded_run_id: uuid.UUID,
    ) -> None:
        if command.event_transition != "scope_revised":
            raise ValidationFailedError(
                "scope revision transition must be named scope_revised"
            )
        if orchestration.state not in SCOPE_REVISION_SOURCE_STATES:
            raise ValidationFailedError(
                "scope revision requires an automatic pre-monitor state"
            )
        if orchestration.current_scope_version_id is None:
            raise ValidationFailedError(
                "scope revision requires a current scope version"
            )
        if (
            command.current_scope_version_id is None
            or command.current_scope_version_id
            == orchestration.current_scope_version_id
        ):
            raise ValidationFailedError(
                "scope revision must change the current scope version"
            )
        old_scope_version = self._session.scalar(
            select(EventResearchScopeVersion.version).where(
                EventResearchScopeVersion.id
                == orchestration.current_scope_version_id
            )
        )
        new_scope_version = self._session.scalar(
            select(EventResearchScopeVersion.version).where(
                EventResearchScopeVersion.id
                == command.current_scope_version_id
            )
        )
        if (
            old_scope_version is None
            or new_scope_version is None
            or new_scope_version <= old_scope_version
        ):
            raise ValidationFailedError(
                "scope revision must adopt a newer scope version"
            )
        if orchestration.current_research_run_id != superseded_run_id:
            raise ValidationFailedError(
                "scope revision superseded run must match the current run"
            )
        superseded = self._session.scalar(
            select(ResearchRun)
            .where(ResearchRun.id == superseded_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if superseded is None or superseded.status != "cancelled":
            raise ValidationFailedError(
                "scope revision requires the previous run to be cancelled"
            )
        if command.target_state == "planning_acquisition":
            if command.user_stage != "acquisition":
                raise ValidationFailedError(
                    "scope revision planning must use acquisition stage"
                )
            if (
                command.current_research_run_id is None
                or command.current_research_run_id == superseded_run_id
            ):
                raise ValidationFailedError(
                    "scope revision must point to a successor run"
                )
            successor = self._session.scalar(
                select(ResearchRun)
                .where(ResearchRun.id == command.current_research_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if successor is None or (
                successor.status,
                successor.stage,
            ) != ("prepared", "awaiting_acquisition"):
                raise ValidationFailedError(
                    "scope revision successor run must be prepared"
                )
            return
        if command.target_state == "needs_scope_decision":
            if command.user_stage != "scope_confirmation":
                raise ValidationFailedError(
                    "blocked scope revision must await scope confirmation"
                )
            if command.current_research_run_id is not None:
                raise ValidationFailedError(
                    "blocked scope revision cannot retain a research run"
                )
            if command.next_action_kind != "complete_research_protocol":
                raise ValidationFailedError(
                    "blocked scope revision must request protocol completion"
                )
            return
        raise ValidationFailedError(
            "scope revision must restart planning or await a scope decision"
        )

    @staticmethod
    def _validate_decision_context(payload: dict) -> None:
        if "action" in payload:
            ResearchOrchestrationRepository._validate_boundary_decision(payload)
            return
        required = {
            "attempted_rounds",
            "unresolved_goal_ids",
            "recommendation",
            "alternatives",
            "impact",
        }
        if not required.issubset(payload):
            raise ValidationFailedError(
                "decision context is missing required fields"
            )
        attempted_rounds = payload["attempted_rounds"]
        if (
            not isinstance(attempted_rounds, int)
            or isinstance(attempted_rounds, bool)
            or attempted_rounds < 0
        ):
            raise ValidationFailedError(
                "decision context attempted_rounds must be nonnegative"
            )
        unresolved = payload["unresolved_goal_ids"]
        if not isinstance(unresolved, list) or not unresolved:
            raise ValidationFailedError(
                "decision context unresolved_goal_ids must be a non-empty list"
            )
        normalized_goals: list[str] = []
        for goal_id in unresolved:
            if not isinstance(goal_id, str) or not goal_id.strip():
                raise ValidationFailedError(
                    "decision context goal IDs must be nonblank strings"
                )
            normalized_goals.append(goal_id.strip())
        if len(set(normalized_goals)) != len(normalized_goals):
            raise ValidationFailedError(
                "decision context goal IDs must be unique"
            )
        _nonblank(payload["recommendation"], "decision context recommendation")
        _nonblank(payload["impact"], "decision context impact")
        alternatives = payload["alternatives"]
        if not isinstance(alternatives, list) or not alternatives:
            raise ValidationFailedError(
                "decision context alternatives must be a non-empty list"
            )
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                raise ValidationFailedError(
                    "decision context alternatives must be objects"
                )
            for field in ("kind", "label", "impact"):
                if field not in alternative:
                    raise ValidationFailedError(
                        f"decision context alternative is missing {field}"
                    )
                _nonblank(
                    alternative[field],
                    f"decision context alternative {field}",
                )

    @staticmethod
    def _validate_boundary_decision(payload: dict) -> None:
        required = {
            "action",
            "attempted_rounds",
            "boundary_codes",
            "affected_goals",
            "missing",
            "attempted",
            "cannot_continue_reason",
            "recommendation",
            "alternatives",
        }
        if set(payload) != required:
            raise ValidationFailedError(
                "typed boundary decision must contain exactly the required fields"
            )
        if payload["action"] != "revise_frozen_research_boundary":
            raise ValidationFailedError("typed boundary decision action is invalid")
        attempted = payload["attempted_rounds"]
        if (
            not isinstance(attempted, int)
            or isinstance(attempted, bool)
            or attempted < 0
        ):
            raise ValidationFailedError(
                "typed boundary decision attempted_rounds must be nonnegative"
            )
        boundaries = payload["boundary_codes"]
        if (
            not isinstance(boundaries, list)
            or not boundaries
            or any(code not in BOUNDARY_REASON_CODES for code in boundaries)
            or len(set(boundaries)) != len(boundaries)
        ):
            raise ValidationFailedError(
                "typed boundary decision boundary_codes are invalid"
            )
        def validate_goals(value, *, field: str) -> tuple[list[str], dict[str, list[str]]]:
            if not isinstance(value, list) or not value:
                raise ValidationFailedError(
                    f"typed boundary decision {field} must be non-empty"
                )
            goal_ids: list[str] = []
            reasons_by_goal: dict[str, list[str]] = {}
            for item in value:
                if not isinstance(item, dict) or set(item) != {
                    "goal_id",
                    "reason_codes",
                }:
                    raise ValidationFailedError(
                        f"typed boundary decision {field} goal is invalid"
                    )
                goal_id = item["goal_id"]
                reasons = item["reason_codes"]
                if not isinstance(goal_id, str) or not goal_id.strip():
                    raise ValidationFailedError(
                        f"typed boundary decision {field} goal_id is invalid"
                    )
                normalized_goal_id = goal_id.strip()
                if (
                    not isinstance(reasons, list)
                    or not reasons
                    or any(reason not in COVERAGE_REASON_CODES for reason in reasons)
                    or len(set(reasons)) != len(reasons)
                ):
                    raise ValidationFailedError(
                        f"typed boundary decision {field} reason_codes are invalid"
                    )
                goal_ids.append(normalized_goal_id)
                reasons_by_goal[normalized_goal_id] = reasons
            if len(set(goal_ids)) != len(goal_ids):
                raise ValidationFailedError(
                    f"typed boundary decision {field} goal IDs must be unique"
                )
            return goal_ids, reasons_by_goal

        affected_ids, affected_reasons = validate_goals(
            payload["affected_goals"], field="affected_goals"
        )
        missing_ids, missing_reasons = validate_goals(
            payload["missing"], field="missing"
        )
        attempted_goals = payload["attempted"]
        if not isinstance(attempted_goals, list) or not attempted_goals:
            raise ValidationFailedError(
                "typed boundary decision attempted must be non-empty"
            )
        attempted_ids: list[str] = []
        searched_by_goal: dict[str, bool] = {}
        for item in attempted_goals:
            if not isinstance(item, dict) or set(item) != {
                "goal_id",
                "search_completed",
            }:
                raise ValidationFailedError(
                    "typed boundary decision attempted goal is invalid"
                )
            goal_id = item["goal_id"]
            if not isinstance(goal_id, str) or not goal_id.strip():
                raise ValidationFailedError(
                    "typed boundary decision attempted goal_id is invalid"
                )
            if not isinstance(item["search_completed"], bool):
                raise ValidationFailedError(
                    "typed boundary decision search_completed is invalid"
                )
            normalized_goal_id = goal_id.strip()
            attempted_ids.append(normalized_goal_id)
            searched_by_goal[normalized_goal_id] = item["search_completed"]
        if len(set(attempted_ids)) != len(attempted_ids):
            raise ValidationFailedError(
                "typed boundary decision attempted goal IDs must be unique"
            )
        if attempted_ids != missing_ids or not set(affected_ids).issubset(missing_ids):
            raise ValidationFailedError(
                "typed boundary decision goal contexts are inconsistent"
            )
        boundary_reasons = set().union(
            *(BOUNDARY_REASON_CODES[code] for code in boundaries)
        )
        expected_affected_ids = [
            goal_id
            for goal_id in missing_ids
            if searched_by_goal[goal_id]
            and boundary_reasons.intersection(missing_reasons[goal_id])
        ]
        if affected_ids != expected_affected_ids:
            raise ValidationFailedError(
                "typed boundary decision affected goals are inconsistent"
            )
        for goal_id in affected_ids:
            reasons = affected_reasons[goal_id]
            expected_reasons = [
                reason
                for reason in missing_reasons[goal_id]
                if reason in boundary_reasons
            ]
            if reasons != expected_reasons:
                raise ValidationFailedError(
                    "typed boundary decision affected reasons are inconsistent"
                )
        for code in boundaries:
            if not any(
                BOUNDARY_REASON_CODES[code].intersection(reasons)
                for reasons in affected_reasons.values()
            ):
                raise ValidationFailedError(
                    "typed boundary decision boundary has no persisted reason"
                )
        if payload["cannot_continue_reason"] != BOUNDARY_DECISION_REASON:
            raise ValidationFailedError(
                "typed boundary decision cannot_continue_reason is invalid"
            )
        if payload["recommendation"] != BOUNDARY_DECISION_RECOMMENDATION:
            raise ValidationFailedError(
                "typed boundary decision recommendation is invalid"
            )
        if payload["alternatives"] != list(BOUNDARY_DECISION_ALTERNATIVES):
            raise ValidationFailedError(
                "typed boundary decision alternatives are invalid"
            )
