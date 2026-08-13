"""Safe v1 wire DTOs for standalone governed acquisition jobs."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.domain.acquisition import EvidenceObjective
from app.schemas.v1.common import V1Model


AcquisitionStatus = Literal[
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
]
AcquisitionStage = Literal[
    "queued",
    "searching",
    "fetching",
    "freezing",
    "extracting",
    "admitting",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
]
AcquisitionGateName = Literal["source", "temporal", "locator", "semantic"]


class AcquisitionJobCreateRequest(V1Model):
    """The caller chooses research intent, never identity or source policy."""

    objective: EvidenceObjective


class AcquisitionJobAcceptedDTO(V1Model):
    id: uuid.UUID
    status: AcquisitionStatus


class AcquisitionCountersDTO(V1Model):
    references: int
    fetched: int
    frozen: int
    admitted: int
    exceptions: int


class AcquisitionErrorSummaryDTO(V1Model):
    code: str
    summary: str


class AcquisitionJobDetailDTO(V1Model):
    id: uuid.UUID
    status: AcquisitionStatus
    stage: AcquisitionStage
    attempt: int
    counters: AcquisitionCountersDTO
    retry_at: datetime | None
    error: AcquisitionErrorSummaryDTO | None


class AcquisitionJobEventPayloadDTO(V1Model):
    attempt: int | None = None
    adapter_key: str | None = None
    operation: str | None = None
    outcome: str | None = None
    reason_code: str | None = None
    retryable: bool | None = None


class AcquisitionJobEventDTO(V1Model):
    seq: int
    status: AcquisitionStatus
    stage: AcquisitionStage
    message: str
    payload: AcquisitionJobEventPayloadDTO
    created_at: datetime


class AcquisitionGateResultDTO(V1Model):
    passed: bool
    reason_code: str


class AcquisitionEvidenceDTO(V1Model):
    evidence_link_id: uuid.UUID
    source_statement_id: uuid.UUID
    document_version_id: uuid.UUID
    source_title: str
    source_url: str | None
    gate_results: dict[AcquisitionGateName, AcquisitionGateResultDTO]
    review_state: Literal["automatically_admitted"]


class AcquisitionExceptionDetailDTO(V1Model):
    adapter_key: str | None = None
    attempt_id: uuid.UUID | None = None
    candidate_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None
    external_record_id: str | None = None
    provider_status: int | str | None = None
    retrieval_artifact_id: uuid.UUID | None = None
    source_reference_id: uuid.UUID | None = None
    status: str | None = None
    outcome: str | None = None
    failed_gates: list[AcquisitionGateName] | None = None
    reason_codes: dict[AcquisitionGateName, str] | None = None
    gate_version: str | None = None
    policy_version: str | None = None


class AcquisitionExceptionDTO(V1Model):
    reason: str
    detail: AcquisitionExceptionDetailDTO
    created_at: datetime
