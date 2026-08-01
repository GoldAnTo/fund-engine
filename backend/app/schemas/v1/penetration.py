"""Penetration v1 wire DTOs (prototype 五层画布 / 行业案例反向穿透)."""
from __future__ import annotations

from app.schemas.v1.common import V1Model


class ExposurePositionDTO(V1Model):
    stock_id: str
    stock_code: str
    stock_name: str
    weight: float
    report_period: str
    pe_ttm: float | None
    pb: float | None


class FundExposureDTO(V1Model):
    """One fund's aggregate exposure to the case's theme stocks."""

    fund_id: str
    fund_code: str
    fund_name: str
    theme_exposure: float
    positions: list[ExposurePositionDTO]


class FundExposureResponse(V1Model):
    """正向穿透: 主题/案件 → 命中基金（按主题暴露度排序）."""

    case_id: str
    as_of: str
    funds: list[FundExposureDTO]


class ThemeHitDTO(V1Model):
    case_id: str
    role: str


class CompositionPositionDTO(V1Model):
    stock_id: str
    stock_code: str
    stock_name: str
    weight: float
    report_period: str
    pe_ttm: float | None
    pb: float | None
    theme_hits: list[ThemeHitDTO]


class FundCompositionResponse(V1Model):
    """反向穿透: 这只基金「真正装了什么」——持仓 + 命中的主题."""

    fund_id: str
    fund_code: str
    fund_name: str
    as_of: str
    positions: list[CompositionPositionDTO]
