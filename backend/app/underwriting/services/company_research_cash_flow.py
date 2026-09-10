"""The shared group FCFF identity, independent of forecast or approval policy."""

from decimal import Decimal, localcontext

from app.underwriting.domain.company_research import COMPANY_RESEARCH_DECIMAL_PRECISION


def unlevered_free_cash_flow(
    *,
    operating_income: Decimal,
    cash_tax_rate: Decimal,
    depreciation: Decimal,
    capex: Decimal,
    working_capital_change: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
        return +(
            operating_income * (Decimal(1) - cash_tax_rate)
            + depreciation
            - capex
            - working_capital_change
        )
