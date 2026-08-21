"""Immutable financial contracts for a company segment earnings bridge.

This module intentionally models economics before valuation.  A result can
describe revenue, profit, cash and normalized earning power, but cannot carry
a price, a multiple, a discount rate or a target price.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from typing import Iterable
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.industry import IndustryScenario
from app.underwriting.domain.metrics import ReconciliationResult, reconcile


_UNALLOCATED_COMPANY_KEY = "unallocated_company"


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
        object.__setattr__(self, "basis", _text(self.basis, "core basis"))


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
    diluted_eps: Decimal | None
    four_core_views: FourCoreView
    reconciliations: EarningsReconciliations
    industry_state_id: UUID | None
    scenario: IndustryScenario | None
    exposures: tuple[CompanyExposure, ...]

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
        for name in ("reported_operating_cash_flow", "reported_cash_capex", "reported_fcf_proxy", "diluted_shares", "diluted_eps"):
            value = getattr(self, name)
            if value is not None:
                _decimal(value, name, nonnegative=name in {"reported_cash_capex", "diluted_shares"})
        if self.diluted_shares is not None and self.diluted_shares <= 0:
            raise ValidationError("diluted_shares must be greater than zero")
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
                    assert self.diluted_eps is not None
                    with localcontext(
                        _calculation_context((self.modeled_nopat, self.diluted_shares))
                    ):
                        if self.diluted_eps != self.modeled_nopat / self.diluted_shares:
                            raise ValidationError("diluted EPS does not reconcile")
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

    def segment(self, key: str) -> SegmentEconomics:
        for item in self.segments:
            if item.key == key:
                return item
        raise KeyError(key)


def ordered_core(values: Iterable[CoreContribution]) -> tuple[CoreContribution, ...]:
    """Sort contributions deterministically, never selecting a single narrative core."""
    return tuple(sorted(values, key=lambda value: (-value.amount, value.segment_key)))
