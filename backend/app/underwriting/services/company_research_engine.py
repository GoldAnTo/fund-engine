"""Pure, audited compiler for the closed Alphabet company-research model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchAssessment,
    CompanyResearchModelInput,
    FinancialBridgeArtifact,
    FinancialBridgeRow,
    ReverseDcfRequest,
    ReverseDcfArtifact,
    SecurityValuationReference,
    SecurityValueRangeArtifact,
    SourceLineageReference,
    ScenarioDcfValue,
    ValuationSetArtifact,
    ValueRange,
)


_MECHANISMS = frozenset(
    {
        "search_cloud_resilience",
        "ai_monetization_and_utilization",
        "search_disruption_and_capital_drag",
    }
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
        self._validate_model_links(model)

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
                scenario_enterprise_values={},
                security_values=(),
                reverse_dcf=None,
                valuation_set=None,
            )

        values = {
            item.scenario_id: self._dcf(
                item.bridge, model.required_return, model.terminal_growth
            )
            for item in model.scenario_bridges
        }
        reverse = (
            self._reverse_dcf(
                request=model.reverse_dcf,
                bridge=next(item.bridge for item in model.scenario_bridges if item.scenario_id == "base"),
                required_return=model.required_return,
                terminal_growth=model.terminal_growth,
            )
            if model.reverse_dcf is not None
            else None
        )
        securities = tuple(
            self._security_value(item, values, model)
            for item in sorted(model.market_bridge.securities, key=lambda value: value.security_external_key)
        )
        valuation_set = ValuationSetArtifact(
            scenario_dcf_values=tuple(
                ScenarioDcfValue(scenario_id, value)
                for scenario_id, value in sorted(values.items())
            ),
            reverse_dcf=(
                ReverseDcfArtifact(
                    reverse.driver_key, reverse.implied_value,
                    reverse.achieved_residual, reverse.iteration_count,
                )
                if reverse is not None
                else None
            ),
            security_value_ranges=tuple(
                SecurityValueRangeArtifact(
                    item.security_external_key, item.value_range, item.cny_return_range
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
            for row in scenario_bridge.bridge.rows:
                refs.extend(row.fact_refs)
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

    @staticmethod
    def _validate_model_links(model: CompanyResearchModelInput) -> None:
        mechanisms = {scenario.mechanism_id for scenario in model.scenario_set.scenarios}
        if mechanisms != _MECHANISMS:
            raise ValidationError("Alphabet scenarios must use distinct mechanisms")
        modules = {item.module_key for item in model.business_map.modules}
        drivers = {item.driver_key for item in model.driver_map.drivers}
        if any(item.module_key not in modules for item in model.driver_map.drivers):
            raise ValidationError("driver references an unknown business module")
        if any(
            override.driver_key not in drivers
            for scenario in model.scenario_set.scenarios
            for override in scenario.driver_overrides
        ):
            raise ValidationError("scenario override references an unknown driver")
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

    @staticmethod
    def _validate_financial_closure(row: FinancialBridgeRow) -> None:
        expected_fcff = (
            row.operating_income * (Decimal("1") - row.cash_tax_rate)
            + row.depreciation - row.capex - row.working_capital_change
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
            context.prec = 60
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
        lower_value = self._dcf(bridge, required_return, terminal_growth, multiplier=request.lower_bound)
        upper_value = self._dcf(bridge, required_return, terminal_growth, multiplier=request.upper_bound)
        target = request.target_enterprise_value
        if not min(lower_value, upper_value) <= target <= max(lower_value, upper_value):
            raise ValidationError("reverse DCF target is outside the bounded driver range")
        lower, upper = request.lower_bound, request.upper_bound
        midpoint = lower
        residual = lower_value - target
        for iteration in range(1, request.max_iterations + 1):
            midpoint = (lower + upper) / Decimal("2")
            residual = self._dcf(bridge, required_return, terminal_growth, multiplier=midpoint) - target
            if abs(residual) <= _REVERSE_TOLERANCE:
                return ReverseDcfResult(request.driver_key, +midpoint, +residual, iteration)
            if (lower_value - target) * residual <= Decimal("0"):
                upper = midpoint
                upper_value = residual + target
            else:
                lower = midpoint
                lower_value = residual + target
        return ReverseDcfResult(request.driver_key, +midpoint, +residual, request.max_iterations)

    def _security_value(
        self,
        security: SecurityValuationReference,
        scenario_values: dict[str, Decimal],
        model: CompanyResearchModelInput,
    ) -> SecurityValueResult:
        market = model.market_bridge
        assert market is not None  # narrowed by compile before this method is called
        capital = market.capital_structure
        equity_values = tuple(
            value + capital.cash + capital.investments - capital.debt
            - capital.minority_interest - capital.pension_liabilities - capital.other_adjustments
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
