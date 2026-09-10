"""Reauthorized operational progress; never copy native messages or payloads."""
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.acquisition import AcquisitionAttempt, AutomaticAdmissionDecision
from app.models.ledger import EvidenceLink
from app.models.operational import ResearchRun
from app.models.research_gateway import ResearchRunSpec
from app.models.source_governance import SourceContract
from app.schemas.v1.research_gateway import (
    GatewayExecutionCountsDTO,
    GatewayExecutionDTO,
    GatewayExecutionTaskDTO,
)
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.research_gateway_artifacts import (
    _require_frozen_authority,
    validated_gateway_source_bindings,
)
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.source_admission import source_contract_is_active

_STAGES = frozenset({"queued", "planning", "retrieve", "analyze", "assessing",
                     "conclude", "complete", "failed", "cancelled"})
_PROVIDERS = frozenset({"gildata", "sse", "szse"})


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def project_execution(session: Session, spec: ResearchRunSpec, run: ResearchRun, *,
                      authorized_evidence_ids: frozenset[UUID], has_authorized_draft: bool) -> GatewayExecutionDTO:
    """Caller already established private owner access and the conversation lock."""
    stage = "retrieve" if run.stage == "waiting_for_sources" else run.stage
    result = GatewayExecutionDTO(
        projection_state="unavailable", stage=stage if stage in _STAGES else "unknown",
        updated_at=_utc(run.updated_at), reason_code="execution_state_unavailable",
        next_action="check_execution", worker_state="unknown", worker_last_seen_at=None, tasks=[],
    )
    try:
        scope = load_automatic_research_scope(session, run)
        _require_frozen_authority(session, spec, run, scope)
        if run.round < 1:
            if run.status == "queued":
                return result.model_copy(update={"projection_state": "available", "reason_code": None,
                                                 "next_action": "wait_for_execution"})
            return result
        bindings = validated_gateway_source_bindings(session, spec, run, scope)
        subject_missing = False
        for job in bindings.jobs_by_task_id.values():
            entities = job.request_snapshot.get("entity_names", ())
            if entities is None:
                entities = ()
            if (not isinstance(entities, (list, tuple))
                    or any(not isinstance(entity, str) for entity in entities)):
                raise ValueError("Gateway acquisition research subject is invalid")
            subject_missing = subject_missing or not any(entity.strip() for entity in entities)
        if bindings.material_job_ids:
            contract = session.scalar(select(SourceContract).where(
                SourceContract.document_version_id == scope.material_document_version_id))
            if contract is None or not contract.allow_display or not source_contract_is_active(contract):
                return result.model_copy(update={"reason_code": "source_policy_blocked"})
        # Admission is a currently visible evidence count, not a historical
        # counter that survives source display-right expiry or lineage failure.
        admitted_by_job = Counter(session.scalars(select(AutomaticAdmissionDecision.job_id)
            .join(EvidenceLink, EvidenceLink.automatic_admission_decision_id == AutomaticAdmissionDecision.id)
            .where(EvidenceLink.id.in_(authorized_evidence_ids),
                   AutomaticAdmissionDecision.job_id.in_(bindings.job_ids)))) if authorized_evidence_ids else Counter()
        providers_by_job: dict = {}
        if bindings.external_job_ids:
            jobs_by_id = {job.id: job for job in bindings.jobs_by_task_id.values()}
            for attempt in session.scalars(select(AcquisitionAttempt).where(
                    AcquisitionAttempt.job_id.in_(bindings.external_job_ids),
                    AcquisitionAttempt.operation.in_(("search", "fetch")))):
                allowed = jobs_by_id[attempt.job_id].policy_snapshot.get("enabled_adapter_keys", [])
                if attempt.adapter_key in _PROVIDERS and attempt.adapter_key in allowed:
                    providers_by_job.setdefault(attempt.job_id, set()).add(attempt.adapter_key)
        tasks = []
        for task in sorted(bindings.tasks_by_id.values(), key=lambda item: (_utc(item.created_at), str(item.id))):
            job = bindings.jobs_by_task_id.get(task.id)
            if job is None:
                continue
            material = task.task_type == "intake_material"
            tasks.append(GatewayExecutionTaskDTO(
                task_id=task.id, task_type=task.task_type, status=job.status, stage=job.stage,
                source_kind="intake_material" if material else "external_sources",
                source_name="用户提供材料" if material else "合规外部来源",
                providers=sorted(providers_by_job.get(job.id, ())), attempt=job.attempt,
                updated_at=_utc(job.updated_at), retry_at=_utc(job.retry_at) if job.retry_at else None,
                counts=GatewayExecutionCountsDTO(discovered=job.reference_count, fetched=job.fetched_count,
                    frozen=job.frozen_count, admitted=min(job.admitted_count, admitted_by_job[job.id]),
                    exceptions=job.exception_count),
            ))
        # This is explicitly subsystem liveness: heartbeat host IDs do not have
        # the same identity format as acquisition lease owners.
        health = WorkerHeartbeatService(session).status(worker_kind="acquisition")
        state = {"available": "online", "stale": "offline", "unavailable": "unknown"}[health["status"]]
        reason = None
        action = "wait_for_execution"
        if subject_missing:
            reason, action = "research_subject_missing", "check_execution"
        elif run.status == "succeeded":
            if has_authorized_draft:
                action = "review_result"
            else:
                reason, action = "execution_state_unavailable", "check_execution"
        elif run.status == "cancelled":
            action = "none"
        elif run.status == "failed":
            reason, action = "native_execution_failed", "check_execution"
        elif state == "offline" and any(task.status in {"queued", "running", "retry_wait"} for task in tasks):
            reason, action = "worker_unavailable", "check_execution"
        elif any(task.status == "retry_wait" for task in tasks):
            reason, action = "source_unavailable", "wait_for_retry"
        elif tasks and all(task.status in {"failed", "cancelled"} for task in tasks):
            reason, action = "source_unavailable", "check_execution"
        return GatewayExecutionDTO(
            projection_state="available", stage=result.stage,
            updated_at=max([result.updated_at, *(task.updated_at for task in tasks)]),
            reason_code=reason, next_action=action, tasks=tasks, worker_state=state,
            worker_last_seen_at=health["last_seen_at"],
        )
    except (ValueError, TypeError, KeyError, ValidationError):
        return result
