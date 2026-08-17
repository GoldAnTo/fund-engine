"""Validate the complete frozen source-task and acquisition-job provenance."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.acquisition import AcquisitionJob
from app.models.ledger import Thesis
from app.models.operational import ResearchRun, ResearchTask


_TASK_BINDINGS = {
    "support": ("support", "supports"),
    "contradict": ("contradict", "contradicts"),
    "alternative": ("alternative_explanation", "contextualizes"),
    "alternative_explanation": (
        "alternative_explanation",
        "contextualizes",
    ),
}
_EXPECTED_OBJECTIVES = frozenset(
    {"support", "contradict", "alternative_explanation"}
)


@dataclass(frozen=True)
class ValidatedAutomaticSourceBindings:
    tasks_by_id: dict[uuid.UUID, ResearchTask]
    jobs_by_task_id: dict[uuid.UUID, AcquisitionJob]
    job_ids: frozenset[uuid.UUID]
    fingerprint: tuple[tuple[uuid.UUID, uuid.UUID], ...]

    def jobs_for_round(self, round_number: int) -> dict[uuid.UUID, AcquisitionJob]:
        return {
            task_id: self.jobs_by_task_id[task_id]
            for task_id, task in self.tasks_by_id.items()
            if task.round == round_number and task_id in self.jobs_by_task_id
        }


def validate_automatic_source_bindings(
    session: Session,
    run: ResearchRun,
    frozen_scope: dict,
    *,
    allow_unbound_current_round: bool = False,
    lock: bool = False,
) -> ValidatedAutomaticSourceBindings:
    """Fail closed unless every source task and run job has one exact binding."""
    if not isinstance(run.round, int) or run.round < 1:
        raise ValueError("automatic source binding round is invalid")
    try:
        run_thesis_ids = [
            uuid.UUID(str(value)) for value in (run.scope_thesis_ids or [])
        ]
        frozen_thesis_ids = [
            uuid.UUID(str(value)) for value in frozen_scope["factor_ids"]
        ]
        factor_statements = list(frozen_scope["factor_statements"])
        plan_items = frozen_scope["automatic_evidence_plan"]["items"]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("automatic source binding frozen scope is invalid") from exc
    if (
        not run_thesis_ids
        or run_thesis_ids != frozen_thesis_ids
        or len(frozen_thesis_ids) != len(factor_statements)
        or len(frozen_thesis_ids) != len(set(frozen_thesis_ids))
        or len(factor_statements) != len(set(factor_statements))
        or not isinstance(plan_items, list)
    ):
        raise ValueError("automatic source binding frozen scope is inconsistent")

    theses_by_id: dict[uuid.UUID, Thesis] = {}
    for thesis_id, statement in zip(frozen_thesis_ids, factor_statements):
        thesis = session.get(Thesis, thesis_id)
        if (
            thesis is None
            or thesis.research_case_id != run.research_case_id
            or thesis.statement != statement
        ):
            raise ValueError("automatic source binding thesis crosses its frozen scope")
        theses_by_id[thesis_id] = thesis

    objectives_by_statement: dict[str, frozenset[str]] = {}
    for item in plan_items:
        if not isinstance(item, dict) or not isinstance(item.get("factor"), str):
            raise ValueError("automatic source binding evidence plan is invalid")
        factor = item["factor"]
        objectives = item.get("objectives")
        if (
            factor in objectives_by_statement
            or factor not in factor_statements
        ):
            raise ValueError("automatic source binding evidence plan factor is invalid")
        if (
            not isinstance(objectives, list)
            or any(not isinstance(value, str) for value in objectives)
            or len(objectives) != len(_EXPECTED_OBJECTIVES)
            or set(objectives) != _EXPECTED_OBJECTIVES
        ):
            raise ValueError("automatic source binding evidence plan is invalid")
        objectives_by_statement[factor] = frozenset(objectives)
    if set(objectives_by_statement) != set(factor_statements):
        raise ValueError("automatic source binding evidence plan is incomplete")

    expected = {
        (round_number, run.research_case_id, thesis_id, objective)
        for round_number in range(1, run.round + 1)
        for thesis_id, statement in zip(frozen_thesis_ids, factor_statements)
        for objective in objectives_by_statement[statement]
    }
    task_query = (
        select(ResearchTask)
        .where(ResearchTask.run_id == run.id)
        .where(ResearchTask.task_type != "result")
        .order_by(ResearchTask.round, ResearchTask.created_at, ResearchTask.id)
    )
    if lock:
        task_query = task_query.with_for_update().execution_options(
            populate_existing=True
        )
    tasks = list(session.scalars(task_query))
    actual: list[tuple[int, uuid.UUID, uuid.UUID, str]] = []
    for task in tasks:
        binding = _TASK_BINDINGS.get(task.task_type)
        if (
            binding is None
            or task.research_case_id != run.research_case_id
            or task.thesis_id not in theses_by_id
            or task.round < 1
            or task.round > run.round
        ):
            raise ValueError("automatic source task matrix crosses the frozen run")
        objective, _target_role = binding
        actual.append(
            (task.round, task.research_case_id, task.thesis_id, objective)
        )
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("automatic source task matrix is not exact for every round")

    job_query = select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    if lock:
        job_query = job_query.with_for_update().execution_options(
            populate_existing=True
        )
    run_jobs = list(session.scalars(job_query))
    jobs_by_id = {job.id: job for job in run_jobs}
    if len(jobs_by_id) != len(run_jobs):
        raise ValueError("automatic source acquisition job identity is duplicated")

    tasks_by_id = {task.id: task for task in tasks}
    jobs_by_task_id: dict[uuid.UUID, AcquisitionJob] = {}
    bound_job_ids: set[uuid.UUID] = set()
    current_tasks = [task for task in tasks if task.round == run.round]
    current_unbound = all(
        not isinstance(task.result, dict)
        or not task.result.get("acquisition_job_id")
        for task in current_tasks
    )
    allow_current_unbound = allow_unbound_current_round and current_unbound

    for task in tasks:
        if allow_current_unbound and task.round == run.round:
            continue
        if not isinstance(task.result, dict):
            raise ValueError("automatic source task acquisition binding is missing")
        objective, target_role = _TASK_BINDINGS[task.task_type]
        try:
            job_id = uuid.UUID(str(task.result["acquisition_job_id"]))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(
                "automatic source task acquisition binding is invalid"
            ) from exc
        if job_id in bound_job_ids:
            raise ValueError("automatic source task acquisition binding is duplicated")
        bound_job_ids.add(job_id)
        job = jobs_by_id.get(job_id)
        expected_key = (
            f"automatic:{run.id}:{task.round}:{task.thesis_id}:{objective}"
        )
        snapshot = job.request_snapshot if job is not None else None
        if (
            job is None
            or job.research_run_id != run.id
            or job.research_case_id != run.research_case_id
            or job.thesis_id != task.thesis_id
            or not isinstance(snapshot, dict)
            or task.result.get("objective") != objective
            or snapshot.get("research_run_id") != str(run.id)
            or snapshot.get("case_id") != str(run.research_case_id)
            or snapshot.get("thesis_id") != str(task.thesis_id)
            or snapshot.get("round") != task.round
            or snapshot.get("objective") != objective
            or snapshot.get("target_link_role") != target_role
            or job.idempotency_key != expected_key
            or snapshot.get("idempotency_key") != expected_key
        ):
            raise ValueError(
                "automatic source task acquisition binding does not match its frozen task"
            )
        jobs_by_task_id[task.id] = job

    if set(jobs_by_id) != bound_job_ids:
        raise ValueError("automatic source acquisition job set contains an unbound job")
    return ValidatedAutomaticSourceBindings(
        tasks_by_id=tasks_by_id,
        jobs_by_task_id=jobs_by_task_id,
        job_ids=frozenset(bound_job_ids),
        fingerprint=tuple(
            (task_id, jobs_by_task_id[task_id].id)
            for task_id in sorted(jobs_by_task_id, key=str)
        ),
    )
