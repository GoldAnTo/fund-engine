"""Core vocabulary and input types for underwriting research."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ResearchObjectKind(StrEnum):
    INDUSTRY = "industry"
    COMPANY = "company"
    SECURITY = "security"


class LedgerKind(StrEnum):
    REALITY = "reality"
    BELIEF = "belief"
    DECISION = "decision"
    CALIBRATION = "calibration"


class AnswerabilityState(StrEnum):
    ANSWERABLE = "answerable"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    NOT_ANSWERABLE = "not_answerable"


class EligibleAction(StrEnum):
    OBSERVE = "observe"
    WAIT_FOR_VALIDATION = "wait_for_validation"
    ELIGIBLE_FOR_PROBE_ENTRY = "eligible_for_probe_entry"
    ELIGIBLE_FOR_STAGED_ENTRY = "eligible_for_staged_entry"
    DO_NOT_ENTER = "do_not_enter"


class BlockerCode(StrEnum):
    MISSING_KEY_BASELINE = "missing_key_baseline"
    UNRESOLVED_SOURCE_CONFLICT = "unresolved_source_conflict"
    MECHANISM_UNIDENTIFIED = "mechanism_unidentified"
    FINANCIAL_MODEL_NOT_CLOSED = "financial_model_not_closed"
    EXPECTATION_SURFACE_UNIDENTIFIABLE = "expectation_surface_unidentifiable"
    SOURCE_UNAVAILABLE = "source_unavailable"
    FUTURE_INFORMATION_LEAKAGE = "future_information_leakage"


@dataclass(frozen=True, slots=True)
class InvestmentMandateInput:
    mandate_key: str
    horizon_years: int
    base_currency: str
    required_return: Decimal
    permanent_loss_limit: Decimal
    comparison_set: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoricalBasisInput:
    cutoff: datetime
    price_as_of: datetime | None
    source_manifest_hash: str


@dataclass(frozen=True, slots=True)
class LedgerEntryInput:
    ledger_kind: LedgerKind
    family_key: str
    entry_type: str
    payload: dict[str, Any]
    effective_at: datetime
    available_at: datetime
    source_boundary: str
