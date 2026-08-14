"""Public, deliberately redacted research-preparation API contracts."""
from __future__ import annotations

import uuid
from typing import Any, Literal
from pydantic import Field
from app.schemas.v1.common import V1Model

class ClaimDecisionDTO(V1Model):
    candidate_id: uuid.UUID
    outcome: Literal["confirmed", "modified", "rejected"]
    reason: str = Field(min_length=1, max_length=2000)
    normalized_text: str | None = None
class ConfirmClaimsRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    decisions: list[ClaimDecisionDTO] = Field(min_length=1)
class ConfirmProtocolRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    draft_sequence: int = Field(ge=1)
    edits: dict[str, object] = Field(default_factory=dict)
class AuthorizeEvidencePlanRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
    plan_sequence: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)
class RetryResearchPreparationRequest(V1Model):
    revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=128)
class PreparationStepDTO(V1Model):
    state: str
    review_state: str | None = None
    artifact_sequence: int | None = None
class PreparationArtifactDTO(V1Model):
    sequence: int
    payload: dict[str, Any]
    state: str
    context_fingerprint: str | None = None
class PreparationInitialMaterialDTO(V1Model):
    document_version_id: uuid.UUID
    title: str | None = None
    parse_state: str
class PreparationProgressDTO(V1Model):
    completed_steps: int = Field(ge=0, le=3)
    total_steps: int = Field(ge=1, le=3)
    current_step: str | None = None
    failed_step: str | None = None
class ResearchPreparationDTO(V1Model):
    case_id: uuid.UUID
    case_title: str
    initial_material: PreparationInitialMaterialDTO | None = None
    progress: PreparationProgressDTO
    revision: int
    status: str
    research_run_id: uuid.UUID | None = None
    system: dict[str, PreparationStepDTO]
    review: dict[str, PreparationStepDTO]
    next_attempt_at: str | None = None
    last_error_message: str | None = None
    artifacts: dict[str, PreparationArtifactDTO | None]
    authorized_evidence_plan: dict[str, Any] | None = None
class ResearchPreparationEventDTO(V1Model):
    seq: int
    type: str
    step: str | None = None
    message: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str
class ResearchPreparationEventsResponse(V1Model):
    items: list[ResearchPreparationEventDTO]
    next_after_seq: int | None = None
