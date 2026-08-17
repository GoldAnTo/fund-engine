"""Public v1 contract for one-click automatic research."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model


class AutomaticResearchStartRequest(V1Model):
    input: str = Field(min_length=1, max_length=100_000)


class AutomaticResearchStartResponse(V1Model):
    case_id: str
    run_id: str
    status: Literal["queued"] = "queued"


class AutomaticResearchStageDTO(V1Model):
    key: Literal["acquire", "parse", "admit", "analyze", "conclude"]
    label: str
    status: Literal["pending", "running", "completed", "failed"]
    summary: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AutomaticResearchSourceDTO(V1Model):
    title: str | None = None
    url: str | None = None
    role: str
    review_state: Literal["automatically_admitted"] = "automatically_admitted"


class AutomaticResearchResultDTO(V1Model):
    label: Literal["系统生成，未经人工审核"] = "系统生成，未经人工审核"
    human_reviewed: Literal[False] = False
    conclusion: str
    key_findings: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[AutomaticResearchSourceDTO] = Field(default_factory=list)


class AutomaticResearchStatsDTO(V1Model):
    source_count: int = Field(ge=0)
    admitted_evidence_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)


class AutomaticResearchExceptionDTO(V1Model):
    reason: str
    stage: Literal["acquire", "parse", "admit", "analyze", "conclude"]
    count: int = Field(ge=1)


class AutomaticResearchViewDTO(V1Model):
    case_id: str
    run_id: str
    title: str
    status: Literal["queued", "running", "completed", "failed"]
    stages: list[AutomaticResearchStageDTO] = Field(min_length=5, max_length=5)
    stats: AutomaticResearchStatsDTO
    recent_activity: list[str] = Field(default_factory=list)
    exceptions: list[AutomaticResearchExceptionDTO] = Field(default_factory=list)
    failure_reason: str | None = None
    result: AutomaticResearchResultDTO | None = None
