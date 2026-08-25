"""Pure, versioned defaults for a company research preview."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Protocol
from uuid import UUID


DEFAULT_MODULES = (
    "overview",
    "business_map",
    "operating_drivers",
    "evidence_and_gaps",
    "industry_competition_regulation",
    "financials_cash_flow_capital_allocation",
    "scenarios_valuation_implied_expectations",
    "counterevidence_risks_next_checks",
    "versions_changes_memo",
)

DEFAULT_STRATEGY_VERSION = "company-research-default.v1"
DEFAULT_HORIZON_YEARS = 5
DEFAULT_BASE_CURRENCY = "CNY"
DEFAULT_REQUIRED_RETURN = Decimal("0.12")
DEFAULT_PERMANENT_LOSS_LIMIT = Decimal("0.25")
_MODULE_KEY = re.compile(r"[a-z][a-z0-9_]*")
_GAP_CODE = re.compile(r"[a-z][a-z0-9_]*")


class CompanyResearchValidationError(ValueError):
    """Raised when a company-research value is not canonical or complete."""


def _require_uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise CompanyResearchValidationError(f"{field_name} must be a UUID")
    return value


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CompanyResearchValidationError(
            f"{field_name} must be non-empty without surrounding whitespace"
        )
    return value


def _require_key(value: object, field_name: str, pattern: re.Pattern[str]) -> str:
    text = _require_text(value, field_name)
    if pattern.fullmatch(text) is None:
        raise CompanyResearchValidationError(
            f"{field_name} must be a lowercase underscore key"
        )
    return text


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CompanyResearchValidationError(
            f"{field_name} must be a timezone-aware datetime"
        )
    return value.astimezone(UTC)


def _canonical_hash(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


@dataclass(frozen=True, slots=True)
class CompanyResearchCompany:
    """Stable company identity supplied by the calling application."""

    object_id: UUID
    external_key: str
    canonical_name: str

    def __post_init__(self) -> None:
        _require_uuid(self.object_id, "company.object_id")
        _require_text(self.external_key, "company.external_key")
        _require_text(self.canonical_name, "company.canonical_name")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "object_id": str(self.object_id),
            "external_key": self.external_key,
            "canonical_name": self.canonical_name,
        }


@dataclass(frozen=True, slots=True)
class CompanyResearchSecurity:
    """Stable security identity, explicitly related to one company object."""

    object_id: UUID
    company_id: UUID
    external_key: str
    symbol: str
    exchange: str
    share_class: str
    trading_currency: str

    def __post_init__(self) -> None:
        _require_uuid(self.object_id, "security.object_id")
        _require_uuid(self.company_id, "security.company_id")
        _require_text(self.external_key, "security.external_key")
        _require_text(self.symbol, "security.symbol")
        _require_text(self.exchange, "security.exchange")
        _require_text(self.share_class, "security.share_class")
        _require_text(self.trading_currency, "security.trading_currency")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "object_id": str(self.object_id),
            "company_id": str(self.company_id),
            "external_key": self.external_key,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "share_class": self.share_class,
            "trading_currency": self.trading_currency,
        }


@dataclass(frozen=True, slots=True)
class CompanyResearchIdentitySet:
    """The complete, canonical identity boundary for a company preview."""

    company: CompanyResearchCompany
    securities: tuple[CompanyResearchSecurity, ...]

    def __post_init__(self) -> None:
        if type(self.company) is not CompanyResearchCompany:
            raise CompanyResearchValidationError(
                "company must be a CompanyResearchCompany"
            )
        if not isinstance(self.securities, tuple) or not self.securities:
            raise CompanyResearchValidationError(
                "securities must contain at least one security"
            )
        if not all(
            type(security) is CompanyResearchSecurity for security in self.securities
        ):
            raise CompanyResearchValidationError(
                "securities must contain CompanyResearchSecurity values"
            )
        if any(
            security.company_id != self.company.object_id
            for security in self.securities
        ):
            raise CompanyResearchValidationError(
                "security.company_id must match company.object_id"
            )
        if len({security.object_id for security in self.securities}) != len(
            self.securities
        ):
            raise CompanyResearchValidationError(
                "security.object_id values must be unique"
            )
        if len({security.external_key for security in self.securities}) != len(
            self.securities
        ):
            raise CompanyResearchValidationError(
                "security.external_key values must be unique"
            )
        object.__setattr__(
            self,
            "securities",
            tuple(sorted(self.securities, key=lambda item: item.external_key)),
        )


@dataclass(frozen=True, slots=True)
class CompanyResearchModule:
    """A readable section in a company research preview."""

    key: str
    label: str

    def __post_init__(self) -> None:
        _require_key(self.key, "module.key", _MODULE_KEY)
        _require_text(self.label, "module.label")

    def canonical_payload(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label}


_DEFAULT_MODULE_VALUES = tuple(
    CompanyResearchModule(key=key, label=key.replace("_", " ").title())
    for key in DEFAULT_MODULES
)


@dataclass(frozen=True, slots=True)
class CompanyResearchDefaultPolicy:
    """The non-overridable first-version default investment-research policy."""

    strategy_version: str = DEFAULT_STRATEGY_VERSION
    horizon_years: int = DEFAULT_HORIZON_YEARS
    base_currency: str = DEFAULT_BASE_CURRENCY
    required_return: Decimal = DEFAULT_REQUIRED_RETURN
    permanent_loss_limit: Decimal = DEFAULT_PERMANENT_LOSS_LIMIT
    generic_modules: tuple[CompanyResearchModule, ...] = _DEFAULT_MODULE_VALUES

    def __post_init__(self) -> None:
        if self.strategy_version != DEFAULT_STRATEGY_VERSION:
            raise CompanyResearchValidationError(
                "strategy_version is fixed by the default policy"
            )
        if self.horizon_years != DEFAULT_HORIZON_YEARS:
            raise CompanyResearchValidationError(
                "horizon_years is fixed by the default policy"
            )
        if self.base_currency != DEFAULT_BASE_CURRENCY:
            raise CompanyResearchValidationError(
                "base_currency is fixed by the default policy"
            )
        if self.required_return != DEFAULT_REQUIRED_RETURN:
            raise CompanyResearchValidationError(
                "required_return is fixed by the default policy"
            )
        if self.permanent_loss_limit != DEFAULT_PERMANENT_LOSS_LIMIT:
            raise CompanyResearchValidationError(
                "permanent_loss_limit is fixed by the default policy"
            )
        if self.generic_modules != _DEFAULT_MODULE_VALUES:
            raise CompanyResearchValidationError(
                "generic_modules are fixed by the default policy"
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "strategy_version": self.strategy_version,
            "horizon_years": self.horizon_years,
            "base_currency": self.base_currency,
            "required_return": format(self.required_return, "f"),
            "permanent_loss_limit": format(self.permanent_loss_limit, "f"),
            "generic_modules": tuple(
                module.canonical_payload() for module in self.generic_modules
            ),
        }


class ResearchGapSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ResearchGap:
    """A closed, source-independent description of missing research coverage."""

    code: str
    module_key: str
    severity: ResearchGapSeverity
    message: str

    def __post_init__(self) -> None:
        _require_key(self.code, "gap.code", _GAP_CODE)
        _require_key(self.module_key, "gap.module_key", _MODULE_KEY)
        if self.module_key not in DEFAULT_MODULES:
            raise CompanyResearchValidationError(
                "gap.module_key must be a known generic module"
            )
        if type(self.severity) is not ResearchGapSeverity:
            raise CompanyResearchValidationError(
                "gap.severity must be a ResearchGapSeverity"
            )
        _require_text(self.message, "gap.message")


class CompanyResearchAdapter(Protocol):
    """Seam for business vocabulary, with no persistence or research side effects."""

    def supports(self, company_external_key: str) -> bool:
        """Return whether the adapter recognizes this company external key."""

    def business_modules(
        self, company_external_key: str
    ) -> tuple[CompanyResearchModule, ...]:
        """Return the adapter-owned business module definitions for that key."""


def _validated_business_modules(value: object) -> tuple[CompanyResearchModule, ...]:
    if not isinstance(value, tuple) or not value:
        raise CompanyResearchValidationError(
            "adapter business modules must be a non-empty tuple"
        )
    if not all(type(module) is CompanyResearchModule for module in value):
        raise CompanyResearchValidationError(
            "adapter business modules must contain CompanyResearchModule values"
        )
    if len({module.key for module in value}) != len(value):
        raise CompanyResearchValidationError(
            "adapter business modules must have unique keys"
        )
    if set(module.key for module in value).intersection(DEFAULT_MODULES):
        raise CompanyResearchValidationError(
            "adapter business modules must not duplicate generic modules"
        )
    return value


@dataclass(frozen=True, slots=True)
class CompanyResearchPreview:
    """A recomputable policy preview; it is not a project or a research result."""

    company: CompanyResearchCompany
    securities: tuple[CompanyResearchSecurity, ...]
    cutoff_at: datetime
    strategy_version: str
    horizon_years: int
    base_currency: str
    required_return: Decimal
    permanent_loss_limit: Decimal
    generic_modules: tuple[CompanyResearchModule, ...]
    business_modules: tuple[CompanyResearchModule, ...]
    input_hash: str = field(default="")

    def __post_init__(self) -> None:
        identities = CompanyResearchIdentitySet(
            company=self.company, securities=self.securities
        )
        object.__setattr__(self, "securities", identities.securities)
        object.__setattr__(self, "cutoff_at", _utc(self.cutoff_at, "cutoff_at"))
        CompanyResearchDefaultPolicy(
            strategy_version=self.strategy_version,
            horizon_years=self.horizon_years,
            base_currency=self.base_currency,
            required_return=self.required_return,
            permanent_loss_limit=self.permanent_loss_limit,
            generic_modules=self.generic_modules,
        )
        _validated_business_modules(self.business_modules)
        calculated_hash = _canonical_hash(self.canonical_payload())
        if self.input_hash and self.input_hash != calculated_hash:
            raise CompanyResearchValidationError(
                "input_hash does not match canonical payload"
            )
        object.__setattr__(self, "input_hash", calculated_hash)

    @property
    def security_external_keys(self) -> tuple[str, ...]:
        return tuple(security.external_key for security in self.securities)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": "company-research-preview.v1",
            "strategy": {
                "strategy_version": self.strategy_version,
                "horizon_years": self.horizon_years,
                "base_currency": self.base_currency,
                "required_return": format(self.required_return, "f"),
                "permanent_loss_limit": format(self.permanent_loss_limit, "f"),
            },
            "cutoff_at": self.cutoff_at.isoformat(),
            "company": self.company.canonical_payload(),
            "securities": tuple(
                security.canonical_payload() for security in self.securities
            ),
            "generic_modules": tuple(
                module.canonical_payload() for module in self.generic_modules
            ),
            "business_modules": tuple(
                module.canonical_payload() for module in self.business_modules
            ),
        }


def build_company_research_preview(
    *,
    adapter: CompanyResearchAdapter,
    identities: CompanyResearchIdentitySet,
    cutoff_at: datetime,
) -> CompanyResearchPreview:
    """Build the deterministic default policy preview for one supported company."""
    if type(identities) is not CompanyResearchIdentitySet:
        raise CompanyResearchValidationError(
            "identities must be a CompanyResearchIdentitySet"
        )
    normalized_cutoff = _utc(cutoff_at, "cutoff_at")
    supports = getattr(adapter, "supports", None)
    business_modules = getattr(adapter, "business_modules", None)
    if not callable(supports) or not callable(business_modules):
        raise CompanyResearchValidationError(
            "adapter must implement CompanyResearchAdapter"
        )
    if supports(identities.company.external_key) is not True:
        raise CompanyResearchValidationError(
            "adapter does not support company external key"
        )
    modules = _validated_business_modules(
        business_modules(identities.company.external_key)
    )
    policy = CompanyResearchDefaultPolicy()
    return CompanyResearchPreview(
        company=identities.company,
        securities=identities.securities,
        cutoff_at=normalized_cutoff,
        strategy_version=policy.strategy_version,
        horizon_years=policy.horizon_years,
        base_currency=policy.base_currency,
        required_return=policy.required_return,
        permanent_loss_limit=policy.permanent_loss_limit,
        generic_modules=policy.generic_modules,
        business_modules=modules,
    )
