"""Fail-closed power-battery industry-state and scenario compiler."""
from __future__ import annotations

from dataclasses import replace
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from typing import Iterable
from uuid import UUID, NAMESPACE_URL, uuid5

from app.models.ledger import ValidationError
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.domain.industry import (
    AnswerabilityBlocked,
    IndustryInputs,
    IndustryMetric,
    IndustryMetricCollection,
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
    validate_compiled_mechanism_integrity,
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
_INPUT_FIELDS = (
    "ev_sales_millions",
    "average_battery_kwh",
    "storage_demand_gwh",
    "nominal_capacity_gwh",
    "commissioned_share",
    "certified_share",
    "yield_rate",
    "shipments_gwh",
    "production_gwh",
    "cell_asp_cny_per_kwh",
    "unit_cash_cost_cny_per_kwh",
)


def _block(code: BlockerCode, detail: str) -> None:
    raise AnswerabilityBlocked(code, detail)


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _calculation_context(values: tuple[Decimal, ...]) -> Context:
    """Use enough precision for inputs and every explicit output quantum.

    This isolates calculation semantics from a request worker's ambient
    Decimal context, which may be intentionally low for unrelated work.
    """
    input_digits = sum(len(value.as_tuple().digits) for value in values)
    return Context(
        prec=max(64, input_digits + 40),
        rounding=ROUND_HALF_EVEN,
        Emin=-999999999,
        Emax=999999999,
    )


def _formal_mechanisms(
    mechanisms: CompiledMechanisms,
) -> tuple[MechanismPack, ...]:
    if type(mechanisms) is not CompiledMechanisms:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "formal industry mechanisms are required")
    try:
        validate_compiled_mechanism_integrity(mechanisms)
    except ValidationError as exc:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, str(exc))
    packs = mechanisms.mechanisms
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
    if len({pack.scope_object_id for pack in packs}) != 1:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, "industry state requires exactly one mechanism scope")
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

    decimal_inputs = (
        ev_sales,
        average_battery,
        storage,
        nominal,
        commissioned,
        certified,
        yield_rate,
        shipments,
        production,
        asp,
        cash_cost,
    )
    try:
        with localcontext(_calculation_context(decimal_inputs)):
            demand = _quantize(ev_sales * average_battery + storage, _QUANTITY_QUANTUM)
            nominal_output = _quantize(nominal, _QUANTITY_QUANTUM)
            effective_capacity = _quantize(
                nominal * commissioned * certified * yield_rate, _QUANTITY_QUANTUM
            )
            if effective_capacity <= 0:
                _block(
                    BlockerCode.MISSING_KEY_BASELINE,
                    "effective_capacity_gwh must be greater than zero",
                )
            utilization = _quantize(shipments / effective_capacity, _RATIO_QUANTUM)
            inventory_change = _quantize(production - shipments, _QUANTITY_QUANTUM)
            unit_margin = _quantize(asp - cash_cost, _UNIT_ECONOMICS_QUANTUM)
            price_range = IndustryRange(
                _quantize(asp, _UNIT_ECONOMICS_QUANTUM),
                _quantize(asp, _UNIT_ECONOMICS_QUANTUM),
            )
            unit_cost_range = IndustryRange(
                _quantize(cash_cost, _UNIT_ECONOMICS_QUANTUM),
                _quantize(cash_cost, _UNIT_ECONOMICS_QUANTUM),
            )
            profit_pool = _quantize(
                shipments * _GWH_TO_KWH * unit_margin, _UNIT_ECONOMICS_QUANTUM
            )
            profit_pool_range = IndustryRange(profit_pool, profit_pool)
    except DecimalException as exc:
        _block(BlockerCode.MISSING_KEY_BASELINE, f"industry decimal arithmetic is invalid: {exc}")
    return (
        demand,
        nominal_output,
        effective_capacity,
        utilization,
        inventory_change,
        unit_margin,
        price_range,
        unit_cost_range,
        profit_pool_range,
    )


def _mechanism_lineage(mechanisms: Iterable[MechanismPack]) -> tuple[tuple[str, UUID, int], ...]:
    return tuple(
        sorted(
            (mechanism.key, mechanism.scope_object_id, mechanism.version)
            for mechanism in mechanisms
        )
    )


def _metric_collection(
    values: tuple[
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        IndustryRange,
        IndustryRange,
        IndustryRange,
    ],
) -> IndustryMetricCollection:
    return IndustryMetricCollection(
        (
            IndustryMetric("industry.battery_demand_gwh", values[0]),
            IndustryMetric("industry.nominal_capacity_gwh", values[1]),
            IndustryMetric("industry.effective_capacity_gwh", values[2]),
            IndustryMetric("industry.utilization", values[3]),
            IndustryMetric("industry.inventory_change_gwh", values[4]),
            IndustryMetric("industry.unit_margin_cny_per_kwh", values[5]),
        )
    )


def _range_payload(value: IndustryRange) -> dict[str, str]:
    return {"low": str(value.low), "high": str(value.high)}


def _state_content_hash(
    *,
    state_id: UUID,
    inputs: IndustryInputs,
    values: tuple[
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        IndustryRange,
        IndustryRange,
        IndustryRange,
    ],
    metrics: IndustryMetricCollection,
    mechanism_lineage: tuple[tuple[str, UUID, int], ...],
    falsifier_keys: tuple[str, ...],
    compiled_mechanism_hash: str,
) -> str:
    """Hash every replay-relevant state field, including its mechanism binding."""
    return canonical_hash(
        {
            "state_id": str(state_id),
            "inputs": {
                field_name: (
                    str(getattr(inputs, field_name))
                    if getattr(inputs, field_name) is not None
                    else None
                )
                for field_name in _INPUT_FIELDS
            },
            "outputs": {
                "battery_demand_gwh": str(values[0]),
                "nominal_capacity_gwh": str(values[1]),
                "effective_capacity_gwh": str(values[2]),
                "utilization": str(values[3]),
                "inventory_change_gwh": str(values[4]),
                "unit_margin_cny_per_kwh": str(values[5]),
                "price_range_cny_per_kwh": _range_payload(values[6]),
                "unit_cost_range_cny_per_kwh": _range_payload(values[7]),
                "industry_profit_pool_range_cny": _range_payload(values[8]),
            },
            "metrics": tuple(
                {"key": metric.key, "value": str(metric.value)} for metric in metrics.values
            ),
            "mechanism_lineage": tuple(
                {"key": key, "scope_object_id": str(scope_id), "version": version}
                for key, scope_id, version in mechanism_lineage
            ),
            "falsifier_keys": falsifier_keys,
            "compiled_mechanism_hash": compiled_mechanism_hash,
        }
    )


def validate_industry_state_integrity(
    value: IndustryState,
    *,
    mechanisms: CompiledMechanisms,
) -> None:
    """Recompute an industry state before permitting it to parent a scenario."""
    if type(value) is not IndustryState:
        raise ValidationError("industry state is required")
    if type(mechanisms) is not CompiledMechanisms:
        raise ValidationError("compiled mechanisms are required")
    try:
        validate_compiled_mechanism_integrity(mechanisms)
        formal_mechanisms = tuple(mechanisms.mechanisms)
        values = _compile_values(value.inputs)
        expected_lineage = _mechanism_lineage(formal_mechanisms)
        expected_falsifiers = _falsifier_keys(formal_mechanisms)
        expected_metrics = _metric_collection(values)
        expected_content_hash = _state_content_hash(
            state_id=value.id,
            inputs=value.inputs,
            values=values,
            metrics=expected_metrics,
            mechanism_lineage=expected_lineage,
            falsifier_keys=expected_falsifiers,
            compiled_mechanism_hash=mechanisms.content_hash,
        )
    except AnswerabilityBlocked as exc:
        raise ValidationError("industry state inputs are not answerable") from exc
    except (AttributeError, TypeError, ValueError, DecimalException) as exc:
        raise ValidationError("industry state integrity is invalid") from exc
    if value.mechanism_lineage != expected_lineage:
        raise ValidationError("industry state mechanism lineage does not match compiled mechanisms")
    if value.compiled_mechanism_hash != mechanisms.content_hash:
        raise ValidationError("industry state is bound to a different compiled mechanism")
    if value.content_hash != expected_content_hash:
        raise ValidationError("industry state content hash does not match its replay inputs")
    if (
        not isinstance(value.falsifier_keys, tuple)
        or not all(isinstance(key, str) and key.strip() for key in value.falsifier_keys)
        or value.falsifier_keys != expected_falsifiers
        or len(value.falsifier_keys) != len(set(value.falsifier_keys))
    ):
        raise ValidationError("industry state falsifiers do not match compiled mechanisms")
    expected_outputs = (
        ("battery_demand_gwh", values[0]),
        ("nominal_capacity_gwh", values[1]),
        ("effective_capacity_gwh", values[2]),
        ("utilization", values[3]),
        ("inventory_change_gwh", values[4]),
        ("unit_margin_cny_per_kwh", values[5]),
        ("price_range_cny_per_kwh", values[6]),
        ("unit_cost_range_cny_per_kwh", values[7]),
        ("industry_profit_pool_range_cny", values[8]),
        ("metrics", expected_metrics),
    )
    if any(getattr(value, field_name) != expected for field_name, expected in expected_outputs):
        raise ValidationError("industry state outputs do not match its inputs")


def compile_industry_state(
    *,
    inputs: IndustryInputs,
    mechanisms: CompiledMechanisms,
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
    lineage = _mechanism_lineage(formal_mechanisms)
    falsifier_keys = _falsifier_keys(formal_mechanisms)
    metrics = _metric_collection(values)
    state_id = new_industry_state_id()
    state_content_hash = _state_content_hash(
        state_id=state_id,
        inputs=inputs,
        values=values,
        metrics=metrics,
        mechanism_lineage=lineage,
        falsifier_keys=falsifier_keys,
        compiled_mechanism_hash=mechanisms.content_hash,
    )
    return IndustryState(
        id=state_id,
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
        metrics=metrics,
        mechanism_lineage=lineage,
        falsifier_keys=falsifier_keys,
        compiled_mechanism_hash=mechanisms.content_hash,
        content_hash=state_content_hash,
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
    mechanisms: CompiledMechanisms,
) -> IndustryScenario:
    """Apply declared driver overrides while retaining the exact state parent."""
    if type(parent) is not IndustryState:
        _block(BlockerCode.MISSING_KEY_BASELINE, "industry-state parent is required")
    if type(spec) is not ScenarioSpec:
        _block(BlockerCode.MISSING_KEY_BASELINE, "scenario specification is required")
    formal_mechanisms = _formal_mechanisms(mechanisms)
    try:
        validate_industry_state_integrity(parent, mechanisms=mechanisms)
    except ValidationError as exc:
        _block(BlockerCode.MECHANISM_UNIDENTIFIED, str(exc))
    lineage = _mechanism_lineage(formal_mechanisms)
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
        id=uuid5(
            NAMESPACE_URL,
            canonical_hash({"parent_industry_state_id": str(parent.id), "kind": spec.kind.value, "overrides": spec.overrides, "falsifiers": spec.falsifiers}),
        ),
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
    mechanisms: CompiledMechanisms,
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
