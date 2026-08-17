"""Governed source dispatch for one-click automatic research runs."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY
from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.domain.automatic_research import AUTOMATIC_SOURCE_JOB_TERMINAL
from app.domain.acquisition import (
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.event_research import EventResearchBrief
from app.models.ledger import AIAssessment, CaseTenantAdmission, EvidenceLink, Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.repositories.acquisition import AcquisitionRepository
from app.repositories.auto_research import AutoResearchRepository
from app.services.acquisition import AcquisitionModule
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
_PLAN_OBJECTIVES = frozenset(
    objective.value for objective, _role in _TASK_OBJECTIVES.values()
)


class AutomaticResearchPipeline:
    """Run the automatic workflow without introducing human review gates."""

    def __init__(
        self,
        session: Session,
        *,
        client: LLMClient | None = None,
        repository: AutoResearchRepository | None = None,
        assessment_generator: Any | None = None,
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
        tasks = self._round_tasks(run)
        source_tasks = [task for task in tasks if task.task_type != "result"]
        if not source_tasks or all(
            not isinstance(task.result, dict)
            or not task.result.get("acquisition_job_id")
            for task in source_tasks
        ):
            return self.dispatch_sources(run)

        jobs = self._linked_jobs(run, source_tasks)
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

        self._reconcile_sources(run, source_tasks, jobs)
        run.budget_used = self._acquisition_count(run)
        admitted_count = self._admitted_count(run)
        final_attempt = run.round >= run.max_rounds or run.budget_used >= run.budget
        if admitted_count == 0 and final_attempt:
            return self._fail_no_usable_evidence(run)

        assessments = self._assess_result_tasks(run, tasks)
        gaps = sorted(
            {
                str(gap).strip()
                for assessment in assessments
                for gap in (assessment.gaps or [])
                if str(gap).strip()
            }
        )
        scope = self._latest_scope(run)
        next_source_count = len(
            self._expected_task_matrix(
                run=run,
                scope=scope,
                plan_by_factor=self._frozen_plan(scope),
            )
        )
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
        scope = self._latest_scope(run)
        plan_by_factor = self._frozen_plan(scope)
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
        expected = self._expected_task_matrix(
            run=run,
            scope=scope,
            plan_by_factor=plan_by_factor,
        )
        actual: list[tuple[uuid.UUID, uuid.UUID, EvidenceObjective]] = []
        for task in current_tasks:
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
        if (
            not actual
            or len(actual) != len(expected)
            or set(actual) != set(expected)
        ):
            raise ValueError(
                "automatic research task matrix does not match the frozen evidence plan"
            )

        pending_requests: list[
            tuple[ResearchTask, AcquisitionRequest, EvidenceObjective]
        ] = []
        for task in current_tasks:
            assert task.thesis_id is not None
            objective, target_link_role = _TASK_OBJECTIVES[task.task_type]
            thesis, plan_item = expected[
                (run.research_case_id, task.thesis_id, objective)
            ]
            allowed_roles = frozenset(plan_item["allowed_source_roles"]) & frozenset(
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
                            f"automatic:{run.id}:{run.round}:"
                            f"{thesis.id}:{objective.value}"
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
        existing_count = self._acquisition_count(run)
        if existing_count + len(current_tasks) - already_bound > run.budget:
            raise ValueError("automatic research acquisition budget is exhausted")

        module = AcquisitionModule(self._session)
        principal = AcquisitionPrincipal(
            tenant_id=admission.tenant_id,
            actor="system:research-worker",
        )
        wrote_task_binding = False
        for task, request, objective in pending_requests:
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
        run.budget_used = self._acquisition_count(run)
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
                .where(ResearchTask.research_case_id == run.research_case_id)
                .where(ResearchTask.round == run.round)
                .order_by(ResearchTask.created_at, ResearchTask.id)
            )
        )
        if not tasks:
            raise ValueError("automatic research current round has no tasks")
        return tasks

    def _linked_jobs(
        self,
        run: ResearchRun,
        tasks: list[ResearchTask],
    ) -> dict[uuid.UUID, AcquisitionJob]:
        jobs: dict[uuid.UUID, AcquisitionJob] = {}
        for task in tasks:
            if not isinstance(task.result, dict):
                raise ValueError("automatic source task has no acquisition binding")
            try:
                job_id = uuid.UUID(str(task.result["acquisition_job_id"]))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise ValueError(
                    "automatic source task acquisition binding is invalid"
                ) from exc
            job = self._session.get(AcquisitionJob, job_id)
            if (
                job is None
                or job.research_run_id != run.id
                or job.research_case_id != run.research_case_id
                or job.thesis_id != task.thesis_id
            ):
                raise ValueError("automatic source task acquisition binding crosses scope")
            jobs[task.id] = job
        return jobs

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
    ) -> list[AIAssessment]:
        assessments: list[AIAssessment] = []
        generator = self._generator()
        for task in tasks:
            if task.task_type != "result":
                continue
            assessment = None
            if task.status == "done" and isinstance(task.result, dict):
                try:
                    assessment = self._session.get(
                        AIAssessment,
                        uuid.UUID(str(task.result.get("assessment_id"))),
                    )
                except (TypeError, ValueError, AttributeError):
                    assessment = None
            if assessment is None:
                if task.thesis_id is None:
                    raise ValueError("automatic result task has no thesis")
                assessment = generator.generate(
                    task.thesis_id,
                    datetime.now(UTC),
                    self._session,
                )
                if assessment is None:
                    raise ValueError("automatic assessment was cancelled")
                task.result = {
                    "task_type": "result",
                    "assessment_id": str(assessment.id),
                    "conclusion": assessment.conclusion,
                    "gaps": list(assessment.gaps or []),
                }
                task.status, task.stage = "done", "completed"
                task.updated_at = datetime.now(UTC)
            assessments.append(assessment)
        if not assessments:
            raise ValueError("automatic research current round has no result task")
        return assessments

    def _generator(self):
        if self._assessment_generator is None:
            self._assessment_generator = AssessmentGenerator(
                self._client or LLMClient.from_env()
            )
        return self._assessment_generator

    def _create_next_round(self, run: ResearchRun, gaps: list[str]) -> None:
        next_round = run.round + 1
        try:
            scoped_thesis_ids = {
                uuid.UUID(str(value)) for value in (run.scope_thesis_ids or [])
            }
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("automatic run thesis scope is invalid") from exc
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
        scope = self._latest_scope(run)
        expected = self._expected_task_matrix(
            run=run,
            scope=scope,
            plan_by_factor=self._frozen_plan(scope),
        )
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

    def _acquisition_count(self, run: ResearchRun) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(AcquisitionJob)
                .where(
                    AcquisitionJob.research_run_id == run.id,
                    AcquisitionJob.research_case_id == run.research_case_id,
                )
            )
            or 0
        )

    def _admitted_count(self, run: ResearchRun) -> int:
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(EvidenceLink)
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
                    AcquisitionJob.research_run_id == run.id,
                    AcquisitionJob.research_case_id == run.research_case_id,
                    EvidenceLink.review_state == "automatically_admitted",
                )
            )
            or 0
        )

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
        scope: dict,
        plan_by_factor: dict[str, dict[str, list[str]]],
    ) -> dict[
        tuple[uuid.UUID, uuid.UUID, EvidenceObjective],
        tuple[Thesis, dict[str, list[str]]],
    ]:
        run_scope = run.scope_thesis_ids
        factor_ids = scope.get("factor_ids")
        factor_statements = scope.get("factor_statements")
        if (
            not isinstance(run_scope, list)
            or not run_scope
            or not isinstance(factor_ids, list)
            or not factor_ids
            or not isinstance(factor_statements, list)
            or len(factor_ids) != len(factor_statements)
            or any(not isinstance(value, str) for value in factor_statements)
        ):
            raise ValueError("automatic research frozen scope is incomplete")
        try:
            run_ids = [uuid.UUID(str(value)) for value in run_scope]
            frozen_ids = [uuid.UUID(str(value)) for value in factor_ids]
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("automatic research frozen scope thesis ids are invalid") from exc
        if (
            len(run_ids) != len(set(run_ids))
            or len(frozen_ids) != len(set(frozen_ids))
            or len(factor_statements) != len(set(factor_statements))
            or set(run_ids) != set(frozen_ids)
            or set(factor_statements) != set(plan_by_factor)
        ):
            raise ValueError(
                "automatic research frozen scope factor ids/statements do not match its plan"
            )

        expected: dict[
            tuple[uuid.UUID, uuid.UUID, EvidenceObjective],
            tuple[Thesis, dict[str, list[str]]],
        ] = {}
        for thesis_id, factor_statement in zip(frozen_ids, factor_statements):
            thesis = self._session.get(Thesis, thesis_id)
            if (
                thesis is None
                or thesis.research_case_id != run.research_case_id
                or thesis.statement != factor_statement
            ):
                raise ValueError(
                    "automatic research frozen scope thesis does not match the run case"
                )
            plan_item = plan_by_factor[factor_statement]
            for raw_objective in plan_item["objectives"]:
                objective = EvidenceObjective(raw_objective)
                expected[(run.research_case_id, thesis.id, objective)] = (
                    thesis,
                    plan_item,
                )
        if not expected:
            raise ValueError("automatic research plan has no acquisition objectives")
        return expected

    def _latest_scope(self, run: ResearchRun) -> dict:
        event = self._session.scalar(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "scope")
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        if event is None or not isinstance(event.payload_json, dict):
            raise ValueError("automatic research scope event is missing")
        if event.payload_json.get("workflow_mode") != "automatic":
            raise ValueError("research run scope is not automatic")
        return event.payload_json

    def _frozen_plan(self, scope: dict) -> dict[str, dict[str, list[str]]]:
        plan = scope.get("automatic_evidence_plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
            raise ValueError("automatic evidence plan is missing")
        result: dict[str, dict[str, list[str]]] = {}
        for item in plan["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("factor"), str):
                raise ValueError("automatic evidence plan factor is invalid")
            factor = item["factor"]
            objectives = item.get("objectives")
            roles = item.get("allowed_source_roles")
            if (
                not isinstance(objectives, list)
                or any(not isinstance(value, str) for value in objectives)
                or len(objectives) != len(_PLAN_OBJECTIVES)
                or set(objectives) != _PLAN_OBJECTIVES
            ):
                raise ValueError(
                    "automatic evidence plan objective is invalid or objective missing"
                )
            if (
                not isinstance(roles, list)
                or any(not isinstance(value, str) for value in roles)
            ):
                raise ValueError("automatic evidence plan source roles are invalid")
            if factor in result:
                raise ValueError(
                    f"automatic evidence plan factor is duplicated: {factor}"
                )
            result[factor] = {
                "objectives": objectives,
                "allowed_source_roles": roles,
            }
        if not result:
            raise ValueError("automatic evidence plan has no factors")
        return result
