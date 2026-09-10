"""Narrow, private projections of authorized Gateway research content."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.v1.automatic_research import AutomaticResearchResultDTO
from app.schemas.v1.common import V1Model


class EvidenceSummaryDTO(V1Model):
    evidence_link_id: str
    task_id: str
    thesis_id: str
    thesis_statement: str
    statement: str
    document_version_id: str
    title: str
    source_authority: str
    source_url: str | None
    published_at: datetime | None
    observed_period: date | None
    relationship: str
    review_state: Literal["automatically_admitted"] = "automatically_admitted"


class EvidenceLocatorDTO(V1Model):
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=0)


class EvidenceDetailDTO(EvidenceSummaryDTO):
    conversation_id: str
    run_spec_id: str
    quote: str
    source_span_id: str
    locator: EvidenceLocatorDTO
    content_sha256: str
    acquired_at: datetime


QualityFlag = Literal[
    "mixed_data_periods",
    "unknown_data_period",
    "user_material_only",
    "no_primary_disclosure",
    "source_independence_unverified",
    "retrieval_direction_unverified",
]


class AssessmentReviewItemDTO(V1Model):
    assessment_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    thesis_id: str = Field(min_length=1)
    thesis_statement: str
    conclusion: Literal["supported", "contradicted", "insufficient_evidence"]
    rationale: str
    gaps: list[str] = Field(max_length=200)
    evidence_link_ids: list[str] = Field(min_length=1, max_length=200)
    quality_flags: list[QualityFlag] = Field(max_length=6)

    @model_validator(mode="after")
    def unique_members(self):
        if any(not value for value in self.evidence_link_ids):
            raise ValueError("review evidence IDs cannot be empty")
        for values in (self.evidence_link_ids, self.quality_flags):
            if len(values) != len(set(values)):
                raise ValueError("review members must be unique")
        return self


class AssessmentReviewDTO(V1Model):
    method_version: Literal["gateway-assessment-review/v1"] = (
        "gateway-assessment-review/v1"
    )
    state: Literal["available", "unavailable"]
    reason_code: Literal["lineage_unavailable", "evidence_list_truncated"] | None
    items: list[AssessmentReviewItemDTO] = Field(max_length=200)
    quality_flags: list[QualityFlag] = Field(max_length=6)

    @model_validator(mode="after")
    def valid_state(self):
        if self.state == "unavailable":
            if self.reason_code is None or self.items or self.quality_flags:
                raise ValueError("unavailable review cannot contain lineage")
        elif self.reason_code is not None or not self.items:
            raise ValueError("available review requires items and no reason")
        for field in ("assessment_id", "snapshot_id", "task_id", "thesis_id"):
            values = [getattr(item, field) for item in self.items]
            if len(values) != len(set(values)):
                raise ValueError("review IDs must be unique")
        if len(self.quality_flags) != len(set(self.quality_flags)):
            raise ValueError("review flags must be unique")
        evidence_ids = [
            value for item in self.items for value in item.evidence_link_ids
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("review evidence IDs must be unique")
        return self


class ResearchContentDTO(V1Model):
    conversation_id: str
    run_spec_id: str
    state: Literal["pending", "available", "withheld", "failed", "cancelled"]
    reason_code: (
        Literal[
            "result_pending",
            "result_not_authorized",
            "execution_failed",
            "execution_cancelled",
        ]
        | None
    )
    draft_id: str | None
    result: AutomaticResearchResultDTO | None
    assessment_review: AssessmentReviewDTO | None = None
    evidence: list[EvidenceSummaryDTO] = Field(max_length=200)
    total_evidence: int = Field(ge=0)
    truncated: bool
    warnings: list[str]
