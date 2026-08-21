"""Fail-closed company segment earnings compiler."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.earnings import (
    CompanyExposure,
    EarningsEngine,
    EarningsReconciliations,
    SegmentBridgeReconciliation,
    SegmentEconomics,
    SegmentInputs,
    VerifiedIndustryDependency,
    derive_four_core_views,
    validate_earnings_engine_integrity,
)
from app.underwriting.domain.industry import IndustryScenario
from app.underwriting.domain.industry import IndustryState, ScenarioSpec
from app.underwriting.domain.metrics import ReconciliationResult, reconcile
from app.underwriting.services.industry_state import (
    compile_industry_scenario,
    validate_industry_state_integrity,
)
from app.underwriting.services.mechanism_compiler import CompiledMechanisms


_GWH_TO_KWH = Decimal("1000000")
_DEFAULT_TOLERANCE = Decimal("1000")


def _context(values: tuple[Decimal, ...]) -> Context:
    return Context(
        prec=max(128, sum(len(value.as_tuple().digits) for value in values) + 32),
        rounding=ROUND_HALF_EVEN,
        Emin=-999999999,
        Emax=999999999,
    )


def _finite(value: object, name: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{name} must be a finite Decimal")
    if nonnegative and value < 0:
        raise ValidationError(f"{name} must not be negative")
    return value


def _sum(values: tuple[Decimal, ...]) -> Decimal:
    with localcontext(_context(values or (Decimal("0"),))):
        return sum(values, Decimal("0"))


def _bridge(total: Decimal, expected: Decimal, tolerance: Decimal, key: str) -> SegmentBridgeReconciliation:
    return SegmentBridgeReconciliation(key, reconcile(total, (expected,), tolerance))


def _physical_bridge(
    segment: SegmentEconomics, *, is_cost: bool, tolerance: Decimal
) -> SegmentBridgeReconciliation:
    assert segment.volume_gwh is not None
    unit_value = segment.unit_cash_cost_cny_per_kwh if is_cost else segment.asp_cny_per_kwh
    assert unit_value is not None
    total = segment.cost if is_cost else segment.revenue
    with localcontext(_context((segment.volume_gwh, unit_value, total, tolerance))):
        expected = segment.volume_gwh * _GWH_TO_KWH * unit_value
        return _bridge(total, expected, tolerance, segment.key)


def _verify_industry_dependency(
    *,
    industry_state: IndustryState,
    scenario: IndustryScenario,
    mechanisms: CompiledMechanisms,
) -> VerifiedIndustryDependency:
    """Reject state/scenario pairs that are not replayable from compiled mechanisms."""
    if type(industry_state) is not IndustryState or type(scenario) is not IndustryScenario:
        raise ValidationError("verified industry state and scenario are required")
    if type(mechanisms) is not CompiledMechanisms:
        raise ValidationError("compiled industry mechanisms are required")
    validate_industry_state_integrity(industry_state, mechanisms=mechanisms)
    if scenario.parent_industry_state_id != industry_state.id:
        raise ValidationError("scenario must belong to industry state")
    try:
        expected = compile_industry_scenario(
            parent=industry_state,
            spec=ScenarioSpec(scenario.kind, scenario.overrides, scenario.falsifier_keys),
            mechanisms=mechanisms,
        )
    except ValidationError as exc:
        raise ValidationError("scenario does not match verified industry state and mechanisms") from exc
    expected_fields = (
        "kind",
        "parent_industry_state_id",
        "overrides",
        "battery_demand_gwh",
        "effective_capacity_gwh",
        "utilization",
        "inventory_change_gwh",
        "price_range_cny_per_kwh",
        "unit_cost_range_cny_per_kwh",
        "industry_profit_pool_range_cny",
        "falsifier_keys",
        "probability",
    )
    if any(getattr(scenario, field) != getattr(expected, field) for field in expected_fields):
        raise ValidationError("scenario does not match verified industry state and mechanisms")
    return VerifiedIndustryDependency(
        industry_state=industry_state,
        scenario=scenario,
        compiled_mechanisms=mechanisms,
        scenario_content_hash=hashlib.sha256(
            json.dumps(
                asdict(scenario),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
    )


def build_segment(value: SegmentInputs) -> SegmentEconomics:
    """Compile one segment without allocating company-only financial lines."""
    if type(value) is not SegmentInputs:
        raise ValidationError("segment inputs are required")
    physical = value.volume_gwh is not None
    try:
        calculation_values = (
            tuple(item for item in (value.volume_gwh, value.asp_cny_per_kwh, value.unit_cash_cost_cny_per_kwh) if item is not None)
            + (
                value.operating_expense,
                value.depreciation,
                value.cash_capex,
                value.working_capital_change,
                value.cash_tax,
            )
            + tuple(item for item in (value.reported_revenue, value.reported_cost) if item is not None)
        )
        with localcontext(_context(calculation_values)):
            if physical:
                assert value.volume_gwh is not None
                assert value.asp_cny_per_kwh is not None
                assert value.unit_cash_cost_cny_per_kwh is not None
                derived_revenue = value.volume_gwh * _GWH_TO_KWH * value.asp_cny_per_kwh
                derived_cost = value.volume_gwh * _GWH_TO_KWH * value.unit_cash_cost_cny_per_kwh
                revenue = value.reported_revenue if value.reported_revenue is not None else derived_revenue
                cost = value.reported_cost if value.reported_cost is not None else derived_cost
                revenue_basis = "reported" if value.reported_revenue is not None else "derived"
                cost_basis = "reported" if value.reported_cost is not None else "derived"
            else:
                assert value.reported_revenue is not None and value.reported_cost is not None
                revenue = value.reported_revenue
                cost = value.reported_cost
                revenue_basis = "reported"
                cost_basis = "reported"
            gross_profit = revenue - cost
            operating_profit = gross_profit - value.operating_expense
            nopat = operating_profit - value.cash_tax
            free_cash_flow = (
                nopat + value.depreciation - value.cash_capex - value.working_capital_change
            )
    except DecimalException as exc:
        raise ValidationError(f"segment decimal arithmetic is invalid: {exc}") from exc
    return SegmentEconomics(
        key=value.key,
        revenue=revenue,
        cost=cost,
        gross_profit=gross_profit,
        operating_expense=value.operating_expense,
        operating_profit=operating_profit,
        cash_tax=value.cash_tax,
        nopat=nopat,
        depreciation=value.depreciation,
        cash_capex=value.cash_capex,
        working_capital_change=value.working_capital_change,
        free_cash_flow=free_cash_flow,
        revenue_basis=revenue_basis,
        cost_basis=cost_basis,
        volume_gwh=value.volume_gwh,
        asp_cny_per_kwh=value.asp_cny_per_kwh,
        unit_cash_cost_cny_per_kwh=value.unit_cash_cost_cny_per_kwh,
        asp_derivation_metric_ids=(
            value.asp_derivation_metric_ids
            if value.asp_derivation_metric_ids is not None
            else (
                (f"segment.{value.key}.revenue", f"segment.{value.key}.volume_gwh")
                if physical
                else None
            )
        ),
        unit_cost_derivation_metric_ids=(
            value.unit_cost_derivation_metric_ids
            if value.unit_cost_derivation_metric_ids is not None
            else (
                (f"segment.{value.key}.cost", f"segment.{value.key}.volume_gwh")
                if physical
                else None
            )
        ),
        normalized_cash_earning_power=value.normalized_cash_earning_power,
    )


def build_company_engine(
    *,
    company_total: Decimal,
    segments: tuple[SegmentEconomics, ...],
    company_total_cost: Decimal | None = None,
    reported_operating_cash_flow: Decimal | None = None,
    reported_cash_capex: Decimal | None = None,
    diluted_shares: Decimal | None = None,
    industry_state: IndustryState | None = None,
    mechanisms: CompiledMechanisms | None = None,
    scenario: IndustryScenario | None = None,
    exposures: tuple[CompanyExposure, ...] = tuple(),
    tolerance: Decimal = _DEFAULT_TOLERANCE,
) -> EarningsEngine:
    """Close the company bridge, failing rather than masking a broken total."""
    _finite(company_total, "company_total", nonnegative=True)
    _finite(tolerance, "tolerance", nonnegative=True)
    if tolerance != _DEFAULT_TOLERANCE:
        raise ValidationError("company reconciliation tolerance must be exactly CNY 1000")
    if company_total_cost is not None:
        _finite(company_total_cost, "company_total_cost", nonnegative=True)
    if not isinstance(segments, tuple) or not segments or not all(type(item) is SegmentEconomics for item in segments):
        raise ValidationError("company segments are required")
    keys = tuple(item.key for item in segments)
    if len(keys) != len(set(keys)):
        raise ValidationError("company segment keys must be unique")
    if not isinstance(exposures, tuple) or not all(type(item) is CompanyExposure for item in exposures):
        raise ValidationError("company exposures are invalid")
    if scenario is None:
        if industry_state is not None or mechanisms is not None or exposures:
            raise ValidationError("industry scenario and exposures must be provided together")
        industry_state_id = None
    else:
        if industry_state is None or mechanisms is None:
            raise ValidationError("verified industry state, scenario, and mechanisms are required")
        industry_dependency = _verify_industry_dependency(
            industry_state=industry_state,
            scenario=scenario,
            mechanisms=mechanisms,
        )
        industry_state_id = industry_state.id
    if scenario is None:
        industry_dependency = None
    bridge_values = (
        company_total,
        tolerance,
        *tuple(segment.revenue for segment in segments),
        *tuple(segment.cost for segment in segments),
        *((company_total_cost,) if company_total_cost is not None else tuple()),
    )
    with localcontext(_context(bridge_values)):
        revenue_reconciliation = reconcile(
            company_total, tuple(segment.revenue for segment in segments), tolerance
        )
        cost_reconciliation = (
            reconcile(company_total_cost, tuple(segment.cost for segment in segments), tolerance)
            if company_total_cost is not None
            else None
        )
    if not revenue_reconciliation.balanced:
        raise ValidationError("revenue does not reconcile")
    if cost_reconciliation is not None and not cost_reconciliation.balanced:
        raise ValidationError("cost does not reconcile")
    if (reported_operating_cash_flow is None) != (reported_cash_capex is None):
        raise ValidationError("reported OCF and cash capex must be provided together")
    if reported_operating_cash_flow is not None:
        _finite(reported_operating_cash_flow, "reported_operating_cash_flow")
        assert reported_cash_capex is not None
        _finite(reported_cash_capex, "reported_cash_capex", nonnegative=True)
        with localcontext(_context((reported_operating_cash_flow, reported_cash_capex))):
            reported_fcf_proxy = reported_operating_cash_flow - reported_cash_capex
    else:
        reported_fcf_proxy = None
    if diluted_shares is not None:
        _finite(diluted_shares, "diluted_shares", nonnegative=True)
        if diluted_shares <= 0:
            raise ValidationError("diluted_shares must be greater than zero")
    modeled_operating_profit = _sum(tuple(segment.operating_profit for segment in segments))
    modeled_nopat = _sum(tuple(segment.nopat for segment in segments))
    modeled_fcf = _sum(tuple(segment.free_cash_flow for segment in segments))
    cash_taxes = _sum(tuple(segment.cash_tax for segment in segments))
    depreciation = _sum(tuple(segment.depreciation for segment in segments))
    cash_capex = _sum(tuple(segment.cash_capex for segment in segments))
    working_capital = _sum(tuple(segment.working_capital_change for segment in segments))
    with localcontext(_context((modeled_operating_profit, modeled_nopat, modeled_fcf, cash_taxes, depreciation, cash_capex, working_capital, tolerance))):
        op_to_nopat = reconcile(modeled_operating_profit, (modeled_nopat + cash_taxes,), tolerance)
        nopat_to_fcf = reconcile(
            modeled_nopat + depreciation,
            (modeled_fcf + cash_capex + working_capital,),
            tolerance,
        )
    # These are formula identities.  Treat a failure as a corruption signal,
    # not something downstream may smooth over.
    if not op_to_nopat.balanced or not nopat_to_fcf.balanced:
        raise ValidationError("operating profit to FCF does not reconcile")
    segment_revenue = tuple(
        _physical_bridge(segment, is_cost=False, tolerance=tolerance)
        for segment in segments
        if segment.volume_gwh is not None
    )
    segment_cost = tuple(
        _physical_bridge(segment, is_cost=True, tolerance=tolerance)
        for segment in segments
        if segment.volume_gwh is not None
    )
    if not all(item.balanced for item in (*segment_revenue, *segment_cost)):
        raise ValidationError("segment volume-price-cost bridge does not reconcile")
    core_views = derive_four_core_views(
        segments,
        industry_state_id=industry_state_id,
        scenario=scenario,
        exposures=exposures,
    )
    if diluted_shares is not None:
        with localcontext(_context((modeled_nopat, diluted_shares))):
            modeled_nopat_per_share = modeled_nopat / diluted_shares
    else:
        modeled_nopat_per_share = None
    return EarningsEngine(
        segments=segments,
        company_revenue=company_total,
        company_cost=company_total_cost,
        modeled_operating_profit=modeled_operating_profit,
        modeled_nopat=modeled_nopat,
        modeled_free_cash_flow=modeled_fcf,
        reported_operating_cash_flow=reported_operating_cash_flow,
        reported_cash_capex=reported_cash_capex,
        reported_fcf_proxy=reported_fcf_proxy,
        diluted_shares=diluted_shares,
        modeled_nopat_per_share=modeled_nopat_per_share,
        four_core_views=core_views,
        reconciliations=EarningsReconciliations(
            revenue= revenue_reconciliation,
            cost=cost_reconciliation,
            operating_profit_to_nopat=op_to_nopat,
            nopat_to_free_cash_flow=nopat_to_fcf,
            segment_revenue=segment_revenue,
            segment_cost=segment_cost,
        ),
        industry_state_id=industry_state_id,
        scenario=scenario,
        exposures=exposures,
        industry_dependency=industry_dependency,
    )
