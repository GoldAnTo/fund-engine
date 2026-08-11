"""V1 contracts for transparent Case-scoped fund disclosure replenishment."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.schemas.v1.common import V1Model


class SaveFundDisclosureSyncConfigRequest(V1Model):
    actor: str = Field(min_length=1, max_length=128)
    fund_codes: list[str] = Field(min_length=1, max_length=50)
    frequency: Literal["weekly", "monthly"]
    report_period: date
    allow_display: bool = False
    change_reason: str = Field(min_length=1)


class FundDisclosureSyncSuggestionDTO(V1Model):
    fund_code: str
    fund_name: str
    matching_stock_codes: list[str]
    latest_report_period: date


class FundDisclosureSyncConfigDTO(V1Model):
    id: str
    version: int
    frequency: Literal["weekly", "monthly"]
    report_period: date | None
    fund_codes: list[str]
    stock_codes: list[str]
    allow_display: bool
    changed_by: str
    change_reason: str
    created_at: datetime


class FundDisclosureSyncRunEventDTO(V1Model):
    seq: int
    stage: str
    status: str
    message: str
    payload: dict
    created_at: datetime


class FundDisclosureSyncRunDTO(V1Model):
    id: str
    config_version_id: str
    trigger: Literal["manual", "scheduled", "retry"]
    report_period: date | None
    fund_codes: list[str]
    stock_codes: list[str]
    allow_display: bool
    status: str
    created_at: datetime
    events: list[FundDisclosureSyncRunEventDTO]


class ActiveFundDisclosureSyncRunDTO(V1Model):
    run_id: str
    case_id: str
    case_title: str
    trigger: Literal["manual", "scheduled", "retry"]
    status: str
    stage: str
    message: str
    fund_codes: list[str]
    stock_codes: list[str]
    updated_at: datetime


class ActiveFundDisclosureSyncRunsResponse(V1Model):
    items: list[ActiveFundDisclosureSyncRunDTO]


class FundDisclosureSyncDetailResponse(V1Model):
    suggestions: list[FundDisclosureSyncSuggestionDTO]
    manual_code_fallback: bool
    effective_config: FundDisclosureSyncConfigDTO | None
    config_history: list[FundDisclosureSyncConfigDTO]
    next_scheduled_at: datetime | None
    runs: list[FundDisclosureSyncRunDTO]
