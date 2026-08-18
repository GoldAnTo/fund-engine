"""Deterministic, tenant-scoped projection for one-click automatic research."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.automatic_research import (
    AUTOMATIC_RESEARCH_ACTIVE_RUN_STATUSES,
    AUTOMATIC_RESEARCH_UNMANAGED_RUN_MESSAGE,
)
from app.errors import ConflictError, NotFoundError
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
    AutomaticResearchResultDTO,
    AutomaticResearchViewDTO,
)
from app.services.automatic_source_bindings import validate_automatic_source_bindings
from app.services.automatic_research_conclusion import (
    AutomaticAssessmentInput,
    AutomaticSourceJobInput,
    build_automatic_research_conclusion,
)
from app.services.automatic_research_scope import (
    AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE,
    AutomaticResearchScopeError,
    ValidatedAutomaticResearchScope,
    validate_automatic_research_scope,
)
from app.services.automatic_research_projection import (
    automatic_research_status,
    project_automatic_research_progress,
    project_automatic_research_result,
)
from app.services.case_tenant_access import CaseTenantAccess


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class _ResultProjection:
    result: AutomaticResearchResultDTO | None
    failure_stage: int | None = None


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
        unmanaged_active_run_id = self._session.scalar(
            select(ResearchRun.id)
            .where(
                ResearchRun.research_case_id == case_id,
                ResearchRun.id != run.id,
                ResearchRun.status.in_(AUTOMATIC_RESEARCH_ACTIVE_RUN_STATUSES),
            )
            .order_by(ResearchRun.created_at, ResearchRun.id)
            .limit(1)
        )
        if unmanaged_active_run_id is not None:
            raise ConflictError(AUTOMATIC_RESEARCH_UNMANAGED_RUN_MESSAGE)

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
        except AutomaticResearchScopeError as exc:
            raise ConflictError(
                AUTOMATIC_RESEARCH_SCOPE_CONFLICT_MESSAGE
            ) from exc

        jobs: list[AcquisitionJob] = []
        job_ids: frozenset[uuid.UUID] = frozenset()
        if run.round >= 1:
            try:
                bindings = validate_automatic_source_bindings(
                    self._session,
                    run,
                    validated_scope,
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
        overall = automatic_research_status(run, lifecycle)
        links = self._validated_links(
            case_id,
            validated_scope.current_scope_id,
            validated_scope,
            job_ids,
        )
        exception_reason_codes = (
            list(
                self._session.scalars(
                    select(AcquisitionException.reason_code)
                    .where(AcquisitionException.job_id.in_(job_ids))
                    .order_by(AcquisitionException.reason_code)
                )
            )
            if job_ids
            else []
        )
        projection = (
            self._result(
                case_id,
                run,
                validated_scope.current_scope_id,
                validated_scope,
                links,
                jobs,
            )
            if overall == "completed"
            else _ResultProjection(result=None)
        )
        progress = project_automatic_research_progress(
            status=overall,
            run=run,
            jobs=jobs,
            run_events=run_events,
            acquisition_events=acquisition_events,
            admitted_evidence_count=len(links),
            exception_reason_codes=exception_reason_codes,
            now=_utcnow(),
            projection_failure_stage=(
                projection.failure_stage
                if overall == "completed" and projection.result is None
                else None
            ),
        )
        return AutomaticResearchViewDTO(
            case_id=str(case_id),
            run_id=str(run.id),
            title=brief.event_title,
            status=progress.status,
            stages=progress.stages,
            stats=progress.stats,
            recent_activity=progress.recent_activity,
            exceptions=progress.exceptions,
            failure_reason=progress.failure_reason,
            result=projection.result,
        )

    def _validated_links(
        self,
        case_id: uuid.UUID,
        current_scope_id: uuid.UUID | None,
        scope: ValidatedAutomaticResearchScope,
        job_ids: frozenset[uuid.UUID],
    ) -> list[tuple[EvidenceLink, SourceStatement, DocumentVersion, SourceContract | None]]:
        if not job_ids or current_scope_id is None:
            return []
        thesis_ids = set(scope.factor_ids)
        rows = list(
            self._session.execute(
                select(EvidenceLink, SourceStatement, DocumentVersion, SourceContract)
                .join(SourceStatement, SourceStatement.id == EvidenceLink.source_statement_id)
                .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
                .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
                .outerjoin(
                    SourceContract,
                    SourceContract.document_version_id == DocumentVersion.id,
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
        scope: ValidatedAutomaticResearchScope,
        links: list[tuple[EvidenceLink, SourceStatement, DocumentVersion, SourceContract | None]],
        jobs: list[AcquisitionJob],
    ) -> _ResultProjection:
        if current_scope_id is None:
            return _ResultProjection(result=None, failure_stage=4)
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
            or conclusion.based_on_conclusion_id is not None
            or conclusion.reviewer is not None
        ):
            return _ResultProjection(result=None, failure_stage=4)
        if not isinstance(conclusion.evidence_link_ids, list):
            return _ResultProjection(result=None, failure_stage=4)
        try:
            conclusion_link_ids = [
                uuid.UUID(str(value)) for value in conclusion.evidence_link_ids
            ]
        except (TypeError, ValueError, AttributeError):
            return _ResultProjection(result=None, failure_stage=4)
        if len(conclusion_link_ids) != len(set(conclusion_link_ids)):
            return _ResultProjection(result=None, failure_stage=4)
        links_by_id = {row[0].id: row for row in links}
        if (
            len(links_by_id) != len(links)
            or set(conclusion_link_ids) != set(links_by_id)
        ):
            return _ResultProjection(result=None, failure_stage=4)
        ordered_links = [links_by_id[link_id] for link_id in conclusion_link_ids]
        thesis_ids = list(scope.factor_ids)
        factor_statements = list(scope.factor_statements)
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
        if (
            len(tasks) != len(thesis_ids)
            or {task.thesis_id for task in tasks} != set(thesis_ids)
        ):
            return _ResultProjection(result=None, failure_stage=3)
        tasks_by_thesis = {task.thesis_id: task for task in tasks}
        tasks = [tasks_by_thesis[thesis_id] for thesis_id in thesis_ids]
        seen_assessments: set[uuid.UUID] = set()
        assessment_inputs: list[AutomaticAssessmentInput] = []
        link_theses = {
            link.id: link.thesis_id for link, _, _, _ in ordered_links
        }
        for task, expected_statement in zip(tasks, factor_statements):
            try:
                assessment_id = uuid.UUID(str((task.result or {})["assessment_id"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                return _ResultProjection(result=None, failure_stage=3)
            if assessment_id in seen_assessments:
                return _ResultProjection(result=None, failure_stage=3)
            seen_assessments.add(assessment_id)
            assessment = self._session.get(AIAssessment, assessment_id)
            snapshot = (
                self._session.get(EvidenceSnapshot, assessment.snapshot_id)
                if assessment
                else None
            )
            thesis = self._session.get(Thesis, task.thesis_id) if task.thesis_id else None
            try:
                snapshot_link_ids = [
                    uuid.UUID(str(value))
                    for value in (snapshot.evidence_link_ids if snapshot else [])
                ]
            except (TypeError, ValueError, AttributeError):
                return _ResultProjection(result=None, failure_stage=3)
            if (
                assessment is None
                or snapshot is None
                or thesis is None
                or thesis.research_case_id != case_id
                or thesis.statement != expected_statement
                or snapshot.thesis_id != task.thesis_id
                or any(link_theses.get(link_id) != task.thesis_id for link_id in snapshot_link_ids)
            ):
                return _ResultProjection(result=None, failure_stage=3)
            assessment_inputs.append(
                AutomaticAssessmentInput(
                    assessment_id=assessment.id,
                    task_thesis_id=task.thesis_id,
                    snapshot_thesis_id=snapshot.thesis_id,
                    thesis_statement=thesis.statement,
                    conclusion=assessment.conclusion,
                    rationale=assessment.rationale,
                    gaps=assessment.gaps,
                    displayed_as_provisional=assessment.displayed_as_provisional,
                    creator_type=assessment.creator_type,
                    evidence_link_ids=snapshot.evidence_link_ids,
                )
            )
        try:
            built = build_automatic_research_conclusion(
                factor_scope=list(zip(thesis_ids, factor_statements)),
                assessments=assessment_inputs,
                source_jobs=[
                    AutomaticSourceJobInput(
                        status=job.status,
                        admitted_count=job.admitted_count,
                        exception_count=job.exception_count,
                    )
                    for job in jobs
                ],
            )
        except ValueError:
            return _ResultProjection(result=None, failure_stage=3)
        if (
            list(built.evidence_link_ids) != conclusion_link_ids
            or built.text != conclusion.text
            or built.primary_factor != conclusion.primary_factor
        ):
            return _ResultProjection(result=None, failure_stage=4)
        return _ResultProjection(
            result=project_automatic_research_result(
                conclusion_text=conclusion.text,
                built=built,
                ordered_links=ordered_links,
            )
        )
