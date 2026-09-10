"""Strict professional outputs and owner-facing team commands."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.v1.common import V1Model

ProfessionalRole = Literal['industry', 'finance', 'strategy', 'quality']


class TeamProgressDTO(V1Model):
    run_spec_id: str
    revision: int = Field(ge=1)
    event_sequence: int = Field(ge=0)
    status: Literal['active', 'paused', 'cancelled']


class ProfessionalCitation(V1Model):
    evidence_link_id: UUID
    quote: str = Field(min_length=1, max_length=8000, description='必须逐字复制同一evidence_link_id对应的quote字段中的连续原文；不能引用URL、标题、期间等元数据字段，也不能改写或拼接原文。')


class ProfessionalFinding(V1Model):
    statement: str = Field(min_length=1, max_length=4000)
    basis: Literal['supported', 'contradicted', 'uncertain']
    citations: list[ProfessionalCitation] = Field(max_length=20)

    @model_validator(mode='after')
    def require_grounding(self):
        if self.basis != 'uncertain' and not self.citations:
            raise ValueError('a definite finding needs a frozen citation')
        ids = [citation.evidence_link_id for citation in self.citations]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate citation')
        return self


class ProfessionalCheck(V1Model):
    kind: Literal['citations', 'periods', 'units', 'source_independence', 'counter_evidence', 'completeness']
    status: Literal['pass', 'warning', 'blocker']
    detail: str = Field(min_length=1, max_length=2000)
    task_ids: list[UUID] = Field(max_length=10)


class ProfessionalResult(V1Model):
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[ProfessionalFinding] = Field(max_length=24)
    gaps: list[str] = Field(max_length=24)
    checks: list[ProfessionalCheck] = Field(max_length=20)
    limitations: list[str] = Field(max_length=16)

    @model_validator(mode='after')
    def require_substance(self):
        if not self.findings and not self.gaps:
            raise ValueError('a result requires findings or explicit evidence gaps')
        for item in self.gaps + self.limitations:
            if not item.strip() or len(item) > 2000:
                raise ValueError('invalid result explanation')
        return self


class TeamMessageRequest(V1Model):
    text: str = Field(min_length=1, max_length=20_000)
    recipient: Literal['team', 'industry', 'finance', 'strategy', 'quality']
    expected_revision: int = Field(ge=0, strict=True)


class TeamCommandRequest(V1Model):
    kind: Literal['start', 'pause', 'resume', 'cancel', 'retry']
    expected_revision: int = Field(ge=0, strict=True)
    task_id: UUID | None = None


class TeamReviewRequest(V1Model):
    expected_revision: int = Field(ge=1, strict=True)
    output_ids: list[UUID] = Field(min_length=4, max_length=4)
    decision: Literal['approved', 'changes_requested']
    comment: str = Field(min_length=1, max_length=4000)


class ProfessionalAttemptDTO(V1Model):
    call_id: str = Field(max_length=64)
    attempt: int = Field(ge=1)
    operation: str = Field(max_length=128)
    provider: str = Field(max_length=128)
    requested_model: str = Field(max_length=128)
    model: str | None = Field(max_length=128)
    provider_request_id: str | None = Field(max_length=256)
    finish_reason: str | None = Field(max_length=64)
    prompt_tokens: int | None = Field(ge=0)
    completion_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    usage_status: str = Field(max_length=32)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    outcome: str = Field(max_length=64)
    retryable: bool
    retry_delay_seconds: float | None = Field(ge=0, allow_inf_nan=False)
    repaired: bool = False


class ProfessionalOutputDTO(V1Model):
    id: UUID
    content: ProfessionalResult
    evidence_ids: list[UUID]
    dependency_output_ids: list[UUID]
    created_at: datetime


class ProfessionalTaskDTO(V1Model):
    id: UUID
    role: ProfessionalRole
    revision: int = Field(ge=1)
    status: Literal['queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled']
    reason_code: str | None = Field(max_length=64)
    attempt: int = Field(ge=0)
    instruction: str = Field(max_length=20_000)
    dependency_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
    output_state: Literal['none', 'available', 'withheld']
    output: ProfessionalOutputDTO | None
    attempts: list[ProfessionalAttemptDTO]


class ProfessionalReviewDTO(V1Model):
    id: UUID
    revision: int
    decision: Literal['approved', 'changes_requested']
    comment: str = Field(max_length=4000)
    reviewed_by: str = Field(max_length=128)
    output_ids: list[UUID]
    created_at: datetime


class ResearchTeamDTO(V1Model):
    conversation_id: UUID
    run_spec_id: UUID
    status: Literal['not_started', 'active', 'paused', 'cancelled']
    revision: int = Field(ge=0)
    event_sequence: int = Field(ge=0)
    tasks: list[ProfessionalTaskDTO]
    reviews: list[ProfessionalReviewDTO]


class TeamActionReceiptDTO(V1Model):
    request_id: UUID
    conversation_id: UUID
    run_spec_id: UUID
    revision: int = Field(ge=1)
    task_ids: list[UUID]
