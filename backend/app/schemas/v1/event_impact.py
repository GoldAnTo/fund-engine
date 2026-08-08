"""Wire contracts for the current-scope company impact trace."""
from __future__ import annotations

from pydantic import Field, field_validator

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

    @field_validator("reason", "reviewer", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        # Strip before Field's min/max validation: a whitespace-only request
        # is invalid and formatting padding never consumes the reviewer limit.
        return value.strip() if isinstance(value, str) else value


class ReviewImpactRelationResponse(V1Model):
    review_id: str
    relation_id: str
    outcome: str
