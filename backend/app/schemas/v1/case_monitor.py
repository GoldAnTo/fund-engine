"""V1 contracts for visible, versioned Case monitors."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model


MonitorSourceType = Literal[
    "licensed_provider", "company_disclosure", "uploaded_file", "pasted_snapshot"
]


class UpdateCaseMonitorRequest(V1Model):
    actor: str = Field(min_length=1, max_length=128)
    frequency: str = Field(min_length=1, max_length=64)
    factor_ids: list[str] = Field(min_length=1)
    allowed_source_types: list[MonitorSourceType] = Field(min_length=1)
    next_verification_event: str = Field(min_length=1)
    budget: int = Field(ge=1)
    change_reason: str = Field(min_length=1)


class CaseMonitorDTO(V1Model):
    id: str
    version: int
    status: str
    frequency: str
    factor_ids: list[str]
    allowed_source_types: list[MonitorSourceType]
    next_verification_event: str
    budget: int
    changed_by: str
    change_reason: str
    created_at: datetime


class LatestResearchRunDTO(V1Model):
    id: str
    status: str
    stage: str
    updated_at: datetime


class CaseMonitorDetailResponse(V1Model):
    monitor: CaseMonitorDTO | None
    latest_run: LatestResearchRunDTO | None
