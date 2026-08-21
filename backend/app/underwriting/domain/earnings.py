"""Immutable financial contracts for a company segment earnings bridge.

This module intentionally models economics before valuation.  A result can
describe revenue, profit, cash and normalized earning power, but cannot carry
a price, a multiple, a discount rate or a target price.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from typing import Iterable
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.industry import IndustryScenario
from app.underwriting.domain.metrics import ReconciliationResult, reconcile


_UNALLOCATED_COMPANY_KEY = "unallocated_company"
_COMPANY_RECONCILIATION_TOLERANCE = Decimal("1000")
_VALUATION_TERMS = (
    "price",
    "multiple",
    "discount",
    "target",
    "dcf",
    "fair value",
    "enterprise value",
    "valuation",
)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must not be empty")
    return value.strip()


def _decimal(value: object, name: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{name} must be a finite Decimal")
    if nonnegative and value < 0:
        raise ValidationError(f"{name} must not be negative")
    return value


def _metric_ids(value: object, name: str) -> tuple[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValidationError(f"{name} must contain numerator and denominator metric IDs")
    numerator, denominator = (_text(item, name) for item in value)
    if numerator == denominator:
        raise ValidationError(f"{name} numerator and denominator must differ")
    return numerator, denominator


def _calculation_context(values: tuple[Decimal, ...]) -> Context:
    return Context(
        # Fixed precision keeps a replay byte-for-byte independent of both
        # the ambient worker context and the unrelated number of segments.
        prec=max(128, sum(len(value.as_tuple().digits) for value in values) + 32),
        rounding=ROUND_HALF_EVEN,
        Emin=-999999999,
        Emax=999999999,
    )


@dataclass(frozen=True, slots=True)
class SegmentInputs:
    """Authorized inputs for one company segment.

    A row uses either a physical volume/price/cost bridge or directly reported
    revenue/cost.  It never silently converts a reported row into a derived
    one.  Optional derivation IDs describe the exact numerator/denominator
    used to obtain an ASP or unit cost from frozen observations.
    """

    key: str
    volume_gwh: Decimal | None
    asp_cny_per_kwh: Decimal | None
    unit_cash_cost_cny_per_kwh: Decimal | None
    operating_expense: Decimal
    depreciation: Decimal
    cash_capex: Decimal
    working_capital_change: Decimal
    cash_tax: Decimal
    reported_revenue: Decimal | None = None
    reported_cost: Decimal | None = None
    asp_derivation_metric_ids: tuple[str, str] | None = None
    unit_cost_derivation_metric_ids: tuple[str, str] | None = None
    normalized_cash_earning_power: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "segment key"))
        physical = (
            self.volume_gwh,
            self.asp_cny_per_kwh,
            self.unit_cash_cost_cny_per_kwh,
        )
        if any(value is None for value in physical) and any(value is not None for value in physical):
            raise ValidationError("volume, ASP, and unit cash cost must be supplied together")
        if all(value is None for value in physical):
            if self.reported_revenue is None or self.reported_cost is None:
                raise ValidationError("reported revenue and cost are required without a physical bridge")
        else:
            _decimal(self.volume_gwh, "volume_gwh", nonnegative=True)
            _decimal(self.asp_cny_per_kwh, "asp_cny_per_kwh", nonnegative=True)
            _decimal(self.unit_cash_cost_cny_per_kwh, "unit_cash_cost_cny_per_kwh", nonnegative=True)
            if (
                self.asp_derivation_metric_ids is None
                or self.unit_cost_derivation_metric_ids is None
            ):
                raise ValidationError("derived physical inputs require parent metric IDs")
        if self.reported_revenue is not None:
            _decimal(self.reported_revenue, "reported_revenue", nonnegative=True)
        if self.reported_cost is not None:
            _decimal(self.reported_cost, "reported_cost", nonnegative=True)
        for name in (
            "operating_expense",
            "depreciation",
            "cash_capex",
            "working_capital_change",
            "cash_tax",
        ):
            _decimal(getattr(self, name), name, nonnegative=True)
        _metric_ids(self.asp_derivation_metric_ids, "asp_derivation_metric_ids")
        _metric_ids(self.unit_cost_derivation_metric_ids, "unit_cost_derivation_metric_ids")
        if self.normalized_cash_earning_power is not None:
            _decimal(self.normalized_cash_earning_power, "normalized_cash_earning_power")
        if self.key == _UNALLOCATED_COMPANY_KEY and any(value is not None for value in physical):
            raise ValidationError("unallocated_company must not contain a physical segment bridge")

    @classmethod
    def unallocated_company(
        cls,
        *,
        operating_expense: Decimal,
        depreciation: Decimal,
        cash_capex: Decimal,
        working_capital_change: Decimal,
        cash_tax: Decimal,
    ) -> "SegmentInputs":
        """Represent company-only financial lines without arbitrary allocation."""
        return cls(
            key=_UNALLOCATED_COMPANY_KEY,
            volume_gwh=None,
            asp_cny_per_kwh=None,
            unit_cash_cost_cny_per_kwh=None,
            reported_revenue=Decimal("0"),
            reported_cost=Decimal("0"),
            operating_expense=operating_expense,
            depreciation=depreciation,
            cash_capex=cash_capex,
            working_capital_change=working_capital_change,
            cash_tax=cash_tax,
        )


@dataclass(frozen=True, slots=True)
class SegmentEconomics:
    """The deterministic revenue-to-cash bridge for a single segment."""

    key: str
    revenue: Decimal
    cost: Decimal
    gross_profit: Decimal
    operating_expense: Decimal
    operating_profit: Decimal
    cash_tax: Decimal
    nopat: Decimal
    depreciation: Decimal
    cash_capex: Decimal
    working_capital_change: Decimal
    free_cash_flow: Decimal
    revenue_basis: str
    cost_basis: str
    volume_gwh: Decimal | None
    asp_cny_per_kwh: Decimal | None
    unit_cash_cost_cny_per_kwh: Decimal | None
    asp_derivation_metric_ids: tuple[str, str] | None
    unit_cost_derivation_metric_ids: tuple[str, str] | None
    normalized_cash_earning_power: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _text(self.key, "segment key"))
        for name in (
            "revenue",
            "cost",
            "gross_profit",
            "operating_expense",
            "operating_profit",
            "cash_tax",
            "nopat",
            "depreciation",
            "cash_capex",
            "working_capital_change",
            "free_cash_flow",
        ):
            _decimal(getattr(self, name), name)
        if self.revenue < 0 or self.cost < 0:
            raise ValidationError("segment revenue and cost must not be negative")
        if self.revenue_basis not in {"reported", "derived"}:
            raise ValidationError("segment revenue_basis is invalid")
        if self.cost_basis not in {"reported", "derived"}:
            raise ValidationError("segment cost_basis is invalid")
        if (self.volume_gwh, self.asp_cny_per_kwh, self.unit_cash_cost_cny_per_kwh).count(None) not in (0, 3):
            raise ValidationError("segment physical bridge is incomplete")
        if self.volume_gwh is not None:
            _decimal(self.volume_gwh, "volume_gwh", nonnegative=True)
            _decimal(self.asp_cny_per_kwh, "asp_cny_per_kwh", nonnegative=True)
            _decimal(self.unit_cash_cost_cny_per_kwh, "unit_cash_cost_cny_per_kwh", nonnegative=True)
            if (
                self.asp_derivation_metric_ids is None
                or self.unit_cost_derivation_metric_ids is None
            ):
                raise ValidationError("derived physical inputs require parent metric IDs")
        _metric_ids(self.asp_derivation_metric_ids, "asp_derivation_metric_ids")
        _metric_ids(self.unit_cost_derivation_metric_ids, "unit_cost_derivation_metric_ids")
        if self.normalized_cash_earning_power is not None:
            _decimal(self.normalized_cash_earning_power, "normalized_cash_earning_power")
        try:
            with localcontext(
                _calculation_context(
                    (
                        self.revenue,
                        self.cost,
                        self.operating_expense,
                        self.cash_tax,
                        self.depreciation,
                        self.cash_capex,
                        self.working_capital_change,
                    )
                )
            ):
                if self.gross_profit != self.revenue - self.cost:
                    raise ValidationError("gross profit does not reconcile")
                if self.operating_profit != self.gross_profit - self.operating_expense:
                    raise ValidationError("operating profit does not reconcile")
                if self.nopat != self.operating_profit - self.cash_tax:
                    raise ValidationError("NOPAT does not reconcile")
                if self.free_cash_flow != (
                    self.nopat
                    + self.depreciation
                    - self.cash_capex
                    - self.working_capital_change
                ):
                    raise ValidationError("free cash flow does not reconcile")
                if self.volume_gwh is not None:
                    expected_revenue = self.volume_gwh * Decimal("1000000") * self.asp_cny_per_kwh
                    expected_cost = (
                        self.volume_gwh * Decimal("1000000") * self.unit_cash_cost_cny_per_kwh
                    )
                    if self.revenue != expected_revenue:
                        raise ValidationError("physical revenue does not reconcile")
                    if self.cost != expected_cost:
                        raise ValidationError("physical cost does not reconcile")
        except DecimalException as exc:
            raise ValidationError(f"segment decimal arithmetic is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CompanyExposure:
    """A declared segment link to exactly one industry-state scenario.

    ``normalized_cash_earning_power_multiplier`` deliberately has no
    probability interpretation.  It is an explicit scenario convention used
    only to select/report a segment's long-run cash-earning-power contribution.
    """

    segment_key: str
    industry_state_id: UUID
    normalized_cash_earning_power_multiplier: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_key", _text(self.segment_key, "exposure segment_key"))
        if self.segment_key == _UNALLOCATED_COMPANY_KEY:
            raise ValidationError("unallocated_company cannot have industry exposure")
        if type(self.industry_state_id) is not UUID:
            raise ValidationError("exposure industry_state_id must be a UUID")
        _decimal(
            self.normalized_cash_earning_power_multiplier,
            "normalized_cash_earning_power_multiplier",
        )


@dataclass(frozen=True, slots=True)
class CoreContribution:
    segment_key: str
    amount: Decimal
    share: Decimal | None
    basis: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_key", _text(self.segment_key, "core segment_key"))
        _decimal(self.amount, "core amount")
        if self.share is not None:
            _decimal(self.share, "core share")
        basis = _text(self.basis, "core basis")
        if any(term in basis.lower() for term in _VALUATION_TERMS):
            raise ValidationError("core basis must not contain valuation terms")
        object.__setattr__(self, "basis", basis)


@dataclass(frozen=True, slots=True)
class FourCoreView:
    revenue_core: tuple[CoreContribution, ...]
    profit_core: tuple[CoreContribution, ...]
    cash_core: tuple[CoreContribution, ...]
    value_core: tuple[CoreContribution, ...]

    def __post_init__(self) -> None:
        for name in ("revenue_core", "profit_core", "cash_core", "value_core"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(type(item) is CoreContribution for item in values):
                raise ValidationError(f"{name} must contain CoreContribution values")


@dataclass(frozen=True, slots=True)
class SegmentBridgeReconciliation:
    segment_key: str
    reconciliation: ReconciliationResult

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_key", _text(self.segment_key, "bridge segment_key"))
        if type(self.reconciliation) is not ReconciliationResult:
            raise ValidationError("bridge reconciliation is invalid")

    @property
    def balanced(self) -> bool:
        return self.reconciliation.balanced


@dataclass(frozen=True, slots=True)
class EarningsReconciliations:
    revenue: ReconciliationResult
    cost: ReconciliationResult | None
    operating_profit_to_nopat: ReconciliationResult
    nopat_to_free_cash_flow: ReconciliationResult
    segment_revenue: tuple[SegmentBridgeReconciliation, ...]
    segment_cost: tuple[SegmentBridgeReconciliation, ...]

    def __post_init__(self) -> None:
        for name in ("revenue", "operating_profit_to_nopat", "nopat_to_free_cash_flow"):
            if type(getattr(self, name)) is not ReconciliationResult:
                raise ValidationError(f"{name} reconciliation is invalid")
        if self.cost is not None and type(self.cost) is not ReconciliationResult:
            raise ValidationError("cost reconciliation is invalid")
        for name in ("segment_revenue", "segment_cost"):
            rows = getattr(self, name)
            if not isinstance(rows, tuple) or not all(type(row) is SegmentBridgeReconciliation for row in rows):
                raise ValidationError(f"{name} reconciliations are invalid")


@dataclass(frozen=True, slots=True)
class EarningsEngine:
    """A closed company financial model with no valuation conclusion."""

    segments: tuple[SegmentEconomics, ...]
    company_revenue: Decimal
    company_cost: Decimal | None
    modeled_operating_profit: Decimal
    modeled_nopat: Decimal
    modeled_free_cash_flow: Decimal
    reported_operating_cash_flow: Decimal | None
    reported_cash_capex: Decimal | None
    reported_fcf_proxy: Decimal | None
    diluted_shares: Decimal | None
    modeled_nopat_per_share: Decimal | None
    four_core_views: FourCoreView
    reconciliations: EarningsReconciliations
    industry_state_id: UUID | None
    scenario: IndustryScenario | None
    exposures: tuple[CompanyExposure, ...]
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple) or not self.segments:
            raise ValidationError("earnings engine segments are required")
        if not all(type(segment) is SegmentEconomics for segment in self.segments):
            raise ValidationError("earnings engine segments are invalid")
        keys = tuple(segment.key for segment in self.segments)
        if len(keys) != len(set(keys)):
            raise ValidationError("earnings engine segment keys must be unique")
        _decimal(self.company_revenue, "company_revenue", nonnegative=True)
        if self.company_cost is not None:
            _decimal(self.company_cost, "company_cost", nonnegative=True)
        for name in ("modeled_operating_profit", "modeled_nopat", "modeled_free_cash_flow"):
            _decimal(getattr(self, name), name)
        for name in (
            "reported_operating_cash_flow",
            "reported_cash_capex",
            "reported_fcf_proxy",
            "diluted_shares",
            "modeled_nopat_per_share",
        ):
            value = getattr(self, name)
            if value is not None:
                _decimal(value, name, nonnegative=name in {"reported_cash_capex", "diluted_shares"})
        if self.diluted_shares is not None and self.diluted_shares <= 0:
            raise ValidationError("diluted_shares must be greater than zero")
        if (self.diluted_shares is None) != (self.modeled_nopat_per_share is None):
            raise ValidationError("diluted shares and modeled NOPAT per share must be provided together")
        if (self.reported_operating_cash_flow is None) != (self.reported_cash_capex is None):
            raise ValidationError("reported OCF and cash capex must be provided together")
        if self.reported_operating_cash_flow is None and self.reported_fcf_proxy is not None:
            raise ValidationError("reported FCF proxy requires reported OCF and cash capex")
        if self.reported_operating_cash_flow is not None and self.reported_fcf_proxy is None:
            raise ValidationError("reported FCF proxy is required with reported OCF and cash capex")
        if type(self.four_core_views) is not FourCoreView:
            raise ValidationError("four core views are invalid")
        if type(self.reconciliations) is not EarningsReconciliations:
            raise ValidationError("earnings reconciliations are invalid")
        if self.reconciliations.revenue.tolerance != _COMPANY_RECONCILIATION_TOLERANCE:
            raise ValidationError("company reconciliation tolerance must be exactly CNY 1000")
        if (
            self.reconciliations.cost is not None
            and self.reconciliations.cost.tolerance != _COMPANY_RECONCILIATION_TOLERANCE
        ):
            raise ValidationError("company reconciliation tolerance must be exactly CNY 1000")
        values = (
            self.company_revenue,
            *tuple(segment.revenue for segment in self.segments),
            *tuple(segment.cost for segment in self.segments),
            self.modeled_operating_profit,
            self.modeled_nopat,
            self.modeled_free_cash_flow,
            *tuple(segment.cash_tax for segment in self.segments),
            *tuple(segment.depreciation for segment in self.segments),
            *tuple(segment.cash_capex for segment in self.segments),
            *tuple(segment.working_capital_change for segment in self.segments),
        )
        try:
            with localcontext(_calculation_context(values)):
                expected_revenue = reconcile(
                    self.company_revenue,
                    tuple(segment.revenue for segment in self.segments),
                    self.reconciliations.revenue.tolerance,
                )
                if expected_revenue != self.reconciliations.revenue or not expected_revenue.balanced:
                    raise ValidationError("company revenue does not reconcile")
                if self.company_cost is not None:
                    if self.reconciliations.cost is None:
                        raise ValidationError("company cost reconciliation is required")
                    expected_cost = reconcile(
                        self.company_cost,
                        tuple(segment.cost for segment in self.segments),
                        self.reconciliations.cost.tolerance,
                    )
                    if expected_cost != self.reconciliations.cost or not expected_cost.balanced:
                        raise ValidationError("company cost does not reconcile")
                elif self.reconciliations.cost is not None:
                    raise ValidationError("company cost reconciliation has no company total")
                expected_operating = sum(
                    (segment.operating_profit for segment in self.segments), Decimal("0")
                )
                expected_nopat = sum((segment.nopat for segment in self.segments), Decimal("0"))
                expected_fcf = sum(
                    (segment.free_cash_flow for segment in self.segments), Decimal("0")
                )
                if (
                    (self.modeled_operating_profit, self.modeled_nopat, self.modeled_free_cash_flow)
                    != (expected_operating, expected_nopat, expected_fcf)
                ):
                    raise ValidationError("company operating profit to FCF does not reconcile")
                cash_taxes = sum((segment.cash_tax for segment in self.segments), Decimal("0"))
                depreciation = sum((segment.depreciation for segment in self.segments), Decimal("0"))
                cash_capex = sum((segment.cash_capex for segment in self.segments), Decimal("0"))
                working_capital = sum(
                    (segment.working_capital_change for segment in self.segments), Decimal("0")
                )
                expected_op_to_nopat = reconcile(
                    self.modeled_operating_profit,
                    (self.modeled_nopat + cash_taxes,),
                    self.reconciliations.operating_profit_to_nopat.tolerance,
                )
                expected_nopat_to_fcf = reconcile(
                    self.modeled_nopat + depreciation,
                    (self.modeled_free_cash_flow + cash_capex + working_capital,),
                    self.reconciliations.nopat_to_free_cash_flow.tolerance,
                )
                if (
                    expected_op_to_nopat != self.reconciliations.operating_profit_to_nopat
                    or not expected_op_to_nopat.balanced
                    or expected_nopat_to_fcf != self.reconciliations.nopat_to_free_cash_flow
                    or not expected_nopat_to_fcf.balanced
                ):
                    raise ValidationError("company operating profit to FCF does not reconcile")
                if self.reported_operating_cash_flow is not None:
                    assert self.reported_cash_capex is not None and self.reported_fcf_proxy is not None
                    if self.reported_fcf_proxy != (
                        self.reported_operating_cash_flow - self.reported_cash_capex
                    ):
                        raise ValidationError("reported FCF proxy does not reconcile")
                if self.diluted_shares is not None:
                    if self.modeled_nopat_per_share is None:
                        raise ValidationError(
                            "diluted shares and modeled NOPAT per share must be provided together"
                        )
                    with localcontext(
                        _calculation_context((self.modeled_nopat, self.diluted_shares))
                    ):
                        if self.modeled_nopat_per_share != self.modeled_nopat / self.diluted_shares:
                            raise ValidationError("modeled NOPAT per share does not reconcile")
                expected_segment_revenue = tuple(
                    SegmentBridgeReconciliation(
                        segment.key,
                        reconcile(
                            segment.revenue,
                            (
                                segment.volume_gwh
                                * Decimal("1000000")
                                * segment.asp_cny_per_kwh,
                            ),
                            _COMPANY_RECONCILIATION_TOLERANCE,
                        ),
                    )
                    for segment in self.segments
                    if segment.volume_gwh is not None
                )
                expected_segment_cost = tuple(
                    SegmentBridgeReconciliation(
                        segment.key,
                        reconcile(
                            segment.cost,
                            (
                                segment.volume_gwh
                                * Decimal("1000000")
                                * segment.unit_cash_cost_cny_per_kwh,
                            ),
                            _COMPANY_RECONCILIATION_TOLERANCE,
                        ),
                    )
                    for segment in self.segments
                    if segment.volume_gwh is not None
                )
                if (
                    self.reconciliations.segment_revenue != expected_segment_revenue
                    or self.reconciliations.segment_cost != expected_segment_cost
                    or not all(
                        row.balanced
                        for row in (*expected_segment_revenue, *expected_segment_cost)
                    )
                ):
                    raise ValidationError("physical segment bridge rows do not reconcile")
        except DecimalException as exc:
            raise ValidationError(f"company decimal arithmetic is invalid: {exc}") from exc
        if (self.industry_state_id is None) != (self.scenario is None):
            raise ValidationError("industry scenario and exposures must be provided together")
        if self.industry_state_id is not None:
            if type(self.industry_state_id) is not UUID or type(self.scenario) is not IndustryScenario:
                raise ValidationError("industry scenario and exposures are invalid")
            if self.scenario.parent_industry_state_id != self.industry_state_id:
                raise ValidationError("scenario must belong to industry state")
        if not isinstance(self.exposures, tuple) or not all(type(item) is CompanyExposure for item in self.exposures):
            raise ValidationError("company exposures are invalid")
        if self.industry_state_id is None and self.exposures:
            raise ValidationError("industry scenario and exposures must be provided together")
        if self.four_core_views != derive_four_core_views(
            self.segments,
            industry_state_id=self.industry_state_id,
            scenario=self.scenario,
            exposures=self.exposures,
        ):
            raise ValidationError("four core views do not reconcile")
        expected_hash = earnings_engine_content_hash(self)
        if self.content_hash is None:
            object.__setattr__(self, "content_hash", expected_hash)
        elif self.content_hash != expected_hash:
            raise ValidationError("earnings engine content hash does not reconcile")

    def segment(self, key: str) -> SegmentEconomics:
        for item in self.segments:
            if item.key == key:
                return item
        raise KeyError(key)


def ordered_core(values: Iterable[CoreContribution]) -> tuple[CoreContribution, ...]:
    """Sort contributions deterministically, never selecting a single narrative core."""
    return tuple(sorted(values, key=lambda value: (-value.amount, value.segment_key)))


def _sum(values: tuple[Decimal, ...]) -> Decimal:
    with localcontext(_calculation_context(values or (Decimal("0"),))):
        return sum(values, Decimal("0"))


def _share(amount: Decimal, total: Decimal) -> Decimal | None:
    if total == 0:
        return None
    with localcontext(_calculation_context((amount, total))):
        return amount / total


def derive_four_core_views(
    segments: tuple[SegmentEconomics, ...],
    *,
    industry_state_id: UUID | None,
    scenario: IndustryScenario | None,
    exposures: tuple[CompanyExposure, ...],
) -> FourCoreView:
    """Derive all four views from economics; never trust a supplied summary."""
    operating_segments = tuple(segment for segment in segments if segment.key != _UNALLOCATED_COMPANY_KEY)
    revenue_total = _sum(tuple(segment.revenue for segment in operating_segments))
    profit_total = _sum(tuple(segment.operating_profit for segment in operating_segments))
    cash_total = _sum(tuple(segment.free_cash_flow for segment in operating_segments))
    revenue_core = ordered_core(
        CoreContribution(segment.key, segment.revenue, _share(segment.revenue, revenue_total), "revenue")
        for segment in operating_segments
    )
    profit_core = ordered_core(
        CoreContribution(
            segment.key,
            segment.operating_profit,
            _share(segment.operating_profit, profit_total),
            "operating_profit",
        )
        for segment in operating_segments
    )
    cash_core = ordered_core(
        CoreContribution(
            segment.key,
            segment.free_cash_flow,
            _share(segment.free_cash_flow, cash_total),
            "free_cash_flow",
        )
        for segment in operating_segments
    )
    scenario_segments = tuple(
        segment for segment in operating_segments if segment.normalized_cash_earning_power is not None
    )
    if scenario_segments and len(scenario_segments) != len(operating_segments):
        raise ValidationError("normalized cash earning power is required for every operating segment")
    if industry_state_id is None:
        if scenario is not None or exposures:
            raise ValidationError("industry scenario and exposures must be provided together")
    else:
        if type(industry_state_id) is not UUID or type(scenario) is not IndustryScenario:
            raise ValidationError("industry scenario and exposures are invalid")
        if scenario.parent_industry_state_id != industry_state_id:
            raise ValidationError("scenario must belong to industry state")
        if not isinstance(exposures, tuple) or not all(type(item) is CompanyExposure for item in exposures):
            raise ValidationError("company exposures are invalid")
        matching_scope = {item.segment_key: item for item in exposures}
        if (
            len(matching_scope) != len(exposures)
            or set(matching_scope) != {segment.key for segment in operating_segments}
            or any(item.industry_state_id != industry_state_id for item in exposures)
        ):
            raise ValidationError(
                "exactly one company exposure is required to cover every operating segment in industry scope"
            )
    if not scenario_segments:
        return FourCoreView(revenue_core, profit_core, cash_core, tuple())
    if industry_state_id is None or scenario is None:
        raise ValidationError("industry scenario and exposures are required for value core")
    if scenario.parent_industry_state_id != industry_state_id:
        raise ValidationError("scenario must belong to industry state")
    matching = {exposure.segment_key: exposure for exposure in exposures}
    with localcontext(
        _calculation_context(
            tuple(
                item
                for segment in scenario_segments
                for item in (
                    segment.normalized_cash_earning_power,
                    matching[segment.key].normalized_cash_earning_power_multiplier,
                )
            )
        )
    ):
        normalized = tuple(
            (
                segment,
                segment.normalized_cash_earning_power
                * matching[segment.key].normalized_cash_earning_power_multiplier,
            )
            for segment in scenario_segments
        )
    value_total = _sum(tuple(amount for _, amount in normalized))
    value_core = ordered_core(
        CoreContribution(
            segment.key,
            amount,
            _share(amount, value_total),
            f"normalized_cash_earning_power:{scenario.kind.value}",
        )
        for segment, amount in normalized
    )
    return FourCoreView(revenue_core, profit_core, cash_core, value_core)


def earnings_engine_content_hash(value: EarningsEngine) -> str:
    """Hash every replay-relevant earnings field, excluding only the hash itself."""
    if type(value) is not EarningsEngine:
        raise ValidationError("earnings engine is required")
    return _canonical_hash(
        {
            "segments": tuple(asdict(segment) for segment in value.segments),
            "company_revenue": str(value.company_revenue),
            "company_cost": str(value.company_cost) if value.company_cost is not None else None,
            "modeled_operating_profit": str(value.modeled_operating_profit),
            "modeled_nopat": str(value.modeled_nopat),
            "modeled_free_cash_flow": str(value.modeled_free_cash_flow),
            "reported_operating_cash_flow": str(value.reported_operating_cash_flow) if value.reported_operating_cash_flow is not None else None,
            "reported_cash_capex": str(value.reported_cash_capex) if value.reported_cash_capex is not None else None,
            "reported_fcf_proxy": str(value.reported_fcf_proxy) if value.reported_fcf_proxy is not None else None,
            "diluted_shares": str(value.diluted_shares) if value.diluted_shares is not None else None,
            "modeled_nopat_per_share": (
                str(value.modeled_nopat_per_share)
                if value.modeled_nopat_per_share is not None
                else None
            ),
            "four_core_views": asdict(value.four_core_views),
            "reconciliations": asdict(value.reconciliations),
            "industry_state_id": str(value.industry_state_id) if value.industry_state_id else None,
            "scenario": asdict(value.scenario) if value.scenario is not None else None,
            "exposures": tuple(asdict(item) for item in value.exposures),
        }
    )


def validate_earnings_engine_integrity(value: EarningsEngine) -> None:
    """Fail closed before a persisted or downstream engine is consumed."""
    if type(value) is not EarningsEngine or value.content_hash is None:
        raise ValidationError("earnings engine content hash is required")
    if value.content_hash != earnings_engine_content_hash(value):
        raise ValidationError("earnings engine content hash does not reconcile")
    # Reconstructing executes all financial, bridge, core-view and hash checks.
    EarningsEngine(
        segments=value.segments,
        company_revenue=value.company_revenue,
        company_cost=value.company_cost,
        modeled_operating_profit=value.modeled_operating_profit,
        modeled_nopat=value.modeled_nopat,
        modeled_free_cash_flow=value.modeled_free_cash_flow,
        reported_operating_cash_flow=value.reported_operating_cash_flow,
        reported_cash_capex=value.reported_cash_capex,
        reported_fcf_proxy=value.reported_fcf_proxy,
        diluted_shares=value.diluted_shares,
        modeled_nopat_per_share=value.modeled_nopat_per_share,
        four_core_views=value.four_core_views,
        reconciliations=value.reconciliations,
        industry_state_id=value.industry_state_id,
        scenario=value.scenario,
        exposures=value.exposures,
        content_hash=value.content_hash,
    )
