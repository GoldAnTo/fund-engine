"""Command service for one locked event-research orchestration projection."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_research import PROTOCOL_COMPLETION_NEXT_HUMAN_ACTION
from app.acquisition.policy import B_SCOPE_POLICY
from app.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import CaseTenantAdmission, Thesis
from app.models.operational import Job, ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.models.research_orchestration import ResearchOrchestration
from app.repositories.research_orchestration import (
    LEGAL_TRANSITIONS,
    OrchestrationTransitionCommand,
    TERMINAL_STATES,
    ResearchOrchestrationRepository,
)
from app.services.auto_research import AutoResearchService
from app.services.event_research_scope_evidence import (
    canonical_scope_thesis_ids,
)
from app.services.research_protocol import ResearchProtocolService


ACQUISITION_PROGRESS_STATES = frozenset(
    {
        "planning_acquisition",
        "acquiring",
        "freezing_sources",
        "assessing_coverage",
        "retry_wait",
        "recovering",
        "failed",
    }
)

SCOPE_DECISIONS = frozenset({"keep_scope", "stop"})
ACQUISITION_RECOVERY_PHASES = frozenset({"planning_acquisition", "acquiring"})
ACQUISITION_RECOVERY_CHECKPOINT_KEY = "_acquisition_recovery"


class _AcquisitionRecoveryCorruption(Exception):
    """Expected persisted-data corruption safe to convert into failure state."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class _AcquisitionRecoveryIdentity:
    episode_id: str
    resume_state: str
    checkpoint_digest: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailedError(f"{field} must not be blank")
    return value.strip()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class OrchestrationPrincipal:
    """Transitional trusted identity until the real identity plan lands."""

    tenant_id: str
    actor: str

    def __post_init__(self) -> None:
        _nonblank(self.tenant_id, "tenant_id")
        _nonblank(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class AcquisitionProgressSnapshot:
    target_state: str
    user_stage: str
    action: str
    reason: str
    checkpoint: dict
    recovery_status: str | None = None

    def __post_init__(self) -> None:
        if self.target_state not in ACQUISITION_PROGRESS_STATES:
            raise ValidationFailedError(
                "target_state is not permitted for acquisition progress"
            )
        _nonblank(self.user_stage, "user_stage")
        _nonblank(self.action, "action")
        _nonblank(self.reason, "reason")
        if not isinstance(self.checkpoint, dict):
            raise ValidationFailedError("checkpoint must be an object")
        if self.recovery_status is not None and self.recovery_status not in {
            "healthy",
            "stale",
            "recovering",
            "failed",
        }:
            raise ValidationFailedError("unsupported recovery_status")


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    kind: str
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in SCOPE_DECISIONS:
            raise ValidationFailedError("scope decision must be keep_scope or stop")
        _nonblank(self.reason, "reason")


class ResearchOrchestrationService:
    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = ResearchOrchestrationRepository(session)
        self._clock = clock or _utcnow

    def confirm_scope(
        self,
        case_id: uuid.UUID,
        principal: OrchestrationPrincipal,
        idempotency_key: str,
        *,
        scope_version_id: uuid.UUID | None = None,
        expected_scope_version: int | None = None,
    ) -> ResearchOrchestration:
        key = _nonblank(idempotency_key, "idempotency_key")
        orchestration = self._lock_owned(case_id, principal, allow_absent=True)
        if orchestration is not None:
            if self._is_confirm_replay(
                orchestration,
                principal,
                key,
                scope_version_id=scope_version_id,
            ):
                return orchestration
            if orchestration.state != "awaiting_scope_confirmation":
                raise ConflictError("research scope is already confirmed")

        scope = self._latest_scope(case_id)
        if scope is None:
            raise ValidationFailedError(
                "event research case has no scope version to confirm"
            )
        if scope_version_id is not None and scope.id != scope_version_id:
            raise ValidationFailedError(
                "scope_version_id must match the current event research scope"
            )
        if (
            expected_scope_version is not None
            and scope.version != expected_scope_version
        ):
            raise ValidationFailedError(
                "scope_version must match the current event research scope"
            )
        active_thesis_ids = canonical_scope_thesis_ids(
            self._session,
            case_id,
            scope.id,
        )
        if not active_thesis_ids:
            raise ValidationFailedError(
                "current event research scope has no or missing active theses"
            )
        if orchestration is None:
            orchestration = self._repository.create_if_absent(
                case_id,
                principal.tenant_id,
                scope.id,
                initial_state="awaiting_scope_confirmation",
                initial_user_stage="scope_confirmation",
                initial_action="Waiting for scope confirmation",
                initial_reason="Acquisition requires a confirmed scope",
            )
            if self._is_confirm_replay(
                orchestration,
                principal,
                key,
                scope_version_id=scope.id,
            ):
                return orchestration
            if orchestration.state != "awaiting_scope_confirmation":
                raise ConflictError("research scope is already confirmed")

        run = AutoResearchService(self._session).start(
            case_id,
            max_rounds=3,
            budget=100,
            thesis_ids=active_thesis_ids,
            trigger="scope_confirmation",
            enqueue=False,
            commit=False,
            scope_context={
                "execution_mode": "orchestrated_evidence_synthesis",
                "scope_version_id": str(scope.id),
                "orchestration_id": str(orchestration.id),
            },
        )
        checkpoint = {
            "scope_version_id": str(scope.id),
            "research_run_id": str(run.id),
            "acquisition_round": 1,
        }

        command = OrchestrationTransitionCommand(
            expected_version=orchestration.version,
            target_state="planning_acquisition",
            user_stage="acquisition",
            action="正在根据已确认范围规划资料获取",
            reason=("研究范围已确认，系统将为每个证据目标生成受治理的获取任务"),
            checkpoint=checkpoint,
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            recovery_status="healthy",
            heartbeat=_utcnow(),
            current_scope_version_id=scope.id,
            current_research_run_id=run.id,
            actor=principal.actor,
            event_transition="scope_confirmed",
            event_message="研究范围已确认，自动研究主线已准备",
            event_payload={
                "scope_version_id": str(scope.id),
                "research_run_id": str(run.id),
                "acquisition_round": 1,
            },
            idempotency_key=key,
        )
        return self._repository.transition(
            orchestration,
            command,
        )

    def lock_scope_revision_target(
        self,
        case_id: uuid.UUID,
    ) -> ResearchOrchestration | None:
        """Lock an existing tenant projection after the stable Case row."""
        tenant_id = self._session.scalar(
            select(CaseTenantAdmission.tenant_id).where(
                CaseTenantAdmission.research_case_id == case_id
            )
        )
        if tenant_id is None:
            return None
        return self._repository.get_for_update(case_id, tenant_id)

    def adopt_revised_scope(
        self,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        *,
        actor: str,
    ) -> ResearchOrchestration:
        """Cancel stale work and atomically adopt one newly persisted scope."""
        actor = _nonblank(actor, "actor")
        orchestration = self.lock_scope_revision_target(case_id)
        if orchestration is None:
            raise NotFoundError("event research orchestration not found")
        latest_scope = self._latest_scope(case_id)
        if latest_scope is None or latest_scope.id != scope_version_id:
            raise ValidationFailedError(
                "scope revision must adopt the current latest scope"
            )
        active_thesis_ids = canonical_scope_thesis_ids(
            self._session,
            case_id,
            scope_version_id,
        )
        if not active_thesis_ids:
            raise ValidationFailedError(
                "revised event research scope has no or missing active theses"
            )
        superseded_run_id = orchestration.current_research_run_id
        if superseded_run_id is None:
            raise ConflictError("scope revision requires a current research run")
        auto_research = AutoResearchService(self._session, clock=self._clock)
        try:
            superseded = auto_research.cancel_for_scope_replacement(
                superseded_run_id, case_id=case_id
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        max_rounds = superseded.max_rounds
        budget = superseded.budget

        blocked: list[tuple[uuid.UUID, list[str]]] = []
        protocol = ResearchProtocolService(self._session)
        for thesis_id in active_thesis_ids:
            thesis = self._session.get(Thesis, thesis_id)
            if thesis is None or not thesis.research_protocol_required:
                continue
            result = protocol.check_researchability(thesis.id)
            if result.status == "blocked":
                blocked.append((thesis.id, result.reason_codes))

        successor = None
        if not blocked:
            successor = auto_research.start(
                case_id,
                max_rounds=max_rounds,
                budget=budget,
                thesis_ids=active_thesis_ids,
                trigger="scope_revision",
                enqueue=False,
                commit=False,
                scope_context={
                    "execution_mode": "orchestrated_evidence_synthesis",
                    "scope_version_id": str(scope_version_id),
                    "orchestration_id": str(orchestration.id),
                    "superseded_run_id": str(superseded_run_id),
                },
            )

        if successor is None:
            target_state = "needs_scope_decision"
            user_stage = "scope_confirmation"
            action = "已确认的研究范围发生变化，等待完成新增因素的研究协议"
            reason = (
                "已确认范围已修订，旧研究运行已取消；新范围包含尚未完成研究协议的因素"
            )
            next_action_kind = "complete_research_protocol"
            next_action_label = PROTOCOL_COMPLETION_NEXT_HUMAN_ACTION
            blocked_theses = []
            for thesis_id, reason_codes in blocked:
                thesis = self._session.get(Thesis, thesis_id)
                if thesis is None:
                    raise ValidationFailedError(
                        "blocked protocol thesis no longer exists"
                    )
                blocked_theses.append(
                    {
                        "thesis_id": str(thesis_id),
                        "statement": thesis.statement,
                        "reason_codes": sorted(set(reason_codes)),
                    }
                )
            next_action_payload = {
                "action": "complete_research_protocol",
                "scope_version_id": str(scope_version_id),
                "scope_version": latest_scope.version,
                "blocked_thesis_ids": [str(thesis_id) for thesis_id, _ in blocked],
                "reason_codes": {
                    str(thesis_id): sorted(set(reason_codes))
                    for thesis_id, reason_codes in blocked
                },
                "blocked_theses": blocked_theses,
                "missing": [
                    {
                        "thesis_id": item["thesis_id"],
                        "statement": item["statement"],
                        "reason_codes": item["reason_codes"],
                    }
                    for item in blocked_theses
                ],
                "attempted": [
                    {
                        "thesis_id": item["thesis_id"],
                        "protocol_check": "blocked",
                        "reason_codes": item["reason_codes"],
                    }
                    for item in blocked_theses
                ],
                "cannot_continue_reason": (
                    "新增命题的研究协议尚未满足可研究性门槛，"
                    "系统不能在当前冻结范围内启动资料获取。"
                ),
                "recommendation": {
                    "kind": "complete_research_protocol",
                    "label": "完成研究协议",
                    "impact": ("补全新增命题的研究协议后，系统可按当前范围启动补证。"),
                },
                "alternatives": [
                    {
                        "kind": "revise_scope",
                        "label": "移除未就绪命题",
                        "impact": "创建新范围版本，并仅研究协议已就绪的命题。",
                    },
                    {
                        "kind": "stop",
                        "label": "停止本次研究",
                        "impact": "不启动新的补证运行，并保留现有过程记录。",
                    },
                ],
            }
            current_research_run_id = None
        else:
            target_state = "planning_acquisition"
            user_stage = "acquisition"
            action = "已确认的研究范围发生变化，正在重新规划资料获取"
            reason = (
                "已确认范围已修订，旧研究运行已取消，系统将按新范围重新规划资料获取"
            )
            next_action_kind = None
            next_action_label = None
            next_action_payload = None
            current_research_run_id = successor.id

        checkpoint = {
            "scope_version_id": str(scope_version_id),
            "research_run_id": (
                None
                if current_research_run_id is None
                else str(current_research_run_id)
            ),
            "superseded_run_id": str(superseded_run_id),
            "acquisition_round": 1,
        }
        command = OrchestrationTransitionCommand(
            expected_version=orchestration.version,
            target_state=target_state,
            user_stage=user_stage,
            action=action,
            reason=reason,
            checkpoint=checkpoint,
            next_action_kind=next_action_kind,
            next_action_label=next_action_label,
            next_action_payload=next_action_payload,
            recovery_status="healthy",
            heartbeat=_utcnow(),
            current_scope_version_id=scope_version_id,
            current_research_run_id=current_research_run_id,
            actor=actor,
            event_transition="scope_revised",
            event_message=action,
            event_payload={
                "previous_scope_version_id": str(
                    orchestration.current_scope_version_id
                ),
                "scope_version_id": str(scope_version_id),
                "superseded_run_id": str(superseded_run_id),
                "successor_run_id": (None if successor is None else str(successor.id)),
                "blocked_thesis_ids": [str(thesis_id) for thesis_id, _ in blocked],
            },
            idempotency_key=f"scope-revised:{scope_version_id}",
        )
        return self._repository.transition_scope_revision(
            orchestration,
            command,
            superseded_run_id=superseded_run_id,
        )

    def resume_after_protocol_completion(
        self,
        case_id: uuid.UUID,
        principal: OrchestrationPrincipal,
        scope_version_id: uuid.UUID,
        idempotency_key: str,
        expected_scope_version: int | None = None,
    ) -> ResearchOrchestration:
        """Prepare the one successor allowed after all protocol gates pass."""
        key = _nonblank(idempotency_key, "idempotency_key")
        orchestration = self._lock_owned(case_id, principal)
        latest_scope = self._latest_scope(case_id)
        if expected_scope_version is not None and (
            latest_scope is None or latest_scope.version != expected_scope_version
        ):
            raise ValidationFailedError(
                "scope_version must match the current latest workflow scope"
            )
        if self._is_protocol_completion_replay(
            orchestration,
            principal,
            key,
            scope_version_id=scope_version_id,
        ):
            return orchestration
        if orchestration.state != "needs_scope_decision":
            raise ConflictError(
                "protocol completion resume requires needs_scope_decision state"
            )
        if (
            latest_scope is None
            or latest_scope.id != scope_version_id
            or orchestration.current_scope_version_id != scope_version_id
        ):
            raise ValidationFailedError(
                "scope_version_id must match the current latest workflow scope"
            )
        if orchestration.current_research_run_id is not None:
            raise ConflictError(
                "protocol completion resume requires no current research run"
            )
        context = orchestration.next_action_payload
        if (
            orchestration.next_action_kind != "complete_research_protocol"
            or orchestration.next_action_label != PROTOCOL_COMPLETION_NEXT_HUMAN_ACTION
            or not isinstance(context, dict)
            or context.get("scope_version_id") != str(scope_version_id)
            or not isinstance(context.get("blocked_thesis_ids"), list)
            or not context["blocked_thesis_ids"]
            or "研究协议" not in (orchestration.system_action_reason or "")
        ):
            raise ConflictError(
                "workflow is not blocked on research protocol completion"
            )

        active_thesis_ids = canonical_scope_thesis_ids(
            self._session,
            case_id,
            scope_version_id,
        )
        if not active_thesis_ids:
            raise ValidationFailedError(
                "current event research scope has no or missing active theses"
            )
        protocol = ResearchProtocolService(self._session)
        blocked_reasons: list[str] = []
        for thesis_id in active_thesis_ids:
            thesis = self._session.get(Thesis, thesis_id)
            if thesis is None or not thesis.research_protocol_required:
                continue
            result = protocol.check_researchability(thesis.id)
            if result.status == "blocked":
                blocked_reasons.extend(result.reason_codes)
        if blocked_reasons:
            raise ValidationFailedError(
                "researchability gate blocked: "
                + ", ".join(sorted(set(blocked_reasons)))
            )

        run = AutoResearchService(self._session).start(
            case_id,
            max_rounds=3,
            budget=100,
            thesis_ids=active_thesis_ids,
            trigger="protocol_completion",
            enqueue=False,
            commit=False,
            scope_context={
                "execution_mode": "orchestrated_evidence_synthesis",
                "scope_version_id": str(scope_version_id),
                "orchestration_id": str(orchestration.id),
            },
        )
        action = "研究协议已完成，正在准备资料获取"
        reason = "当前范围内所有必需研究协议均已通过校验，系统将按确认范围规划资料获取"
        checkpoint = {
            "scope_version_id": str(scope_version_id),
            "research_run_id": str(run.id),
            "acquisition_round": 1,
        }
        command = OrchestrationTransitionCommand(
            expected_version=orchestration.version,
            target_state="planning_acquisition",
            user_stage="acquisition",
            action=action,
            reason=reason,
            checkpoint=checkpoint,
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            recovery_status="healthy",
            heartbeat=_utcnow(),
            current_scope_version_id=scope_version_id,
            current_research_run_id=run.id,
            actor=principal.actor,
            event_transition="protocol_completed",
            event_message=action,
            event_payload={
                "scope_version_id": str(scope_version_id),
                "research_run_id": str(run.id),
                "acquisition_round": 1,
            },
            idempotency_key=key,
        )
        return self._repository.transition(orchestration, command)

    def record_acquisition_progress(
        self,
        case_id: uuid.UUID,
        principal: OrchestrationPrincipal,
        snapshot: AcquisitionProgressSnapshot,
        idempotency_key: str,
    ) -> ResearchOrchestration:
        if not isinstance(snapshot, AcquisitionProgressSnapshot):
            raise ValidationFailedError(
                "snapshot must be an AcquisitionProgressSnapshot"
            )
        key = _nonblank(idempotency_key, "idempotency_key")
        orchestration = self._lock_owned(case_id, principal)
        recovery_status = (
            snapshot.recovery_status
            if snapshot.recovery_status is not None
            else orchestration.recovery_status
        )
        command = OrchestrationTransitionCommand(
            expected_version=orchestration.version,
            target_state=snapshot.target_state,
            user_stage=snapshot.user_stage,
            action=snapshot.action,
            reason=snapshot.reason,
            checkpoint=snapshot.checkpoint,
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            recovery_status=recovery_status,
            heartbeat=_utcnow(),
            current_scope_version_id=orchestration.current_scope_version_id,
            current_research_run_id=orchestration.current_research_run_id,
            actor=principal.actor,
            event_transition="acquisition_progress_recorded",
            event_message=snapshot.action,
            event_payload={
                "target_state": snapshot.target_state,
                "user_stage": snapshot.user_stage,
                "reason": snapshot.reason,
                "checkpoint": snapshot.checkpoint,
                "recovery_status": recovery_status,
            },
            idempotency_key=key,
        )
        return self._repository.transition(orchestration, command)

    def reconcile(
        self, case_id: uuid.UUID, principal: OrchestrationPrincipal
    ) -> ResearchOrchestration:
        orchestration = self._lock_owned(case_id, principal)
        if orchestration.state not in {
            "planning_acquisition",
            "acquiring",
            "assessing_coverage",
            "synthesizing_evidence",
            "adjudicating_thesis",
            "generating_report",
            "retry_wait",
        }:
            return orchestration
        try:
            if orchestration.state == "planning_acquisition":
                return self._dispatch_acquisition(orchestration, principal)
            if orchestration.state == "assessing_coverage":
                if self._coverage_is_ready(orchestration):
                    return self._start_synthesis(orchestration, principal)
                from app.services.acquisition_coverage import (
                    AcquisitionCoverageService,
                )

                return AcquisitionCoverageService(self._session).reconcile(
                    orchestration, principal=principal
                )
            if orchestration.state == "acquiring":
                return self._reconcile_acquisition(orchestration, principal)
            if orchestration.state == "retry_wait":
                return self._resume_synthesis_retry(orchestration, principal)
            if orchestration.state == "synthesizing_evidence":
                return self._reconcile_synthesis(orchestration, principal)
            if orchestration.state == "adjudicating_thesis":
                return self._start_report(orchestration, principal)
            return self._generate_report_and_monitor(orchestration, principal)
        except (
            ConflictError,
            NotFoundError,
            PermissionDeniedError,
            ValidationFailedError,
            ValueError,
        ) as exc:
            return self._fail_acquisition_reconciliation(
                orchestration,
                principal,
                reason_code=type(exc).__name__,
            )

    @staticmethod
    def _coverage_is_ready(orchestration: ResearchOrchestration) -> bool:
        checkpoint = orchestration.checkpoint_json
        decision = (
            checkpoint.get("coverage_decision")
            if isinstance(checkpoint, dict)
            else None
        )
        return isinstance(decision, dict) and decision.get("decision") == "ready"

    def _start_synthesis(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        run_id = orchestration.current_research_run_id
        if run_id is None or orchestration.current_scope_version_id is None:
            raise ValidationFailedError("ready coverage has no frozen run or scope")
        run = self._session.get(ResearchRun, run_id)
        if run is None or run.research_case_id != orchestration.research_case_id:
            raise ValidationFailedError("ready coverage research run is stale")
        AutoResearchService(self._session).enqueue_prepared_run(run.id, commit=False)
        checkpoint = dict(orchestration.checkpoint_json)
        checkpoint["synthesis"] = {"research_run_id": str(run.id), "status": "queued"}
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="synthesizing_evidence",
                user_stage="evidence_synthesis",
                action="正在归并当前范围内的已准入证据",
                reason="冻结资料覆盖已经达标，现有研究运行已幂等入队",
                checkpoint=checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=_utcnow(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=run.id,
                actor=principal.actor,
                event_transition="evidence_synthesis_started",
                event_message="资料覆盖达标，证据归并运行已启动",
                event_payload={"research_run_id": str(run.id)},
                idempotency_key=f"evidence-synthesis-started:{run.id}",
            ),
        )

    def _reconcile_synthesis(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        run_id = orchestration.current_research_run_id
        run = self._session.get(ResearchRun, run_id) if run_id is not None else None
        if run is None or run.research_case_id != orchestration.research_case_id:
            raise ValidationFailedError("synthesis research run is stale")
        if run.status in {"prepared", "queued", "running"}:
            if run.status == "prepared":
                AutoResearchService(self._session).enqueue_prepared_run(
                    run.id, commit=False
                )
            return orchestration
        if run.status == "failed":
            auto_research = AutoResearchService(self._session, clock=self._clock)
            status = auto_research.synthesis_status(run.id)
            if status.state in {"queued", "running", "waiting_for_review"}:
                return orchestration
            if status.state not in {"failed", "scheduled", "exhausted"}:
                return orchestration
            retry = auto_research.reconcile_synthesis_failure(
                run.id,
                commit=False,
            )
            if retry.state == "exhausted":
                return self._fail_exhausted_synthesis(orchestration, principal, retry)
            checkpoint = dict(orchestration.checkpoint_json)
            checkpoint["synthesis_retry"] = {
                "research_run_id": str(run.id),
                "status": retry.state,
                "next_retry_at": (
                    retry.next_retry_at.isoformat()
                    if retry.next_retry_at is not None
                    else None
                ),
                "policy_version": retry.policy_version,
            }
            return self._repository.transition(
                orchestration,
                OrchestrationTransitionCommand(
                    expected_version=orchestration.version,
                    target_state="retry_wait",
                    user_stage="evidence_synthesis",
                    action="证据归并运行失败，正在等待有界自动重试",
                    reason="退避时间持久化；技术失败不改变冻结研究范围",
                    checkpoint=checkpoint,
                    next_action_kind=None,
                    next_action_label=None,
                    next_action_payload=None,
                    recovery_status="recovering",
                    heartbeat=self._clock(),
                    current_scope_version_id=orchestration.current_scope_version_id,
                    current_research_run_id=run.id,
                    actor=principal.actor,
                    event_transition="synthesis_retry_scheduled",
                    event_message="证据归并运行已按持久化策略安排重试",
                    event_payload={"research_run_id": str(run.id)},
                    idempotency_key=f"synthesis-retry:{run.id}:attempt:{retry.attempt}",
                ),
            )
        if run.status != "succeeded":
            return orchestration
        evidence_link_ids = self._validated_synthesis_receipt(orchestration, run)
        if evidence_link_ids is None:
            return orchestration
        checkpoint = dict(orchestration.checkpoint_json)
        checkpoint["synthesis"] = {
            "research_run_id": str(run.id),
            "status": "completed",
            "execution_mode": "orchestrated_evidence_synthesis",
            "evidence_link_ids": evidence_link_ids,
        }
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="adjudicating_thesis",
                user_stage="thesis_adjudication",
                action="正在判定命题与竞争性解释",
                reason="证据抽取与评估运行已完成",
                checkpoint=checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=_utcnow(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=run.id,
                actor=principal.actor,
                event_transition="evidence_synthesized",
                event_message="证据归并运行已完成",
                event_payload={"research_run_id": str(run.id)},
                idempotency_key=f"evidence-synthesized:{run.id}",
            ),
        )

    def _validated_synthesis_receipt(
        self,
        orchestration: ResearchOrchestration,
        run: ResearchRun,
    ) -> list[str] | None:
        """Return a worker receipt only when it belongs to this exact workflow."""
        if (
            run.stage != "complete"
            or run.stop_reason != "orchestrated_evidence_synthesized"
        ):
            return None
        scope_event = self._session.scalar(
            select(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == run.id,
                ResearchRunEvent.stage == "scope",
            )
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        scope_payload = scope_event.payload_json if scope_event is not None else {}
        if (
            scope_payload.get("execution_mode") != "orchestrated_evidence_synthesis"
            or scope_payload.get("scope_version_id")
            != str(orchestration.current_scope_version_id)
            or scope_payload.get("orchestration_id") != str(orchestration.id)
        ):
            return None
        completion_event = self._session.scalar(
            select(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == run.id,
                ResearchRunEvent.stage == "complete",
                ResearchRunEvent.status == "completed",
            )
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        completion = (
            completion_event.payload_json if completion_event is not None else {}
        )
        raw_ids = completion.get("evidence_link_ids")
        job = self._session.scalar(
            select(Job).where(
                Job.kind == "research_run",
                Job.target_type == "research_run",
                Job.target_id == run.id,
                Job.research_case_id == run.research_case_id,
            )
        )
        if (
            completion.get("execution_mode") != "orchestrated_evidence_synthesis"
            or completion.get("scope_version_id")
            != str(orchestration.current_scope_version_id)
            or not isinstance(raw_ids, list)
            or job is None
            or job.status != "succeeded"
            or job.step != "complete"
            or completion.get("job_id") != str(job.id)
            or completion.get("job_attempt") != job.attempt
        ):
            return None
        try:
            evidence_link_ids = sorted({str(uuid.UUID(str(item))) for item in raw_ids})
        except (TypeError, ValueError, AttributeError):
            return None
        if completion.get("evidence_count") != len(evidence_link_ids):
            return None
        tasks = list(
            self._session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run.id)
                .order_by(ResearchTask.id)
            )
        )
        if not tasks:
            return None
        task_evidence_ids: set[str] = set()
        for task in tasks:
            result = task.result if isinstance(task.result, dict) else {}
            raw_task_ids = result.get("evidence_link_ids")
            if (
                task.status != "done"
                or task.stage != "completed"
                or result.get("execution_mode") != "orchestrated_evidence_synthesis"
                or result.get("scope_version_id")
                != str(orchestration.current_scope_version_id)
                or not isinstance(raw_task_ids, list)
            ):
                return None
            try:
                canonical_task_ids = {
                    str(uuid.UUID(str(item))) for item in raw_task_ids
                }
            except (TypeError, ValueError, AttributeError):
                return None
            if task.evidence_count != len(canonical_task_ids):
                return None
            task_evidence_ids.update(canonical_task_ids)
        if task_evidence_ids != set(evidence_link_ids):
            return None
        return evidence_link_ids

    def _resume_synthesis_retry(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        retry = (
            orchestration.checkpoint_json.get("synthesis_retry")
            if isinstance(orchestration.checkpoint_json, dict)
            else None
        )
        run_id = orchestration.current_research_run_id
        run = self._session.get(ResearchRun, run_id) if run_id is not None else None
        if not isinstance(retry, dict) or retry.get("research_run_id") != str(run_id):
            return orchestration
        if run is None or run.research_case_id != orchestration.research_case_id:
            raise ValidationFailedError("synthesis retry run is stale")
        auto_research = AutoResearchService(self._session, clock=self._clock)
        if run.status == "failed":
            retry_status = auto_research.reconcile_synthesis_failure(
                run.id, commit=False
            )
            if retry_status.state == "scheduled":
                return orchestration
            if retry_status.state == "exhausted":
                return self._fail_exhausted_synthesis(
                    orchestration, principal, retry_status
                )
        if run.status not in {"queued", "running", "waiting_for_review", "succeeded"}:
            return orchestration
        status = auto_research.synthesis_status(run.id)
        attempt = status.attempt
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="synthesizing_evidence",
                user_stage="evidence_synthesis",
                action="正在从持久化检查点恢复证据归并",
                reason="同一个研究运行已重新入队，没有创建重复运行",
                checkpoint=dict(orchestration.checkpoint_json),
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=self._clock(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=run.id,
                actor=principal.actor,
                event_transition="synthesis_recovered",
                event_message="证据归并运行已恢复",
                event_payload={"research_run_id": str(run.id)},
                idempotency_key=f"synthesis-recovered:{run.id}:attempt:{attempt}",
            ),
        )

    def _fail_exhausted_synthesis(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
        retry_status,
    ) -> ResearchOrchestration:
        checkpoint = dict(orchestration.checkpoint_json)
        checkpoint["synthesis_retry"] = {
            "research_run_id": str(retry_status.run_id),
            "status": "exhausted",
            "failure_count": retry_status.failure_count,
            "policy_version": retry_status.policy_version,
        }
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="failed",
                user_stage="evidence_synthesis",
                action="证据归并已达到自动重试上限",
                reason="必要研究任务未成功完成，系统不会生成结论或监控",
                checkpoint=checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="failed",
                heartbeat=self._clock(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=retry_status.run_id,
                actor=principal.actor,
                event_transition="synthesis_retry_exhausted",
                event_message="证据归并自动重试次数已耗尽",
                event_payload={
                    "research_run_id": str(retry_status.run_id),
                    "failure_count": retry_status.failure_count,
                    "policy_version": retry_status.policy_version,
                },
                idempotency_key=f"synthesis-retry-exhausted:{retry_status.run_id}",
            ),
        )

    def reconcile_research_run_completion(
        self,
        run_id: uuid.UUID,
        *,
        actor: str,
    ) -> ResearchOrchestration | None:
        """Public worker callback that advances only the run's owned workflow."""
        actor = _nonblank(actor, "actor")
        run = self._session.get(ResearchRun, run_id)
        if run is None:
            raise NotFoundError("research run not found")
        tenant_id = self._session.scalar(
            select(CaseTenantAdmission.tenant_id).where(
                CaseTenantAdmission.research_case_id == run.research_case_id
            )
        )
        if tenant_id is None:
            return None
        principal = OrchestrationPrincipal(tenant_id=tenant_id, actor=actor)
        orchestration = self._repository.get_for_update(run.research_case_id, tenant_id)
        if orchestration is None:
            return None
        if orchestration.current_research_run_id != run.id:
            raise ValidationFailedError(
                "research run is not owned by the current workflow"
            )
        for _ in range(4):
            previous = (orchestration.state, orchestration.version)
            orchestration = self.reconcile(run.research_case_id, principal)
            if (
                orchestration.state == "monitoring"
                or (
                    orchestration.state,
                    orchestration.version,
                )
                == previous
            ):
                break
        return orchestration

    def _start_report(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="generating_report",
                user_stage="report_monitoring",
                action="正在生成带来源的系统研究报告",
                reason="命题判定已完成，报告只引用当前范围的准入证据",
                checkpoint=dict(orchestration.checkpoint_json),
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=_utcnow(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=orchestration.current_research_run_id,
                actor=principal.actor,
                event_transition="thesis_adjudicated",
                event_message="命题判定已完成，开始生成系统报告",
                event_payload={},
                idempotency_key=f"thesis-adjudicated:{orchestration.current_research_run_id}",
            ),
        )

    def _generate_report_and_monitor(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        from app.services.case_monitor import CaseMonitorService
        from app.services.event_conclusion import EventConclusionService

        scope_id = orchestration.current_scope_version_id
        run_id = orchestration.current_research_run_id
        if scope_id is None or run_id is None:
            raise ValidationFailedError("report generation binding is incomplete")
        thesis_ids = canonical_scope_thesis_ids(
            self._session, orchestration.research_case_id, scope_id
        )
        if not thesis_ids:
            raise ValidationFailedError("report scope has no active theses")
        synthesis = orchestration.checkpoint_json.get("synthesis")
        evidence_link_ids = (
            synthesis.get("evidence_link_ids") if isinstance(synthesis, dict) else None
        )
        if not isinstance(evidence_link_ids, list):
            raise ValidationFailedError("report generation has no synthesis receipt")
        draft = EventConclusionService(self._session).create_draft(
            orchestration.research_case_id,
            system_generated=True,
            evidence_link_ids=[uuid.UUID(str(item)) for item in evidence_link_ids],
            research_run_id=run_id,
            tenant_id=orchestration.tenant_id,
        )
        monitor = CaseMonitorService(self._session).ensure_default(
            orchestration.research_case_id,
            actor=f"system:research-run:{run_id}",
            factor_ids=thesis_ids,
            allowed_source_types=sorted(B_SCOPE_POLICY.allowed_source_roles),
        )
        self._repository.append_event(
            orchestration.research_case_id,
            orchestration.tenant_id,
            transition="report_generated",
            actor=principal.actor,
            message="系统研究草稿已生成，未经人工审核",
            payload={
                "conclusion_id": str(draft.id),
                "state": draft.state,
                "reviewer": None,
                "system_generated": True,
            },
            idempotency_key=f"report-generated:{scope_id}",
        )
        checkpoint = dict(orchestration.checkpoint_json)
        checkpoint["report"] = {
            "conclusion_id": str(draft.id),
            "state": "ai_draft",
            "system_generated": True,
            "human_reviewed": False,
        }
        checkpoint["monitor"] = {"monitor_version_id": str(monitor.id)}
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="monitoring",
                user_stage="report_monitoring",
                action="正在持续监测已确认因素与允许来源",
                reason="系统报告已生成并保留未经人工审核标识",
                checkpoint=checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=_utcnow(),
                current_scope_version_id=scope_id,
                current_research_run_id=run_id,
                actor=principal.actor,
                event_transition="monitoring_started",
                event_message="系统草稿已生成并进入持续监测",
                event_payload={
                    "monitor_version_id": str(monitor.id),
                    "conclusion_id": str(draft.id),
                    "system_generated": True,
                    "human_reviewed": False,
                },
                idempotency_key=f"monitoring-started:{scope_id}",
            ),
        )

    def reconcile_batch(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        limit: int = 10,
    ) -> int:
        """Reconcile a bounded worker batch without waiting for acquisition."""
        candidates = self._repository.find_reconcilable(
            now=now,
            stale_before=stale_before,
            limit=limit,
            states=frozenset(
                {
                    "planning_acquisition",
                    "acquiring",
                    "assessing_coverage",
                    "synthesizing_evidence",
                    "adjudicating_thesis",
                    "generating_report",
                    "retry_wait",
                }
            ),
        )
        reconciled = 0
        for candidate in candidates:
            if candidate.state not in {
                "planning_acquisition",
                "acquiring",
                "assessing_coverage",
                "synthesizing_evidence",
                "adjudicating_thesis",
                "generating_report",
                "retry_wait",
            }:
                continue
            self.reconcile(
                candidate.research_case_id,
                OrchestrationPrincipal(
                    tenant_id=candidate.tenant_id,
                    actor="worker:research-orchestration",
                ),
            )
            reconciled += 1
        return reconciled

    def start_stale_acquisition_recoveries(
        self,
        *,
        now: datetime,
        stale_before: datetime,
        limit: int = 10,
    ) -> int:
        """Persist bounded recovery starts for genuinely stale acquisition work."""
        now = _as_utc(now)
        stale_before = _as_utc(stale_before)
        candidates = self._repository.find_stale_acquisition_recovery_candidates(
            now=now,
            stale_before=stale_before,
            limit=limit,
        )
        processed = 0
        for orchestration in candidates:
            case_id = orchestration.research_case_id
            tenant_id = orchestration.tenant_id
            try:
                with self._session.begin_nested():
                    self._start_acquisition_recovery(
                        orchestration,
                        now=now,
                        stale_before=stale_before,
                    )
            except _AcquisitionRecoveryCorruption as exc:
                with self._session.begin_nested():
                    self._fail_acquisition_recovery(
                        case_id=case_id,
                        tenant_id=tenant_id,
                        reason_code=exc.reason_code,
                        now=now,
                    )
            processed += 1
        return processed

    def complete_acquisition_recoveries(
        self,
        *,
        now: datetime,
        limit: int = 10,
    ) -> int:
        """Complete persisted episodes and restore their original phase."""
        now = _as_utc(now)
        candidates = self._repository.find_incomplete_acquisition_recoveries(
            now=now,
            limit=limit,
        )
        processed = 0
        for orchestration in candidates:
            case_id = orchestration.research_case_id
            tenant_id = orchestration.tenant_id
            try:
                with self._session.begin_nested():
                    self._complete_acquisition_recovery(
                        orchestration,
                        now=now,
                    )
            except _AcquisitionRecoveryCorruption as exc:
                with self._session.begin_nested():
                    self._fail_acquisition_recovery(
                        case_id=case_id,
                        tenant_id=tenant_id,
                        reason_code=exc.reason_code,
                        now=now,
                    )
            processed += 1
        return processed

    def _start_acquisition_recovery(
        self,
        orchestration: ResearchOrchestration,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> None:
        checkpoint = orchestration.checkpoint_json
        if not isinstance(checkpoint, dict):
            raise _AcquisitionRecoveryCorruption("checkpoint_not_object")
        if ACQUISITION_RECOVERY_CHECKPOINT_KEY in checkpoint:
            raise _AcquisitionRecoveryCorruption("recovery_marker_collision")
        self._validate_acquisition_recovery_context(
            orchestration,
            checkpoint,
        )
        resume_state = orchestration.state
        if resume_state not in ACQUISITION_RECOVERY_PHASES:
            return
        checkpoint_digest = self._checkpoint_digest(checkpoint)
        stale_heartbeat = (
            "missing"
            if orchestration.last_heartbeat_at is None
            else _as_utc(orchestration.last_heartbeat_at).isoformat()
        )
        episode_id = hashlib.sha256(
            (
                f"{orchestration.id}:{resume_state}:{stale_heartbeat}:"
                f"{orchestration.version}:{checkpoint_digest}"
            ).encode()
        ).hexdigest()[:24]
        recovery_checkpoint = {
            **checkpoint,
            ACQUISITION_RECOVERY_CHECKPOINT_KEY: {
                "episode_id": episode_id,
                "resume_state": resume_state,
                "checkpoint_digest": checkpoint_digest,
                "stale_heartbeat": stale_heartbeat,
                "started_from_version": orchestration.version,
            },
        }
        self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="recovering",
                user_stage="acquisition",
                action="正在从持久化检查点恢复资料获取流程",
                reason="资料获取流程心跳已超过恢复阈值",
                checkpoint=recovery_checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="recovering",
                heartbeat=now,
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=orchestration.current_research_run_id,
                actor="worker:research-orchestration",
                event_transition="acquisition_recovery_started",
                event_message="检测到资料获取流程失联，已开始检查点恢复",
                event_payload={
                    "recovery_kind": "acquisition",
                    "episode_id": episode_id,
                    "resume_state": resume_state,
                    "stale_heartbeat": stale_heartbeat,
                    "stale_before": stale_before.isoformat(),
                    "checkpoint_digest": checkpoint_digest,
                },
                idempotency_key=(
                    f"acquisition-recovery:{orchestration.id}:{episode_id}:started"
                ),
            ),
        )

    def _complete_acquisition_recovery(
        self,
        orchestration: ResearchOrchestration,
        *,
        now: datetime,
    ) -> None:
        identity = self._acquisition_recovery_identity(orchestration)
        checkpoint = orchestration.checkpoint_json
        if not isinstance(checkpoint, dict):
            raise _AcquisitionRecoveryCorruption("checkpoint_not_object")
        restored_checkpoint = dict(checkpoint)
        marker_present = ACQUISITION_RECOVERY_CHECKPOINT_KEY in restored_checkpoint
        marker = restored_checkpoint.pop(
            ACQUISITION_RECOVERY_CHECKPOINT_KEY,
            None,
        )
        if marker_present:
            self._validate_recovery_marker(marker, identity)
        if self._checkpoint_digest(restored_checkpoint) != identity.checkpoint_digest:
            raise _AcquisitionRecoveryCorruption("recovery_checkpoint_digest_mismatch")
        self._validate_acquisition_recovery_context(
            orchestration,
            restored_checkpoint,
        )
        if identity.resume_state == "acquiring":
            from app.services.research_acquisition import (
                ResearchAcquisitionService,
            )

            try:
                ResearchAcquisitionService(self._session).read_checkpointed_jobs(
                    orchestration,
                    principal=OrchestrationPrincipal(
                        tenant_id=orchestration.tenant_id,
                        actor="worker:research-orchestration",
                    ),
                    checkpoint=restored_checkpoint,
                )
            except (
                ConflictError,
                NotFoundError,
                PermissionDeniedError,
                ValidationFailedError,
                ValueError,
            ) as exc:
                raise _AcquisitionRecoveryCorruption(
                    "acquisition_binding_invalid"
                ) from exc
        action = (
            "恢复完成，继续规划资料获取目标"
            if identity.resume_state == "planning_acquisition"
            else "恢复完成，继续对账资料获取任务"
        )
        self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state=identity.resume_state,
                user_stage="acquisition",
                action=action,
                reason="已验证持久化范围、运行与资料获取检查点",
                checkpoint=restored_checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=now,
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=orchestration.current_research_run_id,
                actor="worker:research-orchestration",
                event_transition="recovery_completed",
                event_message="资料获取流程已从同一检查点恢复",
                event_payload={
                    "episode_id": identity.episode_id,
                    "resume_state": identity.resume_state,
                    "checkpoint_digest": identity.checkpoint_digest,
                },
                idempotency_key=(
                    f"acquisition-recovery:{orchestration.id}:"
                    f"{identity.episode_id}:completed"
                ),
            ),
        )

    def _acquisition_recovery_identity(
        self,
        orchestration: ResearchOrchestration,
    ) -> _AcquisitionRecoveryIdentity:
        event = self._repository.latest_acquisition_recovery_start(orchestration.id)
        if event is None or not isinstance(event.payload_json, dict):
            raise _AcquisitionRecoveryCorruption("recovery_start_identity_missing")
        payload = event.payload_json
        if (
            event.transition == "acquisition_recovery_started"
            and payload.get("recovery_kind") != "acquisition"
        ):
            raise _AcquisitionRecoveryCorruption("recovery_start_identity_invalid")
        episode_id = payload.get("episode_id")
        resume_state = payload.get("resume_state")
        checkpoint_digest = payload.get("checkpoint_digest")
        if (
            not isinstance(episode_id, str)
            or re.fullmatch(r"[0-9a-f]{24}", episode_id) is None
        ):
            raise _AcquisitionRecoveryCorruption("recovery_episode_id_invalid")
        if resume_state not in ACQUISITION_RECOVERY_PHASES:
            raise _AcquisitionRecoveryCorruption("recovery_resume_state_invalid")
        if (
            not isinstance(checkpoint_digest, str)
            or re.fullmatch(r"[0-9a-f]{16}", checkpoint_digest) is None
        ):
            raise _AcquisitionRecoveryCorruption("recovery_checkpoint_digest_mismatch")
        expected_key = f"acquisition-recovery:{orchestration.id}:{episode_id}:started"
        if event.idempotency_key != expected_key:
            raise _AcquisitionRecoveryCorruption("recovery_start_identity_invalid")
        return _AcquisitionRecoveryIdentity(
            episode_id=episode_id,
            resume_state=resume_state,
            checkpoint_digest=checkpoint_digest,
        )

    @staticmethod
    def _validate_recovery_marker(
        marker: object,
        identity: _AcquisitionRecoveryIdentity,
    ) -> None:
        if not isinstance(marker, dict):
            raise _AcquisitionRecoveryCorruption("recovery_marker_not_object")
        resume_state = marker.get("resume_state")
        if resume_state not in ACQUISITION_RECOVERY_PHASES:
            raise _AcquisitionRecoveryCorruption("recovery_resume_state_invalid")
        episode_id = marker.get("episode_id")
        if (
            not isinstance(episode_id, str)
            or re.fullmatch(r"[0-9a-f]{24}", episode_id) is None
        ):
            raise _AcquisitionRecoveryCorruption("recovery_episode_id_invalid")
        checkpoint_digest = marker.get("checkpoint_digest")
        if (
            episode_id != identity.episode_id
            or resume_state != identity.resume_state
            or checkpoint_digest != identity.checkpoint_digest
        ):
            raise _AcquisitionRecoveryCorruption("recovery_checkpoint_digest_mismatch")

    def _validate_acquisition_recovery_context(
        self,
        orchestration: ResearchOrchestration,
        checkpoint: object,
    ) -> None:
        if not isinstance(checkpoint, dict):
            raise _AcquisitionRecoveryCorruption("checkpoint_not_object")
        required = (
            checkpoint.get("scope_version_id"),
            checkpoint.get("research_run_id"),
            checkpoint.get("acquisition_round"),
        )
        if (
            not isinstance(required[0], str)
            or not isinstance(required[1], str)
            or not isinstance(required[2], int)
            or isinstance(required[2], bool)
            or required[2] < 1
        ):
            raise _AcquisitionRecoveryCorruption("checkpoint_context_missing")
        if required[0] != str(orchestration.current_scope_version_id) or required[
            1
        ] != str(orchestration.current_research_run_id):
            raise _AcquisitionRecoveryCorruption("checkpoint_context_mismatch")
        from app.services.research_acquisition import ResearchAcquisitionService

        try:
            ResearchAcquisitionService(self._session).validate_frozen_checkpoint(
                orchestration,
                principal=OrchestrationPrincipal(
                    tenant_id=orchestration.tenant_id,
                    actor="worker:research-orchestration",
                ),
                checkpoint=checkpoint,
            )
        except (
            ConflictError,
            NotFoundError,
            PermissionDeniedError,
            ValidationFailedError,
        ) as exc:
            raise _AcquisitionRecoveryCorruption("checkpoint_context_mismatch") from exc

    def _fail_acquisition_recovery(
        self,
        *,
        case_id: uuid.UUID,
        tenant_id: str,
        reason_code: str,
        now: datetime,
    ) -> ResearchOrchestration:
        orchestration = self._repository.get_for_update(case_id, tenant_id)
        if orchestration is None:
            raise NotFoundError("event research orchestration not found")
        if orchestration.state == "failed":
            return orchestration
        safe_scope_id, safe_run_id = self._safe_owned_pointers(orchestration)
        safe_checkpoint = self._safe_failure_checkpoint(
            orchestration,
            reason_code=reason_code,
            safe_scope_id=safe_scope_id,
            safe_run_id=safe_run_id,
        )
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="failed",
                user_stage="acquisition",
                action="资料获取恢复已安全停止",
                reason="持久化恢复检查点无法通过一致性校验",
                checkpoint=safe_checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="failed",
                heartbeat=now,
                current_scope_version_id=safe_scope_id,
                current_research_run_id=safe_run_id,
                actor="worker:research-orchestration",
                event_transition="recovery_failed",
                event_message="资料获取恢复检查点校验失败，流程已停止",
                event_payload={"reason_code": reason_code},
                idempotency_key=(
                    f"acquisition-recovery-failed:{orchestration.id}:"
                    f"version:{orchestration.version}"
                ),
            ),
        )

    def _safe_owned_pointers(
        self,
        orchestration: ResearchOrchestration,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        safe_scope_id = orchestration.current_scope_version_id
        if safe_scope_id is not None:
            scope_owner = self._session.scalar(
                select(EventResearchScopeVersion.research_case_id).where(
                    EventResearchScopeVersion.id == safe_scope_id
                )
            )
            if scope_owner != orchestration.research_case_id:
                safe_scope_id = None
        safe_run_id = orchestration.current_research_run_id
        if safe_run_id is not None:
            run_owner = self._session.scalar(
                select(ResearchRun.research_case_id).where(
                    ResearchRun.id == safe_run_id
                )
            )
            if run_owner != orchestration.research_case_id:
                safe_run_id = None
        return safe_scope_id, safe_run_id

    @staticmethod
    def _safe_failure_checkpoint(
        orchestration: ResearchOrchestration,
        *,
        reason_code: str,
        safe_scope_id: uuid.UUID | None,
        safe_run_id: uuid.UUID | None,
    ) -> dict[str, dict[str, str | None]]:
        raw = orchestration.checkpoint_json
        if isinstance(raw, dict):
            shape = "object"
        elif isinstance(raw, list):
            shape = "array"
        elif raw is None:
            shape = "null"
        elif isinstance(raw, bool):
            shape = "boolean"
        elif isinstance(raw, (int, float)):
            shape = "number"
        else:
            shape = "string"
        return {
            "failure_summary": {
                "reason_code": reason_code,
                "checkpoint_shape": shape,
                "scope_version_id": (
                    None if safe_scope_id is None else str(safe_scope_id)
                ),
                "research_run_id": (None if safe_run_id is None else str(safe_run_id)),
            }
        }

    @staticmethod
    def _checkpoint_digest(checkpoint: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                checkpoint,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()[:16]

    def _dispatch_acquisition(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        from app.services.research_acquisition import ResearchAcquisitionService

        with self._session.begin_nested():
            dispatch = ResearchAcquisitionService(self._session).dispatch_round(
                orchestration,
                principal=principal,
            )
        checkpoint = dict(orchestration.checkpoint_json)
        checkpoint["acquisition"] = dispatch.checkpoint_value()
        key = (
            f"acquisition-dispatched:{orchestration.current_research_run_id}:"
            f"{orchestration.current_scope_version_id}:round:{dispatch.round}"
        )
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="acquiring",
                user_stage="acquisition",
                action="正在从已冻结查询计划获取外部资料",
                reason="每个命题的支持、反证与事件替代解释目标均已派发",
                checkpoint=checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=_utcnow(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=orchestration.current_research_run_id,
                actor=principal.actor,
                event_transition="acquisition_goals_dispatched",
                event_message="资料获取目标与查询计划已冻结并派发",
                event_payload={
                    "acquisition_round": dispatch.round,
                    "goal_count": len(dispatch.goals),
                    "job_ids": [str(goal.job_id) for goal in dispatch.goals],
                },
                idempotency_key=key,
            ),
        )

    def _reconcile_acquisition(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
    ) -> ResearchOrchestration:
        from app.services.research_acquisition import (
            TERMINAL_ACQUISITION_STATUSES,
            ResearchAcquisitionService,
        )

        views = ResearchAcquisitionService(self._session).read_checkpointed_jobs(
            orchestration,
            principal=principal,
        )
        if any(view.status not in TERMINAL_ACQUISITION_STATUSES for view in views):
            return self._repository.record_heartbeat(
                orchestration,
                orchestration.version,
                _utcnow(),
            )
        checkpoint = dict(orchestration.checkpoint_json)
        key = (
            f"acquisition-terminal:{orchestration.current_research_run_id}:"
            f"{orchestration.current_scope_version_id}:"
            f"round:{checkpoint['acquisition_round']}"
        )
        status_counts: dict[str, int] = {}
        for view in views:
            status_counts[view.status] = status_counts.get(view.status, 0) + 1
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="assessing_coverage",
                user_stage="acquisition",
                action="正在评估各资料目标的证据覆盖度",
                reason="本轮资料获取任务均已结束，尚未开始研究综合",
                checkpoint=checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=_utcnow(),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=orchestration.current_research_run_id,
                actor=principal.actor,
                event_transition="acquisition_round_completed",
                event_message="本轮资料获取已结束，进入覆盖度评估",
                event_payload={
                    "acquisition_round": checkpoint["acquisition_round"],
                    "job_count": len(views),
                    "status_counts": status_counts,
                },
                idempotency_key=key,
            ),
        )

    def _fail_acquisition_reconciliation(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
        *,
        reason_code: str,
    ) -> ResearchOrchestration:
        # No exception text is persisted: provider details and malformed
        # checkpoint contents may contain sensitive data.
        key = (
            f"acquisition-reconciliation-failed:{orchestration.id}:"
            f"version:{orchestration.version}"
        )
        safe_scope_id, safe_run_id = self._safe_owned_pointers(orchestration)
        safe_checkpoint = self._safe_failure_checkpoint(
            orchestration,
            reason_code=reason_code,
            safe_scope_id=safe_scope_id,
            safe_run_id=safe_run_id,
        )
        return self._repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="failed",
                user_stage="acquisition",
                action="资料获取流程已安全停止",
                reason="冻结范围或持久化检查点无法通过一致性校验",
                checkpoint=safe_checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="failed",
                heartbeat=_utcnow(),
                current_scope_version_id=safe_scope_id,
                current_research_run_id=safe_run_id,
                actor=principal.actor,
                event_transition="acquisition_reconciliation_failed",
                event_message="资料获取检查点校验失败，流程已停止",
                event_payload={"reason_code": reason_code},
                idempotency_key=key,
            ),
        )

    def decide_scope(
        self,
        case_id: uuid.UUID,
        principal: OrchestrationPrincipal,
        decision: ScopeDecision,
        idempotency_key: str,
        *,
        expected_version: int,
    ) -> ResearchOrchestration:
        if not isinstance(decision, ScopeDecision):
            raise ValidationFailedError("decision must be a ScopeDecision")
        key = _nonblank(idempotency_key, "idempotency_key")
        orchestration = self._lock_owned(case_id, principal)
        replay_fingerprint = self._repository.replay_command_fingerprint(
            orchestration.id,
            key,
        )
        if orchestration.state != "needs_scope_decision" and replay_fingerprint is None:
            raise ConflictError("scope decisions require needs_scope_decision state")
        if replay_fingerprint is None and orchestration.version != expected_version:
            raise ConflictError(
                "scope decision version does not match current workflow"
            )
        if (
            decision.kind == "keep_scope"
            and orchestration.current_research_run_id is None
        ):
            raise ConflictError(
                "keep_scope requires a current run; use protocol-completion resume"
            )
        keep_scope = decision.kind == "keep_scope"
        target = "synthesizing_evidence" if keep_scope else "cancelled"
        action = (
            "正在按当前研究范围归并证据并形成证据不足报告"
            if keep_scope
            else "已按用户决定停止本次研究"
        )
        recovery_status = "healthy" if keep_scope else None
        checkpoint = dict(orchestration.checkpoint_json)
        checkpoint["scope_decision"] = {
            "kind": decision.kind,
            "unresolved_facts_remain_unknown": keep_scope,
        }
        if keep_scope:
            AutoResearchService(self._session).enqueue_prepared_run(
                orchestration.current_research_run_id,
                commit=False,
            )
        command = OrchestrationTransitionCommand(
            expected_version=orchestration.version,
            target_state=target,
            user_stage="evidence_synthesis" if keep_scope else "acquisition",
            action=action,
            reason=decision.reason,
            checkpoint=checkpoint,
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            recovery_status=recovery_status,
            heartbeat=_utcnow(),
            current_scope_version_id=orchestration.current_scope_version_id,
            current_research_run_id=orchestration.current_research_run_id,
            actor=principal.actor,
            event_transition="scope_decided",
            event_message=action,
            event_payload={"decision": decision.kind, "reason": decision.reason},
            idempotency_key=key,
        )
        return self._repository.transition(orchestration, command)

    def recover(
        self,
        case_id: uuid.UUID,
        principal: OrchestrationPrincipal,
        stale_before: datetime,
    ) -> ResearchOrchestration:
        orchestration = self._lock_owned(case_id, principal)
        if orchestration.state in ACQUISITION_RECOVERY_PHASES:
            # Acquisition recovery has a two-transaction episode protocol;
            # the generic one-way recovery command must not intercept it.
            return orchestration
        if (
            orchestration.state in TERMINAL_STATES
            or orchestration.state == "recovering"
        ):
            return orchestration
        heartbeat = orchestration.last_heartbeat_at
        if heartbeat is not None and _as_utc(heartbeat) >= _as_utc(stale_before):
            return orchestration
        # The approved graph deliberately funnels stale work through retry_wait.
        if "recovering" not in LEGAL_TRANSITIONS[orchestration.state]:
            return orchestration

        checkpoint_digest = hashlib.sha256(
            json.dumps(
                orchestration.checkpoint_json,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()[:16]
        key = f"recover:{orchestration.id}:{orchestration.version}:{checkpoint_digest}"
        stale_heartbeat = (
            "missing" if heartbeat is None else _as_utc(heartbeat).isoformat()
        )
        reason = f"Workflow heartbeat {stale_heartbeat} is older than the stale cutoff"
        action = "Recovering the stale workflow from its durable checkpoint"
        command = OrchestrationTransitionCommand(
            expected_version=orchestration.version,
            target_state="recovering",
            user_stage=orchestration.user_stage,
            action=action,
            reason=reason,
            checkpoint=orchestration.checkpoint_json,
            next_action_kind=None,
            next_action_label=None,
            next_action_payload=None,
            recovery_status="recovering",
            heartbeat=_utcnow(),
            current_scope_version_id=orchestration.current_scope_version_id,
            current_research_run_id=orchestration.current_research_run_id,
            actor=principal.actor,
            event_transition="recovery_started",
            event_message=action,
            event_payload={
                "stale_heartbeat": stale_heartbeat,
                "stale_before": _as_utc(stale_before).isoformat(),
                "checkpoint_digest": checkpoint_digest,
            },
            idempotency_key=key,
        )
        return self._repository.transition(orchestration, command)

    def _lock_owned(
        self,
        case_id: uuid.UUID,
        principal: OrchestrationPrincipal,
        *,
        allow_absent: bool = False,
    ) -> ResearchOrchestration | None:
        orchestration = self._repository.get_for_update(case_id, principal.tenant_id)
        admission = self._session.scalar(
            select(CaseTenantAdmission.id).where(
                CaseTenantAdmission.research_case_id == case_id,
                CaseTenantAdmission.tenant_id == principal.tenant_id,
            )
        )
        if admission is None:
            raise NotFoundError("event research orchestration not found")
        if orchestration is None and not allow_absent:
            raise NotFoundError("event research orchestration not found")
        return orchestration

    def _latest_scope(self, case_id: uuid.UUID) -> EventResearchScopeVersion | None:
        return self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(
                EventResearchScopeVersion.version.desc(),
                EventResearchScopeVersion.id.desc(),
            )
            .limit(1)
        )

    def _is_confirm_replay(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
        idempotency_key: str,
        *,
        scope_version_id: uuid.UUID | None,
    ) -> bool:
        replay_fingerprint = self._repository.replay_command_fingerprint(
            orchestration.id, idempotency_key
        )
        if replay_fingerprint is None:
            return False
        expected = {
            "actor": principal.actor,
            "event_transition": "scope_confirmed",
            "current_scope_version_id": str(
                scope_version_id or orchestration.current_scope_version_id
            ),
            "tenant_id": principal.tenant_id,
            "research_case_id": str(orchestration.research_case_id),
        }
        if replay_fingerprint != expected:
            raise ConflictError("idempotency key was used with different input")
        return True

    def _is_protocol_completion_replay(
        self,
        orchestration: ResearchOrchestration,
        principal: OrchestrationPrincipal,
        idempotency_key: str,
        *,
        scope_version_id: uuid.UUID,
    ) -> bool:
        replay_fingerprint = self._repository.replay_command_fingerprint(
            orchestration.id,
            idempotency_key,
        )
        if replay_fingerprint is None:
            return False
        expected = {
            "actor": principal.actor,
            "event_transition": "protocol_completed",
            "current_scope_version_id": str(scope_version_id),
            "tenant_id": principal.tenant_id,
            "research_case_id": str(orchestration.research_case_id),
        }
        if replay_fingerprint != expected:
            raise ConflictError("idempotency key was used with different input")
        return True
