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
    EventResearchScopeVersion,
)
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.ledger import AIAssessment, EvidenceLink, EvidenceSnapshot, Thesis
from app.models.operational import ResearchRun, ResearchTask
from app.services.event_research_scope_evidence import (
    has_current_scope_evidence_coverage,
    current_mapped_evidence_ids,
    lock_event_research_lifecycle,
)
from app.services.automatic_source_bindings import (
    validate_automatic_source_bindings,
)
from app.services.automatic_research_scope import (
    AutomaticResearchScopeError,
    load_automatic_research_scope,
)
from app.services.automatic_research_conclusion import (
    AutomaticAssessmentInput,
    AutomaticSourceJobInput,
    build_automatic_research_conclusion,
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
        if brief_mode != "automatic":
            raise ValidationFailedError("automatic result requires an automatic workflow")
        if lifecycle is None or lifecycle.active_run_id != run.id:
            raise ValidationFailedError("automatic result run is not the active lifecycle run")

        try:
            validated_scope = load_automatic_research_scope(self._session, run)
        except AutomaticResearchScopeError as exc:
            raise ValidationFailedError("automatic result frozen scope is invalid") from exc
        scoped_thesis_ids = list(validated_scope.factor_ids)
        frozen_statements = list(validated_scope.factor_statements)

        self._session.flush()
        try:
            source_bindings = validate_automatic_source_bindings(
                self._session,
                run,
                validated_scope,
                lock=True,
            )
        except ValueError as exc:
            raise ValidationFailedError(
                "automatic result source binding provenance is invalid"
            ) from exc

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
        tasks_by_thesis_id = {task.thesis_id: task for task in result_tasks}
        result_tasks = [tasks_by_thesis_id[thesis_id] for thesis_id in scoped_thesis_ids]

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
                    EventResearchScopeEvidenceAssignment.scope_version_id
                    == validated_scope.current_scope_id,
                    EventResearchScopeEvidenceAssignment.disposition == "mapped",
                    EventResearchScopeEvidenceAssignment.factor_statement == Thesis.statement,
                    Thesis.research_case_id == case_id,
                    AcquisitionJob.research_case_id == case_id,
                    AcquisitionJob.research_run_id == run.id,
                    AcquisitionJob.id.in_(source_bindings.job_ids),
                    AcquisitionJob.thesis_id == EvidenceLink.thesis_id,
                    EvidenceLink.review_state == "automatically_admitted",
                )
                .order_by(EvidenceLink.created_at, EvidenceLink.id)
            )
        )
        valid_evidence_link_ids = [str(link.id) for link, _thesis in evidence_rows]
        if not valid_evidence_link_ids:
            raise ValidationFailedError("automatic result has no admitted evidence")
        valid_evidence_theses = {
            str(link.id): link.thesis_id for link, _thesis in evidence_rows
        }
        if any(
            valid_evidence_theses.get(str(link_id)) != snapshot.thesis_id
            for _assessment, snapshot, _thesis in assessment_rows
            for link_id in snapshot.evidence_link_ids
        ):
            raise ValidationFailedError(
                "automatic result evidence snapshot differs from assessment evidence"
            )
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

        source_inputs: list[AutomaticSourceJobInput] = []
        for task in sorted(
            source_bindings.tasks_by_id.values(),
            key=lambda value: (value.round, value.created_at, str(value.id)),
        ):
            job = source_bindings.jobs_by_task_id[task.id]
            source_inputs.append(
                AutomaticSourceJobInput(
                    status=job.status,
                    admitted_count=job.admitted_count,
                    exception_count=job.exception_count,
                )
            )
        try:
            projection = build_automatic_research_conclusion(
                factor_scope=list(zip(scoped_thesis_ids, frozen_statements)),
                assessments=[
                    AutomaticAssessmentInput(
                        assessment_id=assessment.id,
                        task_thesis_id=thesis.id,
                        snapshot_thesis_id=snapshot.thesis_id,
                        thesis_statement=thesis.statement,
                        conclusion=assessment.conclusion,
                        rationale=assessment.rationale,
                        gaps=assessment.gaps,
                        displayed_as_provisional=assessment.displayed_as_provisional,
                        creator_type=assessment.creator_type,
                        evidence_link_ids=snapshot.evidence_link_ids,
                    )
                    for assessment, snapshot, thesis in assessment_rows
                ],
                source_jobs=source_inputs,
            )
        except ValueError as exc:
            raise ValidationFailedError(
                "automatic result assessment projection is invalid"
            ) from exc
        evidence_link_ids = [str(value) for value in projection.evidence_link_ids]
        if evidence_link_ids != assessment_evidence_ids:
            raise ValidationFailedError(
                "automatic result evidence snapshot differs from assessment evidence"
            )
        text = projection.text
        primary_factor = projection.primary_factor
        unique_gaps = list(projection.limitations)

        conclusion_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"fund-engine:event-research:automatic:{run.id}",
        )
        existing = self._session.get(EventResearchConclusion, conclusion_id)
        if existing is not None:
            if (
                existing.research_case_id != case_id
                or existing.scope_version_id != validated_scope.current_scope_id
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
                scope_version_id=validated_scope.current_scope_id,
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
