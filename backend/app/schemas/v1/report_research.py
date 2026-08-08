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
