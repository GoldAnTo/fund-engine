"""Deterministic, tenant-scoped projection for one-click automatic research."""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.acquisition import (
    AcquisitionException,
    AcquisitionJob,
    AcquisitionJobEvent,
    AutomaticAdmissionDecision,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
)
from app.models.ledger import (
    AIAssessment,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.models.source_governance import SourceContract
from app.schemas.v1.automatic_research import (
    AutomaticResearchExceptionDTO,
    AutomaticResearchResultDTO,
    AutomaticResearchSourceDTO,
    AutomaticResearchStageDTO,
    AutomaticResearchStatsDTO,
    AutomaticResearchViewDTO,
)
from app.services.automatic_source_bindings import validate_automatic_source_bindings
from app.services.automatic_research_scope import validate_automatic_research_scope
from app.services.case_tenant_access import CaseTenantAccess


_STAGES = (
    ("acquire", "采集来源"),
    ("parse", "解析材料"),
    ("admit", "准入证据"),
    ("analyze", "分析证据"),
    ("conclude", "形成结论"),
)
_ACQUISITION_STAGE = {
    "queued": 0,
    "searching": 0,
    "fetching": 0,
    "freezing": 1,
    "extracting": 1,
    "admitting": 2,
    "succeeded": 2,
    "partial": 2,
    "failed": 2,
    "cancelled": 2,
}
_RESEARCH_STAGE = {
    "planning": 0,
    "retrieve": 0,
    "waiting_for_sources": 2,
    "analyze": 3,
    "assessing": 3,
    "conclude": 4,
    "complete": 4,
    "failed": 4,
    "stopped": 4,
}
_EXCEPTION_MESSAGES = {
    "search_failed": ("来源检索暂时失败", "acquire"),
    "search_item_rejected": ("部分检索结果不符合来源要求", "acquire"),
    "source_unavailable": ("部分来源暂时不可用", "acquire"),
    "fetch_failed": ("部分来源获取失败", "acquire"),
    "reference_restore_failed": ("来源引用恢复失败", "acquire"),
    "parser_failure": ("部分材料解析失败", "parse"),
    "invalid_fetch_checkpoint": ("材料处理状态无效", "parse"),
    "automatic_admission_quarantined": ("部分证据未通过自动准入", "admit"),
    "incompatible_source_contract": ("部分来源不允许用于本次研究", "admit"),
    "unsupported_source_policy_version": ("来源策略版本暂不支持", "admit"),
    "variant_conflict": ("来源版本存在冲突", "admit"),
}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class AutomaticResearchQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, case_id: uuid.UUID, tenant_id: str) -> AutomaticResearchViewDTO:
        CaseTenantAccess(self._session).require_case(case_id, tenant_id)
        brief = self._session.scalar(
            select(EventResearchBrief).where(
                EventResearchBrief.research_case_id == case_id
            )
        )
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        if (
            brief is None
            or brief.workflow_mode != "automatic"
            or lifecycle is None
            or lifecycle.active_run_id is None
        ):
            raise NotFoundError("automatic research case not found")
        run = self._session.get(ResearchRun, lifecycle.active_run_id)
        if run is None or run.research_case_id != case_id:
            raise NotFoundError("automatic research case not found")

        run_events = list(
            self._session.scalars(
                select(ResearchRunEvent)
                .where(ResearchRunEvent.run_id == run.id)
                .order_by(ResearchRunEvent.seq, ResearchRunEvent.id)
            )
        )
        scope_event = next(
            (event for event in reversed(run_events) if event.stage == "scope"), None
        )
        frozen_scope = scope_event.payload_json if scope_event is not None else {}
        if frozen_scope.get("workflow_mode") != "automatic":
            raise NotFoundError("automatic research case not found")
        try:
            validated_scope = validate_automatic_research_scope(
                self._session, run, frozen_scope
            )
        except ValueError:
            validated_scope = None

        jobs: list[AcquisitionJob] = []
        job_ids: frozenset[uuid.UUID] = frozenset()
        if run.round >= 1 and validated_scope is not None:
            try:
                bindings = validate_automatic_source_bindings(
                    self._session,
                    run,
                    frozen_scope,
                    allow_unbound_current_round=run.status == "queued",
                )
            except ValueError:
                bindings = None
            if bindings is not None:
                job_ids = bindings.job_ids
                jobs = sorted(
                    bindings.jobs_by_task_id.values(),
                    key=lambda job: (job.created_at, str(job.id)),
                )

        acquisition_events = (
            list(
                self._session.scalars(
                    select(AcquisitionJobEvent)
                    .where(AcquisitionJobEvent.job_id.in_(job_ids))
                    .order_by(
                        AcquisitionJobEvent.created_at,
                        AcquisitionJobEvent.job_id,
                        AcquisitionJobEvent.seq,
                    )
                )
            )
            if job_ids
            else []
        )
        overall = self._overall_status(run, lifecycle)
        stages = self._stages(overall, run, jobs, run_events, acquisition_events)
        links = self._validated_links(
            case_id,
            validated_scope.current_scope_id if validated_scope else None,
            frozen_scope,
            job_ids,
        )
        exceptions = self._exceptions(job_ids)
        result = (
            self._result(
                case_id,
                run,
                validated_scope.current_scope_id if validated_scope else None,
                frozen_scope,
                links,
            )
            if overall == "completed"
            else None
        )
        if overall == "completed" and result is None:
            # A terminal run is not a completed product until its exact
            # system-generated conclusion can be projected from validated
            # run-local evidence.
            overall = "failed"
            stages = self._stages(overall, run, jobs, run_events, acquisition_events)
        terminal_times = [
            value
            for value in (
                run.updated_at,
                *(job.finished_at or job.updated_at for job in jobs),
                *(event.created_at for event in run_events),
                *(event.created_at for event in acquisition_events),
            )
            if value is not None
        ]
        duration = max(
            0.0,
            (
                (max(terminal_times) if terminal_times else run.created_at)
                - run.created_at
            ).total_seconds(),
        )
        activity = self._activity(run_events, acquisition_events)
        return AutomaticResearchViewDTO(
            case_id=str(case_id),
            run_id=str(run.id),
            title=brief.event_title,
            status=overall,
            stages=stages,
            stats=AutomaticResearchStatsDTO(
                source_count=sum(max(0, job.reference_count) for job in jobs),
                admitted_evidence_count=len(links),
                skipped_count=sum(max(0, job.exception_count) for job in jobs),
                duration_seconds=int(duration),
            ),
            recent_activity=activity,
            exceptions=exceptions,
            failure_reason=(
                "自动研究未能完成，请稍后重试" if overall == "failed" else None
            ),
            result=result,
        )

    @staticmethod
    def _overall_status(run: ResearchRun, lifecycle: EventResearchLifecycle) -> str:
        if run.status == "queued":
            return "queued"
        if (
            run.status == "succeeded"
            and run.stage == "complete"
            and lifecycle.status == "completed"
        ):
            return "completed"
        if run.status in {"failed", "cancelled"}:
            return "failed"
        return "running"

    def _stages(
        self,
        overall: str,
        run: ResearchRun,
        jobs: list[AcquisitionJob],
        run_events: list[ResearchRunEvent],
        acquisition_events: list[AcquisitionJobEvent],
    ) -> list[AutomaticResearchStageDTO]:
        if overall == "queued":
            current = 0
        elif overall == "completed":
            current = 5
        elif overall == "failed":
            current = self._failure_stage(
                run, jobs, run_events, acquisition_events
            )
        elif jobs and any(
            job.status not in {"succeeded", "partial", "failed", "cancelled"}
            for job in jobs
        ):
            current = min(
                _ACQUISITION_STAGE.get(job.stage, 0)
                for job in jobs
                if job.status
                not in {"succeeded", "partial", "failed", "cancelled"}
            )
        elif overall == "running" and jobs:
            current = 3
        else:
            current = _RESEARCH_STAGE.get(run.stage, 3)
        timestamps: dict[int, list[datetime]] = {index: [] for index in range(5)}
        for event in acquisition_events:
            timestamps[_ACQUISITION_STAGE.get(event.stage, 0)].append(event.created_at)
        for event in run_events:
            index = _RESEARCH_STAGE.get(event.stage)
            if index is not None:
                timestamps[index].append(event.created_at)
        projected: list[AutomaticResearchStageDTO] = []
        for index, (key, label) in enumerate(_STAGES):
            if overall == "completed" or index < current:
                state = "completed"
            elif overall == "failed" and index == current:
                state = "failed"
            elif overall == "running" and index == current:
                state = "running"
            else:
                state = "pending"
            values = timestamps[index]
            projected.append(
                AutomaticResearchStageDTO(
                    key=key,
                    label=label,
                    status=state,
                    summary={
                        "pending": f"{label}尚未开始",
                        "running": f"正在{label}",
                        "completed": f"{label}已完成",
                        "failed": f"{label}未能完成",
                    }[state],
                    started_at=min(values) if values else None,
                    completed_at=(
                        max(values)
                        if values and state in {"completed", "failed"}
                        else None
                    ),
                )
            )
        return projected

    @staticmethod
    def _failure_stage(
        run: ResearchRun,
        jobs: list[AcquisitionJob],
        run_events: list[ResearchRunEvent],
        acquisition_events: list[AcquisitionJobEvent],
    ) -> int:
        # Generic terminal "failed" events do not identify the genuine stage;
        # prefer a concrete analyze/conclude/retrieve event when one exists.
        concrete_run_stages = [
            _RESEARCH_STAGE[event.stage]
            for event in run_events
            if event.status == "failed"
            and event.stage in _RESEARCH_STAGE
            and event.stage != "failed"
        ]
        if concrete_run_stages:
            return concrete_run_stages[-1]

        acquisition_failure_stages = [
            _ACQUISITION_STAGE[event.stage]
            for event in acquisition_events
            if event.status in {"failed", "cancelled"}
            and event.stage in _ACQUISITION_STAGE
        ]
        if acquisition_failure_stages:
            return acquisition_failure_stages[-1]

        reason = run.stop_reason or ""
        if reason == "no_usable_evidence":
            return 2
        if "assessment" in reason or "analy" in reason:
            return 3
        if "conclusion" in reason or "result" in reason:
            return 4
        if not jobs:
            return 0
        failed_job_stages = [
            _ACQUISITION_STAGE.get(job.stage, 2)
            for job in jobs
            if job.status in {"failed", "cancelled"}
        ]
        if failed_job_stages:
            return failed_job_stages[-1]
        # All sources were terminal before an unclassified worker failure, so
        # the next genuine stage was analysis rather than conclusion.
        return 3

    def _validated_links(
        self,
        case_id: uuid.UUID,
        current_scope_id: uuid.UUID | None,
        frozen_scope: dict,
        job_ids: frozenset[uuid.UUID],
    ) -> list[tuple[EvidenceLink, SourceStatement, DocumentVersion, SourceContract | None]]:
        if not job_ids or current_scope_id is None:
            return []
        try:
            thesis_ids = {uuid.UUID(str(value)) for value in frozen_scope["factor_ids"]}
        except (KeyError, TypeError, ValueError):
            return []
        rows = list(
            self._session.execute(
                select(EvidenceLink, SourceStatement, DocumentVersion, SourceContract)
                .join(SourceStatement, SourceStatement.id == EvidenceLink.source_statement_id)
                .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
                .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
                .outerjoin(SourceContract, SourceContract.document_version_id == DocumentVersion.id)
                .join(
                    AutomaticAdmissionDecision,
                    AutomaticAdmissionDecision.id
                    == EvidenceLink.automatic_admission_decision_id,
                )
                .join(
                    AcquisitionJob,
                    AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
                )
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .join(
                    EventResearchScopeEvidenceAssignment,
                    EventResearchScopeEvidenceAssignment.evidence_link_id
                    == EvidenceLink.id,
                )
                .where(
                    AutomaticAdmissionDecision.job_id.in_(job_ids),
                    AutomaticAdmissionDecision.outcome == "admitted",
                    AcquisitionJob.research_case_id == case_id,
                    AcquisitionJob.thesis_id == EvidenceLink.thesis_id,
                    EvidenceLink.thesis_id.in_(thesis_ids),
                    Thesis.research_case_id == case_id,
                    EvidenceLink.review_state == "automatically_admitted",
                    EventResearchScopeEvidenceAssignment.scope_version_id
                    == current_scope_id,
                    EventResearchScopeEvidenceAssignment.disposition == "mapped",
                    EventResearchScopeEvidenceAssignment.factor_statement == Thesis.statement,
                )
                .order_by(EvidenceLink.available_at, EvidenceLink.id)
            )
        )
        return rows

    def _result(
        self,
        case_id: uuid.UUID,
        run: ResearchRun,
        current_scope_id: uuid.UUID | None,
        frozen_scope: dict,
        links: list[tuple[EvidenceLink, SourceStatement, DocumentVersion, SourceContract | None]],
    ) -> AutomaticResearchResultDTO | None:
        if current_scope_id is None:
            return None
        link_ids = {link.id for link, _, _, _ in links}
        conclusion = self._session.get(
            EventResearchConclusion,
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"fund-engine:event-research:automatic:{run.id}",
            ),
        )
        if (
            conclusion is None
            or conclusion.research_case_id != case_id
            or conclusion.scope_version_id != current_scope_id
            or conclusion.state != "system_generated"
        ):
            return None
        try:
            conclusion_link_ids = {
                uuid.UUID(str(value)) for value in conclusion.evidence_link_ids
            }
        except (TypeError, ValueError, AttributeError):
            return None
        if conclusion_link_ids != link_ids:
            return None
        findings: list[str] = []
        limitations: list[str] = []
        try:
            thesis_ids = {uuid.UUID(str(value)) for value in frozen_scope["factor_ids"]}
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        tasks = list(self._session.scalars(
            select(ResearchTask)
            .where(
                ResearchTask.run_id == run.id,
                ResearchTask.research_case_id == case_id,
                ResearchTask.round == run.round,
                ResearchTask.task_type == "result",
                ResearchTask.status == "done",
                ResearchTask.stage == "completed",
            )
            .order_by(ResearchTask.created_at, ResearchTask.id)
        ))
        if len(tasks) != len(thesis_ids) or {task.thesis_id for task in tasks} != thesis_ids:
            return None
        seen_assessments: set[uuid.UUID] = set()
        assessment_link_ids: list[uuid.UUID] = []
        link_theses = {link.id: link.thesis_id for link, _, _, _ in links}
        for task in tasks:
            try:
                assessment_id = uuid.UUID(str((task.result or {})["assessment_id"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if assessment_id in seen_assessments:
                return None
            seen_assessments.add(assessment_id)
            assessment = self._session.get(AIAssessment, assessment_id)
            snapshot = (
                self._session.get(EvidenceSnapshot, assessment.snapshot_id)
                if assessment
                else None
            )
            try:
                snapshot_link_ids = [
                    uuid.UUID(str(value))
                    for value in (snapshot.evidence_link_ids if snapshot else [])
                ]
            except (TypeError, ValueError, AttributeError):
                return None
            if (
                assessment is None
                or snapshot is None
                or assessment.displayed_as_provisional is not True
                or assessment.creator_type != "ai"
                or snapshot.thesis_id != task.thesis_id
                or any(link_theses.get(link_id) != task.thesis_id for link_id in snapshot_link_ids)
            ):
                return None
            assessment_link_ids.extend(snapshot_link_ids)
            findings.append(assessment.rationale)
            limitations.extend(str(value) for value in assessment.gaps or [])
        if (
            len(assessment_link_ids) != len(set(assessment_link_ids))
            or set(assessment_link_ids) != link_ids
        ):
            return None
        sources: list[AutomaticResearchSourceDTO] = []
        source_keys: set[tuple[str | None, str | None, str]] = set()
        for link, _, document, contract in links:
            title = document.title if contract is not None and contract.allow_display else None
            url = document.source_url if contract is not None and contract.allow_display else None
            key = (title, url, link.role)
            if key in source_keys:
                continue
            source_keys.add(key)
            sources.append(
                AutomaticResearchSourceDTO(
                    title=title,
                    url=url,
                    role=link.role,
                    review_state="automatically_admitted",
                )
            )
        counter_evidence = [
            statement.normalized_text
            for link, statement, _, contract in links
            if link.role == "contradicts"
            and contract is not None
            and contract.allow_display
        ]
        return AutomaticResearchResultDTO(
            label="系统生成，未经人工审核",
            human_reviewed=False,
            conclusion=conclusion.text,
            key_findings=_dedupe(findings),
            counter_evidence=_dedupe(counter_evidence),
            limitations=_dedupe(limitations),
            sources=sources,
        )

    def _exceptions(self, job_ids: frozenset[uuid.UUID]) -> list[AutomaticResearchExceptionDTO]:
        if not job_ids:
            return []
        reasons = self._session.scalars(
            select(AcquisitionException.reason_code)
            .where(AcquisitionException.job_id.in_(job_ids))
            .order_by(AcquisitionException.reason_code)
        )
        counts = Counter(reasons)
        return [
            AutomaticResearchExceptionDTO(
                reason=_EXCEPTION_MESSAGES.get(code, ("部分材料处理异常", "admit"))[0],
                stage=_EXCEPTION_MESSAGES.get(code, ("部分材料处理异常", "admit"))[1],
                count=count,
            )
            for code, count in sorted(counts.items())
        ]

    @staticmethod
    def _activity(
        run_events: list[ResearchRunEvent],
        acquisition_events: list[AcquisitionJobEvent],
    ) -> list[str]:
        rows = [
            (event.created_at, f"研究阶段更新：{event.stage}") for event in run_events
        ] + [
            (event.created_at, f"来源处理更新：{event.stage}")
            for event in acquisition_events
        ]
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [message for _, message in rows[:20]]
