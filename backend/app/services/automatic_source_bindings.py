"""Validate the complete frozen source-task and acquisition-job provenance."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.acquisition import AcquisitionJob
from app.models.operational import ResearchRun, ResearchTask
from app.services.automatic_research_scope import ValidatedAutomaticResearchScope


_TASK_BINDINGS = {
    "support": ("support", "supports"),
    "contradict": ("contradict", "contradicts"),
    "alternative": ("alternative_explanation", "contextualizes"),
    "alternative_explanation": (
        "alternative_explanation",
        "contextualizes",
    ),
    "intake_material": ("support", "supports"),
}
@dataclass(frozen=True)
class ValidatedAutomaticSourceBindings:
    tasks_by_id: dict[uuid.UUID, ResearchTask]
    jobs_by_task_id: dict[uuid.UUID, AcquisitionJob]
    job_ids: frozenset[uuid.UUID]
    fingerprint: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    external_job_ids: frozenset[uuid.UUID]
    material_job_ids: frozenset[uuid.UUID]

    def jobs_for_round(self, round_number: int) -> dict[uuid.UUID, AcquisitionJob]:
        return {
            task_id: self.jobs_by_task_id[task_id]
            for task_id, task in self.tasks_by_id.items()
            if task.round == round_number and task_id in self.jobs_by_task_id
        }


def validate_automatic_source_bindings(
    session: Session,
    run: ResearchRun,
    scope: ValidatedAutomaticResearchScope,
    *,
    allow_unbound_current_round: bool = False,
    lock: bool = False,
) -> ValidatedAutomaticSourceBindings:
    """Fail closed unless every source task and run job has one exact binding."""
    if not isinstance(run.round, int) or run.round < 1:
        raise ValueError("automatic source binding round is invalid")
    expected = {
        (round_number, run.research_case_id, factor.thesis_id, objective)
        for round_number in range(1, run.round + 1)
        for factor in scope.factors
        for objective in factor.objectives
    }
    material_document_id = scope.material_document_version_id
    if scope.input_kind == "material":
        assert material_document_id is not None
        expected.update(
            (1, run.research_case_id, thesis_id, "intake_material")
            for thesis_id in scope.factor_ids
        )
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
            or task.thesis_id not in scope.factor_ids
            or task.round < 1
            or task.round > run.round
        ):
            raise ValueError("automatic source task matrix crosses the frozen run")
        objective, _target_role = binding
        if task.task_type == "intake_material":
            objective = "intake_material"
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
    for task in tasks:
        task_unbound = (
            not isinstance(task.result, dict)
            or not task.result.get("acquisition_job_id")
        )
        if allow_unbound_current_round and task.round == run.round and task_unbound:
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
        is_material = task.task_type == "intake_material"
        expected_key = (
            f"automatic-material:{run.id}:{task.thesis_id}:{material_document_id}"
            if is_material
            else f"automatic:{run.id}:{task.round}:{task.thesis_id}:{objective}"
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
            or snapshot.get("acquisition_kind")
            != ("intake_material" if is_material else "external_gap")
            or snapshot.get("document_version_id")
            != (str(material_document_id) if is_material else None)
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
        external_job_ids=frozenset(
            job.id for task_id, job in jobs_by_task_id.items()
            if tasks_by_id[task_id].task_type != "intake_material"
        ),
        material_job_ids=frozenset(
            job.id for task_id, job in jobs_by_task_id.items()
            if tasks_by_id[task_id].task_type == "intake_material"
        ),
    )
