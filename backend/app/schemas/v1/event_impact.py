"""Wire contracts for the current-scope company impact trace."""
from __future__ import annotations

from pydantic import Field

from app.schemas.v1.common import V1Model


class EventImpactTraceDTO(V1Model):
    scope_version: int
    as_of: str | None = None
    factors: list[dict]
    alternatives: list[dict]
    progress: dict


class ReviewImpactRelationRequest(V1Model):
    outcome: str = Field(pattern="^(accepted|rejected|needs_more)$")
    reason: str = Field(min_length=1)
    reviewer: str = Field(min_length=1, max_length=128)


class ReviewImpactRelationResponse(V1Model):
    review_id: str
    relation_id: str
    outcome: str
