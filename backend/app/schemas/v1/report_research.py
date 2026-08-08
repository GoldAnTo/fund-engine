"""Wire contracts for report-first research intake.

The PDF endpoint deliberately accepts the raw ``application/pdf`` body.  This
keeps file upload available without a second multipart parser dependency and
ensures the exact uploaded bytes are the bytes frozen into the evidence ledger.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.v1.common import V1Model


ReportInputKind = Literal["pdf_upload", "pasted_text", "web_content"]
ReportResearchState = Literal["ready_to_extract", "needs_text_or_pages"]


class CreateReportResearchRequest(V1Model):
    input_kind: Literal["pasted_text", "web_content"]
    title: str = Field(min_length=1, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=2000)
    content: str = Field(min_length=1)
    created_by: str = Field(default="report-research-system", min_length=1, max_length=128)

    @model_validator(mode="after")
    def normalize_strings(self) -> "CreateReportResearchRequest":
        self.title = self.title.strip()
        self.publisher = self.publisher.strip() if self.publisher else None
        self.source_url = self.source_url.strip() if self.source_url else None
        if not self.title:
            raise ValueError("title must not be blank")
        # Validation may look at a trimmed view, but the original user text
        # is ledger evidence and must never be rewritten before hashing.
        if not self.content.strip():
            raise ValueError("content must not be blank")
        return self


class ReportResearchDocumentDTO(V1Model):
    id: uuid.UUID
    kind: Literal["research_report"] = "research_report"
    input_kind: ReportInputKind
    title: str
    publisher: str | None
    published_at: datetime | None
    source_url: str
    parse_state: Literal["success", "partial", "failed"]


class ReportResearchCaseDTO(V1Model):
    id: uuid.UUID
    title: str


class ReportResearchCreatedResponse(V1Model):
    case: ReportResearchCaseDTO
    document: ReportResearchDocumentDTO
    state: ReportResearchState
    needs_text_or_pages: bool = False
    source_statement_ids: list[uuid.UUID]


ReportWikiNodeKind = Literal[
    "report_claim", "company", "evidence", "market_window", "fund"
]
ReportWikiNodeStatus = Literal[
    "report_claim", "verified", "candidate", "rejected", "market_observation"
]
ReportFactorClassification = Literal["key", "alternative", "evidence_gap"]
ReportWikiCompanyKind = Literal["listed_a_share", "unlisted_transmission"]
ReportWikiFundCoverage = Literal["complete", "partial", "stale", "insufficient"]


class ReportWikiAssetMappingDTO(V1Model):
    """Safe asset-readiness projection; intentionally contains no ledger ids."""

    company_kind: ReportWikiCompanyKind | None = None
    a_share_codes: list[str] = Field(default_factory=list)
    fund_coverage: ReportWikiFundCoverage | None = None
    computable: bool | None = None


class ReportWikiNodeDTO(V1Model):
    """One source-addressable node in a report's read-only Wiki graph.

    Company names that cannot safely resolve to a ledger company deliberately
    use a stable synthetic identifier.  Their source locator is still the
    exact report span that asserted the name.
    """

    id: str
    kind: ReportWikiNodeKind
    label: str
    status: ReportWikiNodeStatus
    source_locator: str | None = None
    asset_mapping: ReportWikiAssetMappingDTO | None = None
    scope_version: int


class ReportWikiEdgeDTO(V1Model):
    id: str
    source_id: str
    target_id: str
    kind: str
    status: ReportWikiNodeStatus
    relation_id: uuid.UUID | None = None
    source_locator: str | None = None
    scope_version: int


class ReportFactorDTO(V1Model):
    claim_id: uuid.UUID
    relation_id: uuid.UUID | None = None
    statement: str
    classification: ReportFactorClassification
    components: dict[str, bool]
    explanation: str


class ReportResearchScopeDTO(V1Model):
    version: int
    document_id: uuid.UUID
    visibility_cutoff_at: datetime
    research_question: str
    factor_selection: list[str]
    evidence_plan: list[str]
    selected_claim_ids: list[uuid.UUID]
    selected_relation_ids: list[uuid.UUID]


class ReportWikiGraphDTO(V1Model):
    """The selected report-document scope, never a mixed history view."""

    research_case_id: uuid.UUID
    scope_version: int
    scope: ReportResearchScopeDTO
    document_id: uuid.UUID
    nodes: list[ReportWikiNodeDTO]
    edges: list[ReportWikiEdgeDTO]
    factors: list[ReportFactorDTO]


class ReportEmbedWikiNodeDTO(V1Model):
    """One opaque external graph node; never a ledger or source identifier."""

    id: str
    kind: ReportWikiNodeKind
    label: str
    status: ReportWikiNodeStatus


class ReportEmbedWikiEdgeDTO(V1Model):
    """One opaque external graph edge; relation provenance stays internal."""

    id: str
    source_id: str
    target_id: str
    kind: str
    status: ReportWikiNodeStatus


class ReportEmbedFactorDTO(V1Model):
    """Safe factor classification without claim or relation identifiers."""

    classification: ReportFactorClassification
    components: dict[str, bool]
    explanation: str


class ReportEmbedWikiGraphDTO(V1Model):
    """External, read-only report Wiki projection with no dossier identifiers."""

    nodes: list[ReportEmbedWikiNodeDTO]
    edges: list[ReportEmbedWikiEdgeDTO]
    factors: list[ReportEmbedFactorDTO]
