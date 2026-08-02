"""Theme-tag command + theme read DTOs (横切主题 ThemeView).

The ThemeView is a pure aggregation projection over case-level effective
state. It never synthesizes a theme-level conclusion: every aggregate row
carries its provenance, and the response top level lists ``derived_from``
references so any number can be expanded back to cases, theses, theme roles
and disclosures.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from app.schemas.v1.common import HistoricalBasisDTO, V1Model
from app.schemas.v1.companies import AssessmentViewDTO, RoleReviewDTO


class UpdateThemeTagsRequest(V1Model):
    tags: list[str]


class ThemeTagsResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    case_id: uuid.UUID
    tags: list[str]
    events_appended: int


class ThemeListItemDTO(V1Model):
    tag: str
    case_count: int
    company_count: int
    thesis_count: int


class ThemeListResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    items: list[ThemeListItemDTO]


class ThemeCaseThesisDTO(V1Model):
    thesis_id: uuid.UUID
    statement: str
    title: str | None = None
    ai_assessment: AssessmentViewDTO | None = None
    review: RoleReviewDTO | None = None


class ThemeCaseDTO(V1Model):
    case_id: uuid.UUID
    case_title: str
    thesis_counts: dict[str, int]
    theses: list[ThemeCaseThesisDTO]


class ThemeCompanyRoleDTO(V1Model):
    company_id: uuid.UUID
    company_code: str
    company_name: str
    case_id: uuid.UUID | None = None
    case_title: str | None = None
    role: str
    scope: dict[str, Any]
    applicable_from: str | None = None
    applicable_to: str | None = None
    statement_id: uuid.UUID | None = None


class ThemeExposurePositionDTO(V1Model):
    fund_id: uuid.UUID
    fund_code: str
    fund_name: str
    stock_id: uuid.UUID
    stock_code: str
    stock_name: str
    weight: float
    report_period: str
    source: str


class DerivedFromDTO(V1Model):
    case_ids: list[uuid.UUID]
    thesis_ids: list[uuid.UUID]
    theme_role_ids: list[uuid.UUID]
    disclosure_ids: list[uuid.UUID]


class ThemeViewResponse(V1Model):
    schema_version: Literal["v1"] = "v1"
    basis: HistoricalBasisDTO
    tag: str
    cases: list[ThemeCaseDTO]
    company_roles: list[ThemeCompanyRoleDTO]
    fund_exposure: list[ThemeExposurePositionDTO]
    derived_from: DerivedFromDTO
