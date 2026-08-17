"""Create and publish bounded, evidence-linked event conclusions."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ValidationFailedError
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.ledger import AIAssessment, EvidenceLink, EvidenceSnapshot, Thesis
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.services.event_research_scope_evidence import (
    has_current_scope_evidence_coverage,
    current_mapped_evidence_ids,
    lock_event_research_lifecycle,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventConclusionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def latest(self, case_id: uuid.UUID) -> EventResearchConclusion | None:
        return self._session.scalar(
            select(EventResearchConclusion)
            .where(EventResearchConclusion.research_case_id == case_id)
            .order_by(EventResearchConclusion.created_at.desc())
            .limit(1)
        )

    def create_draft(self, case_id: uuid.UUID) -> EventResearchConclusion:
        # Scope, reviewed evidence, and the draft must come from one locked
        # event snapshot; scope updates take this same case -> lifecycle lock.
        lock_event_research_lifecycle(self._session, case_id)
        if not has_current_scope_evidence_coverage(self._session, case_id):
            raise ValidationFailedError(
                "current scope lacks sufficient reviewed mapped evidence for a conclusion draft"
            )
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        mapped_evidence_ids = current_mapped_evidence_ids(self._session, case_id)
        evidence = list(
            self._session.execute(
                select(EvidenceLink, Thesis)
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(Thesis.research_case_id == case_id)
                .where(EvidenceLink.review_state == "reviewed")
                .where(EvidenceLink.id.in_(mapped_evidence_ids))
                .order_by(EvidenceLink.available_at.desc())
            )
        )
        scores: dict[str, int] = defaultdict(int)
        for link, thesis in evidence:
            if link.role == "supports":
                scores[thesis.statement] += 1
            elif link.role == "contradicts":
                scores[thesis.statement] -= 1
        primary_factor = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else None
        if primary_factor is None:
            text = "当前尚无足以支持主要因素判断的已审核证据；本研究不能给出因果结论。"
        else:
            text = (
                f"在当前已审核证据范围内，{primary_factor} 是对本次市场反应最受支持的解释。"
                "这是一项可复核的暂定判断，不等同于唯一因果结论；反证和覆盖范围见下方证据。"
            )
        draft = EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=scope.id if scope is not None else None,
            state="ai_draft",
            text=text,
            primary_factor=primary_factor,
            evidence_link_ids=[str(link.id) for link, _ in evidence],
            based_on_conclusion_id=(
                self._session.scalar(
                    select(EventResearchConclusion.id)
                    .where(EventResearchConclusion.research_case_id == case_id)
                    .where(EventResearchConclusion.state == "published")
                    .order_by(EventResearchConclusion.created_at.desc())
                    .limit(1)
                )
            ),
            reviewer=None,
            created_at=_utcnow(),
        )
        self._session.add(draft)
        self._session.flush()
        return draft

    def create_automatic_result(
        self,
        case_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> EventResearchConclusion:
        """Freeze one system-generated result from one automatic run."""
        lifecycle = lock_event_research_lifecycle(self._session, case_id)
        run = self._session.scalar(
            select(ResearchRun)
            .where(ResearchRun.id == run_id)
            .where(ResearchRun.research_case_id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise ValidationFailedError("automatic result run does not belong to case")
        brief_mode = self._session.scalar(
            select(EventResearchBrief.workflow_mode)
            .where(EventResearchBrief.research_case_id == case_id)
            .order_by(EventResearchBrief.created_at.desc(), EventResearchBrief.id.desc())
            .limit(1)
        )
        scope_event = self._session.scalar(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "scope")
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        scope_payload = scope_event.payload_json if scope_event is not None else None
        if (
            brief_mode != "automatic"
            or not isinstance(scope_payload, dict)
            or scope_payload.get("workflow_mode") != "automatic"
        ):
            raise ValidationFailedError("automatic result requires an automatic workflow")
        if lifecycle is None or lifecycle.active_run_id != run.id:
            raise ValidationFailedError("automatic result run is not the active lifecycle run")

        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        if scope is None:
            raise ValidationFailedError("automatic result requires a current scope")

        try:
            scoped_thesis_ids = [
                uuid.UUID(str(value)) for value in (run.scope_thesis_ids or [])
            ]
            frozen_thesis_ids = [
                uuid.UUID(str(value)) for value in scope_payload["factor_ids"]
            ]
            frozen_statements = list(scope_payload["factor_statements"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValidationFailedError("automatic result frozen scope is invalid") from exc
        current_statements = list(
            self._session.scalars(
                select(EventResearchScopeFactor.statement)
                .where(EventResearchScopeFactor.scope_version_id == scope.id)
                .order_by(EventResearchScopeFactor.position, EventResearchScopeFactor.id)
            )
        )
        if (
            scoped_thesis_ids != frozen_thesis_ids
            or len(scoped_thesis_ids) != len(frozen_statements)
            or len(current_statements) != len(frozen_statements)
            or len(set(current_statements)) != len(current_statements)
            or set(current_statements) != set(frozen_statements)
        ):
            raise ValidationFailedError(
                "automatic result current scope differs from the frozen run scope"
            )
        for thesis_id, statement in zip(scoped_thesis_ids, frozen_statements):
            thesis = self._session.get(Thesis, thesis_id)
            if (
                thesis is None
                or thesis.research_case_id != case_id
                or thesis.statement != statement
            ):
                raise ValidationFailedError(
                    "automatic result frozen thesis identity is invalid"
                )

        result_tasks = list(
            self._session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run.id)
                .where(ResearchTask.research_case_id == case_id)
                .where(ResearchTask.round == run.round)
                .where(ResearchTask.task_type == "result")
                .where(ResearchTask.status == "done")
                .where(ResearchTask.stage == "completed")
                .order_by(ResearchTask.created_at, ResearchTask.id)
            )
        )
        if (
            len(result_tasks) != len(scoped_thesis_ids)
            or {task.thesis_id for task in result_tasks} != set(scoped_thesis_ids)
        ):
            raise ValidationFailedError(
                "automatic result requires exactly one completed task per thesis"
            )

        assessment_rows: list[tuple[AIAssessment, EvidenceSnapshot, Thesis]] = []
        seen_assessment_ids: set[uuid.UUID] = set()
        for task in result_tasks:
            if not isinstance(task.result, dict) or task.thesis_id is None:
                raise ValidationFailedError("automatic result task binding is missing")
            try:
                assessment_id = uuid.UUID(str(task.result["assessment_id"]))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise ValidationFailedError(
                    "automatic result task assessment binding is invalid"
                ) from exc
            if assessment_id in seen_assessment_ids:
                raise ValidationFailedError(
                    "automatic result assessment is bound to more than one task"
                )
            assessment = self._session.get(AIAssessment, assessment_id)
            snapshot = (
                self._session.get(EvidenceSnapshot, assessment.snapshot_id)
                if assessment is not None
                else None
            )
            thesis = self._session.get(Thesis, task.thesis_id)
            if (
                assessment is None
                or snapshot is None
                or thesis is None
                or snapshot.thesis_id != task.thesis_id
                or thesis.research_case_id != case_id
            ):
                raise ValidationFailedError(
                    "automatic result assessment crosses its task thesis"
                )
            seen_assessment_ids.add(assessment_id)
            assessment_rows.append((assessment, snapshot, thesis))
        assessment_rows.sort(
            key=lambda row: (row[2].statement, str(row[2].id), str(row[0].id))
        )

        evidence_rows = list(
            self._session.execute(
                select(EvidenceLink, Thesis)
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .join(
                    EventResearchScopeEvidenceAssignment,
                    EventResearchScopeEvidenceAssignment.evidence_link_id
                    == EvidenceLink.id,
                )
                .join(
                    AutomaticAdmissionDecision,
                    AutomaticAdmissionDecision.id
                    == EvidenceLink.automatic_admission_decision_id,
                )
                .join(
                    AcquisitionJob,
                    AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
                )
                .where(
                    EventResearchScopeEvidenceAssignment.scope_version_id == scope.id,
                    EventResearchScopeEvidenceAssignment.disposition == "mapped",
                    EventResearchScopeEvidenceAssignment.factor_statement == Thesis.statement,
                    Thesis.research_case_id == case_id,
                    AcquisitionJob.research_case_id == case_id,
                    AcquisitionJob.research_run_id == run.id,
                    EvidenceLink.review_state == "automatically_admitted",
                )
                .order_by(EvidenceLink.created_at, EvidenceLink.id)
            )
        )
        valid_evidence_link_ids = [str(link.id) for link, _thesis in evidence_rows]
        if not valid_evidence_link_ids:
            raise ValidationFailedError("automatic result has no admitted evidence")
        assessment_evidence_ids = [
            str(link_id)
            for _assessment, snapshot, _thesis in assessment_rows
            for link_id in snapshot.evidence_link_ids
        ]
        if (
            len(assessment_evidence_ids) != len(set(assessment_evidence_ids))
            or set(assessment_evidence_ids) != set(valid_evidence_link_ids)
        ):
            raise ValidationFailedError(
                "automatic result evidence snapshot differs from assessment evidence"
            )
        evidence_link_ids = assessment_evidence_ids

        labels = {
            "supported": "得到当前证据支持",
            "contradicted": "受到当前证据反驳",
            "insufficient_evidence": "证据不足",
        }
        text_lines: list[str] = []
        gaps: list[str] = []
        primary_factor = None
        for assessment, _snapshot, thesis in assessment_rows:
            label = labels.get(assessment.conclusion)
            if label is None:
                raise ValidationFailedError("automatic assessment conclusion is invalid")
            text_lines.append(f"{thesis.statement}：{label}。{assessment.rationale}")
            if primary_factor is None and assessment.conclusion == "supported":
                primary_factor = thesis.statement
            gaps.extend(
                str(gap).strip()
                for gap in (assessment.gaps or [])
                if str(gap).strip()
            )
        unique_gaps = sorted(set(gaps))
        if unique_gaps:
            text_lines.append("证据缺口：" + "；".join(unique_gaps))
        acquisition_statuses = []
        skipped = 0
        exception_count = 0
        partial_without_admitted_count = 0
        for task in self._session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.research_case_id == case_id)
            .where(ResearchTask.task_type != "result")
        ):
            result = task.result if isinstance(task.result, dict) else {}
            status = result.get("acquisition_status")
            task_exception_count = result.get("exception_count", 0)
            if (
                isinstance(task_exception_count, bool)
                or not isinstance(task_exception_count, int)
                or task_exception_count < 0
            ):
                raise ValidationFailedError(
                    "automatic source exception count is invalid"
                )
            exception_count += task_exception_count
            if status == "partial":
                admitted_count = result.get("admitted_count")
                if (
                    isinstance(admitted_count, bool)
                    or not isinstance(admitted_count, int)
                    or admitted_count < 0
                ):
                    raise ValidationFailedError(
                        "automatic partial source admitted count is invalid"
                    )
                if admitted_count == 0:
                    partial_without_admitted_count += 1
            if status is None:
                skipped += 1
            else:
                acquisition_statuses.append(status)
        failed_count = acquisition_statuses.count("failed")
        cancelled_count = acquisition_statuses.count("cancelled")
        partial_count = acquisition_statuses.count("partial")
        text_lines.append(
            "局限：结论仅基于本次冻结范围内自动准入且映射到当前范围的证据。"
            f"采集任务：失败 {failed_count}，取消 {cancelled_count}，"
            f"部分完成 {partial_count}，"
            f"部分完成但无准入证据 {partial_without_admitted_count}，"
            f"未执行 {skipped}，"
            f"跳过/异常条目 {exception_count}。"
        )
        text = "\n".join(text_lines)

        conclusion_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"fund-engine:event-research:automatic:{run.id}",
        )
        existing = self._session.get(EventResearchConclusion, conclusion_id)
        if existing is not None:
            if (
                existing.research_case_id != case_id
                or existing.scope_version_id != scope.id
                or existing.state != "system_generated"
                or existing.evidence_link_ids != evidence_link_ids
                or existing.text != text
                or existing.primary_factor != primary_factor
                or existing.based_on_conclusion_id is not None
                or existing.reviewer is not None
            ):
                raise ValidationFailedError("automatic result snapshot is immutable")
            result = existing
        else:
            result = EventResearchConclusion(
                id=conclusion_id,
                research_case_id=case_id,
                scope_version_id=scope.id,
                state="system_generated",
                text=text,
                primary_factor=primary_factor,
                evidence_link_ids=evidence_link_ids,
                based_on_conclusion_id=None,
                reviewer=None,
                created_at=_utcnow(),
            )
            self._session.add(result)

        run.status = "succeeded"
        run.stage = "complete"
        run.stop_reason = "automatic_completed"
        run.updated_at = _utcnow()
        lifecycle.status = "completed"
        lifecycle.active_run_id = run.id
        lifecycle.current_round = run.round
        lifecycle.status_summary = text
        lifecycle.current_gap = "；".join(unique_gaps) if unique_gaps else None
        lifecycle.next_human_action = None
        lifecycle.updated_at = _utcnow()
        self._session.flush()
        return result

    def publish(
        self, case_id: uuid.UUID, *, text: str, reviewer: str
    ) -> EventResearchConclusion:
        # Serialize with scope updates and worker lifecycle projections before
        # reading a draft or mutating the lifecycle row.
        lifecycle = lock_event_research_lifecycle(self._session, case_id)
        if lifecycle is None or lifecycle.status != "draft_ready":
            raise ValidationFailedError("event conclusion is not ready to publish")
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        draft = (
            self._session.scalar(
                select(EventResearchConclusion)
                .where(EventResearchConclusion.research_case_id == case_id)
                .where(EventResearchConclusion.state == "ai_draft")
                .where(EventResearchConclusion.scope_version_id == scope.id)
                .order_by(EventResearchConclusion.created_at.desc())
                .limit(1)
            )
            if scope is not None
            else None
        )
        if draft is None:
            raise ValidationFailedError(
                "event conclusion draft is not for the current scope"
            )
        published = EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=draft.scope_version_id,
            state="published",
            text=text,
            primary_factor=draft.primary_factor,
            evidence_link_ids=draft.evidence_link_ids,
            based_on_conclusion_id=draft.id,
            reviewer=reviewer,
            created_at=_utcnow(),
        )
        self._session.add(published)
        if lifecycle is not None:
            lifecycle.status = "published"
            lifecycle.status_summary = "研究结论已人工确认并发布"
            lifecycle.current_gap = None
            lifecycle.next_human_action = None
            lifecycle.updated_at = _utcnow()
        self._session.flush()
        return published
