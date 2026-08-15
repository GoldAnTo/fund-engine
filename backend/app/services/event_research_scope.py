"""Append-only event-research scope snapshots and evidence mapping counts."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_research import PROTOCOL_COMPLETION_NEXT_HUMAN_ACTION
from app.errors import NotFoundError, ValidationFailedError
from app.models.event_research import (
    EventResearchBrief,
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import EvidenceLink, Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_preparation import ResearchPreparation
from app.models.research_monitor import ResearchRunEvent
from app.services.auto_research import AutoResearchService
from app.services.case_monitor import ResearchRunEventRepository
from app.services.event_research_factors import (
    EventResearchScopeFactorValue,
    normalize_event_research_scope_factors,
)
from app.services.event_research_scope_evidence import lock_event_research_lifecycle
from app.services.event_review_queue import EventReviewQueueService
from app.services.research_preparation import ResearchPreparationService
from app.services.research_protocol import ResearchProtocolService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


MAX_PREPARATION_RUN_LINEAGE_DEPTH = 32


@dataclass(frozen=True)
class UpdatedEventResearchScope:
    version: int
    factors: list[EventResearchScopeFactorValue]
    reclassified_evidence_count: int
    unmapped_evidence_count: int


class EventResearchScopeService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def update(
        self,
        case_id: uuid.UUID,
        factors: list[object],
        changed_by: str,
        change_reason: str = "未记录具体原因（兼容旧客户端）",
    ) -> UpdatedEventResearchScope:
        try:
            normalized = normalize_event_research_scope_factors([
                factor if isinstance(factor, (str, EventResearchScopeFactorValue))
                else EventResearchScopeFactorValue(
                    statement=getattr(factor, "statement"),
                    description=getattr(factor, "description", None),
                )
                for factor in factors
            ])
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc
        if not changed_by.strip():
            raise ValidationFailedError("changed_by must not be empty")
        if not change_reason.strip():
            raise ValidationFailedError("change_reason must not be empty")
        if self._session.scalar(
            select(EventResearchBrief.id).where(
                EventResearchBrief.research_case_id == case_id
            )
        ) is None:
            raise NotFoundError("event research case not found")
        # Held to the route's commit together with the scope snapshot and its
        # reviewed-evidence assignments; proposal publication uses this lock too.
        # This takes ResearchCase before lifecycle.  Keep it ahead of every
        # scope read/backfill so legacy cases cannot race with publication.
        lifecycle = lock_event_research_lifecycle(self._session, case_id)
        if lifecycle is not None and lifecycle.status == "published":
            raise ValidationFailedError(
                "published event research cannot update its scope"
            )

        previous = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        if previous is None:
            previous = self._backfill_legacy_scope(case_id)
        previous_factors = self._factors_for(previous.id) if previous else []
        active_statements = [factor.statement for factor in normalized]
        retained = set(previous_factors).intersection(active_statements)
        removed = set(previous_factors).difference(active_statements)
        now = _utcnow()
        scope = EventResearchScopeVersion(
            research_case_id=case_id,
            version=(previous.version if previous else 0) + 1,
            changed_by=changed_by.strip(),
            change_summary=change_reason.strip(),
            created_at=now,
        )
        self._session.add(scope)
        self._session.flush()
        for position, factor in enumerate(normalized, start=1):
            self._session.add(
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=factor.statement,
                    description=factor.description,
                    position=position,
                )
            )
        self._session.flush()
        active_theses = self._sync_active_factor_theses(
            case_id, active_statements, changed_by.strip(), now
        )
        reviewed_evidence = list(
            self._session.execute(
                select(EvidenceLink, Thesis)
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(Thesis.research_case_id == case_id)
                .where(EvidenceLink.review_state == "reviewed")
            )
        )
        active_factors = set(active_statements)
        for link, thesis in reviewed_evidence:
            is_mapped = thesis.statement in active_factors
            self._session.add(
                EventResearchScopeEvidenceAssignment(
                    scope_version_id=scope.id,
                    evidence_link_id=link.id,
                    factor_statement=thesis.statement if is_mapped else None,
                    disposition="mapped" if is_mapped else "unmapped",
                    created_at=now,
                )
            )
        # Pending proposals for factors removed from this just-created scope
        # remain immutable audit records, but their review tasks must no
        # longer appear actionable while the successor run is pending.
        EventReviewQueueService(self._session).reconcile_event_review_queue(case_id)
        self._revoke_active_preparation_run(lifecycle, case_id)
        preparation = ResearchPreparationService(
            self._session
        ).invalidate_from_scope_change(
            case_id,
            scope.id,
            actor=changed_by.strip(),
            case_locked=True,
        )
        # Preparation supersedes automatic formal-run continuation.  Legacy
        # event Cases without a preparation preserve their historical path.
        if lifecycle is not None and preparation is None:
            self._continue_research_if_needed(lifecycle, active_theses, now)
        self._session.flush()
        return UpdatedEventResearchScope(
            version=scope.version,
            factors=normalized,
            reclassified_evidence_count=sum(
                thesis.statement in retained for _, thesis in reviewed_evidence
            ),
            unmapped_evidence_count=sum(
                thesis.statement in removed for _, thesis in reviewed_evidence
            ),
        )

    def _revoke_active_preparation_run(
        self, lifecycle: EventResearchLifecycle | None, case_id: uuid.UUID
    ) -> None:
        """Stop a formal run before scope invalidates its preparation.

        The caller already holds the stable Case then lifecycle lock.  The
        cancellation path extends that order with ResearchRun then its Job,
        so a provider-returning worker cannot commit output for a superseded
        scope.  No successor is created here; a new run requires a later
        authorization.
        """
        preparation = self._session.scalar(
            select(ResearchPreparation).where(
                ResearchPreparation.research_case_id == case_id
            )
        )
        if (
            preparation is None
            or preparation.status != "authorized"
            or preparation.research_run_id is None
        ):
            return
        run_ids = {preparation.research_run_id}
        if (
            lifecycle is not None
            and lifecycle.active_run_id is not None
            and lifecycle.active_run_id != preparation.research_run_id
            and self._is_preparation_successor(
                lifecycle.active_run_id, preparation.research_run_id, case_id
            )
        ):
            run_ids.add(lifecycle.active_run_id)
        auto_research = AutoResearchService(self._session)
        locked_runs: list[ResearchRun] = []
        for run_id in sorted(run_ids, key=str):
            run = auto_research._lock_run_for_transition(run_id, case_locked=True)
            if run is not None and run.research_case_id == case_id:
                locked_runs.append(run)
        cancelled_run_ids: set[uuid.UUID] = set()
        # All mutable run rows are now locked in canonical order before any
        # cancellation traverses into Job rows.
        for run in locked_runs:
            if auto_research.repo.cancel_run(run):
                run.stop_reason = "scope_changed"
                cancelled_run_ids.add(run.id)
                ResearchRunEventRepository(self._session).append(
                    run.id,
                    stage="stopped",
                    status="cancelled",
                    message="研究范围已变更；已撤销本次正式研究运行。",
                    payload_json={"stop_reason": "scope_changed"},
                )
        if lifecycle is not None and lifecycle.active_run_id in cancelled_run_ids:
            lifecycle.active_run_id = None
            lifecycle.status = "awaiting_key_review"
            lifecycle.status_summary = "资料已冻结；系统正在准备候选陈述、研究协议草案和补证计划"
            lifecycle.current_gap = "研究准备尚未完成；ResearchRun 未创建，正式补证尚未启动"
            lifecycle.next_human_action = None
            lifecycle.updated_at = _utcnow()

    def _is_preparation_successor(
        self,
        run_id: uuid.UUID,
        predecessor_run_id: uuid.UUID,
        case_id: uuid.UUID,
    ) -> bool:
        """Prove an active run descends from an authorized preparation run.

        Lineage is carried only in each run's immutable frozen-scope event.
        Treat absent, malformed, cyclic, cross-case, and overlong histories
        as unproven so a scope rewrite never cancels an unrelated run.
        """
        current_run_id = run_id
        visited: set[uuid.UUID] = set()
        for _ in range(MAX_PREPARATION_RUN_LINEAGE_DEPTH):
            if current_run_id in visited:
                return False
            visited.add(current_run_id)
            current_run = self._session.get(ResearchRun, current_run_id)
            if current_run is None or current_run.research_case_id != case_id:
                return False
            if current_run_id == predecessor_run_id:
                return True
            event = self._session.scalar(
                select(ResearchRunEvent)
                .where(ResearchRunEvent.run_id == current_run_id)
                .where(ResearchRunEvent.stage == "scope")
                .order_by(ResearchRunEvent.seq)
                .limit(1)
            )
            if event is None or not isinstance(event.payload_json, dict):
                return False
            raw_predecessor_id = event.payload_json.get("predecessor_run_id")
            try:
                next_run_id = uuid.UUID(str(raw_predecessor_id))
            except (TypeError, ValueError, AttributeError):
                return False
            next_run = self._session.get(ResearchRun, next_run_id)
            if next_run is None or next_run.research_case_id != case_id:
                return False
            current_run_id = next_run_id
        return False

    def _backfill_legacy_scope(
        self, case_id: uuid.UUID
    ) -> EventResearchScopeVersion | None:
        drafts = list(
            self._session.scalars(
                select(EventResearchFactorDraft)
                .where(EventResearchFactorDraft.research_case_id == case_id)
                .order_by(EventResearchFactorDraft.position)
            )
        )
        if not drafts:
            return None
        scope = EventResearchScopeVersion(
            research_case_id=case_id,
            version=1,
            changed_by=drafts[0].created_by,
            change_summary="Backfilled initial event research factors",
            created_at=drafts[0].created_at,
        )
        self._session.add(scope)
        self._session.flush()
        for draft in drafts:
            self._session.add(
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=draft.statement,
                    description=None,
                    position=draft.position,
                )
            )
        self._session.flush()
        return scope

    def _sync_active_factor_theses(
        self,
        case_id: uuid.UUID,
        factors: list[str],
        changed_by: str,
        created_at: datetime,
    ) -> list[Thesis]:
        theses: list[Thesis] = []
        for statement in factors:
            thesis = self._session.scalar(
                select(Thesis)
                .where(Thesis.research_case_id == case_id)
                .where(Thesis.statement == statement)
                .order_by(Thesis.created_at, Thesis.id)
                .limit(1)
            )
            if thesis is None:
                thesis = Thesis(
                    research_case_id=case_id,
                    statement=statement,
                    created_by=changed_by,
                    created_at=created_at,
                    creator_type="human",
                    review_state="confirmed",
                    research_protocol_required=True,
                )
                self._session.add(thesis)
                self._session.flush()
            theses.append(thesis)
        return theses

    def _continue_research_if_needed(
        self,
        lifecycle: EventResearchLifecycle,
        active_theses: list[Thesis],
        now: datetime,
    ) -> None:
        current_run = (
            self._session.get(ResearchRun, lifecycle.active_run_id)
            if lifecycle.active_run_id is not None
            else None
        )
        auto_research = AutoResearchService(self._session)
        if current_run is not None and current_run.status in {
            "queued",
            "running",
            "waiting_for_review",
        }:
            # Preserve the old run and its task/job audit trail, but prevent a
            # worker from continuing to research the superseded thesis set.
            locked_run = auto_research._lock_run_for_transition(
                current_run.id,
                case_locked=True,
            )
            if locked_run is not None:
                auto_research.repo.cancel_run(locked_run)
        blocked_protocols: list[tuple[Thesis, list[str]]] = []
        protocol = ResearchProtocolService(self._session)
        for thesis in active_theses:
            if not thesis.research_protocol_required:
                continue
            result = protocol.check_researchability(thesis.id)
            if result.status == "blocked":
                blocked_protocols.append((thesis, result.reason_codes))
        if blocked_protocols:
            blocked_details = "；".join(
                f"{thesis.statement}（{', '.join(reason_codes[:3]) or 'blocked'}）"
                for thesis, reason_codes in blocked_protocols[:3]
            )
            if len(blocked_protocols) > 3:
                blocked_details += f"；另有 {len(blocked_protocols) - 3} 个因素"
            lifecycle.status = "awaiting_scope"
            lifecycle.active_run_id = None
            lifecycle.status_summary = "研究范围已更新，新增因素需先完成研究协议"
            lifecycle.current_gap = f"研究协议未完成：{blocked_details}"
            lifecycle.next_human_action = PROTOCOL_COMPLETION_NEXT_HUMAN_ACTION
            lifecycle.updated_at = now
            return
        successor = auto_research.start(
            lifecycle.research_case_id,
            max_rounds=current_run.max_rounds if current_run is not None else 3,
            budget=current_run.budget if current_run is not None else 100,
            commit=False,
            thesis_ids=[thesis.id for thesis in active_theses],
        )
        lifecycle.status = "continuing"
        lifecycle.active_run_id = successor.id
        lifecycle.current_round = min(lifecycle.current_round + 1, 3)
        lifecycle.status_summary = "已更新因素，正在重新归类证据并继续检索"
        lifecycle.current_gap = "已更新因素，正在重新归类证据"
        lifecycle.next_human_action = None
        lifecycle.updated_at = now

    def _factors_for(self, scope_version_id: uuid.UUID) -> list[str]:
        return list(
            self._session.scalars(
                select(EventResearchScopeFactor.statement)
                .where(EventResearchScopeFactor.scope_version_id == scope_version_id)
                .order_by(EventResearchScopeFactor.position)
            )
        )
