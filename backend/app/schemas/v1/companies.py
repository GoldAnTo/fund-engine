"""Company-centric read DTOs (公司研究 CompanyDossier).

The company dossier is an assembled read model: every field traces back to a
ledger row (theme role -> case/statement/span, judgment -> assessment/review,
holder -> disclosure). Nothing here is a company-level conclusion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from app.schemas.v1.common import CursorPage, HistoricalBasisDTO, V1Model


class CompanyListItemDTO(V1Model):
    id: uuid.UUID
    code: str
    name: str
    type: str
    stock_count: int
    theme_role_count: int
    latest_report_period: str | None = None


class CompanyListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    items: list[CompanyListItemDTO]
    page: CursorPage


class CompanyIdentityDTO(V1Model):
    id: uuid.UUID
    code: str
    name: str
    type: str
    created_at: str | None = None


class StockViewDTO(V1Model):
    id: uuid.UUID
    code: str
    name: str
    market: str


class ThemeRoleViewDTO(V1Model):
    id: uuid.UUID
    case_id: uuid.UUID | None = None
    case_title: str | None = None
    role: str
    scope: dict[str, Any]
    applicable_from: str | None = None
    applicable_to: str | None = None
    statement_id: uuid.UUID | None = None
    statement_text: str | None = None
    span_id: uuid.UUID | None = None
    document_version_id: uuid.UUID | None = None


class AssessmentViewDTO(V1Model):
    """The latest AI assessment at the cutoff, always visibly provisional."""

    id: uuid.UUID
    conclusion: str
    provisional: bool
    assessed_at: str | None = None


class RoleReviewDTO(V1Model):
    outcome: str
    conclusion: str | None = None
    reason: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


class RelatedThesisDTO(V1Model):
    thesis_id: uuid.UUID
    case_id: uuid.UUID
    case_title: str
    statement: str
    title: str | None = None
    ai_assessment: AssessmentViewDTO | None = None
    review: RoleReviewDTO | None = None


class ValuationViewDTO(V1Model):
    stock_id: uuid.UUID
    stock_code: str
    metric_name: str
    metric_value: float
    as_of_date: str
    source: str
    definition: str


class FundHolderDTO(V1Model):
    fund_id: uuid.UUID
    fund_code: str
    fund_name: str
    stock_id: uuid.UUID
    stock_code: str
    weight: float
    report_period: str
    published_at: str | None = None
    acquired_at: str | None = None
    source: str


class CompanyDossierResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    company: CompanyIdentityDTO
    stocks: list[StockViewDTO]
    theme_roles: list[ThemeRoleViewDTO]
    related_theses: list[RelatedThesisDTO]
    valuations: list[ValuationViewDTO]
    fund_holders: list[FundHolderDTO]
