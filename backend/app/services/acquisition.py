"""Authorized request/read seam for governed evidence acquisition."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.domain.acquisition import (
    AcquisitionJobRef,
    AcquisitionJobView,
    AcquisitionPrincipal,
    AcquisitionRequest,
    AdmittedEvidenceRef,
)
from app.errors import NotFoundError, PermissionDeniedError
from app.models.ledger import Thesis
from app.models.operational import ResearchRun
from app.models.acquisition import AcquisitionJobEvent
from app.repositories.acquisition import AcquisitionRepository
from app.services.case_tenant_access import CaseTenantAccess


def _json_safe(value: Any) -> Any:
    """Canonicalize frozen contracts into deterministic JSON-compatible values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
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
    ) -> None:
        self._session = session
        self._repository = repository or AcquisitionRepository(session)
        self._policy = policy
        self._case_access = CaseTenantAccess(session)

    def request(
        self,
        request: AcquisitionRequest,
        *,
        principal: AcquisitionPrincipal,
    ) -> AcquisitionJobRef:
        if principal.tenant_id != request.tenant_id:
            raise PermissionDeniedError("principal tenant does not match request tenant")
        self._case_access.require_case(request.case_id, principal.tenant_id)
        thesis = self._session.get(Thesis, request.thesis_id)
        if thesis is None or thesis.research_case_id != request.case_id:
            raise NotFoundError("thesis not found")
        if request.research_run_id is not None:
            research_run = self._session.get(ResearchRun, request.research_run_id)
            if (
                research_run is None
                or research_run.research_case_id != request.case_id
            ):
                raise NotFoundError("research run not found")
        if request.source_policy_version != self._policy.version:
            raise ValueError("request source policy version is not active")
        if not request.allowed_source_roles <= self._policy.allowed_source_roles:
            raise ValueError("request source roles exceed active policy roles")

        request_snapshot = _json_safe(request)
        policy_snapshot = _json_safe(self._policy)
        assert isinstance(request_snapshot, dict)
        assert isinstance(policy_snapshot, dict)
        return self._repository.create_or_get(
            tenant_id=request.tenant_id,
            research_case_id=request.case_id,
            thesis_id=request.thesis_id,
            research_run_id=request.research_run_id,
            idempotency_key=request.idempotency_key,
            request_snapshot=request_snapshot,
            policy_snapshot=policy_snapshot,
            creation_payload={"actor": principal.actor},
        )

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

    def _authorize_job(
        self, job_id: uuid.UUID, principal: AcquisitionPrincipal
    ) -> None:
        job = self._repository.get_record(job_id)
        if job is None or job.tenant_id != principal.tenant_id:
            raise NotFoundError("acquisition job not found")
        self._case_access.require_case(job.research_case_id, principal.tenant_id)
