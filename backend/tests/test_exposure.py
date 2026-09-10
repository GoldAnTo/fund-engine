from datetime import date
from decimal import Decimal


def test_theme_exposure_uses_holding_disclosure_not_latest_portfolio(
    exposure_service, fund, mapped_stock, holding_disclosure
):
    exposure = exposure_service.for_fund(fund.id, as_of=date(2026, 6, 30))
    assert exposure.theme_weight == Decimal("0.082")
    assert exposure.report_period == date(2026, 3, 31)


def test_future_holding_disclosure_is_hidden_from_historical_as_of(
    exposure_service, fund, future_disclosure
):
    exposure = exposure_service.for_fund(fund.id, as_of=date(2026, 6, 30))
    assert future_disclosure.id not in {row.disclosure_id for row in exposure.rows}


def test_latest_report_period_wins_per_stock(
    exposure_service, fund, mapped_stock, holding_disclosure, instrument_repository
):
    earlier = instrument_repository.add_holding_disclosure(
        fund_id=fund.id,
        stock_id=mapped_stock.id,
        weight=Decimal("0.050"),
        report_period=date(2025, 12, 31),
        published_at=date(2026, 1, 25),
        source="fund-report-2025Q4",
    )
    exposure = exposure_service.for_fund(fund.id, as_of=date(2026, 6, 30))
    assert earlier.id not in {row.disclosure_id for row in exposure.rows}
    assert holding_disclosure.id in {row.disclosure_id for row in exposure.rows}
    assert exposure.theme_weight == Decimal("0.082")


def test_stock_without_theme_role_excluded_from_theme_weight(
    exposure_service, fund, company, holding_disclosure, instrument_repository
):
    unmapped_stock = instrument_repository.add_stock(
        company_id=company.id, code="688999", name="Unmapped Co", market="SSE"
    )
    instrument_repository.add_holding_disclosure(
        fund_id=fund.id,
        stock_id=unmapped_stock.id,
        weight=Decimal("0.300"),
        report_period=date(2026, 3, 31),
        published_at=date(2026, 4, 22),
        source="fund-report-2026Q1",
    )
    exposure = exposure_service.for_fund(fund.id, as_of=date(2026, 6, 30))
    assert unmapped_stock.id not in {row.stock_id for row in exposure.rows}
    assert exposure.theme_weight == Decimal("0.082")
