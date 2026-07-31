"""Case list and dossier v1 wire DTOs."""

from typing import Any, Literal

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class CaseSummaryDTO(V1Model):
    id: str
    title: str
    topic: str
    created_by: str
    created_at: str
    updated_at: str


class ThesisSummaryDTO(V1Model):
    id: str
    statement: str
    created_by: str
    created_at: str


class EvidenceRecordDTO(V1Model):
    link_id: str
    statement_id: str
    statement_text: str | None
    statement_kind: str | None
    span_id: str | None
    verbatim_text: str | None
    locator: dict[str, Any] | None
    role: Literal["supports", "contradicts", "contextualizes"]
    reason: str
    scope: dict[str, Any]
    observed_period: str | None
    available_at: str
    review_state: str


class AssessmentDTO(V1Model):
    id: str
    thesis_id: str
    conclusion: Literal["supported", "contradicted", "insufficient_evidence"]
    rationale: str
    gaps: list[str]
    provisional: bool
    review: dict[str, Any] | None


class CausalStepDTO(V1Model):
    id: str
    sequence: int
    description: str


class CaseListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    items: list[CaseSummaryDTO]
    page: CursorPage


class DossierResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    case: CaseSummaryDTO
    theses: list[ThesisSummaryDTO]
    focus_thesis_id: str
    assessment: AssessmentDTO | None
    causal_chain: list[CausalStepDTO]
    evidence: dict[str, list[EvidenceRecordDTO]]
    competitive_explanations: list[str]
    gaps: list[str]
