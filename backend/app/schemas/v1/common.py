"""Shared v1 wire schemas: base model, health, historical basis, cursor page, errors."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class V1Model(BaseModel):
    """Base for every v1 wire DTO. Rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(V1Model):
    service: str
    status: str
    schema_version: Literal["v1"] = "v1"


class HistoricalBasisDTO(V1Model):
    cutoff: datetime
    is_historical: bool
    ledger_high_watermark: str | None = None
    projection_built_at: datetime | None = None
    projection_schema_version: str | None = None


class CursorPage(V1Model):
    next_cursor: str | None = None
    has_more: bool = False


class ErrorBody(V1Model):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(V1Model):
    # Design 4.1: every response, including errors, carries schema_version.
    schema_version: Literal["v1"] = "v1"
    error: ErrorBody
