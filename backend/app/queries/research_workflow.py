"""One truthful, server-owned projection of an event research workflow."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import (
    NotFoundError,
    ValidationFailedError,
    WorkflowNotInitializedError,
)
from app.models.acquisition import AcquisitionJob
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.domain.acquisition import (
    AcquisitionPrincipal,
    AcquisitionWorkflowLedgerPage,
    AcquisitionWorkflowLedgerRecord,
    WorkflowLedgerStatus,
)
from app.domain.research_workflow import (
    WorkflowLedgerCursor,
    as_utc,
    classify_user_decision,
    decode_ledger_cursor,
    encode_ledger_cursor,
    ledger_key,
    parse_utc_datetime,
)
from app.models.operational import Job, JobEvent
from app.models.research_monitor import CaseMonitorVersion
from app.models.research_orchestration import (
    AcquisitionGoalCoverage,
    AcquisitionQueryPlan,
    AcquisitionSeries,
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.schemas.v1.research_workflow import (
    ResearchWorkflowResponse,
    WorkflowAcquisitionDTO,
    WorkflowAcquisitionRoundDTO,
    WorkflowConclusionDTO,
    WorkflowCoverageDTO,
    WorkflowEventDTO,
    WorkflowEventIdentityDTO,
    WorkflowEventsPageDTO,
    WorkflowLedgerPageDTO,
    WorkflowMonitorDTO,
    WorkflowRecoveryDTO,
    WorkflowResearchExecutionDTO,
    WorkflowScopeDTO,
    WorkflowScopeFactorDTO,
    WorkflowSourceLedgerCountsDTO,
    WorkflowSourceLedgerDTO,
    WorkflowSourceLedgerDrilldownDTO,
    WorkflowSourceLedgerItemDTO,
    WorkflowStageDTO,
    WorkflowSystemActionDTO,
    WorkflowUserActionDTO,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.services.acquisition import AcquisitionModule
from app.services.research_worker_heartbeat import WorkerHeartbeatService


_STAGES = (
    ("event_intake", "建立事件"),
    ("scope_confirmation", "确认命题"),
    ("source_acquisition", "主动补证"),
    ("evidence_synthesis", "证据归并"),
    ("thesis_adjudication", "命题判定"),
    ("report_monitoring", "报告与监测"),
)
_STATE_STAGE = {
    "intake": 0,
    "awaiting_scope_confirmation": 1,
    "planning_acquisition": 2,
    "acquiring": 2,
    "freezing_sources": 2,
    "assessing_coverage": 2,
    "needs_scope_decision": 2,
    "exhausted": 2,
    "synthesizing_evidence": 3,
    "adjudicating_thesis": 4,
    "generating_report": 5,
    "monitoring": 5,
}
_USER_STAGE_INDEX = {
    "intake": 0,
    "scope_confirmation": 1,
    "acquisition": 2,
    "evidence_synthesis": 3,
    "thesis_adjudication": 4,
    "report_monitoring": 5,
}


@dataclass(frozen=True, slots=True)
class _RecoveryCandidate:
    severity: int
    state: str
    status: str | None
    reason: str | None
    display_reason: str | None
    source: str
    lease_expires_at: datetime | None = None
    retry_at: datetime | None = None
    failed_at: datetime | None = None


class ResearchWorkflowQueries:
    """Read durable workflow records without consulting compatibility lifecycle."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    def get(self, case_id: uuid.UUID, *, tenant_id: str) -> ResearchWorkflowResponse:
        CaseTenantAccess(self._session).require_case(case_id, tenant_id)
        orchestration = self._session.scalar(
            select(ResearchOrchestration).where(
                ResearchOrchestration.research_case_id == case_id,
                ResearchOrchestration.tenant_id == tenant_id,
            )
        )
        if orchestration is None:
            raise self._not_initialized(case_id)
        brief = self._session.scalar(
            select(EventResearchBrief)
            .where(EventResearchBrief.research_case_id == case_id)
            .order_by(
                EventResearchBrief.created_at.desc(), EventResearchBrief.id.desc()
            )
            .limit(1)
        )
        if brief is None or orchestration.current_scope_version_id is None:
            raise NotFoundError("event research case not found")
        scope = self._session.get(
            EventResearchScopeVersion, orchestration.current_scope_version_id
        )
        if scope is None or scope.research_case_id != case_id:
            raise NotFoundError("event research case not found")
        factors = list(
            self._session.scalars(
                select(EventResearchScopeFactor)
                .where(EventResearchScopeFactor.scope_version_id == scope.id)
                .order_by(
                    EventResearchScopeFactor.position, EventResearchScopeFactor.id
                )
            )
        )
        run_id = orchestration.current_research_run_id
        job = self._research_job(case_id, run_id)
        checkpoint = orchestration.checkpoint_json or {}
        recovery = checkpoint.get("recovery")
        if not isinstance(recovery, dict):
            recovery = {}
        retry_at = (
            job.next_retry_at
            if job is not None
            else self._checkpoint_datetime(checkpoint.get("next_retry_at"))
        )
        acquisition = self._acquisition(run_id)
        recovery_truth = self._recovery_truth(
            orchestration,
            job=job,
            acquisition=acquisition,
            checkpoint_recovery=recovery,
            checkpoint_retry_at=retry_at,
        )
        decision = classify_user_decision(
            orchestration.next_action_kind,
            orchestration.next_action_payload,
        )
        incomplete_decision = (
            orchestration.next_action_kind is not None
            and orchestration.next_action_label is not None
            and decision is None
        )
        decision_diagnostic = (
            dict(orchestration.next_action_payload or {})
            if incomplete_decision
            else None
        )
        if incomplete_decision:
            recovery_truth = {
                **recovery_truth,
                "status": "incomplete_decision_context",
                "reason": "incomplete_decision_context",
                "display_reason": (
                    "服务端检测到历史决策上下文不完整，必须重建或恢复后"
                    "才能提供可执行操作。"
                ),
                "source": "checkpoint",
            }
        effective_state = recovery_truth["state"]
        recovery_status = recovery_truth["status"]
        recovery_reason = recovery_truth["reason"]
        lease_expires_at = recovery_truth["lease_expires_at"]
        ledger_page = AcquisitionModule(self._session).workflow_ledger_page(
            case_id=case_id,
            scope_version_id=scope.id,
            principal=AcquisitionPrincipal(
                tenant_id=tenant_id,
                actor="system:workflow-query",
            ),
            status=None,
            after=None,
            high_watermark=None,
            limit=20,
        )
        source_ledger = self._source_ledger(ledger_page)
        user_action = self._user_action(orchestration, decision)
        return ResearchWorkflowResponse(
            orchestration_id=orchestration.id,
            research_run_id=run_id,
            research_execution=self._research_execution(job),
            event=WorkflowEventIdentityDTO(
                case_id=case_id,
                title=brief.event_title,
                research_question=brief.research_question,
            ),
            scope=WorkflowScopeDTO(
                id=scope.id,
                version=scope.version,
                factors=[
                    WorkflowScopeFactorDTO(
                        statement=item.statement,
                        description=item.description,
                        position=item.position,
                    )
                    for item in factors
                ],
            ),
            state=effective_state,
            stages=self._stages(
                orchestration,
                effective_state=effective_state,
                effective_reason=recovery_truth["display_reason"],
            ),
            system_action=WorkflowSystemActionDTO(
                label=(
                    "正在恢复不完整的决策上下文"
                    if incomplete_decision
                    else orchestration.current_system_action
                ),
                reason=(
                    recovery_truth["display_reason"]
                    or orchestration.system_action_reason
                ),
                started_at=orchestration.state_started_at,
                heartbeat_at=self._as_utc(orchestration.last_heartbeat_at),
                lease_expires_at=self._as_utc(lease_expires_at),
                retry_at=self._as_utc(recovery_truth["retry_at"]),
                recovery_status=recovery_status,
            ),
            user_action=user_action,
            user_action_summary=(
                orchestration.next_action_label
                if user_action is not None
                else "当前无需操作"
            ),
            events=self._latest_events(orchestration.id),
            acquisition=acquisition,
            source_ledger=source_ledger,
            coverage=self._coverage(run_id, scope.id),
            conclusion=self._conclusion(
                case_id,
                scope.id,
                tenant_id=tenant_id,
            ),
            monitor=self._monitor(case_id),
            recovery=WorkflowRecoveryDTO(
                status=recovery_status,
                reason=recovery_reason,
                source=recovery_truth["source"],
                attempt=self._integer(recovery.get("attempt")),
                evaluated_at=recovery_truth["evaluated_at"],
                orchestration_heartbeat_at=self._as_utc(
                    orchestration.last_heartbeat_at
                ),
                worker_heartbeat_at=recovery_truth["worker_heartbeat_at"],
                lease_expires_at=self._as_utc(lease_expires_at),
                retry_at=self._as_utc(recovery_truth["retry_at"]),
                failed_at=self._as_utc(recovery_truth["failed_at"]),
                last_checkpoint=checkpoint,
                decision_diagnostic=decision_diagnostic,
            ),
            version=orchestration.version,
            updated_at=orchestration.updated_at,
        )

    def _research_execution(
        self,
        job: Job | None,
    ) -> WorkflowResearchExecutionDTO | None:
        if job is None:
            return None
        recovery_count, last_recovered_at = self._session.execute(
            select(func.count(JobEvent.id), func.max(JobEvent.created_at)).where(
                JobEvent.job_id == job.id,
                JobEvent.step == "recovered",
            )
        ).one()
        return WorkflowResearchExecutionDTO(
            job_id=job.id,
            status=job.status,
            step=job.step,
            attempt=job.attempt,
            failure_count=job.failure_count,
            started_at=job.started_at,
            finished_at=job.finished_at,
            recovery_count=int(recovery_count or 0),
            last_recovered_at=last_recovered_at,
        )

    def events(
        self,
        case_id: uuid.UUID,
        *,
        tenant_id: str,
        cursor: int | None,
        limit: int,
    ) -> WorkflowEventsPageDTO:
        CaseTenantAccess(self._session).require_case(case_id, tenant_id)
        orchestration_id = self._session.scalar(
            select(ResearchOrchestration.id).where(
                ResearchOrchestration.research_case_id == case_id,
                ResearchOrchestration.tenant_id == tenant_id,
            )
        )
        if orchestration_id is None:
            raise self._not_initialized(case_id)
        statement = select(ResearchOrchestrationEvent).where(
            ResearchOrchestrationEvent.orchestration_id == orchestration_id
        )
        if cursor is not None:
            statement = statement.where(ResearchOrchestrationEvent.sequence > cursor)
        rows = list(
            self._session.scalars(
                statement.order_by(
                    ResearchOrchestrationEvent.sequence,
                    ResearchOrchestrationEvent.id,
                ).limit(limit + 1)
            )
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        return WorkflowEventsPageDTO(
            items=[self._event(item) for item in items],
            next_cursor=items[-1].sequence if items else None,
            has_more=has_more,
        )

    def ledger(
        self,
        case_id: uuid.UUID,
        *,
        tenant_id: str,
        status: WorkflowLedgerStatus | None,
        cursor: str | None,
        limit: int,
    ) -> WorkflowLedgerPageDTO:
        CaseTenantAccess(self._session).require_case(case_id, tenant_id)
        scope_id = self._session.scalar(
            select(ResearchOrchestration.current_scope_version_id).where(
                ResearchOrchestration.research_case_id == case_id,
                ResearchOrchestration.tenant_id == tenant_id,
            )
        )
        if scope_id is None:
            raise self._not_initialized(case_id)
        decoded = None
        if cursor is not None:
            try:
                decoded = decode_ledger_cursor(cursor)
            except ValueError as exc:
                raise ValidationFailedError("invalid workflow ledger cursor") from exc
            if decoded.status != status:
                raise ValidationFailedError(
                    "workflow ledger cursor does not match status filter"
                )
        page = AcquisitionModule(self._session).workflow_ledger_page(
            case_id=case_id,
            scope_version_id=scope_id,
            principal=AcquisitionPrincipal(
                tenant_id=tenant_id,
                actor="system:workflow-query",
            ),
            status=status,
            after=decoded.after if decoded is not None else None,
            high_watermark=(decoded.high_watermark if decoded is not None else None),
            limit=limit,
        )
        if page.high_watermark is None:
            return WorkflowLedgerPageDTO(
                items=[],
                total=0,
                has_more=False,
                next_cursor=None,
            )
        next_cursor = None
        if page.has_more and page.records:
            next_cursor = encode_ledger_cursor(
                WorkflowLedgerCursor(
                    status=status,
                    after=self._ledger_record_key(page.records[-1]),
                    high_watermark=page.high_watermark,
                )
            )
        return WorkflowLedgerPageDTO(
            items=[self._ledger_item(item) for item in page.records],
            total=page.counts.total,
            has_more=page.has_more,
            next_cursor=next_cursor,
        )

    def _stages(
        self,
        orchestration: ResearchOrchestration,
        *,
        effective_state: str,
        effective_reason: str | None = None,
    ) -> list[WorkflowStageDTO]:
        current = _STATE_STAGE.get(
            effective_state,
            _USER_STAGE_INDEX.get(orchestration.user_stage, 0),
        )
        special = None
        if effective_state in {"retry_wait", "recovering"} or (
            orchestration.recovery_status in {"stale", "recovering"}
        ):
            special = "recovering"
        elif effective_state == "needs_scope_decision":
            special = "blocked"
        elif effective_state in {"failed", "exhausted"}:
            special = "failed"
        elif effective_state == "cancelled":
            special = "cancelled"
        values = []
        for index, (code, display_name) in enumerate(_STAGES):
            status = "completed" if index < current else "pending"
            if index == current:
                status = special or "active"
            values.append(
                WorkflowStageDTO(
                    code=code,
                    display_name=display_name,
                    status=status,
                    reason=(
                        effective_reason or orchestration.system_action_reason
                        if index == current
                        else "该阶段已完成"
                        if index < current
                        else "等待前序阶段完成"
                    ),
                )
            )
        return values

    def _latest_events(self, orchestration_id: uuid.UUID) -> list[WorkflowEventDTO]:
        rows = list(
            self._session.scalars(
                select(ResearchOrchestrationEvent)
                .where(ResearchOrchestrationEvent.orchestration_id == orchestration_id)
                .order_by(
                    ResearchOrchestrationEvent.sequence.desc(),
                    ResearchOrchestrationEvent.id.desc(),
                )
                .limit(12)
            )
        )
        return [self._event(item) for item in rows]

    @staticmethod
    def _event(item: ResearchOrchestrationEvent) -> WorkflowEventDTO:
        return WorkflowEventDTO(
            id=item.id,
            sequence=item.sequence,
            transition=item.transition,
            actor=item.actor,
            message=item.message,
            payload=item.payload_json or {},
            created_at=item.created_at,
        )

    def _research_job(self, case_id: uuid.UUID, run_id: uuid.UUID | None) -> Job | None:
        if run_id is None:
            return None
        return self._session.scalar(
            select(Job)
            .where(
                Job.kind == "research_run",
                Job.research_case_id == case_id,
                Job.target_id == run_id,
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )

    def _acquisition(self, run_id: uuid.UUID | None) -> WorkflowAcquisitionDTO:
        if run_id is None:
            return WorkflowAcquisitionDTO(series_count=0, rounds=[])
        rows = list(
            self._session.execute(
                select(AcquisitionSeries, AcquisitionQueryPlan, AcquisitionJob)
                .join(
                    AcquisitionQueryPlan,
                    AcquisitionQueryPlan.series_id == AcquisitionSeries.id,
                )
                .join(
                    AcquisitionJob,
                    AcquisitionJob.id == AcquisitionQueryPlan.acquisition_job_id,
                )
                .where(AcquisitionSeries.research_run_id == run_id)
                .order_by(
                    AcquisitionSeries.goal_id,
                    AcquisitionQueryPlan.acquisition_round,
                )
            )
        )
        return WorkflowAcquisitionDTO(
            series_count=len({series.id for series, _plan, _job in rows}),
            rounds=[
                WorkflowAcquisitionRoundDTO(
                    series_id=series.id,
                    query_plan_id=plan.id,
                    goal_id=series.goal_id,
                    round=plan.acquisition_round,
                    job_id=job.id,
                    status=job.status,
                    stage=job.stage,
                    attempt=job.attempt,
                    retry_at=job.retry_at,
                    lease_expires_at=job.lease_expires_at,
                    finished_at=job.finished_at,
                    error_code=job.error_code,
                    error_detail=job.error_detail,
                    recovery_status=self._acquisition_recovery_status(job),
                    planner_version=plan.planner_version,
                    policy_version=plan.policy_version,
                    frozen_inputs=plan.frozen_inputs_json or {},
                    ordered_query_count=len(plan.ordered_queries_json or []),
                    expansion_trigger=plan.expansion_trigger,
                )
                for series, plan, job in rows
            ],
        )

    def _coverage(
        self, run_id: uuid.UUID | None, scope_id: uuid.UUID
    ) -> list[WorkflowCoverageDTO]:
        if run_id is None:
            return []
        rows = list(
            self._session.scalars(
                select(AcquisitionGoalCoverage)
                .where(
                    AcquisitionGoalCoverage.research_run_id == run_id,
                    AcquisitionGoalCoverage.scope_version_id == scope_id,
                )
                .order_by(AcquisitionGoalCoverage.goal_id)
            )
        )
        return [
            WorkflowCoverageDTO(
                goal_id=item.goal_id,
                thesis_id=item.thesis_id,
                objective=item.objective,
                status=item.status,
                reason_codes=list(item.reason_codes_json or []),
                required_authority_count=item.required_authority_count,
                observed_authority_count=item.observed_authority_count,
                required_independent_source_count=item.required_independent_source_count,
                observed_independent_source_count=item.observed_independent_source_count,
                contrary_search_completed=item.contrary_search_completed,
                evidence_link_ids=[
                    str(value) for value in item.evidence_link_ids_json or []
                ],
                unresolved=list(item.conflict_details_json or []),
                unknown=list(item.unknown_details_json or []),
                evaluation_round=item.evaluation_round,
            )
            for item in rows
        ]

    def _source_ledger(
        self,
        page: AcquisitionWorkflowLedgerPage,
    ) -> WorkflowSourceLedgerDTO:
        return WorkflowSourceLedgerDTO(
            counts=WorkflowSourceLedgerCountsDTO(
                total=page.counts.total,
                reviewed=page.counts.reviewed,
                automatically_admitted=page.counts.automatically_admitted,
                deduplicated=page.counts.by_status["deduplicated"],
                quarantined=page.counts.by_status["quarantined"],
                by_status=page.counts.by_status,
            ),
            items=[self._ledger_item(item) for item in page.records],
            total=page.counts.total,
            has_more=page.has_more,
        )

    def _ledger_item(
        self,
        item: AcquisitionWorkflowLedgerRecord,
    ) -> WorkflowSourceLedgerItemDTO:
        return WorkflowSourceLedgerItemDTO(
            record_id=item.record_id,
            record_type=item.record_type,
            status=item.status,
            reason=item.reason,
            reason_code=item.reason_code,
            recorded_at=item.recorded_at,
            evidence_link_id=item.evidence_link_id,
            review_state=item.review_state,
            role=item.role,
            source_role=item.source_role,
            mapping_disposition=item.mapping_disposition,
            mapping_kind=item.mapping_kind,
            adapter_key=item.adapter_key,
            attempt_id=item.attempt_id,
            source_url=item.source_url,
            final_url=item.final_url,
            retrieved_at=item.retrieved_at,
            content_sha256=item.content_sha256,
            publication_key=item.publication_key,
            dedup_relation=item.dedup_relation,
            admission_outcome=item.admission_outcome,
            drilldown=(
                WorkflowSourceLedgerDrilldownDTO(
                    kind=self._ledger_drilldown_kind(item.status),
                    href=self._ledger_drilldown_href(item.status, item.job_id),
                )
                if item.job_id is not None
                else None
            ),
            drilldown_unavailable_reason=(
                None
                if item.job_id is not None
                else "人工映射证据没有对应的资料获取任务记录。"
            ),
        )

    @staticmethod
    def _ledger_record_key(item: AcquisitionWorkflowLedgerRecord):
        return ledger_key(item.recorded_at, item.record_type, item.record_id)

    @staticmethod
    def _ledger_drilldown_kind(status: str) -> str:
        if status == "admitted":
            return "acquisition_evidence"
        if status in {"quarantined", "skipped", "conflicted"}:
            return "acquisition_exceptions"
        if status in {"search_candidate", "fetching"}:
            return "acquisition_events"
        return "acquisition_job"

    @staticmethod
    def _ledger_drilldown_href(status: str, job_id: uuid.UUID) -> str:
        suffix = {
            "admitted": "/evidence",
            "quarantined": "/exceptions",
            "skipped": "/exceptions",
            "conflicted": "/exceptions",
            "search_candidate": "/events",
            "fetching": "/events",
        }.get(status, "")
        return f"/api/v1/acquisition-jobs/{job_id}{suffix}"

    def _conclusion(
        self,
        case_id: uuid.UUID,
        scope_id: uuid.UUID,
        *,
        tenant_id: str,
    ) -> WorkflowConclusionDTO | None:
        item = self._session.scalar(
            select(EventResearchConclusion)
            .where(
                EventResearchConclusion.research_case_id == case_id,
                EventResearchConclusion.scope_version_id == scope_id,
            )
            .order_by(
                EventResearchConclusion.created_at.desc(),
                EventResearchConclusion.id.desc(),
            )
            .limit(1)
        )
        if item is None:
            return None
        human_reviewed = item.state == "published" and item.reviewer is not None
        evidence_ids = [str(value) for value in item.evidence_link_ids or []]
        valid_evidence_ids: list[uuid.UUID] = []
        for value in evidence_ids:
            try:
                valid_evidence_ids.append(uuid.UUID(value))
            except ValueError:
                continue
        citation_records = AcquisitionModule(
            self._session
        ).workflow_ledger_for_evidence_links(
            case_id=case_id,
            scope_version_id=scope_id,
            evidence_link_ids=tuple(valid_evidence_ids),
            principal=AcquisitionPrincipal(
                tenant_id=tenant_id,
                actor="system:workflow-query",
            ),
        )
        return WorkflowConclusionDTO(
            id=item.id,
            state=item.state,
            text=item.text,
            primary_factor=item.primary_factor,
            evidence_link_ids=evidence_ids,
            citations=[self._ledger_item(citation) for citation in citation_records],
            reviewer=item.reviewer,
            system_generated=item.state == "ai_draft",
            human_reviewed=human_reviewed,
            review_label=(
                "人工已审核"
                if human_reviewed
                else "系统生成，未经人工审核"
                if item.state == "ai_draft"
                else "人工审核状态未声明"
            ),
            created_at=item.created_at,
        )

    def _monitor(self, case_id: uuid.UUID) -> WorkflowMonitorDTO | None:
        item = self._session.scalar(
            select(CaseMonitorVersion)
            .where(CaseMonitorVersion.research_case_id == case_id)
            .order_by(CaseMonitorVersion.version.desc(), CaseMonitorVersion.id.desc())
            .limit(1)
        )
        if item is None:
            return None
        return WorkflowMonitorDTO(
            id=item.id,
            version=item.version,
            status=item.status,
            frequency=item.frequency,
            factor_ids=list(item.factor_ids or []),
            source_types=list(item.allowed_source_types or []),
            next_verification_event=item.next_verification_event,
            created_at=item.created_at,
        )

    @staticmethod
    def _user_action(orchestration: ResearchOrchestration, decision):
        if not orchestration.next_action_kind or not orchestration.next_action_label:
            return None
        if decision is None:
            return None
        return WorkflowUserActionDTO(
            kind=orchestration.next_action_kind,
            label=orchestration.next_action_label,
            reason=decision.reason,
            recommendation=decision.recommendation,
            alternatives=list(decision.alternatives),
            impact=decision.impact,
            payload=decision.payload,
        )

    @staticmethod
    def _checkpoint_datetime(value):
        return parse_utc_datetime(value)

    @staticmethod
    def _as_utc(value):
        return as_utc(value)

    def _recovery_truth(
        self,
        orchestration: ResearchOrchestration,
        *,
        job: Job | None,
        acquisition: WorkflowAcquisitionDTO,
        checkpoint_recovery: dict,
        checkpoint_retry_at: datetime | None,
    ) -> dict[str, object]:
        """Elevate the highest-severity durable/operational recovery fact."""
        now = self._as_utc(self._clock())
        assert now is not None
        candidates: list[_RecoveryCandidate] = []
        worker_heartbeat_at = None

        if orchestration.state in {"failed", "exhausted"}:
            candidates.append(
                _RecoveryCandidate(
                    severity=100,
                    state=orchestration.state,
                    status=orchestration.state,
                    reason=orchestration.state,
                    display_reason=orchestration.system_action_reason,
                    source="orchestration",
                )
            )
        if job is not None and job.status == "failed":
            if job.next_retry_at is not None and self._as_utc(job.next_retry_at) > now:
                candidates.append(
                    _RecoveryCandidate(
                        70,
                        "retry_wait",
                        "retry_wait",
                        "retry_wait",
                        "研究任务正在等待自动重试。",
                        "research_job",
                        retry_at=self._as_utc(job.next_retry_at),
                    )
                )
            else:
                candidates.append(
                    _RecoveryCandidate(
                        90,
                        "failed",
                        "failed",
                        "research_job_failed",
                        job.error or "研究任务已经失败。",
                        "research_job",
                        failed_at=self._as_utc(job.finished_at),
                    )
                )

        for round_item in acquisition.rounds:
            lease = self._as_utc(round_item.lease_expires_at)
            if round_item.status == "running" and (lease is None or lease <= now):
                candidates.append(
                    _RecoveryCandidate(
                        80,
                        "recovering",
                        "recovering",
                        "lost_lease",
                        "资料获取任务的执行租约已过期，系统必须恢复后才能继续。",
                        "acquisition_job",
                        lease_expires_at=lease,
                    )
                )
            elif round_item.status == "retry_wait":
                candidates.append(
                    _RecoveryCandidate(
                        70,
                        "retry_wait",
                        "retry_wait",
                        "retry_wait",
                        "资料获取任务正在等待自动重试。",
                        "acquisition_job",
                        lease_expires_at=lease,
                        retry_at=self._as_utc(round_item.retry_at),
                    )
                )
            elif round_item.status == "failed":
                candidates.append(
                    _RecoveryCandidate(
                        90,
                        "failed",
                        "failed",
                        round_item.error_code or "acquisition_failed",
                        round_item.error_detail or "资料获取任务已经失败。",
                        "acquisition_job",
                        lease_expires_at=lease,
                        failed_at=self._as_utc(round_item.finished_at),
                    )
                )

        if self._heartbeat_relevant(orchestration):
            heartbeat = self._as_utc(orchestration.last_heartbeat_at)
            if (
                heartbeat is not None
                and heartbeat < now - WorkerHeartbeatService.stale_after
            ):
                candidates.append(
                    _RecoveryCandidate(
                        50,
                        "recovering",
                        "stale",
                        "orchestration_heartbeat_stale",
                        "当前研究编排心跳已经超过健康阈值。",
                        "orchestration",
                    )
                )
            worker_status = WorkerHeartbeatService(self._session).status(now=now)
            worker_heartbeat_at = self._checkpoint_datetime(
                worker_status["last_seen_at"]
            )
            if (
                worker_status["last_seen_at"] is not None
                and worker_status["status"] == "stale"
            ):
                candidates.append(
                    _RecoveryCandidate(
                        60,
                        "recovering",
                        "stale",
                        "worker_heartbeat_stale",
                        "研究 worker 心跳已经超过健康阈值。",
                        "worker",
                    )
                )

        persisted_reason = self._string(checkpoint_recovery.get("reason"))
        if orchestration.state == "retry_wait":
            candidates.append(
                _RecoveryCandidate(
                    70,
                    "retry_wait",
                    orchestration.recovery_status or "retry_wait",
                    persisted_reason or "retry_wait",
                    orchestration.system_action_reason,
                    "checkpoint",
                    retry_at=self._as_utc(checkpoint_retry_at),
                )
            )
        elif orchestration.state == "recovering" or orchestration.recovery_status in {
            "stale",
            "recovering",
        }:
            candidates.append(
                _RecoveryCandidate(
                    40,
                    "recovering",
                    orchestration.recovery_status or "recovering",
                    persisted_reason or "recovering",
                    orchestration.system_action_reason,
                    "checkpoint",
                )
            )

        if not candidates:
            return {
                "state": orchestration.state,
                "status": orchestration.recovery_status,
                "reason": persisted_reason,
                "display_reason": None,
                "lease_expires_at": self._active_lease_expiry(acquisition),
                "retry_at": self._as_utc(checkpoint_retry_at),
                "failed_at": None,
                "source": None,
                "evaluated_at": now,
                "worker_heartbeat_at": worker_heartbeat_at,
            }
        selected = max(candidates, key=lambda item: item.severity)
        return {
            "state": selected.state,
            "status": selected.status,
            "reason": selected.reason,
            "display_reason": selected.display_reason,
            "lease_expires_at": (
                selected.lease_expires_at or self._active_lease_expiry(acquisition)
            ),
            "retry_at": selected.retry_at or self._as_utc(checkpoint_retry_at),
            "failed_at": selected.failed_at,
            "source": selected.source,
            "evaluated_at": now,
            "worker_heartbeat_at": worker_heartbeat_at,
        }

    @staticmethod
    def _heartbeat_relevant(orchestration: ResearchOrchestration) -> bool:
        if orchestration.state not in {
            "planning_acquisition",
            "acquiring",
            "freezing_sources",
            "assessing_coverage",
            "synthesizing_evidence",
            "adjudicating_thesis",
            "generating_report",
        }:
            return False
        return True

    @staticmethod
    def _active_lease_expiry(acquisition: WorkflowAcquisitionDTO):
        leases = [
            ResearchWorkflowQueries._as_utc(item.lease_expires_at)
            for item in acquisition.rounds
            if item.status == "running" and item.lease_expires_at is not None
        ]
        return min(leases) if leases else None

    def _acquisition_recovery_status(self, job: AcquisitionJob) -> str | None:
        lease_expires_at = ResearchWorkflowQueries._as_utc(job.lease_expires_at)
        if job.status == "running" and (
            lease_expires_at is None or lease_expires_at <= self._as_utc(self._clock())
        ):
            return "lost_lease"
        if job.status == "retry_wait":
            return "retry_wait"
        if job.status == "failed":
            return "failed"
        return None

    def _not_initialized(self, case_id: uuid.UUID) -> WorkflowNotInitializedError:
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(
                EventResearchScopeVersion.version.desc(),
                EventResearchScopeVersion.id.desc(),
            )
            .limit(1)
        )
        if scope is None:
            action: dict[str, object] = {
                "kind": "establish_scope",
                "label": "建立并确认研究范围",
                "method": "GET",
                "href": f"/api/v1/event-research/{case_id}/workbench",
                "payload": {},
            }
        else:
            action = {
                "kind": "initialize_workflow",
                "label": "确认命题并开始研究",
                "method": "POST",
                "href": f"/api/v1/event-research/{case_id}/workflow/confirm",
                "payload": {
                    "scope_version_id": str(scope.id),
                    "scope_version": scope.version,
                    "idempotency_key": (
                        f"workflow:initialize:{scope.id}:v{scope.version}"
                    ),
                },
            }
        return WorkflowNotInitializedError(
            "research workflow is not initialized",
            safe_action=action,
        )

    @staticmethod
    def _string(value: object) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _integer(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
