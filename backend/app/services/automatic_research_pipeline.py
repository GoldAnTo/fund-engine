"""Governed source dispatch for one-click automatic research runs."""
from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY
from app.domain.acquisition import (
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.models.event_research import EventResearchBrief
from app.models.ledger import CaseTenantAdmission, Thesis
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.services.acquisition import AcquisitionModule
from app.services.case_monitor import ResearchRunEventRepository


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
    """Translate a frozen automatic scope into governed acquisition jobs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def dispatch_sources(self, run: ResearchRun) -> str:
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
        pending_requests: list[
            tuple[ResearchTask, AcquisitionRequest, EvidenceObjective]
        ] = []
        for task in current_tasks:
            mapping = _TASK_OBJECTIVES.get(task.task_type)
            if mapping is None:
                raise ValueError(
                    f"invalid automatic evidence objective task: {task.task_type}"
                )
            if task.thesis_id is None:
                raise ValueError("automatic acquisition task has no thesis")
            thesis = self._session.get(Thesis, task.thesis_id)
            if thesis is None or thesis.research_case_id != run.research_case_id:
                raise ValueError(
                    "automatic acquisition task thesis is outside the run case"
                )
            plan_item = plan_by_factor.get(thesis.statement)
            if plan_item is None:
                raise ValueError(
                    f"automatic evidence plan factor missing: {thesis.statement}"
                )
            objective, target_link_role = mapping
            objectives = plan_item["objectives"]
            if objective.value not in objectives:
                raise ValueError(
                    f"automatic evidence plan objective missing: {objective.value}"
                )
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
                wrote_task_binding = True

        run.status = "waiting_for_sources"
        run.stage = "retrieve"
        if wrote_task_binding:
            ResearchRunEventRepository(self._session).append(
                run.id,
                stage="retrieve",
                status="waiting",
                message="等待受控证据采集任务完成",
                payload_json={"round": run.round},
            )
        return "waiting_for_sources"

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
                or not objectives
                or any(not isinstance(value, str) for value in objectives)
                or not set(objectives) <= _PLAN_OBJECTIVES
            ):
                raise ValueError("automatic evidence plan objective is invalid")
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
