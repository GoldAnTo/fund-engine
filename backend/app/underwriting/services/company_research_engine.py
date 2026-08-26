"""Pure, audited compiler for the closed Alphabet company-research model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchAssessment,
    COMPANY_RESEARCH_DECIMAL_PRECISION,
    CompanyResearchModelInput,
    FinancialBridgeArtifact,
    FinancialBridgeRow,
    ReverseDcfRequest,
    ReverseDcfArtifact,
    RequiredReturnComparisonArtifact,
    SecurityValuationReference,
    SecurityValueRangeArtifact,
    SourceLineageReference,
    ScenarioDcfValue,
    SCENARIO_FINANCIAL_DRIVER_EQUATIONS,
    SCENARIO_FINANCIAL_DRIVER_KEYS,
    ValuationSetArtifact,
    ValueRange,
)


_REVERSE_TOLERANCE = Decimal("0.000001")
@dataclass(frozen=True, slots=True)
class ReverseDcfResult:
    driver_key: str
    implied_value: Decimal
    achieved_residual: Decimal
    iteration_count: int


@dataclass(frozen=True, slots=True)
class SecurityValueResult:
    security_external_key: str
    value_range: ValueRange
    cny_return_range: ValueRange


@dataclass(frozen=True, slots=True)
class CompanyResearchModelResult:
    assessment: CompanyResearchAssessment
    financial_bridges: dict[str, FinancialBridgeArtifact]
    scenario_enterprise_values: dict[str, Decimal]
    security_values: tuple[SecurityValueResult, ...]
    reverse_dcf: ReverseDcfResult | None
    valuation_set: ValuationSetArtifact | None


class CompanyResearchEngine:
    """Compile typed artifacts only; no source fetch, persistence, or AI inference."""

    def compile(self, model: CompanyResearchModelInput) -> CompanyResearchModelResult:
        if type(model) is not CompanyResearchModelInput:
            raise ValidationError("company research model input must be typed")
        if model.terminal_growth >= model.required_return:
            raise ValidationError("terminal growth must be less than the discount rate")
        self._validate_lineage(model)
        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            compiled_bridges = self._validate_model_links(model)

            critical_gaps = any(gap.severity.value == "critical" for gap in model.research_gaps)
            judgment = model.judgment_context
            if (
                not model.scenario_bridges
                or model.market_bridge is None
                or critical_gaps
                or not judgment.operating_baseline_available
                or not judgment.financial_bridge_closed
                or not judgment.market_security_bridge_available
            ):
                return CompanyResearchModelResult(
                    assessment=CompanyResearchAssessment.not_answerable(),
                    financial_bridges=compiled_bridges,
                    scenario_enterprise_values={},
                    security_values=(),
                    reverse_dcf=None,
                    valuation_set=None,
                )

            values = {
                scenario_id: self._dcf(bridge, model.required_return, model.terminal_growth)
                for scenario_id, bridge in compiled_bridges.items()
            }
            reverse = (
                self._reverse_dcf(
                    request=model.reverse_dcf,
                    bridge=compiled_bridges["base"],
                    required_return=model.required_return,
                    terminal_growth=model.terminal_growth,
                )
                if model.reverse_dcf is not None
                else None
            )
            securities = tuple(
                self._security_value(item, values, model)
                for item in sorted(
                    model.market_bridge.securities,
                    key=lambda value: value.security_external_key,
                )
            )
            valuation_set = ValuationSetArtifact(
                scenario_dcf_values=tuple(
                    ScenarioDcfValue(scenario_id, value)
                    for scenario_id, value in sorted(values.items())
                ),
                reverse_dcf=(
                    ReverseDcfArtifact(
                        reverse.driver_key,
                        reverse.implied_value,
                        reverse.achieved_residual,
                        reverse.iteration_count,
                    )
                    if reverse is not None
                    else None
                ),
                security_value_ranges=tuple(
                    SecurityValueRangeArtifact(
                        item.security_external_key,
                        item.value_range,
                        item.cny_return_range,
                    )
                    for item in securities
                ),
                required_return=model.required_return,
                required_return_comparisons=tuple(
                    RequiredReturnComparisonArtifact(
                        security_external_key=item.security_external_key,
                        required_return=model.required_return,
                        achieved_return_range=item.cny_return_range,
                        meets_required_return=(
                            item.cny_return_range.minimum >= model.required_return
                        ),
                    )
                    for item in securities
                ),
            )
        return CompanyResearchModelResult(
            assessment=(
                CompanyResearchAssessment.partially_answerable()
                if model.research_gaps
                else CompanyResearchAssessment.answerable()
            ),
            financial_bridges=compiled_bridges,
            scenario_enterprise_values=values,
            security_values=securities,
            reverse_dcf=reverse,
            valuation_set=valuation_set,
        )

    @staticmethod
    def _all_references(model: CompanyResearchModelInput) -> tuple[SourceLineageReference, ...]:
        refs: list[SourceLineageReference] = []
        for module in model.business_map.modules:
            refs.extend(module.fact_refs)
        for driver in model.driver_map.drivers:
            refs.extend(driver.fact_refs)
            refs.extend(driver.assumption_refs)
        for scenario_bridge in model.scenario_bridges:
            for forecast in scenario_bridge.driver_forecasts:
                refs.extend(forecast.fact_refs)
                refs.extend(forecast.assumption_refs)
        refs.append(model.evidence_gap_contract.source_ref)
        refs.extend(model.judgment_context.strongest_counterevidence)
        if model.market_bridge is not None:
            refs.append(model.market_bridge.capital_structure.source_ref)
            refs.append(model.market_bridge.fx_ref)
            for security in model.market_bridge.securities:
                refs.extend((security.rights_ref, security.price_ref))
        return tuple(refs)

    def _validate_lineage(self, model: CompanyResearchModelInput) -> None:
        available = {item.fact_key: item for item in model.source_lineage}
        if len(available) != len(model.source_lineage):
            raise ValidationError("source lineage fact keys must be unique")
        for reference in self._all_references(model):
            if available.get(reference.fact_key) != reference:
                raise ValidationError("artifact source lineage must match authenticated evidence exactly")

    @classmethod
    def _validate_model_links(
        cls, model: CompanyResearchModelInput
    ) -> dict[str, FinancialBridgeArtifact]:
        mechanisms = {scenario.mechanism_id for scenario in model.scenario_set.scenarios}
        if len(mechanisms) != 3:
            raise ValidationError("scenarios must use three distinct mechanisms")
        modules = {item.module_key for item in model.business_map.modules}
        drivers_by_key = {item.driver_key: item for item in model.driver_map.drivers}
        drivers = set(drivers_by_key)
        if any(item.module_key not in modules for item in model.driver_map.drivers):
            raise ValidationError("driver references an unknown business module")
        for driver_key, equation in SCENARIO_FINANCIAL_DRIVER_EQUATIONS.items():
            driver = drivers_by_key.get(driver_key)
            if driver is None or driver.equation != equation or driver.output_metric != driver_key:
                raise ValidationError(
                    "scenario financial forecast drivers must have closed named equations"
                )
        if any(
            override.driver_key not in drivers
            for scenario in model.scenario_set.scenarios
            for override in scenario.driver_overrides
        ):
            raise ValidationError(
                "scenario overrides must target named financial forecast drivers"
            )
        bridge_ids = {item.scenario_id for item in model.scenario_bridges}
        scenario_ids = {item.scenario_id for item in model.scenario_set.scenarios}
        if bridge_ids and bridge_ids != scenario_ids:
            raise ValidationError("scenario financial bridges must exactly cover scenarios")
        if model.market_bridge is not None:
            expected = {"NASDAQ:GOOG", "NASDAQ:GOOGL"}
            actual = {item.security_external_key for item in model.market_bridge.securities}
            if actual != expected:
                raise ValidationError("valuation requires exact GOOGL and GOOG security references")
            if any(
                item.usd_cny_rate != model.market_bridge.usd_cny_rate
                for item in model.market_bridge.securities
            ):
                raise ValidationError("security FX references must use the exact market FX rate")

        if not model.scenario_bridges:
            return {}
        scenarios_by_id = {
            scenario.scenario_id: scenario for scenario in model.scenario_set.scenarios
        }
        bridges_by_id = {
            scenario_bridge.scenario_id: scenario_bridge
            for scenario_bridge in model.scenario_bridges
        }
        for bridge in model.scenario_bridges:
            for forecast in bridge.driver_forecasts:
                driver = drivers_by_key[forecast.driver_key]
                if (
                    forecast.input_state is not driver.input_state
                    or forecast.values != driver.values
                    or forecast.fact_refs != driver.fact_refs
                    or forecast.assumption_refs != driver.assumption_refs
                    or forecast.assumption_key != driver.assumption_key
                    or forecast.equation_id != driver.equation_id
                ):
                    raise ValidationError(
                        "scenario forecast provenance must match its driver state"
                    )
        for scenario_id, scenario in scenarios_by_id.items():
            override_keys = {override.driver_key for override in scenario.driver_overrides}
            if override_keys != set(SCENARIO_FINANCIAL_DRIVER_KEYS):
                raise ValidationError(
                    "scenario overrides must target every named financial forecast driver"
                )
            if scenario_id == "base" and any(
                override.value != Decimal("1") for override in scenario.driver_overrides
            ):
                raise ValidationError("base scenario overrides must preserve named financial drivers")

        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            baseline_signatures = {
                cls._forecast_signature(bridge)
                for bridge in bridges_by_id.values()
            }
            if len(baseline_signatures) != 1:
                raise ValidationError(
                    "scenario financial forecasts must share one named-driver baseline"
                )
            compiled = {
                scenario_id: cls._compile_scenario_bridge(
                    bridge=bridges_by_id[scenario_id],
                    scenario=scenarios_by_id[scenario_id],
                )
                for scenario_id in ("base", "bull", "bear")
            }
        if len({cls._bridge_signature(bridge) for bridge in compiled.values()}) != len(compiled):
            raise ValidationError(
                "scenario overrides must produce distinct mechanism-specific financial forecasts"
            )
        return compiled

    @staticmethod
    def _forecast_signature(
        scenario_bridge,
    ) -> tuple[
        int,
        tuple[
            tuple[
                str,
                tuple[Decimal, ...],
                tuple[SourceLineageReference, ...],
                tuple[SourceLineageReference, ...],
                str,
                str | None,
                str | None,
            ], ...
        ],
    ]:
        return (
            scenario_bridge.first_fiscal_year,
            tuple(
                sorted(
                    (
                        forecast.driver_key,
                        forecast.values,
                        forecast.fact_refs,
                        forecast.assumption_refs,
                        forecast.input_state.value,
                        forecast.assumption_key,
                        forecast.equation_id,
                    )
                    for forecast in scenario_bridge.driver_forecasts
                )
            ),
        )

    @staticmethod
    def _bridge_signature(bridge: FinancialBridgeArtifact) -> tuple[tuple[Decimal, ...], ...]:
        return tuple(
            (
                row.revenue,
                row.operating_income,
                row.cash_tax_rate,
                row.depreciation,
                row.capex,
                row.working_capital_change,
                row.fcff,
            )
            for row in bridge.rows
        )

    @staticmethod
    def _compile_scenario_bridge(*, bridge, scenario) -> FinancialBridgeArtifact:
        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            forecasts = {
                forecast.driver_key: forecast for forecast in bridge.driver_forecasts
            }
            overrides = {
                override.driver_key: override.value
                for override in scenario.driver_overrides
            }
            source_refs = tuple(
                dict.fromkeys(
                    reference
                    for driver_key in SCENARIO_FINANCIAL_DRIVER_KEYS
                    for reference in (
                        *forecasts[driver_key].fact_refs,
                        *forecasts[driver_key].assumption_refs,
                    )
                )
            )
            rows = []
            for offset in range(5):
                revenue = forecasts["revenue"].values[offset] * overrides["revenue"]
                operating_margin = (
                    forecasts["operating_margin"].values[offset]
                    * overrides["operating_margin"]
                )
                cash_tax_rate = (
                    forecasts["cash_tax_rate"].values[offset]
                    * overrides["cash_tax_rate"]
                )
                depreciation = (
                    forecasts["depreciation"].values[offset]
                    * overrides["depreciation"]
                )
                capex = forecasts["capex"].values[offset] * overrides["capex"]
                working_capital_change = (
                    forecasts["working_capital_change"].values[offset]
                    * overrides["working_capital_change"]
                )
                operating_income = revenue * operating_margin
                fcff = +(
                    operating_income * (Decimal("1") - cash_tax_rate)
                    + depreciation
                    - capex
                    - working_capital_change
                )
                rows.append(
                    FinancialBridgeRow(
                        fiscal_year=bridge.first_fiscal_year + offset,
                        revenue=revenue,
                        operating_income=operating_income,
                        cash_tax_rate=cash_tax_rate,
                        depreciation=depreciation,
                        capex=capex,
                        working_capital_change=working_capital_change,
                        fcff=fcff,
                        fact_refs=source_refs,
                    )
                )
        return FinancialBridgeArtifact(rows=tuple(rows))

    @staticmethod
    def _validate_financial_closure(row: FinancialBridgeRow) -> None:
        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            expected_fcff = +(
                row.operating_income * (Decimal("1") - row.cash_tax_rate)
                + row.depreciation
                - row.capex
                - row.working_capital_change
            )
        if row.fcff != expected_fcff:
            raise ValidationError("financial bridge does not close")

    def _dcf(
        self,
        bridge: FinancialBridgeArtifact,
        required_return: Decimal,
        terminal_growth: Decimal,
        *,
        multiplier: Decimal = Decimal("1"),
    ) -> Decimal:
        if terminal_growth >= required_return:
            raise ValidationError("terminal growth must be less than the discount rate")
        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            present_value = Decimal("0")
            for year, row in enumerate(bridge.rows, start=1):
                self._validate_financial_closure(row)
                present_value += row.fcff * multiplier / ((Decimal("1") + required_return) ** year)
            final_fcff = bridge.rows[-1].fcff * multiplier
            terminal_value = final_fcff * (Decimal("1") + terminal_growth) / (
                required_return - terminal_growth
            )
            return +(present_value + terminal_value / ((Decimal("1") + required_return) ** 5))

    def _reverse_dcf(
        self,
        *,
        request: ReverseDcfRequest,
        bridge: FinancialBridgeArtifact,
        required_return: Decimal,
        terminal_growth: Decimal,
    ) -> ReverseDcfResult:
        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            lower_value = self._dcf(
                bridge, required_return, terminal_growth, multiplier=request.lower_bound
            )
            upper_value = self._dcf(
                bridge, required_return, terminal_growth, multiplier=request.upper_bound
            )
            target = request.target_enterprise_value
            if not min(lower_value, upper_value) <= target <= max(lower_value, upper_value):
                raise ValidationError("reverse DCF target is outside the bounded driver range")
            lower, upper = request.lower_bound, request.upper_bound
            midpoint = lower
            residual = lower_value - target
            for iteration in range(1, request.max_iterations + 1):
                midpoint = (lower + upper) / Decimal("2")
                residual = (
                    self._dcf(
                        bridge,
                        required_return,
                        terminal_growth,
                        multiplier=midpoint,
                    )
                    - target
                )
                if abs(residual) <= _REVERSE_TOLERANCE:
                    return ReverseDcfResult(
                        request.driver_key, +midpoint, +residual, iteration
                    )
                if (lower_value - target) * residual <= Decimal("0"):
                    upper = midpoint
                    upper_value = residual + target
                else:
                    lower = midpoint
                    lower_value = residual + target
            return ReverseDcfResult(
                request.driver_key, +midpoint, +residual, request.max_iterations
            )

    def _security_value(
        self,
        security: SecurityValuationReference,
        scenario_values: dict[str, Decimal],
        model: CompanyResearchModelInput,
    ) -> SecurityValueResult:
        with localcontext() as context:
            context.prec = COMPANY_RESEARCH_DECIMAL_PRECISION
            market = model.market_bridge
            assert market is not None  # narrowed by compile before this method is called
            capital = market.capital_structure
            equity_values = tuple(
                value
                + capital.cash
                + capital.investments
                - capital.debt
                - capital.minority_interest
                - capital.pension_liabilities
                - capital.other_adjustments
                for value in scenario_values.values()
            )
            per_share = tuple(value / security.share_count for value in equity_values)
            cny_values = tuple(value * market.usd_cny_rate for value in per_share)
            market_cny = security.market_price_usd * market.usd_cny_rate
            returns = tuple(value / market_cny - Decimal("1") for value in cny_values)
            return SecurityValueResult(
                security_external_key=security.security_external_key,
                value_range=ValueRange(min(per_share), max(per_share)),
                cny_return_range=ValueRange(min(returns), max(returns)),
            )
