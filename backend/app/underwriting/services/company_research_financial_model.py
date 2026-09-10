"""Conditional group valuation drafts; never an investment approval.

The old research compiler retains its evidence gates. This calculator makes the
next editable input package reviewable without claiming the missing operating
drivers have been observed or the user's assumptions have been confirmed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import COMPANY_RESEARCH_DECIMAL_PRECISION
from app.underwriting.services.company_research_cash_flow import (
    unlevered_free_cash_flow,
)

_V1_INPUT_SCHEMA = "company-research.financial-model-inputs.v1"
_V1_RESULT_SCHEMA = "company-research.financial-model-result.v1"
INPUT_SCHEMA = _V1_INPUT_SCHEMA
RESULT_SCHEMA = _V1_RESULT_SCHEMA
DRIVERS = (
    "revenue",
    "operating_margin",
    "cash_tax_rate",
    "depreciation",
    "capex",
    "working_capital_change",
)
SCENARIOS = ("base", "bull", "bear")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_INPUT_FIELDS = {
    "schema_version",
    "first_fiscal_year",
    "scenarios",
    "discount_rate",
    "discount_rate_rationale",
    "terminal_growth",
    "terminal_roic",
    "terminal_rationale",
    "first_year_cash_flow_fraction",
    "timing_rationale",
}
_LABELS = {
    "revenue": "集团收入",
    "operating_margin": "集团营业利润率",
    "cash_tax_rate": "经营现金税率",
    "depreciation": "集团固定资产折旧",
    "capex": "集团资本开支",
    "working_capital_change": "经营营运资本净投入",
}


def _number(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 64 or not _NUMBER.fullmatch(value):
        raise ValidationError(f"{label} must be a finite decimal string")
    number = Decimal(value)
    if abs(number) > Decimal(1000000000):
        raise ValidationError(f"{label} exceeds the supported model range")
    return number


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not 4 <= len(value.strip()) <= 3000:
        raise ValidationError(
            f"{label} requires an explicit rationale (4–3000 characters)"
        )


def _shape(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"{label} has missing or unsupported fields")
    return value


def _cutoff(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValidationError("model cutoff requires a timezone")
    value = value.astimezone(UTC)
    if value.year != 2026:
        raise ValidationError(
            "this financial baseline supports the FY2026–2030 model only"
        )
    return value


def _remaining_year(cutoff: datetime) -> Decimal:
    end = datetime(cutoff.year + 1, 1, 1, tzinfo=UTC)
    start = datetime(cutoff.year, 1, 1, tzinfo=UTC)
    # Exact integer microseconds avoid a floating-point timestamp in a saved model.
    delta = end - cutoff
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Decimal(microseconds) / Decimal((end - start).days * 86400 * 1_000_000)


def _fact(baseline: dict, key: str) -> Decimal:
    matches = [
        fact for fact in baseline.get("facts", []) if fact.get("fact_key") == key
    ]
    if (
        len(matches) != 1
        or matches[0].get("scope") != "group"
        or matches[0].get("unit") != "USD_million"
    ):
        raise ValidationError(
            f"required group baseline {key} is missing or has the wrong scope/unit"
        )
    return _number(matches[0]["value"], key)


def _json_safe(value):
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def financial_model_market_snapshot(market_context) -> dict | None:
    """Snapshot an already-authenticated market context; no fetching or writes."""
    if market_context is None:
        return None
    result = _json_safe(market_context.market_bridge)
    result["market_at"] = market_context.market_at.isoformat()
    result["snapshot_bindings"] = _json_safe(market_context.snapshot_bindings)
    return result


def candidate_financial_inputs(baseline: dict, cutoff_at: datetime) -> dict:
    """Build explicit research assumptions anchored to disclosed annual/H1 sales."""
    cutoff = _cutoff(cutoff_at)
    with localcontext() as context:
        context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
        h2_previous = _fact(baseline, "fy2025_group_revenue") - _fact(
            baseline, "h1_2025_group_revenue"
        )
        h1_current = _fact(baseline, "h1_2026_group_revenue")
        if h2_previous <= 0 or h1_current <= 0:
            raise ValidationError(
                "annual and interim revenue baselines do not reconcile"
            )
        specifications = (
            (
                "base",
                "搜索维持增长、Cloud扩张；算力投入先行，利用率随后改善",
                "0.22",
                ("0.16", "0.14", "0.12", "0.10"),
                ("0.35", "0.35", "0.355", "0.36", "0.36"),
                "0.20",
                (31000, 45000, 60000, 75000, 90000),
                (190000, 195000, 190000, 185000, 180000),
                (12000, 14000, 15000, 16000, 17000),
            ),
            (
                "bull",
                "AI需求转化与产能利用共同改善；更高增长仍需要额外资本投入",
                "0.30",
                ("0.22", "0.20", "0.17", "0.14"),
                ("0.36", "0.37", "0.38", "0.385", "0.39"),
                "0.20",
                (32000, 48000, 65000, 82000, 98000),
                (205000, 230000, 235000, 235000, 235000),
                (14000, 17000, 20000, 23000, 25000),
            ),
            (
                "bear",
                "搜索变现承压、Cloud增速回落；已承诺产能和折旧造成利润拖累",
                "0.12",
                ("0.08", "0.06", "0.06", "0.05"),
                ("0.32", "0.30", "0.29", "0.29", "0.30"),
                "0.22",
                (31000, 46000, 63000, 79000, 93000),
                (190000, 200000, 195000, 185000, 175000),
                (15000, 17000, 18000, 19000, 20000),
            ),
        )
        scenarios = []
        for (
            key,
            mechanism,
            h2_growth,
            growths,
            margins,
            tax,
            depreciation,
            capex,
            wc,
        ) in specifications:
            revenue = [h1_current + h2_previous * (Decimal(1) + Decimal(h2_growth))]
            for growth in growths:
                revenue.append(revenue[-1] * (Decimal(1) + Decimal(growth)))
            paths = dict(
                zip(
                    DRIVERS,
                    (
                        [str(number) for number in revenue],
                        list(margins),
                        [tax] * 5,
                        list(map(str, depreciation)),
                        list(map(str, capex)),
                        list(map(str, wc)),
                    ),
                    strict=True,
                )
            )
            rationales = {
                "revenue": f"2026收入=已披露H1收入+2025H2收入×(1+{h2_growth})；H2增速及以后各年增速{', '.join(growths)}均为研究假设，非公司指引。{mechanism}。",
                "operating_margin": "以集团年度及H1营业利润率作对照；各年率是研究假设，已包含SBC费用和折旧，不用Cloud分部率替代集团率。"
                + mechanism
                + "。",
                "cash_tax_rate": f"假设经营利润现金税率为{tax}；报表有效税率受非经营收益影响，不能直接替代。需核验地域税率及现金支付时点。",
                "depreciation": "集团P&E折旧假设；历史H1折旧作下限，新增资产投产后逐年增加，不将集团折旧标为Cloud专属，也不额外加回SBC。",
                "capex": "这些金额是资本投入情景假设，非公司指引。H1实际资本开支作下限；可用中报未披露新的全年数值范围，后续路径取决于建设进度和产能利用，需复核。"
                + mechanism
                + "。",
                "working_capital_change": "经营营运资本净投入假设；正数消耗现金，负数释放现金。现金流量表总资产负债调整不直接充当该指标，尚需应收、应付及递延收入周转测算。",
            }
            scenarios.append(
                {
                    "scenario_id": key,
                    "mechanism": mechanism,
                    "paths": paths,
                    "rationales": rationales,
                }
            )
        return {
            "schema_version": INPUT_SCHEMA,
            "first_fiscal_year": 2026,
            "scenarios": scenarios,
            "discount_rate": "0.10",
            "discount_rate_rationale": "集团FCFF折现率暂假设为10%，用于条件敏感性；尚未由资本成本研究认证，与任务中的股东要求回报率分开。",
            "terminal_growth": "0.03",
            "terminal_roic": "0.12",
            "terminal_rationale": "永续增长3%、终值增量投入资本回报率12%均为研究假设；按g/ROIC将终值NOPAT的25%用于再投资。终值是稳定状态简化，需复核竞争和长期资本效率。",
            "first_year_cash_flow_fraction": str(
                _remaining_year(cutoff).quantize(Decimal("0.00000001"))
            ),
            "timing_rationale": "首年剩余现金流暂按完整年度FCFF乘以截止日后剩余日历时间占比；这是年内均匀分布假设，并非已披露的剩余现金流。可编辑比例以检查资本开支集中发生的影响。",
        }


def _validate_inputs(inputs: dict, baseline: dict, cutoff: datetime) -> None:
    _shape(inputs, _INPUT_FIELDS, "model inputs")
    if (
        inputs["schema_version"] != _V1_INPUT_SCHEMA
        or type(inputs["first_fiscal_year"]) is not int
        or inputs["first_fiscal_year"] != cutoff.year
    ):
        raise ValidationError("model schema or fiscal horizon is unsupported")
    for key in ("discount_rate_rationale", "terminal_rationale", "timing_rationale"):
        _text(inputs[key], key)
    discount = _number(inputs["discount_rate"], "discount_rate")
    growth = _number(inputs["terminal_growth"], "terminal_growth")
    roic = _number(inputs["terminal_roic"], "terminal_roic")
    fraction = _number(
        inputs["first_year_cash_flow_fraction"], "first_year_cash_flow_fraction"
    )
    if (
        not Decimal("0.005") <= discount <= 1
        or not 0 <= growth <= Decimal("0.1")
        or not growth < discount
    ):
        raise ValidationError(
            "terminal growth must be below a supported positive FCFF discount rate"
        )
    if not growth < roic <= 1 or not 0 < fraction <= 1:
        raise ValidationError(
            "terminal ROIC must exceed growth; remaining-year cash flow fraction must be in (0, 1]"
        )
    scenarios = inputs["scenarios"]
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        raise ValidationError("exactly three scenarios are required")
    first_year_minima = {
        "revenue": _fact(baseline, "h1_2026_group_revenue"),
        "capex": _fact(baseline, "h1_2026_group_capex"),
        "depreciation": _fact(baseline, "h1_2026_group_ppe_depreciation"),
    }
    # Authenticate the annual/H1 revenue relationship even after a user edits paths.
    if _fact(baseline, "fy2025_group_revenue") <= _fact(
        baseline, "h1_2025_group_revenue"
    ):
        raise ValidationError("annual/interim revenue baseline does not reconcile")
    signatures = []
    mechanisms = []
    for expected, scenario in zip(SCENARIOS, scenarios, strict=True):
        _shape(
            scenario, {"scenario_id", "mechanism", "paths", "rationales"}, "scenario"
        )
        if scenario["scenario_id"] != expected:
            raise ValidationError(
                "scenarios must be unique and ordered base, bull, bear"
            )
        _text(scenario["mechanism"], "scenario mechanism")
        mechanisms.append(scenario["mechanism"].strip())
        _shape(scenario["paths"], set(DRIVERS), "scenario paths")
        _shape(scenario["rationales"], set(DRIVERS), "scenario rationales")
        signature = []
        for key in DRIVERS:
            _text(scenario["rationales"][key], f"{expected}.{key} rationale")
            path = scenario["paths"][key]
            if not isinstance(path, list) or len(path) != 5:
                raise ValidationError(f"{expected}.{key} requires five fiscal years")
            values = tuple(_number(value, f"{expected}.{key}") for value in path)
            if key == "revenue" and min(values) <= 0:
                raise ValidationError("revenue must be positive")
            if key in {"depreciation", "capex"} and min(values) < 0:
                raise ValidationError(f"{key} must be nonnegative")
            if key == "operating_margin" and (
                min(values) < -1 or max(values) > 1 or values[-1] <= 0
            ):
                raise ValidationError(
                    "operating margins must be in [-1, 1] with positive terminal-year margin"
                )
            if key == "cash_tax_rate" and (min(values) < 0 or max(values) > 1):
                raise ValidationError("cash tax rate must be in [0, 1]")
            if key in first_year_minima and values[0] < first_year_minima[key]:
                raise ValidationError(
                    f"full-year {key} cannot be below disclosed H1 actuals"
                )
            signature.append(values)
        signatures.append(tuple(signature))
    if len(set(mechanisms)) != 3 or len(set(signatures)) != 3:
        raise ValidationError(
            "three distinct mechanisms and financial paths are required"
        )


def _securities(enterprise_value: Decimal, market: dict | None) -> list[dict]:
    if market is None:
        return []
    try:
        capital = market["capital_structure"]
        values = {
            key: _number(capital[key], f"capital_structure.{key}")
            for key in (
                "cash",
                "investments",
                "debt",
                "minority_interest",
                "pension_liabilities",
                "other_adjustments",
                "diluted_shares",
            )
        }
        shares = values.pop("diluted_shares")
        fx = _number(market["usd_cny_rate"], "usd_cny_rate")
        if shares <= 0 or fx <= 0 or min(values.values()) < 0:
            raise ValidationError(
                "capital structure requires nonnegative adjustments and positive diluted shares/FX"
            )
        equity = (
            enterprise_value
            + values["cash"]
            + values["investments"]
            - sum(
                values[key]
                for key in (
                    "debt",
                    "minority_interest",
                    "pension_liabilities",
                    "other_adjustments",
                )
            )
        )
        rows = market["securities"]
        if (
            not isinstance(rows, list)
            or {row["security_external_key"] for row in rows}
            != {"NASDAQ:GOOG", "NASDAQ:GOOGL"}
            or len(rows) != 2
        ):
            raise ValidationError(
                "Alphabet requires exact GOOG and GOOGL security identities"
            )
        securities = []
        for security in rows:
            parameters = {
                key: _number(security[key], key)
                for key in (
                    "conversion_ratio",
                    "adr_ratio",
                    "dividend_rights_per_unit",
                    "market_price_usd",
                )
            }
            if min(parameters.values()) <= 0:
                raise ValidationError(
                    "security rights ratios and price must be positive"
                )
            rights = (
                parameters["conversion_ratio"]
                * parameters["dividend_rights_per_unit"]
                / parameters["adr_ratio"]
            )
            per_share = max(Decimal(0), equity) / shares * rights
            price = parameters["market_price_usd"]
            securities.append(
                {
                    "security_external_key": security["security_external_key"],
                    "value_usd_per_share": str(+per_share),
                    "value_cny_per_share": str(+(per_share * fx)),
                    "market_price_usd": str(price),
                    "value_price_gap_ratio": str(+(per_share / price - 1)),
                }
            )
        return securities
    except (KeyError, TypeError) as exc:
        raise ValidationError("market snapshot is incomplete") from exc


def _calculate_financial_model_v1(
    inputs: dict, baseline: dict, market: dict | None, cutoff_at: datetime
) -> dict:
    # Persisted v1 recipe: keep its dependencies and policy constants unchanged.
    # A future numerical policy needs a new version and a new implementation.
    cutoff = _cutoff(cutoff_at)
    with localcontext() as context:
        context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
        _validate_inputs(inputs, baseline, cutoff)
        rate = Decimal(inputs["discount_rate"])
        growth = Decimal(inputs["terminal_growth"])
        roic = Decimal(inputs["terminal_roic"])
        cash_fraction = Decimal(inputs["first_year_cash_flow_fraction"])
        stub = _remaining_year(cutoff)
        result_scenarios = []
        for scenario in inputs["scenarios"]:
            rows = []
            present_value = Decimal(0)
            for year in range(5):
                values = {key: Decimal(scenario["paths"][key][year]) for key in DRIVERS}
                operating_income = values["revenue"] * values["operating_margin"]
                fcff = unlevered_free_cash_flow(
                    operating_income=operating_income,
                    cash_tax_rate=values["cash_tax_rate"],
                    depreciation=values["depreciation"],
                    capex=values["capex"],
                    working_capital_change=values["working_capital_change"],
                )
                discounted_cash_flow = (
                    fcff
                    * (cash_fraction if year == 0 else 1)
                    / (1 + rate) ** (stub + year)
                )
                present_value += discounted_cash_flow
                rows.append(
                    {
                        "fiscal_year": cutoff.year + year,
                        **{key: str(+value) for key, value in values.items()},
                        "operating_income": str(+operating_income),
                        "fcff": str(fcff),
                        "discounted_fcff": str(+discounted_cash_flow),
                        "forecast_state": "assumption",
                    }
                )
            terminal_nopat = (
                Decimal(rows[-1]["operating_income"])
                * (1 - Decimal(rows[-1]["cash_tax_rate"]))
                * (1 + growth)
            )
            terminal_fcff = terminal_nopat * (1 - growth / roic)
            terminal_pv = terminal_fcff / (rate - growth) / (1 + rate) ** (stub + 4)
            enterprise_value = present_value + terminal_pv
            result_scenarios.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "mechanism": scenario["mechanism"],
                    "rows": rows,
                    "enterprise_value_usd_million": str(+enterprise_value),
                    "terminal_fcff_usd_million": str(+terminal_fcff),
                    "terminal_value_share": str(+(terminal_pv / enterprise_value))
                    if enterprise_value > 0
                    else None,
                    "securities": _securities(enterprise_value, market),
                }
            )
        warnings = [
            "所有前瞻路径和估值参数仍是未确认研究假设；条件测算不改变原冻结报告的正式判断。",
            "集团FCF=CFO−资本开支；此处FCFF按税后营业利润桥接，两者口径不同。",
            "首年剩余现金流采用可编辑比例近似，未建立月度收入与资本开支时点模型。",
            "现金税率及营运资本投入尚需专项推演，历史有效税率和现金流表调整项不直接认证预测。",
        ]
        warnings.extend(
            f"{gap['label']}：{gap['detail']}"
            for gap in baseline.get("research_gaps", [])
        )
        if any(
            item["terminal_value_share"] is not None
            and Decimal(item["terminal_value_share"]) > Decimal("0.75")
            for item in result_scenarios
        ):
            warnings.append(
                "至少一个情景的终值现值占企业价值超过75%；估值主要依赖长期增长、终值资本回报率及折现率假设。占比超过100%表示预测期现金流现值合计为负。"
            )
        if market is None:
            warnings.append(
                "未取得可验证市场/股本快照，仅计算集团企业价值，不提供每股价值或价格比较。"
            )
        return {
            "schema_version": _V1_RESULT_SCHEMA,
            "status": "unreviewed",
            "valuation_date": cutoff.isoformat(),
            "model_scope": "group",
            "discount_rate": str(rate),
            "terminal_growth": str(growth),
            "terminal_roic": str(roic),
            "timing": {
                "first_year_discount_period": str(+stub),
                "first_year_cash_flow_fraction": str(cash_fraction),
            },
            "scenarios": result_scenarios,
            "warnings": warnings,
            "policies": [
                "金额单位为百万美元；营业利润率采用集团口径。FCFF=收入×营业利润率×(1−经营现金税率)+P&E折旧−资本开支−营运资本净投入。",
                "SBC费用保留在营业利润中，不额外加回；采用已冻结稀释加权股数作为每股换算近似，未来稀释/回购尚未建模。",
                "企业价值经现金、投资、债务及其他权益调整换算每股价值；权益残值为负时普通股价值取零。",
                "终值FCFF=末年NOPAT×(1+g)×(1−g/终值ROIC)，显式计入稳定增长再投资。",
                "价值/价格差=每股内在价值÷截止口径价格−1，不是年化收益率；人民币换算采用同一冻结汇率，不是未来汇率预测。",
            ],
            "input_anchors": {
                key: [
                    fact["fact_key"]
                    for fact in baseline.get("facts", [])
                    if fact.get("scope") == "group"
                    and fact["fact_key"].endswith(
                        {
                            "revenue": "_revenue",
                            "operating_margin": "_operating_margin",
                            "cash_tax_rate": "_tax_expense",
                            "depreciation": "_ppe_depreciation",
                            "capex": "_capex",
                            "working_capital_change": "_working_capital_investment",
                        }[key]
                    )
                ]
                for key in _LABELS
            },
        }


def replay_financial_model(
    inputs: dict, baseline: dict, market: dict | None, cutoff_at: datetime,
    *, result_schema_version: str,
) -> dict:
    """Dispatch by saved versions, independent of the default for new drafts."""
    if (
        inputs.get("schema_version") != _V1_INPUT_SCHEMA
        or result_schema_version != _V1_RESULT_SCHEMA
    ):
        raise ValidationError("saved financial model recipe is unsupported")
    return _calculate_financial_model_v1(inputs, baseline, market, cutoff_at)


def calculate_financial_model(
    inputs: dict, baseline: dict, market: dict | None, cutoff_at: datetime,
) -> dict:
    """Current default for a new calculation; saved drafts use version dispatch."""
    return _calculate_financial_model_v1(inputs, baseline, market, cutoff_at)
