"""Governed source dispatch for one-click automatic research runs."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY, INTAKE_MATERIAL_POLICY
from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.domain.automatic_research import AUTOMATIC_SOURCE_JOB_TERMINAL
from app.domain.acquisition import (
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeEvidenceAssignment,
)
from app.models.ledger import (
    AIAssessment,
    CaseTenantAdmission,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    Thesis,
)
from app.models.operational import (
    EventResearchLifecycle,
    Job,
    ResearchRun,
    ResearchTask,
)
from app.repositories.acquisition import AcquisitionRepository
from app.repositories.auto_research import AutoResearchRepository
from app.services.acquisition import AcquisitionModule
from app.services.automatic_source_bindings import (
    validate_automatic_source_bindings,
)
from app.services.automatic_research_scope import (
    AutomaticResearchFactorScope,
    ValidatedAutomaticResearchScope,
    load_automatic_research_scope,
)
from app.services.case_monitor import ResearchRunEventRepository
from app.services.event_conclusion import EventConclusionService
from app.services.event_research_scope_evidence import (
    append_current_scope_evidence_assignment,
)


_TASK_OBJECTIVES = {
    "support": (EvidenceObjective.SUPPORT, "supports"),
    "contradict": (EvidenceObjective.CONTRADICT, "contradicts"),
    "alternative": (
        EvidenceObjective.ALTERNATIVE_EXPLANATION,
        "contextualizes",
    ),
    "alternative_explanation": (
        EvidenceObjective.ALTERNATIVE_EXPLANATION,
        "contextualizes",
    ),
}
class _AssessmentGeneratorProtocol(Protocol):
    def generate(
        self,
        thesis_id: uuid.UUID,
        cutoff: datetime,
        session: Session,
        *,
        evidence_link_ids: list[uuid.UUID],
        before_persist: Callable[[], bool],
    ) -> AIAssessment | None: ...


class AutomaticResearchPipeline:
    """Run the automatic workflow without introducing human review gates."""

    def __init__(
        self,
        session: Session,
        *,
        client: LLMClient | None = None,
        repository: AutoResearchRepository | None = None,
        assessment_generator: _AssessmentGeneratorProtocol | None = None,
    ) -> None:
        self._session = session
        self._client = client
        self._repository = repository or AutoResearchRepository(session)
        self._assessment_generator = assessment_generator

    def advance(self, run: ResearchRun) -> str:
        """Advance one durable automatic run by at most one acquisition round."""
        locked_run, locked_job = self._repository.lock_source_dispatch(run.id)
        if locked_run is None or locked_job is None:
            raise ValueError("automatic research run is missing")
        run = locked_run
        if run.status == "succeeded":
            return "completed"
        if run.status == "failed":
            return "failed"
        if (
            run.status == "cancelled"
            or locked_job.status == "cancelled"
            or locked_job.cancel_requested
        ):
            raise ValueError("automatic research run is no longer dispatchable")

        run.round = max(1, run.round or 1)
        scope = load_automatic_research_scope(self._session, run)
        current_scope_id = scope.current_scope_id
        tasks = self._round_tasks(run)
        self._validate_result_task_matrix(run, tasks, scope)
        source_tasks = [task for task in tasks if task.task_type != "result"]
        bindings = validate_automatic_source_bindings(
            self._session,
            run,
            scope,
            allow_unbound_current_round=True,
        )
        jobs = bindings.jobs_for_round(run.round)
        unbound_tasks = [
            task for task in source_tasks if task.id not in bindings.jobs_by_task_id
        ]
        if not jobs:
            return self.dispatch_sources(run)

        if any(
            job.status not in AUTOMATIC_SOURCE_JOB_TERMINAL
            or job.lease_owner is not None
            or job.lease_token is not None
            or job.lease_expires_at is not None
            for job in jobs.values()
        ):
            run.status = "waiting_for_sources"
            run.stage = "retrieve"
            run.updated_at = datetime.now(UTC)
            return "waiting_for_sources"

        bound_tasks = [
            task for task in source_tasks if task.id in bindings.jobs_by_task_id
        ]
        self._reconcile_sources(run, bound_tasks, jobs)
        if unbound_tasks:
            return self.dispatch_sources(run)
        run.budget_used = len(bindings.external_job_ids)
        allowed_evidence = self._allowed_evidence_by_thesis(
            run,
            scope=scope,
            scope_version_id=current_scope_id,
            job_ids=bindings.job_ids,
        )
        admitted_count = sum(len(link_ids) for link_ids in allowed_evidence.values())
        final_attempt = run.round >= run.max_rounds or run.budget_used >= run.budget
        if admitted_count == 0 and final_attempt:
            return self._fail_no_usable_evidence(run)

        assessments = self._assess_result_tasks(
            run,
            tasks,
            allowed_evidence=allowed_evidence,
            frozen_scope=scope,
            scope_version_id=current_scope_id,
            research_job=locked_job,
            source_binding_fingerprint=bindings.fingerprint,
        )
        gaps = sorted(
            {
                str(gap).strip()
                for assessment in assessments
                for gap in (assessment.gaps or [])
                if str(gap).strip()
            }
        )
        next_source_count = len(self._expected_task_matrix(run=run, scope=scope))
        can_replenish = (
            bool(gaps)
            and run.round < run.max_rounds
            and run.budget_used + next_source_count <= run.budget
        )
        if can_replenish:
            self._create_next_round(run, gaps)
            return self.dispatch_sources(run)
        if admitted_count == 0:
            return self._fail_no_usable_evidence(run)

        EventConclusionService(self._session).create_automatic_result(
            run.research_case_id,
            run.id,
        )
        ResearchRunEventRepository(self._session).append(
            run.id,
            stage="complete",
            status="completed",
            message="自动研究已基于自动准入证据完成",
            payload_json={
                "round": run.round,
                "budget_used": run.budget_used,
                "stop_reason": "automatic_completed",
            },
        )
        return "completed"

    def dispatch_sources(self, run: ResearchRun) -> str:
        locked_run, locked_job = self._repository.lock_source_dispatch(run.id)
        if (
            locked_run is None
            or locked_job is None
            or locked_run.status
            not in {"queued", "running", "waiting_for_sources"}
            or locked_job.status
            not in {"queued", "running", "waiting_for_sources"}
            or locked_job.cancel_requested
        ):
            raise ValueError("automatic research run is no longer dispatchable")
        # A savepoint isolates the complete governed dispatch from the outer
        # worker transaction. If any request fails, no earlier request, task
        # binding, run state, or retrieve event survives for terminalization.
        with self._session.begin_nested():
            return self._dispatch_locked(locked_run)

    def _dispatch_locked(self, run: ResearchRun) -> str:
        scope = load_automatic_research_scope(self._session, run)
        admission = self._session.scalar(
            select(CaseTenantAdmission).where(
                CaseTenantAdmission.research_case_id == run.research_case_id
            )
        )
        if admission is None:
            raise ValueError("automatic research case has no tenant admission")
        brief = self._session.scalar(
            select(EventResearchBrief)
            .where(EventResearchBrief.research_case_id == run.research_case_id)
            .order_by(EventResearchBrief.created_at.desc(), EventResearchBrief.id.desc())
            .limit(1)
        )
        if brief is None or brief.workflow_mode != "automatic":
            raise ValueError("automatic research brief is missing")

        run.round = max(1, run.round or 1)
        cutoff = run.created_at
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        else:
            cutoff = cutoff.astimezone(UTC)
        current_tasks = list(
            self._session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run.id)
                .where(ResearchTask.round == run.round)
                .where(ResearchTask.status == "queued")
                .where(ResearchTask.task_type != "result")
                .order_by(ResearchTask.created_at, ResearchTask.id)
            )
        )
        all_current_tasks = self._round_tasks(run)
        self._validate_result_task_matrix(run, all_current_tasks, scope)
        source_bindings = validate_automatic_source_bindings(
            self._session,
            run,
            scope,
            allow_unbound_current_round=True,
        )
        expected = self._expected_task_matrix(run=run, scope=scope)
        material_tasks = [
            task for task in current_tasks if task.task_type == "intake_material"
        ]
        external_tasks = [
            task for task in current_tasks if task.task_type != "intake_material"
        ]
        if material_tasks:
            expected_material_ids = {value[0].id for value in expected.values()}
            actual_material_ids = [task.thesis_id for task in material_tasks]
            if (
                run.round != 1
                or len(material_tasks) != len(expected_material_ids)
                or set(actual_material_ids) != expected_material_ids
            ):
                raise ValueError(
                    "automatic intake material task matrix is not exact"
                )
            current_tasks = material_tasks
        else:
            current_tasks = external_tasks
        actual: list[tuple[uuid.UUID, uuid.UUID, EvidenceObjective]] = []
        for task in current_tasks:
            if task.task_type == "intake_material":
                continue
            mapping = _TASK_OBJECTIVES.get(task.task_type)
            if (
                task.research_case_id != run.research_case_id
                or task.thesis_id is None
                or mapping is None
            ):
                raise ValueError(
                    "automatic research task matrix does not match the frozen run case"
                )
            objective, _target_link_role = mapping
            actual.append((task.research_case_id, task.thesis_id, objective))
        if not material_tasks and (
            not actual or len(actual) != len(expected) or set(actual) != set(expected)
        ):
            raise ValueError(
                "automatic research task matrix does not match the frozen evidence plan"
            )

        pending_requests: list[
            tuple[ResearchTask, AcquisitionRequest, EvidenceObjective]
        ] = []
        for task in current_tasks:
            assert task.thesis_id is not None
            if task.task_type == "intake_material":
                objective, target_link_role = EvidenceObjective.SUPPORT, "supports"
                thesis = self._session.get(Thesis, task.thesis_id)
                if thesis is None or thesis.research_case_id != run.research_case_id:
                    raise ValueError("automatic intake material thesis is invalid")
                allowed_roles = frozenset({"user_provided_material"})
            else:
                objective, target_link_role = _TASK_OBJECTIVES[task.task_type]
                thesis, factor = expected[
                    (run.research_case_id, task.thesis_id, objective)
                ]
                allowed_roles = frozenset(factor.allowed_source_roles) & frozenset(
                    B_SCOPE_POLICY.allowed_source_roles
                )
            if not allowed_roles:
                raise ValueError(
                    "automatic evidence plan source roles are empty after policy intersection"
                )
            pending_requests.append(
                (
                    task,
                    AcquisitionRequest(
                        tenant_id=admission.tenant_id,
                        case_id=run.research_case_id,
                        thesis_id=thesis.id,
                        research_run_id=run.id,
                        round=run.round,
                        objective=objective,
                        target_link_role=target_link_role,
                        thesis_statement=thesis.statement,
                        entity_names=(
                            (brief.company_name,) if brief.company_name else ()
                        ),
                        security_codes=((brief.ticker,) if brief.ticker else ()),
                        metric_terms=(thesis.statement,),
                        period_start=(
                            cutoff.date() - timedelta(days=3 * 365)
                        ).isoformat(),
                        period_end=cutoff.date().isoformat(),
                        cutoff=cutoff,
                        allowed_source_roles=allowed_roles,
                        source_policy_version=B_SCOPE_POLICY.version,
                        idempotency_key=(
                            f"automatic-material:{run.id}:{thesis.id}:"
                            f"{scope.material_document_version_id}"
                            if task.task_type == "intake_material"
                            else f"automatic:{run.id}:{run.round}:"
                            f"{thesis.id}:{objective.value}"
                        ),
                        acquisition_kind=(
                            "intake_material"
                            if task.task_type == "intake_material"
                            else "external_gap"
                        ),
                        document_version_id=(
                            scope.material_document_version_id
                            if task.task_type == "intake_material"
                            else None
                        ),
                    ),
                    objective,
                ),
            )

        already_bound = sum(
            1
            for task in current_tasks
            if isinstance(task.result, dict) and task.result.get("acquisition_job_id")
        )
        resulting_count = len(source_bindings.external_job_ids) + sum(
            1
            for task in current_tasks
            if task.task_type != "intake_material"
            and not (
                isinstance(task.result, dict)
                and task.result.get("acquisition_job_id")
            )
        )
        if resulting_count > run.budget:
            raise ValueError("automatic research acquisition budget is exhausted")

        principal = AcquisitionPrincipal(
            tenant_id=admission.tenant_id,
            actor="system:research-worker",
        )
        wrote_task_binding = False
        for task, request, objective in pending_requests:
            module = AcquisitionModule(
                self._session,
                policy=(
                    INTAKE_MATERIAL_POLICY
                    if task.task_type == "intake_material"
                    else B_SCOPE_POLICY
                ),
            )
            acquisition_job = module.request(request, principal=principal)
            expected_result = {
                "acquisition_job_id": str(acquisition_job.id),
                "objective": objective.value,
            }
            if task.result != expected_result or task.stage != "acquire":
                task.result = expected_result
                task.stage = "acquire"
                task.updated_at = datetime.now(UTC)
                wrote_task_binding = True

        run.status = "waiting_for_sources"
        run.stage = "retrieve"
        run.budget_used = resulting_count
        if wrote_task_binding:
            ResearchRunEventRepository(self._session).append(
                run.id,
                stage="retrieve",
                status="waiting",
                message="等待受控证据采集任务完成",
                payload_json={"round": run.round},
            )
        return "waiting_for_sources"

    def _round_tasks(self, run: ResearchRun) -> list[ResearchTask]:
        tasks = list(
            self._session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run.id)
                .where(ResearchTask.round == run.round)
                .order_by(ResearchTask.created_at, ResearchTask.id)
            )
        )
        if not tasks:
            raise ValueError("automatic research current round has no tasks")
        return tasks

    def _validate_result_task_matrix(
        self,
        run: ResearchRun,
        tasks: list[ResearchTask],
        scope: ValidatedAutomaticResearchScope,
    ) -> None:
        scoped_ids = scope.factor_ids
        allowed_types = {*_TASK_OBJECTIVES, "result"}
        if scope.input_kind == "material" and run.round == 1:
            allowed_types.add("intake_material")
        for task in tasks:
            if (
                task.run_id != run.id
                or task.research_case_id != run.research_case_id
                or task.round != run.round
                or task.thesis_id not in scoped_ids
                or task.task_type not in allowed_types
            ):
                raise ValueError(
                    "automatic result task matrix crosses the frozen run scope"
                )
        result_tasks = [task for task in tasks if task.task_type == "result"]
        actual_ids = [task.thesis_id for task in result_tasks]
        if len(result_tasks) != len(scoped_ids) or set(actual_ids) != set(scoped_ids):
            raise ValueError(
                "automatic result task matrix must contain exactly one task per thesis"
            )
        if any(
            task.status not in {"queued", "done"}
            or (task.status == "done" and task.stage != "completed")
            for task in result_tasks
        ):
            raise ValueError("automatic result task matrix contains a non-writable task")

    def _reconcile_sources(
        self,
        run: ResearchRun,
        tasks: list[ResearchTask],
        jobs: dict[uuid.UUID, AcquisitionJob],
    ) -> None:
        acquisition = AcquisitionRepository(self._session)
        now = datetime.now(UTC)
        admitted: list[tuple[EvidenceLink, Thesis]] = []
        for task in tasks:
            job = jobs[task.id]
            task.result = {
                **(task.result or {}),
                "acquisition_status": job.status,
                "reference_count": job.reference_count,
                "frozen_count": job.frozen_count,
                "admitted_count": job.admitted_count,
                "exception_count": job.exception_count,
            }
            if job.status in {"succeeded", "partial"}:
                task.status, task.stage = "done", "completed"
            else:
                task.status, task.stage = "failed", "failed"
            task.evidence_count = job.admitted_count
            task.updated_at = now
            for ref in acquisition.admitted_evidence(job.id):
                link = self._session.get(EvidenceLink, ref.evidence_link_id)
                thesis = self._session.get(Thesis, job.thesis_id)
                if (
                    link is None
                    or thesis is None
                    or link.thesis_id != thesis.id
                    or thesis.research_case_id != run.research_case_id
                    or link.review_state != "automatically_admitted"
                ):
                    raise ValueError("admitted evidence is outside the automatic run scope")
                admitted.append((link, thesis))

        # Acquisition jobs are terminal and their leases have been released before
        # this Case -> lifecycle mapping lock is acquired.
        for link, thesis in admitted:
            assignment = append_current_scope_evidence_assignment(
                self._session,
                case_id=run.research_case_id,
                evidence_link_id=link.id,
                factor_statement=thesis.statement,
                created_at=now,
            )
            if (
                assignment is None
                or assignment.disposition != "mapped"
                or assignment.factor_statement != thesis.statement
            ):
                raise ValueError(
                    "admitted evidence is not assignable to the current scope"
                )

    def _assess_result_tasks(
        self,
        run: ResearchRun,
        tasks: list[ResearchTask],
        *,
        allowed_evidence: dict[uuid.UUID, list[uuid.UUID]],
        frozen_scope: ValidatedAutomaticResearchScope,
        scope_version_id: uuid.UUID,
        research_job: Job,
        source_binding_fingerprint: tuple[tuple[uuid.UUID, uuid.UUID], ...],
    ) -> list[AIAssessment]:
        assessments: list[AIAssessment] = []
        generator = self._generator()
        for task in tasks:
            if task.task_type != "result":
                continue
            assessment = None
            if task.thesis_id is None or task.thesis_id not in allowed_evidence:
                raise ValueError("automatic result task thesis is outside evidence scope")
            allowed_ids = allowed_evidence[task.thesis_id]
            if task.status == "done" and isinstance(task.result, dict):
                try:
                    assessment = self._session.get(
                        AIAssessment,
                        uuid.UUID(str(task.result.get("assessment_id"))),
                    )
                except (TypeError, ValueError, AttributeError):
                    assessment = None
            if assessment is None:
                job_attempt = research_job.attempt
                job_claim_token = research_job.claim_token
                assessment = generator.generate(
                    task.thesis_id,
                    datetime.now(UTC),
                    self._session,
                    evidence_link_ids=allowed_ids,
                    before_persist=partial(
                        self._claim_assessment_output_slot,
                        run_id=run.id,
                        case_id=run.research_case_id,
                        job_id=research_job.id,
                        job_attempt=job_attempt,
                        job_claim_token=job_claim_token,
                        task_id=task.id,
                        thesis_id=task.thesis_id,
                        round=run.round,
                        frozen_scope=frozen_scope,
                        scope_version_id=scope_version_id,
                        evidence_link_ids=tuple(allowed_ids),
                        source_binding_fingerprint=source_binding_fingerprint,
                    ),
                )
                if assessment is None:
                    raise ValueError("automatic assessment was cancelled or stale")
                run = self._session.get(ResearchRun, run.id)
                task = self._session.get(ResearchTask, task.id)
                if run is None or task is None:
                    raise ValueError("automatic assessment output slot is stale")
                self._validate_assessment_binding(task, assessment, allowed_ids)
                task.result = {
                    "task_type": "result",
                    "assessment_id": str(assessment.id),
                    "conclusion": assessment.conclusion,
                    "gaps": list(assessment.gaps or []),
                }
                task.status, task.stage = "done", "completed"
                task.updated_at = datetime.now(UTC)
            else:
                self._validate_assessment_binding(task, assessment, allowed_ids)
            assessments.append(assessment)
        if not assessments:
            raise ValueError("automatic research current round has no result task")
        return assessments

    def _validate_assessment_binding(
        self,
        task: ResearchTask,
        assessment: AIAssessment,
        evidence_link_ids: list[uuid.UUID],
    ) -> None:
        if (
            assessment.displayed_as_provisional is not True
            or assessment.creator_type != "ai"
        ):
            raise ValueError("automatic assessment provenance is invalid")
        snapshot = self._session.get(EvidenceSnapshot, assessment.snapshot_id)
        expected_ids = [str(link_id) for link_id in evidence_link_ids]
        if (
            snapshot is None
            or snapshot.thesis_id != task.thesis_id
            or snapshot.evidence_link_ids != expected_ids
        ):
            raise ValueError(
                "automatic assessment thesis or evidence scope does not match its task"
            )

    def _claim_assessment_output_slot(
        self,
        *,
        run_id: uuid.UUID,
        case_id: uuid.UUID,
        job_id: uuid.UUID,
        job_attempt: int,
        job_claim_token: str | None,
        task_id: uuid.UUID,
        thesis_id: uuid.UUID,
        round: int,
        frozen_scope: ValidatedAutomaticResearchScope,
        scope_version_id: uuid.UUID,
        evidence_link_ids: tuple[uuid.UUID, ...],
        source_binding_fingerprint: tuple[tuple[uuid.UUID, uuid.UUID], ...],
    ) -> bool:
        with self._session.no_autoflush:
            case = self._session.scalar(
                select(ResearchCase)
                .where(ResearchCase.id == case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_run = self._session.scalar(
                select(ResearchRun)
                .where(ResearchRun.id == run_id)
                .where(ResearchRun.research_case_id == case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_job = self._session.scalar(
                select(Job)
                .where(Job.id == job_id)
                .where(Job.target_type == "research_run")
                .where(Job.target_id == run_id)
                .where(Job.research_case_id == case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_task = self._session.scalar(
                select(ResearchTask)
                .where(ResearchTask.id == task_id)
                .where(ResearchTask.run_id == run_id)
                .where(ResearchTask.research_case_id == case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            lifecycle = self._session.scalar(
                select(EventResearchLifecycle)
                .where(EventResearchLifecycle.research_case_id == case_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        if (
            case is None
            or current_run is None
            or current_job is None
            or current_task is None
            or lifecycle is None
            or lifecycle.active_run_id != run_id
            or current_run.status
            not in {"queued", "running", "waiting_for_sources"}
            or current_run.round != round
            or current_job.status
            not in {"queued", "running", "waiting_for_sources"}
            or current_job.cancel_requested
            or current_job.attempt != job_attempt
            or current_job.claim_token != job_claim_token
            or current_task.round != round
            or current_task.thesis_id != thesis_id
            or current_task.task_type != "result"
            or current_task.status != "queued"
        ):
            return False
        try:
            current_scope = load_automatic_research_scope(self._session, current_run)
            if current_scope != frozen_scope or current_scope.current_scope_id != scope_version_id:
                return False
            bindings = validate_automatic_source_bindings(
                self._session,
                current_run,
                current_scope,
                lock=True,
            )
            if bindings.fingerprint != source_binding_fingerprint:
                return False
            current_ids = self._allowed_evidence_by_thesis(
                current_run,
                scope=current_scope,
                scope_version_id=scope_version_id,
                job_ids=bindings.job_ids,
            ).get(thesis_id, [])
        except ValueError:
            return False
        return current_ids == list(evidence_link_ids)

    def _allowed_evidence_by_thesis(
        self,
        run: ResearchRun,
        *,
        scope: ValidatedAutomaticResearchScope,
        scope_version_id: uuid.UUID,
        job_ids: frozenset[uuid.UUID],
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        thesis_ids = scope.factor_ids
        result = {thesis_id: [] for thesis_id in thesis_ids}
        rows = self._session.execute(
            select(EvidenceLink.id, EvidenceLink.thesis_id)
            .join(
                AutomaticAdmissionDecision,
                AutomaticAdmissionDecision.id
                == EvidenceLink.automatic_admission_decision_id,
            )
            .join(
                AcquisitionJob,
                AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
            )
            .join(
                EventResearchScopeEvidenceAssignment,
                EventResearchScopeEvidenceAssignment.evidence_link_id
                == EvidenceLink.id,
            )
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .where(
                AcquisitionJob.research_run_id == run.id,
                AcquisitionJob.research_case_id == run.research_case_id,
                AcquisitionJob.id.in_(job_ids),
                AcquisitionJob.thesis_id == EvidenceLink.thesis_id,
                EventResearchScopeEvidenceAssignment.scope_version_id
                == scope_version_id,
                EventResearchScopeEvidenceAssignment.disposition == "mapped",
                EventResearchScopeEvidenceAssignment.factor_statement
                == Thesis.statement,
                EvidenceLink.review_state == "automatically_admitted",
                EvidenceLink.thesis_id.in_(thesis_ids),
            )
            .order_by(EvidenceLink.thesis_id, EvidenceLink.created_at, EvidenceLink.id)
        )
        for link_id, thesis_id in rows:
            result[thesis_id].append(link_id)
        return result

    def _generator(self) -> _AssessmentGeneratorProtocol:
        if self._assessment_generator is None:
            self._assessment_generator = AssessmentGenerator(
                self._client or LLMClient.from_env()
            )
        return self._assessment_generator

    def _create_next_round(self, run: ResearchRun, gaps: list[str]) -> None:
        next_round = run.round + 1
        scope = load_automatic_research_scope(self._session, run)
        scoped_thesis_ids = set(scope.factor_ids)
        expected_tasks = {
            (thesis_id, task_type)
            for thesis_id in scoped_thesis_ids
            for task_type in ("support", "contradict", "alternative", "result")
        }
        existing = list(
            self._session.scalars(
                select(ResearchTask).where(
                    ResearchTask.run_id == run.id,
                    ResearchTask.round == next_round,
                )
            )
        )
        if existing:
            actual_tasks = {
                (task.thesis_id, task.task_type)
                for task in existing
                if task.research_case_id == run.research_case_id
            }
            if len(existing) != len(expected_tasks) or actual_tasks != expected_tasks:
                raise ValueError("automatic replenishment task matrix is incomplete")
            run.round = next_round
            run.status = "queued"
            run.stage = "planning"
            run.updated_at = datetime.now(UTC)
            self._session.flush()
            return
        expected = self._expected_task_matrix(run=run, scope=scope)
        theses_by_id = {value[0].id: value[0] for value in expected.values()}
        theses = [theses_by_id[key] for key in sorted(theses_by_id, key=str)]
        gap_text = "；".join(gaps)
        labels = {
            "support": "寻找支持证据",
            "contradict": "寻找反方证据",
            "alternative": "寻找替代解释",
            "result": "形成研究结论",
        }
        for thesis in theses:
            for task_type in ("support", "contradict", "alternative", "result"):
                self._repository.create_task(
                    run_id=run.id,
                    research_case_id=run.research_case_id,
                    thesis_id=thesis.id,
                    task_type=task_type,
                    query=(
                        f"{labels[task_type]}: {thesis.statement}；"
                        f"待补证据：{gap_text}"
                    ),
                    round=next_round,
                )
        run.round = next_round
        run.status = "queued"
        run.stage = "planning"
        run.updated_at = datetime.now(UTC)
        # The dispatch lock refreshes the run from storage. Persist the new
        # round before reacquiring it so populate_existing cannot restore the
        # just-completed round and validate the wrong task matrix.
        self._session.flush()

    def _fail_no_usable_evidence(self, run: ResearchRun) -> str:
        run.status = "failed"
        run.stage = "failed"
        run.stop_reason = "no_usable_evidence"
        run.updated_at = datetime.now(UTC)
        lifecycle = self._session.get(EventResearchLifecycle, run.research_case_id)
        if lifecycle is not None and lifecycle.active_run_id == run.id:
            lifecycle.status = "exhausted"
            lifecycle.current_round = run.round
            lifecycle.status_summary = "自动研究未获得可用证据"
            lifecycle.current_gap = "未获得可自动准入的证据"
            lifecycle.next_human_action = None
            lifecycle.updated_at = datetime.now(UTC)
        ResearchRunEventRepository(self._session).append(
            run.id,
            stage="failed",
            status="failed",
            message="自动研究未获得可用证据",
            payload_json={"stop_reason": "no_usable_evidence"},
        )
        return "failed"

    def _expected_task_matrix(
        self,
        *,
        run: ResearchRun,
        scope: ValidatedAutomaticResearchScope,
    ) -> dict[
        tuple[uuid.UUID, uuid.UUID, EvidenceObjective],
        tuple[Thesis, AutomaticResearchFactorScope],
    ]:
        expected: dict[
            tuple[uuid.UUID, uuid.UUID, EvidenceObjective],
            tuple[Thesis, AutomaticResearchFactorScope],
        ] = {}
        for factor in scope.factors:
            thesis = self._session.get(Thesis, factor.thesis_id)
            if (
                thesis is None
                or thesis.research_case_id != run.research_case_id
                or thesis.statement != factor.statement
            ):
                raise ValueError(
                    "automatic research frozen scope thesis does not match the run case"
                )
            for raw_objective in factor.objectives:
                objective = EvidenceObjective(raw_objective)
                expected[(run.research_case_id, thesis.id, objective)] = (
                    thesis,
                    factor,
                )
        if not expected:
            raise ValueError("automatic research plan has no acquisition objectives")
        return expected
