"""Dossier wire contract. Identity is exclusively taken from authentication."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = Annotated[str, Field(min_length=1, max_length=12000)]
Focus = Annotated[str, Field(min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and (
            not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value)
        ):
            raise ValueError("text must be nonempty and contain no control characters")
        return value


class StudyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    symbol: str | None = Field(default=None, min_length=1, max_length=64)
    market: Literal["CN", "HK", "US", "other"]
    focus: Focus


class ActivityCreate(StrictModel):
    kind: Literal["baseline", "event", "material", "refresh"]
    text: Text


class LinkCreate(StrictModel):
    conversation_id: UUID


class RevisionCreate(StrictModel):
    activity_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    note: Focus


class MonitorCreate(StrictModel):
    status: Literal["active", "paused"]
    frequency: Literal["daily", "weekly"]
    focus: Focus


class RetryRequest(StrictModel):
    pass


class StudyDTO(StudyCreate):
    id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime


class ActivityDTO(BaseModel):
    id: UUID
    study_id: UUID
    kind: Literal["baseline", "event", "material", "refresh", "linked"]
    title: str
    text: str
    status: Literal["queued", "starting", "running", "completed", "failed", "blocked"]
    conversation_id: UUID | None
    run_spec_id: UUID | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class RevisionDTO(BaseModel):
    id: UUID
    version: int
    activity_id: UUID
    conversation_id: UUID
    run_spec_id: UUID
    team_revision: int
    note: str
    created_at: datetime


class MonitorDTO(MonitorCreate):
    version: int
    next_due_at: datetime | None


class StudyListDTO(BaseModel):
    studies: list[StudyDTO]


class StudyDetailDTO(BaseModel):
    study: StudyDTO
    activities: list[ActivityDTO]
    revisions: list[RevisionDTO]
    monitor: MonitorDTO | None
