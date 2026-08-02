"""Schemas for causal-chain command endpoints (steps and edges under a thesis)."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.v1.common import V1Model


class CreateCausalStepRequest(V1Model):
    description: str
    sequence: int


class CreatedCausalStepDTO(V1Model):
    id: uuid.UUID
    thesis_id: uuid.UUID
    description: str
    sequence: int
    created_at: datetime


class CreateCausalEdgeRequest(V1Model):
    source_step_id: uuid.UUID
    target_step_id: uuid.UUID
    rationale: str
    creator_type: str = "human"


class CreatedCausalEdgeDTO(V1Model):
    id: uuid.UUID
    source_step_id: uuid.UUID
    target_step_id: uuid.UUID
    rationale: str
    creator_type: str
    review_state: str
    created_at: datetime
