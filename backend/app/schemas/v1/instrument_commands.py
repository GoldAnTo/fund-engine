"""Schemas for instrument command endpoints (companies, stocks, funds, holding
disclosures, theme roles)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.schemas.v1.common import V1Model


class CreateCompanyRequest(V1Model):
    code: str
    name: str
    type: str


class CompanyDTO(V1Model):
    id: uuid.UUID
    code: str
    name: str
    type: str
    created_at: datetime


class CreateStockRequest(V1Model):
    code: str
    name: str
    market: str


class StockDTO(V1Model):
    id: uuid.UUID
    company_id: uuid.UUID
    code: str
    name: str
    market: str
    created_at: datetime


class CreateFundRequest(V1Model):
    code: str
    name: str
    fund_type: str
    scale: Decimal | None = None
    establish_date: date | None = None
    management_company_id: uuid.UUID | None = None


class FundDTO(V1Model):
    id: uuid.UUID
    code: str
    name: str
    fund_type: str
    scale: Decimal | None = None
    establish_date: date | None = None
    management_company_id: uuid.UUID | None = None
    created_at: datetime


class CreateHoldingDisclosureRequest(V1Model):
    stock_id: uuid.UUID
    weight: Decimal
    report_period: date
    published_at: datetime
    source: str


class HoldingDisclosureDTO(V1Model):
    id: uuid.UUID
    fund_id: uuid.UUID
    stock_id: uuid.UUID
    weight: Decimal
    report_period: date
    published_at: datetime
    acquired_at: datetime
    source: str
    created_at: datetime


class CreateValuationSnapshotRequest(V1Model):
    as_of_date: date
    metric_name: str
    metric_value: Decimal
    source: str
    definition: str


class ValuationSnapshotDTO(V1Model):
    id: uuid.UUID
    stock_id: uuid.UUID
    as_of_date: date
    metric_name: str
    metric_value: Decimal
    source: str
    definition: str
    created_at: datetime


class CreateThemeRoleRequest(V1Model):
    role: str
    research_case_id: uuid.UUID | None = None
    scope: dict[str, Any] | None = None
    applicable_from: date | None = None
    applicable_to: date | None = None
    source_statement_id: uuid.UUID | None = None


class ThemeRoleDTO(V1Model):
    id: uuid.UUID
    company_id: uuid.UUID
    research_case_id: uuid.UUID | None = None
    role: str
    scope: dict[str, Any] | None = None
    applicable_from: date | None = None
    applicable_to: date | None = None
    source_statement_id: uuid.UUID | None = None
    created_at: datetime
