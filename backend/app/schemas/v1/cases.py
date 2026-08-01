"""Case list and dossier v1 wire DTOs."""

from typing import Any, Literal

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


ReviewOutcome = Literal["confirmed", "modified", "rejected"]


class ReviewDecisionDTO(V1Model):
    outcome: ReviewOutcome
    conclusion: str | None
    reason: str
    reviewer: str
    reviewed_at: str


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
    # Falsifiable-proposition framing (新建研究); null for legacy theses.
    title: str | None = None
    observation_start: str | None = None
    observation_end: str | None = None
    support_condition: str | None = None
    falsification_condition: str | None = None
    next_verification_event: str | None = None
    creator_type: str = "human"
    review_state: str = "confirmed"


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
    review: ReviewDecisionDTO | None


class CausalStepDTO(V1Model):
    id: str
    sequence: int
    description: str


class AssessFailureDTO(V1Model):
    """Latest failed assess attempt for the focus thesis.

    Surfaced only when the failure is NEWER than the latest successful
    assessment (or no assessment exists) — e.g. a compliance refusal on
    the last rerun.  The refused text itself never reached the ledger;
    this is the audit-trail view of it.
    """

    model_version: str
    error: str
    failed_at: str


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
    assess_failure: AssessFailureDTO | None = None
    causal_chain: list[CausalStepDTO]
    evidence: dict[str, list[EvidenceRecordDTO]]
    competitive_explanations: list[str]
    gaps: list[str]
