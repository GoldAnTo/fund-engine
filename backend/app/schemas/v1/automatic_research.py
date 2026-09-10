"""Public v1 contract for one-click automatic research."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.v1.common import V1Model


class AutomaticResearchStartRequest(V1Model):
    input: str = Field(min_length=1, max_length=100_000)

    @field_validator("input", mode="before")
    @classmethod
    def trim_input(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AutomaticResearchUploadedStartRequest(V1Model):
    """Optional human prompt accompanying one uploaded original."""

    input: str | None = Field(default=None, max_length=100_000)

    @field_validator("input", mode="before")
    @classmethod
    def trim_input(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AutomaticResearchStartResponse(V1Model):
    case_id: str
    run_id: str
    status: Literal["queued"]


class AutomaticResearchStageDTO(V1Model):
    key: Literal["acquire", "parse", "admit", "analyze", "conclude"]
    label: str
    status: Literal["pending", "running", "completed", "failed"]
    summary: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AutomaticResearchSourceDTO(V1Model):
    title: str | None
    url: str | None
    role: str
    review_state: Literal["automatically_admitted"]


class AutomaticResearchResultDTO(V1Model):
    label: Literal["系统生成，未经人工审核"]
    human_reviewed: Literal[False]
    conclusion: str
    key_findings: list[str]
    counter_evidence: list[str]
    limitations: list[str]
    sources: list[AutomaticResearchSourceDTO]


class AutomaticResearchStatsDTO(V1Model):
    source_count: int = Field(ge=0)
    admitted_evidence_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)


class AutomaticResearchNarrativeDTO(V1Model):
    current_action: str
    completed_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    next_action: str
    elapsed_seconds: int = Field(ge=0)
    estimated_remaining_seconds_min: int | None = Field(default=None, ge=0)
    estimated_remaining_seconds_max: int | None = Field(default=None, ge=0)


class AutomaticResearchActivityDetailDTO(V1Model):
    occurred_at: datetime
    work_item: str
    internal_status: str


class AutomaticResearchActivityDTO(V1Model):
    label: str
    count: int = Field(ge=1)
    technical_details: list[AutomaticResearchActivityDetailDTO] = Field(min_length=1)


class AutomaticResearchExceptionDTO(V1Model):
    reason: str
    count: int = Field(ge=1)
    impact: str
    system_action: str


class AutomaticResearchFactorDTO(V1Model):
    statement: str
    classification: Literal["key", "secondary", "pending", "excluded"]
    ranking_reason: str
    support_count: int = Field(ge=0)
    counter_evidence_count: int = Field(ge=0)
    evidence_gap: str | None


class AutomaticResearchViewDTO(V1Model):
    case_id: str
    run_id: str
    title: str
    status: Literal["queued", "running", "completed", "failed"]
    stages: list[AutomaticResearchStageDTO] = Field(min_length=5, max_length=5)
    stats: AutomaticResearchStatsDTO
    narrative: AutomaticResearchNarrativeDTO
    activities: list[AutomaticResearchActivityDTO]
    exceptions: list[AutomaticResearchExceptionDTO]
    factors: list[AutomaticResearchFactorDTO]
    failure_reason: str | None
    result: AutomaticResearchResultDTO | None
