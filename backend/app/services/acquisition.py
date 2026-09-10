"""Authorized request/read seam for governed evidence acquisition."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY, AcquisitionQueryPlanner, SourcePolicy
from app.domain.acquisition import (
    AcquisitionCoverageSnapshot,
    AcquisitionEvidenceView,
    AcquisitionJobRef,
    AcquisitionJobView,
    AcquisitionWorkflowLedgerKey,
    AcquisitionWorkflowLedgerPage,
    AcquisitionWorkflowLedgerRecord,
    AcquisitionPrincipal,
    AcquisitionQueryPlanView,
    AcquisitionRequest,
    AcquisitionSearchOperationView,
    AdmittedEvidenceRef,
    WorkflowLedgerStatus,
)
from app.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.ledger import Thesis
from app.models.event_research import EventResearchScopeVersion
from app.models.operational import ResearchRun
from app.models.acquisition import AcquisitionJobEvent
from app.repositories.acquisition import AcquisitionRepository
from app.services.case_tenant_access import CaseTenantAccess


def _json_safe(value: Any) -> Any:
    """Canonicalize frozen contracts into deterministic JSON-compatible values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("persisted datetime must be timezone-aware")
        normalized = value.astimezone(UTC).isoformat()
        return normalized.removesuffix("+00:00") + "Z"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(child) for child in value), key=_sort_key)
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON-safe")


def _sort_key(value: Any) -> tuple[str, str]:
    return type(value).__name__, repr(value)


class AcquisitionModule:
    """Authorize, freeze, enqueue, and serve governed acquisition reads.

    The caller owns commit and rollback. Mutations delegated to the repository
    remain in the caller's transaction. The caller must commit
    before external work begins; this module never commits internally.
    """

    def __init__(
        self,
        session: Session,
        *,
        repository: AcquisitionRepository | None = None,
        policy: SourcePolicy = B_SCOPE_POLICY,
        planner: AcquisitionQueryPlanner | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or AcquisitionRepository(session)
        self._policy = policy
        self._planner = planner or AcquisitionQueryPlanner()
        self._case_access = CaseTenantAccess(session)

    def request(
        self,
        request: AcquisitionRequest,
        *,
        principal: AcquisitionPrincipal,
    ) -> AcquisitionJobRef:
        if principal.tenant_id != request.tenant_id:
            raise PermissionDeniedError(
                "principal tenant does not match request tenant"
            )
        self._case_access.require_case(request.case_id, principal.tenant_id)
        thesis = self._session.get(Thesis, request.thesis_id)
        if thesis is None or thesis.research_case_id != request.case_id:
            raise NotFoundError("thesis not found")
        research_run = self._session.get(ResearchRun, request.research_run_id)
        if research_run is None or research_run.research_case_id != request.case_id:
            raise NotFoundError("research run not found")
        scope_version = self._session.get(
            EventResearchScopeVersion, request.scope_version_id
        )
        if scope_version is None or scope_version.research_case_id != request.case_id:
            raise NotFoundError("event research scope version not found")
        if request.source_policy_version != self._policy.version:
            raise ValueError("request source policy version is not active")
        if request.planner_version != self._planner.version:
            raise ValueError("request planner version is not active")
        if not request.allowed_source_roles <= self._policy.allowed_source_roles:
            raise ValueError("request source roles exceed active policy roles")

        request_snapshot = _json_safe(request)
        policy_snapshot = _json_safe(self._policy)
        assert isinstance(request_snapshot, dict)
        assert isinstance(policy_snapshot, dict)

        existing = self._repository.by_idempotency(
            request.tenant_id, request.idempotency_key
        )
        if existing is not None:
            existing_plan = self._repository.query_plan_for_job(existing.id)
            if existing_plan is None:
                raise ConflictError("existing acquisition job has no frozen query plan")
            request_snapshot["query_plan_id"] = str(existing_plan.id)
            return self._repository.create_or_get_with_plan(
                tenant_id=request.tenant_id,
                research_case_id=request.case_id,
                thesis_id=request.thesis_id,
                research_run_id=request.research_run_id,
                scope_version_id=request.scope_version_id,
                goal_id=request.goal_id,
                acquisition_round=request.round,
                idempotency_key=request.idempotency_key,
                request_snapshot=request_snapshot,
                policy_snapshot=policy_snapshot,
                plan_id=existing_plan.id,
                planner_version=request.planner_version,
                ordered_queries=list(existing_plan.ordered_queries_json),
                previous_query_plan_id=request.previous_query_plan_id,
                expansion_trigger=(
                    request.expansion.trigger if request.expansion is not None else None
                ),
                diff_json=existing_plan.diff_json,
                creation_payload={"actor": principal.actor},
            )

        planned_queries = self._planner.plan(request, self._policy)
        ordered_queries = [
            {
                "adapter_key": query.adapter_key,
                "objective": query.objective.value,
                "query": query.query,
            }
            for query in planned_queries
        ]
        plan_id = uuid.uuid4()
        request_snapshot["query_plan_id"] = str(plan_id)
        diff_json = None
        expansion_trigger = None
        if request.expansion is not None:
            previous = self._repository.query_plan(request.previous_query_plan_id)
            if previous is None:
                raise NotFoundError("previous query plan not found")
            previous_queries = list(previous.ordered_queries_json)
            added_queries = [
                item for item in ordered_queries if item not in previous_queries
            ]
            removed_queries = [
                item for item in previous_queries if item not in ordered_queries
            ]
            expansion_trigger = request.expansion.trigger
            diff_json = {
                "added_queries": added_queries,
                "removed_queries": removed_queries,
                "reason": request.expansion.reason,
            }
        return self._repository.create_or_get_with_plan(
            tenant_id=request.tenant_id,
            research_case_id=request.case_id,
            thesis_id=request.thesis_id,
            research_run_id=request.research_run_id,
            scope_version_id=request.scope_version_id,
            goal_id=request.goal_id,
            acquisition_round=request.round,
            idempotency_key=request.idempotency_key,
            request_snapshot=request_snapshot,
            policy_snapshot=policy_snapshot,
            plan_id=plan_id,
            planner_version=request.planner_version,
            ordered_queries=ordered_queries,
            previous_query_plan_id=request.previous_query_plan_id,
            expansion_trigger=expansion_trigger,
            diff_json=diff_json,
            creation_payload={"actor": principal.actor},
        )

    def replay_existing(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        case_id: uuid.UUID,
        thesis_id: uuid.UUID,
        objective: str,
        principal: AcquisitionPrincipal,
    ) -> AcquisitionJobRef | None:
        """Authorize and validate an existing frozen HTTP command binding."""
        if principal.tenant_id != tenant_id:
            raise PermissionDeniedError(
                "principal tenant does not match request tenant"
            )
        existing = self._repository.by_idempotency(tenant_id, idempotency_key)
        if existing is None:
            return None
        self._authorize_job(existing.id, principal)
        snapshot = (
            existing.request_snapshot
            if isinstance(existing.request_snapshot, dict)
            else {}
        )
        policy_snapshot = (
            existing.policy_snapshot
            if isinstance(existing.policy_snapshot, dict)
            else {}
        )
        plan = self._repository.query_plan_for_job(existing.id)
        series = self._repository.series(plan.series_id) if plan is not None else None
        previous = (
            self._repository.query_plan(plan.previous_query_plan_id)
            if plan is not None and plan.previous_query_plan_id is not None
            else None
        )
        expansion = snapshot.get("expansion")
        ordered_queries = plan.ordered_queries_json if plan is not None else []
        ordered_adapter_keys = [
            item.get("adapter_key")
            for item in ordered_queries
            if isinstance(item, dict)
        ]
        if (
            existing.research_case_id != case_id
            or existing.thesis_id != thesis_id
            or snapshot.get("tenant_id") != tenant_id
            or snapshot.get("idempotency_key") != idempotency_key
            or snapshot.get("objective") != objective
            or plan is None
            or series is None
            or snapshot.get("query_plan_id") != str(plan.id)
            or snapshot.get("scope_version_id") != str(series.scope_version_id)
            or snapshot.get("research_run_id") != str(series.research_run_id)
            or snapshot.get("goal_id") != series.goal_id
            or snapshot.get("round") != plan.acquisition_round
            or plan.acquisition_job_id != existing.id
            or plan.goal_id != series.goal_id
            or plan.policy_version != snapshot.get("source_policy_version")
            or plan.policy_version != policy_snapshot.get("version")
            or plan.planner_version != snapshot.get("planner_version")
            or ordered_adapter_keys
            != list(policy_snapshot.get("enabled_adapter_keys") or [])
            or len(ordered_adapter_keys) != len(ordered_queries)
            or any(
                item.get("objective") != objective
                or not isinstance(item.get("query"), str)
                or not item["query"].strip()
                for item in ordered_queries
            )
            or (
                str(plan.previous_query_plan_id)
                if plan.previous_query_plan_id is not None
                else None
            )
            != snapshot.get("previous_query_plan_id")
            or (
                plan.acquisition_round > 1
                and (
                    previous is None
                    or previous.series_id != series.id
                    or previous.acquisition_round != plan.acquisition_round - 1
                    or not isinstance(expansion, dict)
                    or plan.expansion_trigger != expansion.get("trigger")
                    or not isinstance(plan.diff_json, dict)
                    or plan.diff_json.get("reason") != expansion.get("reason")
                )
            )
            or plan.frozen_inputs_json
            != {key: value for key, value in snapshot.items() if key != "query_plan_id"}
            or series.tenant_id != tenant_id
            or series.research_case_id != case_id
            or existing.research_run_id != series.research_run_id
            or series.thesis_id != thesis_id
        ):
            raise ConflictError(
                "idempotency key already identifies a different acquisition request"
            )
        return AcquisitionJobRef(id=existing.id, status=existing.status)

    def get(
        self,
        job_id: uuid.UUID,
        *,
        principal: AcquisitionPrincipal,
    ) -> AcquisitionJobView:
        self._authorize_job(job_id, principal)
        view = self._repository.get(job_id)
        if view is None:  # Handles a concurrent delete without disclosing it.
            raise NotFoundError("acquisition job not found")
        return view

    def verify_existing(
        self,
        request: AcquisitionRequest,
        *,
        expected_job_id: uuid.UUID,
        principal: AcquisitionPrincipal,
    ) -> AcquisitionJobView:
        """Return one fully verified immutable request/plan projection."""
        ref = self.replay_existing(
            tenant_id=request.tenant_id,
            idempotency_key=request.idempotency_key,
            case_id=request.case_id,
            thesis_id=request.thesis_id,
            objective=request.objective.value,
            principal=principal,
        )
        if ref is None or ref.id != expected_job_id:
            raise ConflictError(
                "checkpoint does not identify the canonical acquisition job"
            )
        existing = self._repository.get_record(expected_job_id)
        plan = self._repository.query_plan_for_job(expected_job_id)
        if existing is None or plan is None:
            raise NotFoundError("acquisition job not found")
        expected_snapshot = _json_safe(request)
        expected_policy = _json_safe(self._policy)
        assert isinstance(expected_snapshot, dict)
        assert isinstance(expected_policy, dict)
        expected_snapshot["query_plan_id"] = str(plan.id)
        if (
            existing.request_snapshot != expected_snapshot
            or existing.policy_snapshot != expected_policy
        ):
            raise ConflictError("frozen acquisition request binding changed")
        return self.get(expected_job_id, principal=principal)

    def events(
        self,
        job_id: uuid.UUID,
        *,
        principal: AcquisitionPrincipal,
    ) -> tuple[AcquisitionJobEvent, ...]:
        self._authorize_job(job_id, principal)
        return self._repository.events(job_id)

    def admitted_evidence(
        self,
        job_id: uuid.UUID,
        *,
        principal: AcquisitionPrincipal,
    ) -> tuple[AdmittedEvidenceRef, ...]:
        self._authorize_job(job_id, principal)
        return self._repository.admitted_evidence(job_id)

    def coverage_evidence(
        self,
        job_id: uuid.UUID,
        *,
        principal: AcquisitionPrincipal,
    ) -> tuple[AcquisitionEvidenceView, ...]:
        self._authorize_job(job_id, principal)
        return self._repository.coverage_evidence(job_id)

    def query_plan(
        self,
        job_id: uuid.UUID,
        *,
        principal: AcquisitionPrincipal,
    ) -> AcquisitionQueryPlanView:
        self._authorize_job(job_id, principal)
        view = self._repository.query_plan_view_for_job(job_id)
        if view is None:
            raise NotFoundError("acquisition query plan not found")
        return view

    def coverage_search_operations(
        self,
        job_id: uuid.UUID,
        *,
        principal: AcquisitionPrincipal,
    ) -> tuple[AcquisitionSearchOperationView, ...]:
        self._authorize_job(job_id, principal)
        return self._repository.coverage_search_operations(job_id)

    def coverage_snapshots(
        self,
        job_ids: tuple[uuid.UUID, ...],
        *,
        principal: AcquisitionPrincipal,
    ) -> dict[uuid.UUID, AcquisitionCoverageSnapshot]:
        """Authorize and batch-load immutable coverage inputs for locked reconcile."""
        ordered_ids = tuple(dict.fromkeys(job_ids))
        snapshots = self._repository.coverage_snapshots(ordered_ids)
        if set(snapshots) != set(ordered_ids):
            raise NotFoundError("acquisition job not found")
        case_ids: set[uuid.UUID] = set()
        for snapshot in snapshots.values():
            if snapshot.job.tenant_id != principal.tenant_id:
                raise NotFoundError("acquisition job not found")
            if snapshot.job.research_case_id is None:
                raise NotFoundError("acquisition job not found")
            case_ids.add(snapshot.job.research_case_id)
        for case_id in case_ids:
            self._case_access.require_case(case_id, principal.tenant_id)
        return {job_id: snapshots[job_id] for job_id in ordered_ids}

    def workflow_ledger(
        self,
        *,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        principal: AcquisitionPrincipal,
    ) -> tuple[AcquisitionWorkflowLedgerRecord, ...]:
        """Authorized, fixed-batch source-process projection for workflow UI."""
        self._case_access.require_case(case_id, principal.tenant_id)
        scope = self._session.get(EventResearchScopeVersion, scope_version_id)
        if scope is None or scope.research_case_id != case_id:
            raise NotFoundError("event research scope version not found")
        return self._repository.workflow_ledger(scope_version_id)

    def workflow_ledger_page(
        self,
        *,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        principal: AcquisitionPrincipal,
        status: WorkflowLedgerStatus | None,
        after: AcquisitionWorkflowLedgerKey | None,
        high_watermark: AcquisitionWorkflowLedgerKey | None,
        limit: int,
    ) -> AcquisitionWorkflowLedgerPage:
        """Authorize one bounded, immutable workflow-ledger snapshot page."""
        self._case_access.require_case(case_id, principal.tenant_id)
        scope = self._session.get(EventResearchScopeVersion, scope_version_id)
        if scope is None or scope.research_case_id != case_id:
            raise NotFoundError("event research scope version not found")
        return self._repository.workflow_ledger_page(
            scope_version_id,
            status=status,
            after=after,
            high_watermark=high_watermark,
            limit=limit,
        )

    def workflow_ledger_for_evidence_links(
        self,
        *,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        evidence_link_ids: tuple[uuid.UUID, ...],
        principal: AcquisitionPrincipal,
    ) -> tuple[AcquisitionWorkflowLedgerRecord, ...]:
        """Authorize exact provenance rows for report citations."""
        self._case_access.require_case(case_id, principal.tenant_id)
        scope = self._session.get(EventResearchScopeVersion, scope_version_id)
        if scope is None or scope.research_case_id != case_id:
            raise NotFoundError("event research scope version not found")
        return self._repository.workflow_ledger_for_evidence_links(
            scope_version_id,
            evidence_link_ids,
        )

    def _authorize_job(
        self, job_id: uuid.UUID, principal: AcquisitionPrincipal
    ) -> None:
        job = self._repository.get_record(job_id)
        if job is None or job.tenant_id != principal.tenant_id:
            raise NotFoundError("acquisition job not found")
        self._case_access.require_case(job.research_case_id, principal.tenant_id)
