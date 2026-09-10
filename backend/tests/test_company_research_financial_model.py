"""Independent arithmetic and boundary tests; small numbers are synthetic."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.services.company_research_financial_model import (
    calculate_financial_model,
    candidate_financial_inputs,
    financial_model_market_snapshot,
)

CUTOFF = datetime(2026, 1, 1, tzinfo=UTC)


def baseline():
    values = {
        "fy2025_group_revenue": "400",
        "h1_2025_group_revenue": "180",
        "h1_2026_group_revenue": "220",
        "h1_2026_group_capex": "20",
        "h1_2026_group_ppe_depreciation": "5",
    }
    return {
        "content_hash": "a" * 64,
        "facts": [
            {"fact_key": key, "value": value, "scope": "group", "unit": "USD_million"}
            for key, value in values.items()
        ],
        "research_gaps": [
            {"key": "cloud_workload", "label": "云工作负载", "detail": "未披露"}
        ],
    }


def inputs():
    value = candidate_financial_inputs(baseline(), CUTOFF)
    for index, scenario in enumerate(value["scenarios"]):
        scenario["paths"] = {
            "revenue": [str(1000 + index * 100)] * 5,
            "operating_margin": ["0.2"] * 5,
            "cash_tax_rate": ["0.25"] * 5,
            "depreciation": ["10"] * 5,
            "capex": ["30"] * 5,
            "working_capital_change": ["5"] * 5,
        }
    value.update(
        discount_rate="0.1",
        terminal_growth="0.02",
        terminal_roic="0.1",
        first_year_cash_flow_fraction="1",
    )
    return value


def market():
    return {
        "capital_structure": {
            "cash": "20",
            "investments": "10",
            "debt": "15",
            "minority_interest": "1",
            "pension_liabilities": "0",
            "other_adjustments": "4",
            "diluted_shares": "100",
        },
        "usd_cny_rate": "7",
        "securities": [
            {
                "security_external_key": key,
                "conversion_ratio": "1",
                "adr_ratio": "1",
                "dividend_rights_per_unit": "1",
                "market_price_usd": price,
            }
            for key, price in (("NASDAQ:GOOG", "15"), ("NASDAQ:GOOGL", "14"))
        ],
    }


def test_fcff_terminal_reinvestment_and_price_gap_are_independently_reconciled():
    result = calculate_financial_model(inputs(), baseline(), market(), CUTOFF)
    base = result["scenarios"][0]
    # 1000 * .2 * .75 + 10 - 30 - 5 = 125. Terminal reinvestment is 20% of NOPAT.
    assert [Decimal(row["fcff"]) for row in base["rows"]] == [Decimal(125)] * 5
    terminal_cf = Decimal(150) * Decimal("1.02") * Decimal("0.8")
    expected = sum(Decimal(125) / Decimal("1.1") ** year for year in range(1, 6))
    expected += terminal_cf / Decimal("0.08") / Decimal("1.1") ** 5
    assert abs(Decimal(base["enterprise_value_usd_million"]) - expected) < Decimal(
        "1e-20"
    )
    security = base["securities"][0]
    per_share = (expected + Decimal(10)) / Decimal(100)
    assert abs(Decimal(security["value_usd_per_share"]) - per_share) < Decimal("1e-20")
    assert abs(
        Decimal(security["value_price_gap_ratio"]) - (per_share / 15 - 1)
    ) < Decimal("1e-20")
    assert "annualized_return" not in security
    assert result["status"] == "unreviewed"


def test_midyear_counts_only_assumed_remaining_cash_flow_and_discounts_a_stub():
    cutoff = datetime(2026, 7, 2, 12, tzinfo=UTC)  # precisely half of a 365-day year
    value = inputs()
    value["first_year_cash_flow_fraction"] = "0.5"
    base = calculate_financial_model(value, baseline(), None, cutoff)["scenarios"][0]
    expected = Decimal("62.5") / Decimal("1.1") ** Decimal("0.5")
    expected += sum(
        Decimal(125) / Decimal("1.1") ** (Decimal("0.5") + year) for year in range(1, 5)
    )
    expected += Decimal(1530) / Decimal("1.1") ** Decimal("4.5")
    assert abs(Decimal(base["enterprise_value_usd_million"]) - expected) < Decimal(
        "1e-20"
    )
    assert base["securities"] == []


def test_candidate_uses_prior_second_half_and_current_first_half_not_old_fixture():
    value = candidate_financial_inputs(baseline(), CUTOFF)
    assert Decimal(value["scenarios"][0]["paths"]["revenue"][0]) == Decimal(
        220
    ) + Decimal(220) * Decimal("1.22")
    assert len({tuple(s["paths"]["capex"]) for s in value["scenarios"]}) == 3
    assert "非公司指引" in value["scenarios"][0]["rationales"]["capex"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(discount_rate="NaN"),
        lambda v: v.update(discount_rate=0.1),
        lambda v: v.update(discount_rate="1e999"),
        lambda v: v.update(terminal_growth="0.1"),
        lambda v: v.update(terminal_roic="0.01"),
        lambda v: v.update(first_year_cash_flow_fraction="0"),
        lambda v: v.update(first_fiscal_year=2025),
        lambda v: v.update(extra="unexpected"),
        lambda v: v["scenarios"][0]["paths"].update(revenue=["100"] * 5),
        lambda v: v["scenarios"][0]["paths"].update(capex=["19"] * 5),
        lambda v: v["scenarios"][0]["paths"].update(depreciation=["4"] * 5),
        lambda v: v["scenarios"][0]["paths"].update(operating_margin=["1.1"] * 5),
        lambda v: v["scenarios"][0]["paths"].update(cash_tax_rate=["-0.1"] * 5),
        lambda v: v["scenarios"][0]["paths"].update(revenue=["1000"] * 4),
        lambda v: v["scenarios"][0]["rationales"].update(revenue=""),
        lambda v: v["scenarios"][1].update(scenario_id="base"),
    ],
)
def test_invalid_or_unreasoned_inputs_fail_closed(mutation):
    value = inputs()
    mutation(value)
    with pytest.raises(ValidationError):
        calculate_financial_model(value, baseline(), market(), CUTOFF)


def test_negative_cash_flow_and_working_capital_release_are_not_clamped():
    value = inputs()
    value["scenarios"][0]["paths"]["capex"][0] = "300"
    value["scenarios"][0]["paths"]["working_capital_change"][0] = "-5"
    row = calculate_financial_model(value, baseline(), None, CUTOFF)["scenarios"][0][
        "rows"
    ][0]
    assert Decimal(row["fcff"]) == Decimal(-135)


def test_calculation_does_not_mutate_inputs_and_is_decimal_context_independent():
    from decimal import localcontext

    value = inputs()
    before = deepcopy(value)
    first = calculate_financial_model(value, baseline(), market(), CUTOFF)
    with localcontext() as context:
        context.prec = 6
        second = calculate_financial_model(value, baseline(), market(), CUTOFF)
    assert first == second
    assert value == before
    assert financial_model_market_snapshot(None) is None


def test_missing_or_segment_substituted_group_baselines_are_rejected():
    data = baseline()
    data["facts"][0]["scope"] = "google_cloud"
    with pytest.raises(ValidationError):
        candidate_financial_inputs(data, CUTOFF)


def test_invalid_share_denominator_does_not_leak_non_finite_security_values():
    data = market()
    data["capital_structure"]["diluted_shares"] = "0"
    with pytest.raises(ValidationError):
        calculate_financial_model(inputs(), baseline(), data, CUTOFF)


def test_market_snapshot_serializes_scaled_decimal_zero_without_exponent():
    from types import SimpleNamespace

    context = SimpleNamespace(
        market_bridge={"capital_structure": {"pension_liabilities": Decimal("0E+6")}},
        market_at=CUTOFF,
        snapshot_bindings=(),
    )
    assert (
        financial_model_market_snapshot(context)["capital_structure"][
            "pension_liabilities"
        ]
        == "0"
    )


def test_dominant_terminal_value_is_exposed_as_a_model_dependency():
    value = inputs()
    for scenario in value["scenarios"]:
        scenario["paths"]["capex"] = ["140"] * 5
    result = calculate_financial_model(value, baseline(), None, CUTOFF)
    assert any("终值" in warning and "75%" in warning for warning in result["warnings"])


def test_saved_v1_replay_does_not_follow_a_new_default_calculator(monkeypatch):
    from app.underwriting.services import company_research_financial_model as module

    value = inputs()
    saved = calculate_financial_model(value, baseline(), market(), CUTOFF)

    def newer_default(*args, **kwargs):
        raise AssertionError("a saved recipe must not follow the new default")

    monkeypatch.setattr(module, "calculate_financial_model", newer_default)
    monkeypatch.setattr(module, "INPUT_SCHEMA", "future-default-inputs.v2")
    monkeypatch.setattr(module, "RESULT_SCHEMA", "future-default-result.v2")
    assert module.replay_financial_model(
        value, baseline(), market(), CUTOFF, result_schema_version=saved["schema_version"]
    ) == saved
    with pytest.raises(ValidationError):
        module.replay_financial_model(
            value, baseline(), market(), CUTOFF, result_schema_version="unsupported.v2"
        )
