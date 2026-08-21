"""Fail-closed power-battery industry-state and scenario compiler."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Iterable

from app.models.ledger import ValidationError
from app.underwriting.domain.industry import (
    AnswerabilityBlocked,
    IndustryInputs,
    IndustryRange,
    IndustryScenario,
    IndustryState,
    ScenarioDriverOverride,
    ScenarioKind,
    ScenarioSpec,
    new_industry_scenario_id,
    new_industry_state_id,
)
from app.underwriting.domain.mechanisms import MechanismPack
from app.underwriting.domain.types import BlockerCode
from app.underwriting.services.mechanism_compiler import (
    CompiledMechanisms,
    validate_formal_mechanism,
)


_QUANTITY_QUANTUM = Decimal("0.0001")
_UNIT_ECONOMICS_QUANTUM = Decimal("0.0001")
# The explicit quantum preserves the correctly-rounded Decimal division used
# in the plan while avoiding an implicit, ambient precision decision.
_RATIO_QUANTUM = Decimal("0.0000000000000000000000000001")
_GWH_TO_KWH = Decimal("1000000")

_INPUT_FIELD_BY_DRIVER = {
    "industry.ev_sales_millions": "ev_sales_millions",
    "industry.average_battery_kwh": "average_battery_kwh",
    "industry.storage_demand_gwh": "storage_demand_gwh",
    "industry.nominal_capacity_gwh": "nominal_capacity_gwh",
    "industry.commissioned_share": "commissioned_share",
    "industry.certified_share": "certified_share",
    "industry.yield_rate": "yield_rate",
    "industry.shipments_gwh": "shipments_gwh",
    "industry.production_gwh": "production_gwh",
    "industry.cell_asp_cny_per_kwh": "cell_asp_cny_per_kwh",
    "industry.unit_cash_cost_cny_per_kwh": "unit_cash_cost_cny_per_kwh",
}
_PHYSICAL_INPUT_FIELDS = frozenset(
    {
        "ev_sales_millions",
        "average_battery_kwh",
        "storage_demand_gwh",
        "nominal_capacity_gwh",
        "shipments_gwh",
        "production_gwh",
        "cell_asp_cny_per_kwh",
        "unit_cash_cost_cny_per_kwh",
    }
)
_SHARE_INPUT_FIELDS = frozenset({"commissioned_share", "certified_share", "yield_rate"})


def _block(code: BlockerCode, detail: str) -> None:
    raise AnswerabilityBlocked(code, detail)


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _formal_mechanisms(
    mechanisms: tuple[MechanismPack, ...] | CompiledMechanisms,
) -> tuple[MechanismPack, ...]:
    if type(mechanisms) is CompiledMechanisms:
        packs = mechanisms.mechanisms
    elif isinstance(mechanisms, tuple):
        packs = mechanisms
    else:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "formal industry mechanisms are required")
    if not packs:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "formal industry mechanisms are required")
    for pack in packs:
        if type(pack) is not MechanismPack:
            _block(BlockerCode.MECHANISM_UNIDENTIFIED, "mechanism contract is invalid")
        try:
            validate_formal_mechanism(pack)
        except ValidationError as exc:
            _block(BlockerCode.MECHANISM_UNIDENTIFIED, str(exc))
    keys = tuple(pack.key for pack in packs)
    if len(keys) != len(set(keys)):
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "formal mechanism keys must be unique")
    return tuple(packs)


def _driver_keys(mechanisms: Iterable[MechanismPack]) -> frozenset[str]:
    return frozenset(driver for mechanism in mechanisms for driver in mechanism.driver_keys)


def _falsifier_keys(mechanisms: Iterable[MechanismPack]) -> tuple[str, ...]:
    keys = tuple(
        falsifier.key for mechanism in mechanisms for falsifier in mechanism.falsifiers
    )
    return tuple(dict.fromkeys(keys))


def _validate_baselines(inputs: IndustryInputs) -> None:
    if type(inputs) is not IndustryInputs:
        _block(BlockerCode.MISSING_KEY_BASELINE, "industry inputs are required")
    for field_name in _INPUT_FIELD_BY_DRIVER.values():
        value = getattr(inputs, field_name)
        if not isinstance(value, Decimal) or not value.is_finite():
            _block(BlockerCode.MISSING_KEY_BASELINE, f"{field_name} is required")
        if field_name in _PHYSICAL_INPUT_FIELDS and value < 0:
            _block(BlockerCode.MISSING_KEY_BASELINE, f"{field_name} must not be negative")
        if field_name in _SHARE_INPUT_FIELDS and not Decimal("0") <= value <= Decimal("1"):
            _block(BlockerCode.MISSING_KEY_BASELINE, f"{field_name} must be between 0 and 1")


def _compile_values(inputs: IndustryInputs) -> tuple[
    Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, IndustryRange, IndustryRange, IndustryRange
]:
    _validate_baselines(inputs)
    # Validation above narrows the domain values; assignment keeps the public
    # IndustryInputs nullable so a missing baseline can have answerability semantics.
    ev_sales = inputs.ev_sales_millions
    average_battery = inputs.average_battery_kwh
    storage = inputs.storage_demand_gwh
    nominal = inputs.nominal_capacity_gwh
    commissioned = inputs.commissioned_share
    certified = inputs.certified_share
    yield_rate = inputs.yield_rate
    shipments = inputs.shipments_gwh
    production = inputs.production_gwh
    asp = inputs.cell_asp_cny_per_kwh
    cash_cost = inputs.unit_cash_cost_cny_per_kwh
    assert all(
        isinstance(value, Decimal)
        for value in (
            ev_sales, average_battery, storage, nominal, commissioned, certified,
            yield_rate, shipments, production, asp, cash_cost,
        )
    )

    demand = _quantize(ev_sales * average_battery + storage, _QUANTITY_QUANTUM)
    effective_capacity = _quantize(
        nominal * commissioned * certified * yield_rate, _QUANTITY_QUANTUM
    )
    if effective_capacity <= 0:
        _block(BlockerCode.MISSING_KEY_BASELINE, "effective_capacity_gwh must be greater than zero")
    utilization = _quantize(shipments / effective_capacity, _RATIO_QUANTUM)
    inventory_change = _quantize(production - shipments, _QUANTITY_QUANTUM)
    unit_margin = _quantize(asp - cash_cost, _UNIT_ECONOMICS_QUANTUM)
    price_range = IndustryRange(
        _quantize(asp, _UNIT_ECONOMICS_QUANTUM), _quantize(asp, _UNIT_ECONOMICS_QUANTUM)
    )
    unit_cost_range = IndustryRange(
        _quantize(cash_cost, _UNIT_ECONOMICS_QUANTUM),
        _quantize(cash_cost, _UNIT_ECONOMICS_QUANTUM),
    )
    profit_pool = _quantize(shipments * _GWH_TO_KWH * unit_margin, Decimal("0.0001"))
    profit_pool_range = IndustryRange(profit_pool, profit_pool)
    return (
        demand,
        _quantize(nominal, _QUANTITY_QUANTUM),
        effective_capacity,
        utilization,
        inventory_change,
        unit_margin,
        price_range,
        unit_cost_range,
        profit_pool_range,
    )


def compile_industry_state(
    *,
    inputs: IndustryInputs,
    mechanisms: tuple[MechanismPack, ...] | CompiledMechanisms,
) -> IndustryState:
    """Compile a single power-battery state from formal mechanisms only."""
    formal_mechanisms = _formal_mechanisms(mechanisms)
    drivers = _driver_keys(formal_mechanisms)
    unknown = drivers.difference(_INPUT_FIELD_BY_DRIVER)
    if unknown:
        _block(
            BlockerCode.MECHANISM_UNIDENTIFIED,
            f"formal mechanism references unsupported industry driver {sorted(unknown)[0]}",
        )
    values = _compile_values(inputs)
    lineage = tuple(
        sorted((mechanism.key, mechanism.scope_object_id, mechanism.version) for mechanism in formal_mechanisms)
    )
    return IndustryState(
        id=new_industry_state_id(),
        inputs=inputs,
        battery_demand_gwh=values[0],
        nominal_capacity_gwh=values[1],
        effective_capacity_gwh=values[2],
        utilization=values[3],
        inventory_change_gwh=values[4],
        unit_margin_cny_per_kwh=values[5],
        price_range_cny_per_kwh=values[6],
        unit_cost_range_cny_per_kwh=values[7],
        industry_profit_pool_range_cny=values[8],
        mechanism_lineage=lineage,
        falsifier_keys=_falsifier_keys(formal_mechanisms),
    )


def _apply_overrides(inputs: IndustryInputs, overrides: tuple[ScenarioDriverOverride, ...]) -> IndustryInputs:
    changes: dict[str, Decimal] = {}
    for override in overrides:
        field_name = _INPUT_FIELD_BY_DRIVER.get(override.driver_key)
        if field_name is None:
            _block(
                BlockerCode.MECHANISM_UNIDENTIFIED,
                f"scenario override references unsupported industry driver {override.driver_key}",
            )
        changes[field_name] = override.value
    return replace(inputs, **changes)


def compile_industry_scenario(
    *,
    parent: IndustryState,
    spec: ScenarioSpec,
    mechanisms: tuple[MechanismPack, ...] | CompiledMechanisms,
) -> IndustryScenario:
    """Apply declared driver overrides while retaining the exact state parent."""
    if type(parent) is not IndustryState:
        _block(BlockerCode.MISSING_KEY_BASELINE, "industry-state parent is required")
    if type(spec) is not ScenarioSpec:
        _block(BlockerCode.MISSING_KEY_BASELINE, "scenario specification is required")
    formal_mechanisms = _formal_mechanisms(mechanisms)
    lineage = tuple(
        sorted((mechanism.key, mechanism.scope_object_id, mechanism.version) for mechanism in formal_mechanisms)
    )
    if lineage != parent.mechanism_lineage:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "scenario mechanisms must match the parent state")
    declared_drivers = _driver_keys(formal_mechanisms)
    for override in spec.overrides:
        if override.driver_key not in declared_drivers:
            _block(
                BlockerCode.MECHANISM_UNIDENTIFIED,
                f"scenario override is not a declared mechanism driver: {override.driver_key}",
            )
    available_falsifiers = set(_falsifier_keys(formal_mechanisms))
    if any(key not in available_falsifiers for key in spec.falsifiers):
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "scenario falsifier is not declared by a formal mechanism")
    scenario_inputs = _apply_overrides(parent.inputs, spec.overrides)
    values = _compile_values(scenario_inputs)
    return IndustryScenario(
        id=new_industry_scenario_id(),
        kind=spec.kind,
        parent_industry_state_id=parent.id,
        overrides=spec.overrides,
        battery_demand_gwh=values[0],
        effective_capacity_gwh=values[2],
        utilization=values[3],
        inventory_change_gwh=values[4],
        price_range_cny_per_kwh=values[6],
        unit_cost_range_cny_per_kwh=values[7],
        industry_profit_pool_range_cny=values[8],
        falsifier_keys=spec.falsifiers,
    )


def compile_industry_scenarios(
    *,
    parent: IndustryState,
    specs: tuple[ScenarioSpec, ...],
    mechanisms: tuple[MechanismPack, ...] | CompiledMechanisms,
) -> tuple[IndustryScenario, ...]:
    """Compile one base, one upside and one downside branch without weights."""
    if not isinstance(specs, tuple):
        _block(BlockerCode.MISSING_KEY_BASELINE, "scenario specifications must be a tuple")
    kinds = tuple(spec.kind for spec in specs if type(spec) is ScenarioSpec)
    if len(kinds) != len(specs) or set(kinds) != set(ScenarioKind) or len(kinds) != len(set(kinds)):
        _block(BlockerCode.MISSING_KEY_BASELINE, "base, upside, and downside scenarios are required exactly once")
    return tuple(
        compile_industry_scenario(parent=parent, spec=spec, mechanisms=mechanisms)
        for spec in specs
    )
