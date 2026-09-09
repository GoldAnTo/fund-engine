"""Pure, versioned defaults for a company research preview."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import (
    Context,
    Decimal,
    MAX_EMAX,
    MAX_PREC,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    localcontext,
)
from enum import StrEnum
import hashlib
import json
import re
from typing import Protocol
from unicodedata import normalize
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
COMPANY_RESEARCH_DECIMAL_PRECISION = 60
_MODULE_KEY = re.compile(r"[a-z][a-z0-9_]*")
_GAP_CODE = re.compile(r"[a-z][a-z0-9_]*")
_INPUT_HASH = re.compile(r"[0-9a-f]{64}")
_IDENTITY_NAME_MAX_LENGTH = 512


def _company_research_decimal_context(*, precision: int) -> Context:
    """Return a fully-defined, caller-independent Decimal context."""
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[],
    )


_COMPANY_RESEARCH_ARITHMETIC_CONTEXT = _company_research_decimal_context(
    precision=COMPANY_RESEARCH_DECIMAL_PRECISION,
)
_COMPANY_RESEARCH_CANONICAL_DECIMAL_CONTEXT = _company_research_decimal_context(
    precision=MAX_PREC,
)


class CompanyResearchValidationError(ValueError):
    """Raised when a company-research value is not canonical or complete."""


def normalize_company_research_focus(value: object) -> str | None:
    """Canonical optional focus shared by the request, preview hash and scope."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise CompanyResearchValidationError("user_focus must be a string")
    result = normalize("NFC", value).strip()
    if len(result) > 2000:
        raise CompanyResearchValidationError("user_focus must not exceed 2000 characters")
    return result or None


def company_research_product_status(status: str, current_step: str | None) -> str:
    """Project actual worker checkpoints; atomic bundles can skip display stages."""
    if status == "building_model":
        if current_step in {"financial_bridge", "scenario_set", "valuation_set"}:
            return "building_forecast"
        if current_step in {"judgment_context", "memo"}:
            return "generating_report"
        if current_step in {"business_map", "driver_map", "model_bundle"}:
            return "analyzing_company"
        raise CompanyResearchValidationError("unknown company research model step")
    statuses = {
        "queued": "queued", "preparing_sources": "collecting_sources",
        "awaiting_evidence_review": "needs_input", "awaiting_judgment_review": "needs_input",
        "ready_to_freeze": "completed", "completed": "completed",
        "recoverable_failure": "failed", "blocked": "failed",
    }
    if status not in statuses:
        raise CompanyResearchValidationError("unknown company research preparation status")
    return statuses[status]


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


def _require_canonical_name(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    if text != normalize("NFC", text):
        raise CompanyResearchValidationError(f"{field_name} must use NFC normalization")
    if len(text) > _IDENTITY_NAME_MAX_LENGTH:
        raise CompanyResearchValidationError(
            f"{field_name} must not exceed {_IDENTITY_NAME_MAX_LENGTH} characters"
        )
    return text


def _require_finite_decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not Decimal:
        raise CompanyResearchValidationError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise CompanyResearchValidationError(f"{field_name} must be finite")
    return value


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
        _require_canonical_name(self.canonical_name, "company.canonical_name")

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
    canonical_name: str
    symbol: str
    exchange: str
    share_class: str
    trading_currency: str

    def __post_init__(self) -> None:
        _require_uuid(self.object_id, "security.object_id")
        _require_uuid(self.company_id, "security.company_id")
        _require_text(self.external_key, "security.external_key")
        _require_canonical_name(self.canonical_name, "security.canonical_name")
        _require_text(self.symbol, "security.symbol")
        _require_text(self.exchange, "security.exchange")
        _require_text(self.share_class, "security.share_class")
        _require_text(self.trading_currency, "security.trading_currency")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "object_id": str(self.object_id),
            "company_id": str(self.company_id),
            "external_key": self.external_key,
            "canonical_name": self.canonical_name,
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
        if type(self.horizon_years) is not int:
            raise CompanyResearchValidationError("horizon_years must be an int")
        _require_finite_decimal(self.required_return, "required_return")
        _require_finite_decimal(self.permanent_loss_limit, "permanent_loss_limit")
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
        object.__setattr__(self, "required_return", DEFAULT_REQUIRED_RETURN)
        object.__setattr__(self, "permanent_loss_limit", DEFAULT_PERMANENT_LOSS_LIMIT)

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
        if type(self.severity) is not ResearchGapSeverity:
            raise CompanyResearchValidationError(
                "gap.severity must be a ResearchGapSeverity"
            )
        _require_text(self.message, "gap.message")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "code": self.code,
            "module_key": self.module_key,
            "severity": self.severity.value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class EvidenceGapContract:
    """Immutable source-derived identity for the gaps supplied to a model."""

    source_ref: "SourceLineageReference"
    gaps: tuple[ResearchGap, ...]
    content_hash: str = ""

    def __post_init__(self) -> None:
        if type(self.source_ref) is not SourceLineageReference:
            raise CompanyResearchValidationError(
                "evidence gap contract source_ref must be source lineage"
            )
        if self.source_ref.fact_key != "evidence_gap_contract":
            raise CompanyResearchValidationError(
                "evidence gap contract must reference the source-derived gap contract"
            )
        if not isinstance(self.gaps, tuple) or not all(
            type(gap) is ResearchGap for gap in self.gaps
        ):
            raise CompanyResearchValidationError(
                "evidence gap contract gaps must be a typed tuple"
            )
        if len({gap.code for gap in self.gaps}) != len(self.gaps):
            raise CompanyResearchValidationError(
                "evidence gap contract gap codes must be unique"
            )
        calculated_hash = _canonical_hash(self.canonical_payload())
        if type(self.content_hash) is not str:
            raise CompanyResearchValidationError(
                "evidence gap contract content_hash must be a string"
            )
        if self.content_hash and _INPUT_HASH.fullmatch(self.content_hash) is None:
            raise CompanyResearchValidationError(
                "evidence gap contract content_hash must be a lowercase SHA-256 hex digest"
            )
        if self.content_hash and self.content_hash != calculated_hash:
            raise CompanyResearchValidationError(
                "evidence gap contract content_hash does not match canonical payload"
            )
        object.__setattr__(self, "content_hash", calculated_hash)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": "company-research-evidence-gaps.v1",
            "source_ref": self.source_ref.canonical_payload(),
            "gaps": tuple(gap.canonical_payload() for gap in self.gaps),
        }


def validate_research_gaps(
    gaps: tuple[ResearchGap, ...], allowed_module_keys: tuple[str, ...]
) -> tuple[ResearchGap, ...]:
    """Validate closed gap descriptions against modules owned by a preview."""
    if not isinstance(gaps, tuple) or not all(type(gap) is ResearchGap for gap in gaps):
        raise CompanyResearchValidationError(
            "gaps must be a tuple of ResearchGap values"
        )
    if not isinstance(allowed_module_keys, tuple) or not allowed_module_keys:
        raise CompanyResearchValidationError(
            "allowed_module_keys must be a non-empty tuple"
        )
    if not all(
        isinstance(module_key, str) and _MODULE_KEY.fullmatch(module_key) is not None
        for module_key in allowed_module_keys
    ):
        raise CompanyResearchValidationError(
            "allowed_module_keys must contain module keys"
        )
    if len(set(allowed_module_keys)) != len(allowed_module_keys):
        raise CompanyResearchValidationError("allowed_module_keys must be unique")
    if any(gap.module_key not in allowed_module_keys for gap in gaps):
        raise CompanyResearchValidationError("research gap module_key is not allowed")
    return gaps


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
    research_gaps: tuple[ResearchGap, ...] = ()
    input_hash: str = field(default="")
    user_focus: str | None = None

    def __post_init__(self) -> None:
        identities = CompanyResearchIdentitySet(
            company=self.company, securities=self.securities
        )
        object.__setattr__(self, "securities", identities.securities)
        object.__setattr__(self, "cutoff_at", _utc(self.cutoff_at, "cutoff_at"))
        policy = CompanyResearchDefaultPolicy(
            strategy_version=self.strategy_version,
            horizon_years=self.horizon_years,
            base_currency=self.base_currency,
            required_return=self.required_return,
            permanent_loss_limit=self.permanent_loss_limit,
            generic_modules=self.generic_modules,
        )
        _validated_business_modules(self.business_modules)
        object.__setattr__(self, "required_return", policy.required_return)
        object.__setattr__(self, "permanent_loss_limit", policy.permanent_loss_limit)
        self.validate_research_gaps(self.research_gaps)
        object.__setattr__(self, "user_focus", normalize_company_research_focus(self.user_focus))
        calculated_hash = _canonical_hash(self.canonical_payload())
        if type(self.input_hash) is not str:
            raise CompanyResearchValidationError("input_hash must be a string")
        if self.input_hash and _INPUT_HASH.fullmatch(self.input_hash) is None:
            raise CompanyResearchValidationError(
                "input_hash must be a lowercase SHA-256 hex digest"
            )
        if self.input_hash and self.input_hash != calculated_hash:
            raise CompanyResearchValidationError(
                "input_hash does not match canonical payload"
            )
        object.__setattr__(self, "input_hash", calculated_hash)

    @property
    def security_external_keys(self) -> tuple[str, ...]:
        return tuple(security.external_key for security in self.securities)

    @property
    def allowed_module_keys(self) -> tuple[str, ...]:
        return DEFAULT_MODULES + tuple(module.key for module in self.business_modules)

    def validate_research_gaps(
        self, gaps: tuple[ResearchGap, ...]
    ) -> tuple[ResearchGap, ...]:
        return validate_research_gaps(gaps, self.allowed_module_keys)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": "company-research-preview.v2" if self.user_focus is not None else "company-research-preview.v1",
            **({"user_focus": self.user_focus} if self.user_focus is not None else {}),
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
            "research_gaps": tuple(
                gap.canonical_payload() for gap in self.research_gaps
            ),
        }


def build_company_research_preview(
    *,
    adapter: CompanyResearchAdapter,
    identities: CompanyResearchIdentitySet,
    cutoff_at: datetime,
    user_focus: str | None = None,
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
        user_focus=user_focus,
    )


# The contracts below are deliberately numeric and closed.  They are the seam
# between authenticated evidence preparation and a later durable workbench;
# prose, arbitrary formulas, and probability-weighted targets do not cross it.
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSIONED_ASSUMPTION_KEY = re.compile(
    r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*:[a-z][a-z0-9_]*\Z"
)
SCENARIO_FINANCIAL_DRIVER_KEYS = (
    "revenue",
    "operating_margin",
    "cash_tax_rate",
    "depreciation",
    "capex",
    "working_capital_change",
)
SCENARIO_FINANCIAL_DRIVER_EQUATIONS = {
    "revenue": "revenue = volume * monetization",
    "operating_margin": "operating_income = revenue * operating_margin",
    "cash_tax_rate": "cash_tax_rate = reported_tax_rate",
    "depreciation": "depreciation = reported_depreciation",
    "capex": "capex = reported_capex",
    "working_capital_change": "working_capital_change = reported_working_capital_change",
}
_DRIVER_EQUATIONS = frozenset(
    {
        *SCENARIO_FINANCIAL_DRIVER_EQUATIONS.values(),
        "fcff = nopat + depreciation - capex - working_capital_change",
        "reported_value = reviewed_fact",
    }
)


def _artifact_text(value: object, field_name: str) -> str:
    return _require_text(value, field_name)


def _artifact_decimal(value: object, field_name: str) -> Decimal:
    return _require_finite_decimal(value, field_name)


def _artifact_refs(
    value: object, field_name: str
) -> tuple["SourceLineageReference", ...]:
    if not isinstance(value, tuple) or not value:
        raise CompanyResearchValidationError(f"{field_name} must be a non-empty tuple")
    if not all(type(item) is SourceLineageReference for item in value):
        raise CompanyResearchValidationError(
            f"{field_name} must contain SourceLineageReference values"
        )
    if len(set(value)) != len(value):
        raise CompanyResearchValidationError(
            f"{field_name} must not contain duplicates"
        )
    return value


def _artifact_optional_refs(
    value: object, field_name: str
) -> tuple["SourceLineageReference", ...]:
    if not isinstance(value, tuple) or not all(
        type(item) is SourceLineageReference for item in value
    ):
        raise CompanyResearchValidationError(
            f"{field_name} must contain SourceLineageReference values"
        )
    if len(set(value)) != len(value):
        raise CompanyResearchValidationError(
            f"{field_name} must not contain duplicates"
        )
    return value


@dataclass(frozen=True, slots=True)
class SourceLineageReference:
    """One immutable source locator and digest, including its fact identity."""

    fact_key: str
    source_role: str
    source_url: str
    source_locator: str
    raw_hash: str

    def __post_init__(self) -> None:
        _require_key(self.fact_key, "source.fact_key", _GAP_CODE)
        _artifact_text(self.source_role, "source.source_role")
        _artifact_text(self.source_url, "source.source_url")
        _artifact_text(self.source_locator, "source.source_locator")
        if (
            not isinstance(self.raw_hash, str)
            or _SHA256.fullmatch(self.raw_hash) is None
        ):
            raise CompanyResearchValidationError(
                "source.raw_hash must be a SHA-256 hex digest"
            )

    def canonical_payload(self) -> dict[str, str]:
        return {
            "fact_key": self.fact_key,
            "source_role": self.source_role,
            "source_url": self.source_url,
            "source_locator": self.source_locator,
            "raw_hash": self.raw_hash,
        }


class ModelInputState(StrEnum):
    """Controlled provenance state for one numeric model-input path."""

    REPORTED = "reported"
    DERIVED = "derived"
    ASSUMPTION = "assumption"


@dataclass(frozen=True, slots=True)
class DriverInput:
    """One numeric path whose reported, derived, or assumed state is explicit."""

    driver_key: str
    state: ModelInputState
    values: tuple[Decimal, ...]
    source_refs: tuple[SourceLineageReference, ...]
    assumption_key: str | None
    equation_id: str | None = None
    assumption_rationale: str | None = None
    assumption_equation: str | None = None

    def __post_init__(self) -> None:
        _require_key(self.driver_key, "driver input driver_key", _GAP_CODE)
        if type(self.state) is not ModelInputState:
            raise CompanyResearchValidationError(
                "driver input state must be a ModelInputState"
            )
        if not isinstance(self.values, tuple) or not self.values:
            raise CompanyResearchValidationError(
                "driver input missing values must remain a ResearchGap"
            )
        for value in self.values:
            _artifact_decimal(value, "driver input value")
        _artifact_optional_refs(self.source_refs, "driver input source refs")

        if self.state is ModelInputState.REPORTED:
            if not self.source_refs:
                raise CompanyResearchValidationError(
                    "reported driver input requires exact source refs"
                )
            if (
                self.assumption_key is not None
                or self.equation_id is not None
                or self.assumption_rationale is not None
                or self.assumption_equation is not None
            ):
                raise CompanyResearchValidationError(
                    "reported driver input cannot carry an assumption or equation; "
                    "strategy paths require state=assumption"
                )
        elif self.state is ModelInputState.DERIVED:
            if not self.source_refs:
                raise CompanyResearchValidationError(
                    "derived driver input requires exact source refs"
                )
            if self.equation_id not in _DRIVER_EQUATIONS:
                raise CompanyResearchValidationError(
                    "derived driver input requires a closed equation"
                )
            if (
                self.assumption_key is not None
                or self.assumption_rationale is not None
                or self.assumption_equation is not None
            ):
                raise CompanyResearchValidationError(
                    "derived driver input cannot carry an assumption_key"
                )
        else:
            if (
                not isinstance(self.assumption_key, str)
                or _VERSIONED_ASSUMPTION_KEY.fullmatch(self.assumption_key) is None
            ):
                raise CompanyResearchValidationError(
                    "assumption driver input requires a versioned assumption_key"
                )
            if self.source_refs or self.equation_id is not None:
                raise CompanyResearchValidationError(
                    "assumption driver input must not be labeled as sourced or derived"
                )
            for value, field in (
                (self.assumption_rationale, "assumption_rationale"),
                (self.assumption_equation, "assumption_equation"),
            ):
                if value is not None:
                    _artifact_text(value, f"driver input {field}")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "driver_key": self.driver_key,
            "state": self.state.value,
            "values": tuple(canonical_decimal_string(value) for value in self.values),
            "source_refs": tuple(ref.canonical_payload() for ref in self.source_refs),
            "assumption_key": self.assumption_key,
            "equation_id": self.equation_id,
            "assumption_rationale": self.assumption_rationale,
            "assumption_equation": self.assumption_equation,
        }


@dataclass(frozen=True, slots=True)
class ClassifiedBusinessEvidenceArtifact:
    fact_ref: SourceLineageReference
    metric_key: str
    category: str
    value: Decimal
    currency: str
    unit: str
    period_start: str
    period_end: str

    def __post_init__(self) -> None:
        if type(self.fact_ref) is not SourceLineageReference:
            raise CompanyResearchValidationError(
                "classified evidence requires a fact ref"
            )
        _require_key(self.metric_key, "classified evidence metric_key", _GAP_CODE)
        if self.category not in {"revenue", "cost", "capital"}:
            raise CompanyResearchValidationError(
                "classified evidence category is invalid"
            )
        _artifact_decimal(self.value, "classified evidence value")
        for name in ("currency", "unit", "period_start", "period_end"):
            _artifact_text(getattr(self, name), f"classified evidence {name}")


@dataclass(frozen=True, slots=True)
class BusinessModuleArtifact:
    module_key: str
    revenue_sources: tuple[str, ...]
    cost_structure: tuple[str, ...]
    capital_needs: tuple[str, ...]
    fact_refs: tuple[SourceLineageReference, ...]
    gap_refs: tuple[str, ...]
    classified_evidence: tuple[ClassifiedBusinessEvidenceArtifact, ...] = ()

    def __post_init__(self) -> None:
        _require_key(self.module_key, "business_map.module_key", _MODULE_KEY)
        for name in ("revenue_sources", "cost_structure", "capital_needs"):
            values = getattr(self, name)
            if (
                not isinstance(values, tuple)
                or not values
                or not all(
                    isinstance(item, str) and item.strip() == item and item
                    for item in values
                )
            ):
                raise CompanyResearchValidationError(
                    f"business_map.{name} must contain non-empty canonical text"
                )
        _artifact_optional_refs(self.fact_refs, "business_map.fact_refs")
        if not isinstance(self.gap_refs, tuple) or not all(
            isinstance(item, str) and _GAP_CODE.fullmatch(item)
            for item in self.gap_refs
        ):
            raise CompanyResearchValidationError(
                "business_map.gap_refs must contain gap keys"
            )
        if not self.fact_refs and not self.gap_refs:
            raise CompanyResearchValidationError(
                "business module requires confirmed fact refs or explicit gap refs"
            )
        if not isinstance(self.classified_evidence, tuple) or not all(
            type(item) is ClassifiedBusinessEvidenceArtifact
            for item in self.classified_evidence
        ):
            raise CompanyResearchValidationError(
                "business module classified evidence must be typed"
            )
        if any(
            item.fact_ref not in self.fact_refs for item in self.classified_evidence
        ):
            raise CompanyResearchValidationError(
                "classified evidence must use module fact refs"
            )
        classified_refs = tuple(item.fact_ref for item in self.classified_evidence)
        if len(set(classified_refs)) != len(classified_refs) or set(
            classified_refs
        ) != set(self.fact_refs):
            raise CompanyResearchValidationError(
                "business module fact refs must be classified exactly once"
            )


@dataclass(frozen=True, slots=True)
class BusinessMapArtifact:
    modules: tuple[BusinessModuleArtifact, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.modules, tuple)
            or not self.modules
            or not all(type(item) is BusinessModuleArtifact for item in self.modules)
        ):
            raise CompanyResearchValidationError(
                "business_map.modules must be a non-empty typed tuple"
            )
        if len({item.module_key for item in self.modules}) != len(self.modules):
            raise CompanyResearchValidationError(
                "business_map.module_key values must be unique"
            )


@dataclass(frozen=True, slots=True)
class DriverMetricArtifact:
    driver_key: str
    module_key: str
    fact_refs: tuple[SourceLineageReference, ...]
    assumption_refs: tuple[SourceLineageReference, ...]
    equation: str
    output_metric: str
    input_state: ModelInputState
    assumption_key: str | None
    equation_id: str | None
    values: tuple[Decimal, ...]
    assumption_rationale: str | None = None
    assumption_equation: str | None = None

    def __post_init__(self) -> None:
        _require_key(self.driver_key, "driver.driver_key", _GAP_CODE)
        _require_key(self.module_key, "driver.module_key", _MODULE_KEY)
        _artifact_optional_refs(self.fact_refs, "driver.fact_refs")
        _artifact_optional_refs(self.assumption_refs, "driver.assumption_refs")
        if self.equation not in _DRIVER_EQUATIONS:
            raise CompanyResearchValidationError(
                "driver.equation must be a closed equation identifier"
            )
        _require_key(self.output_metric, "driver.output_metric", _GAP_CODE)
        if not isinstance(self.values, tuple) or not self.values:
            raise CompanyResearchValidationError("driver values must be non-empty")
        for value in self.values:
            _artifact_decimal(value, "driver value")
        if type(self.input_state) is not ModelInputState:
            raise CompanyResearchValidationError(
                "driver.input_state must be controlled"
            )
        if self.input_state is ModelInputState.REPORTED:
            if (
                not self.fact_refs
                or self.assumption_refs
                or self.assumption_key is not None
                or self.equation_id is not None
            ):
                raise CompanyResearchValidationError(
                    "reported driver requires fact refs only"
                )
        elif self.input_state is ModelInputState.DERIVED:
            if (
                not self.fact_refs
                or self.assumption_refs
                or self.assumption_key is not None
                or self.equation_id not in _DRIVER_EQUATIONS
            ):
                raise CompanyResearchValidationError(
                    "derived driver requires fact refs and a closed equation_id"
                )
        elif (
            self.fact_refs
            or not self.assumption_refs
            or self.equation_id is not None
            or not isinstance(self.assumption_key, str)
            or _VERSIONED_ASSUMPTION_KEY.fullmatch(self.assumption_key) is None
        ):
            raise CompanyResearchValidationError(
                "assumption driver requires candidate refs and a versioned assumption_key"
            )
        if bool(self.assumption_rationale) != bool(self.assumption_equation):
            raise CompanyResearchValidationError(
                "assumption driver metadata must preserve rationale and equation together"
            )


@dataclass(frozen=True, slots=True)
class DriverMapArtifact:
    drivers: tuple[DriverMetricArtifact, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.drivers, tuple)
            or not self.drivers
            or not all(type(item) is DriverMetricArtifact for item in self.drivers)
        ):
            raise CompanyResearchValidationError(
                "driver_map.drivers must be a non-empty typed tuple"
            )
        if len({item.driver_key for item in self.drivers}) != len(self.drivers):
            raise CompanyResearchValidationError(
                "driver_map.driver_key values must be unique"
            )


@dataclass(frozen=True, slots=True)
class FinancialBridgeRow:
    fiscal_year: int
    revenue: Decimal
    operating_income: Decimal
    cash_tax_rate: Decimal
    depreciation: Decimal
    capex: Decimal
    working_capital_change: Decimal
    fcff: Decimal
    fact_refs: tuple[SourceLineageReference, ...]
    assumption_refs: tuple[SourceLineageReference, ...]
    input_states: tuple[ModelInputState, ...]

    def __post_init__(self) -> None:
        if type(self.fiscal_year) is not int or self.fiscal_year < 1900:
            raise CompanyResearchValidationError(
                "financial_bridge.fiscal_year must be a year"
            )
        for name in (
            "revenue",
            "operating_income",
            "cash_tax_rate",
            "depreciation",
            "capex",
            "working_capital_change",
            "fcff",
        ):
            _artifact_decimal(getattr(self, name), f"financial_bridge.{name}")
        if self.cash_tax_rate < Decimal("0") or self.cash_tax_rate > Decimal("1"):
            raise CompanyResearchValidationError(
                "financial_bridge.cash_tax_rate must be between zero and one"
            )
        _artifact_optional_refs(self.fact_refs, "financial_bridge.fact_refs")
        _artifact_optional_refs(
            self.assumption_refs, "financial_bridge.assumption_refs"
        )
        if not self.fact_refs and not self.assumption_refs:
            raise CompanyResearchValidationError(
                "financial bridge requires factual or assumption provenance"
            )
        if set(self.fact_refs) & set(self.assumption_refs):
            raise CompanyResearchValidationError(
                "financial bridge factual and assumption provenance must be separate"
            )
        if (
            not isinstance(self.input_states, tuple)
            or not self.input_states
            or not all(type(item) is ModelInputState for item in self.input_states)
            or len(set(self.input_states)) != len(self.input_states)
        ):
            raise CompanyResearchValidationError(
                "financial bridge input states must be unique controlled states"
            )
        factual_state_present = any(
            item in {ModelInputState.REPORTED, ModelInputState.DERIVED}
            for item in self.input_states
        )
        assumption_state_present = ModelInputState.ASSUMPTION in self.input_states
        if (
            bool(self.fact_refs) != factual_state_present
            or bool(self.assumption_refs) != assumption_state_present
        ):
            raise CompanyResearchValidationError(
                "financial bridge provenance must match each input state"
            )
        with localcontext(_COMPANY_RESEARCH_ARITHMETIC_CONTEXT):
            expected_fcff = +(
                self.operating_income * (Decimal("1") - self.cash_tax_rate)
                + self.depreciation
                - self.capex
                - self.working_capital_change
            )
        if self.fcff != expected_fcff:
            raise CompanyResearchValidationError("financial bridge does not close")


@dataclass(frozen=True, slots=True)
class FinancialBridgeArtifact:
    rows: tuple[FinancialBridgeRow, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.rows, tuple)
            or len(self.rows) != 5
            or not all(type(item) is FinancialBridgeRow for item in self.rows)
        ):
            raise CompanyResearchValidationError(
                "financial_bridge.rows must contain exactly five typed rows"
            )
        years = tuple(item.fiscal_year for item in self.rows)
        if years != tuple(range(years[0], years[0] + 5)):
            raise CompanyResearchValidationError(
                "financial_bridge forecast years must be consecutive"
            )


@dataclass(frozen=True, slots=True)
class ScenarioDriverOverride:
    driver_key: str
    value: Decimal
    state: ModelInputState | None = None
    assumption_key: str | None = None
    rationale: str | None = None
    equation: str | None = None

    def __post_init__(self) -> None:
        _require_key(self.driver_key, "scenario.driver_key", _GAP_CODE)
        _artifact_decimal(self.value, "scenario.value")
        if self.state is not None and type(self.state) is not ModelInputState:
            raise CompanyResearchValidationError(
                "scenario override state must be controlled"
            )
        for value, field_name in (
            (self.assumption_key, "assumption_key"),
            (self.rationale, "rationale"),
            (self.equation, "equation"),
        ):
            if value is not None:
                _artifact_text(value, f"scenario override {field_name}")


@dataclass(frozen=True, slots=True)
class ScenarioArtifact:
    scenario_id: str
    mechanism_id: str
    driver_overrides: tuple[ScenarioDriverOverride, ...]

    def __post_init__(self) -> None:
        if self.scenario_id not in {"base", "bull", "bear"}:
            raise CompanyResearchValidationError(
                "scenario.scenario_id must be base, bull, or bear"
            )
        _artifact_text(self.mechanism_id, "scenario.mechanism_id")
        if (
            not isinstance(self.driver_overrides, tuple)
            or not self.driver_overrides
            or not all(
                type(item) is ScenarioDriverOverride for item in self.driver_overrides
            )
        ):
            raise CompanyResearchValidationError(
                "scenario.driver_overrides must be a non-empty typed tuple"
            )
        if len({item.driver_key for item in self.driver_overrides}) != len(
            self.driver_overrides
        ):
            raise CompanyResearchValidationError(
                "scenario.driver_overrides must have unique driver keys"
            )


@dataclass(frozen=True, slots=True)
class ScenarioSetArtifact:
    scenarios: tuple[ScenarioArtifact, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scenarios, tuple)
            or len(self.scenarios) != 3
            or not all(type(item) is ScenarioArtifact for item in self.scenarios)
        ):
            raise CompanyResearchValidationError(
                "scenario_set.scenarios must contain three typed scenarios"
            )
        if {item.scenario_id for item in self.scenarios} != {"base", "bull", "bear"}:
            raise CompanyResearchValidationError(
                "scenario_set must contain base, bull, and bear exactly once"
            )


@dataclass(frozen=True, slots=True)
class ScenarioFinancialDriverForecast:
    """Five-year baseline values for one financial driver in a scenario path."""

    driver_key: str
    values: tuple[Decimal, ...]
    fact_refs: tuple[SourceLineageReference, ...]
    assumption_refs: tuple[SourceLineageReference, ...]
    input_state: ModelInputState
    assumption_key: str | None
    equation_id: str | None
    assumption_rationale: str | None = None
    assumption_equation: str | None = None

    def __post_init__(self) -> None:
        if self.driver_key not in SCENARIO_FINANCIAL_DRIVER_KEYS:
            raise CompanyResearchValidationError(
                "scenario financial forecast must use a named financial driver"
            )
        if not isinstance(self.values, tuple) or len(self.values) != 5:
            raise CompanyResearchValidationError(
                "scenario financial forecast values must contain five Decimal values"
            )
        for value in self.values:
            _artifact_decimal(value, "scenario financial forecast value")
        _artifact_optional_refs(self.fact_refs, "scenario financial forecast fact_refs")
        _artifact_optional_refs(
            self.assumption_refs, "scenario financial forecast assumption_refs"
        )
        if type(self.input_state) is not ModelInputState:
            raise CompanyResearchValidationError(
                "scenario financial forecast input_state must be controlled"
            )
        if self.input_state is ModelInputState.REPORTED:
            if (
                not self.fact_refs
                or self.assumption_refs
                or self.assumption_key is not None
                or self.equation_id is not None
            ):
                raise CompanyResearchValidationError(
                    "reported forecast requires fact refs only"
                )
        elif self.input_state is ModelInputState.DERIVED:
            if (
                not self.fact_refs
                or self.assumption_refs
                or self.assumption_key is not None
                or self.equation_id not in _DRIVER_EQUATIONS
            ):
                raise CompanyResearchValidationError(
                    "derived forecast requires fact refs and a closed equation_id"
                )
        elif (
            self.fact_refs
            or not self.assumption_refs
            or self.equation_id is not None
            or not isinstance(self.assumption_key, str)
            or _VERSIONED_ASSUMPTION_KEY.fullmatch(self.assumption_key) is None
        ):
            raise CompanyResearchValidationError(
                "assumption forecast requires candidate refs and a versioned assumption_key"
            )
        if bool(self.assumption_rationale) != bool(self.assumption_equation):
            raise CompanyResearchValidationError(
                "assumption forecast metadata must preserve rationale and equation together"
            )


@dataclass(frozen=True, slots=True)
class ScenarioFinancialBridge:
    scenario_id: str
    first_fiscal_year: int
    driver_forecasts: tuple[ScenarioFinancialDriverForecast, ...]

    def __post_init__(self) -> None:
        if self.scenario_id not in {"base", "bull", "bear"}:
            raise CompanyResearchValidationError(
                "scenario financial bridge must be named"
            )
        if type(self.first_fiscal_year) is not int or self.first_fiscal_year < 1900:
            raise CompanyResearchValidationError(
                "scenario financial bridge first_fiscal_year must be a year"
            )
        if not isinstance(self.driver_forecasts, tuple) or not all(
            type(item) is ScenarioFinancialDriverForecast
            for item in self.driver_forecasts
        ):
            raise CompanyResearchValidationError(
                "scenario financial bridge driver forecasts must be typed"
            )
        keys = tuple(item.driver_key for item in self.driver_forecasts)
        if set(keys) != set(SCENARIO_FINANCIAL_DRIVER_KEYS) or len(keys) != len(
            SCENARIO_FINANCIAL_DRIVER_KEYS
        ):
            raise CompanyResearchValidationError(
                "scenario financial bridge must cover each named financial driver exactly once"
            )


@dataclass(frozen=True, slots=True)
class CapitalStructureReference:
    cash: Decimal
    debt: Decimal
    minority_interest: Decimal
    investments: Decimal
    pension_liabilities: Decimal
    other_adjustments: Decimal
    basic_shares: Decimal
    diluted_shares: Decimal
    source_ref: SourceLineageReference
    capital_bridge_policy_version: str
    policy_ref: SourceLineageReference
    policy_excluded_adjustments: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "cash",
            "debt",
            "minority_interest",
            "investments",
            "pension_liabilities",
            "other_adjustments",
            "basic_shares",
            "diluted_shares",
        ):
            _artifact_decimal(getattr(self, name), f"capital_structure.{name}")
        if self.basic_shares <= Decimal("0") or self.diluted_shares < self.basic_shares:
            raise CompanyResearchValidationError(
                "capital_structure requires positive basic_shares and diluted_shares >= basic_shares"
            )
        if type(self.source_ref) is not SourceLineageReference:
            raise CompanyResearchValidationError(
                "capital_structure.source_ref must be source lineage"
            )
        _artifact_text(
            self.capital_bridge_policy_version,
            "capital_structure.capital_bridge_policy_version",
        )
        if ".v" not in self.capital_bridge_policy_version:
            raise CompanyResearchValidationError(
                "capital structure bridge policy must be versioned"
            )
        if (
            type(self.policy_ref) is not SourceLineageReference
            or self.policy_ref.fact_key != "capital_bridge_policy"
        ):
            raise CompanyResearchValidationError(
                "capital structure bridge policy requires exact lineage"
            )
        allowed_exclusions = {"pension_liabilities"}
        if (
            not isinstance(self.policy_excluded_adjustments, tuple)
            or len(set(self.policy_excluded_adjustments))
            != len(self.policy_excluded_adjustments)
            or not set(self.policy_excluded_adjustments).issubset(allowed_exclusions)
            or any(
                getattr(self, name) != Decimal("0")
                for name in self.policy_excluded_adjustments
            )
        ):
            raise CompanyResearchValidationError(
                "capital structure policy-excluded adjustments must be closed and zero"
            )


@dataclass(frozen=True, slots=True)
class SecurityValuationReference:
    security_external_key: str
    listed_class_economic_units: Decimal
    conversion_ratio: Decimal
    adr_ratio: Decimal
    dividend_rights_per_unit: Decimal
    market_price_usd: Decimal
    usd_cny_rate: Decimal
    rights_ref: SourceLineageReference
    price_ref: SourceLineageReference

    def __post_init__(self) -> None:
        _artifact_text(self.security_external_key, "security.security_external_key")
        for name in (
            "listed_class_economic_units",
            "conversion_ratio",
            "adr_ratio",
            "dividend_rights_per_unit",
            "market_price_usd",
            "usd_cny_rate",
        ):
            _artifact_decimal(getattr(self, name), f"security.{name}")
        if self.listed_class_economic_units <= Decimal("0"):
            raise CompanyResearchValidationError(
                "security.listed_class_economic_units must be positive"
            )
        if self.conversion_ratio <= Decimal("0") or self.adr_ratio <= Decimal("0"):
            raise CompanyResearchValidationError(
                "security conversion_ratio and adr_ratio must be positive"
            )
        if self.dividend_rights_per_unit <= Decimal("0"):
            raise CompanyResearchValidationError(
                "security dividend_rights_per_unit must be positive for valuation"
            )
        if self.market_price_usd <= Decimal("0"):
            raise CompanyResearchValidationError(
                "security.market_price_usd must be positive"
            )
        if self.usd_cny_rate <= Decimal("0"):
            raise CompanyResearchValidationError(
                "security.usd_cny_rate must be positive"
            )
        if (
            type(self.rights_ref) is not SourceLineageReference
            or type(self.price_ref) is not SourceLineageReference
        ):
            raise CompanyResearchValidationError(
                "security rights and price refs must be source lineage"
            )


@dataclass(frozen=True, slots=True)
class MarketBridgeArtifact:
    capital_structure: CapitalStructureReference
    securities: tuple[SecurityValuationReference, ...]
    usd_cny_rate: Decimal
    fx_ref: SourceLineageReference

    def __post_init__(self) -> None:
        if type(self.capital_structure) is not CapitalStructureReference:
            raise CompanyResearchValidationError(
                "market_bridge.capital_structure must be typed"
            )
        if (
            not isinstance(self.securities, tuple)
            or not self.securities
            or not all(
                type(item) is SecurityValuationReference for item in self.securities
            )
        ):
            raise CompanyResearchValidationError(
                "market_bridge.securities must be a non-empty typed tuple"
            )
        if len({item.security_external_key for item in self.securities}) != len(
            self.securities
        ):
            raise CompanyResearchValidationError(
                "market_bridge securities must not duplicate"
            )
        _artifact_decimal(self.usd_cny_rate, "market_bridge.usd_cny_rate")
        if (
            self.usd_cny_rate <= Decimal("0")
            or type(self.fx_ref) is not SourceLineageReference
        ):
            raise CompanyResearchValidationError(
                "market_bridge must have an exact positive FX reference"
            )
        references = (
            self.capital_structure.source_ref,
            self.capital_structure.policy_ref,
            self.fx_ref,
            *(
                reference
                for security in self.securities
                for reference in (security.rights_ref, security.price_ref)
            ),
        )
        if len(set(references)) != len(references) or len(
            {item.fact_key for item in references}
        ) != len(references):
            raise CompanyResearchValidationError(
                "market bridge references must be unique across capital, rights, price, and FX roles"
            )
        if self.capital_structure.source_ref.fact_key != "capital_structure_usd":
            raise CompanyResearchValidationError(
                "market bridge capital reference must identify USD capital structure"
            )
        if self.fx_ref.fact_key != "usd_cny_fx":
            raise CompanyResearchValidationError(
                "market bridge FX reference must identify the USD/CNY pair"
            )
        for security in self.securities:
            suffix = re.sub(
                r"[^a-z0-9]+", "_", security.security_external_key.lower()
            ).strip("_")
            if security.rights_ref.fact_key != f"security_rights_{suffix}":
                raise CompanyResearchValidationError(
                    "market bridge rights reference must identify its security"
                )
            if security.price_ref.fact_key != f"market_price_usd_{suffix}":
                raise CompanyResearchValidationError(
                    "market bridge price reference must identify its security and USD currency"
                )


@dataclass(frozen=True, slots=True)
class ReverseDcfRequest:
    driver_key: str
    target_enterprise_value: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    max_iterations: int

    def __post_init__(self) -> None:
        if self.driver_key != "fcff_multiplier":
            raise CompanyResearchValidationError(
                "reverse DCF driver must be fcff_multiplier"
            )
        for name in ("target_enterprise_value", "lower_bound", "upper_bound"):
            _artifact_decimal(getattr(self, name), f"reverse_dcf.{name}")
        if (
            type(self.max_iterations) is not int
            or self.lower_bound >= self.upper_bound
            or self.max_iterations <= 0
        ):
            raise CompanyResearchValidationError(
                "reverse DCF bounds and iteration count must be valid"
            )


@dataclass(frozen=True, slots=True)
class MarketEquityComponentReference:
    component_key: str
    economic_units: Decimal
    price_proxy_security_external_key: str
    unit_source_ref: SourceLineageReference
    price_ref: SourceLineageReference
    votes_per_unit: Decimal | None = None
    conversion_to_security_external_key: str | None = None
    conversion_ratio: Decimal | None = None
    dividend_rights_per_unit: Decimal | None = None
    economic_rights_per_unit: Decimal | None = None
    legal_rights_ref: SourceLineageReference | None = None
    price_proxy_ref: SourceLineageReference | None = None
    price_proxy_policy_version: str | None = None

    def __post_init__(self) -> None:
        if self.component_key not in {"class_a", "class_b", "class_c"}:
            raise CompanyResearchValidationError(
                "market equity component key is invalid"
            )
        _artifact_decimal(self.economic_units, "market equity component units")
        if self.economic_units < 0:
            raise CompanyResearchValidationError(
                "market equity component units are invalid"
            )
        _artifact_text(
            self.price_proxy_security_external_key, "market equity price proxy"
        )
        if (
            type(self.unit_source_ref) is not SourceLineageReference
            or type(self.price_ref) is not SourceLineageReference
        ):
            raise CompanyResearchValidationError(
                "market equity component lineage must be typed"
            )


@dataclass(frozen=True, slots=True)
class CompanyResearchModelInput:
    business_map: BusinessMapArtifact
    driver_map: DriverMapArtifact
    scenario_set: ScenarioSetArtifact
    scenario_bridges: tuple[ScenarioFinancialBridge, ...]
    source_lineage: tuple[SourceLineageReference, ...]
    research_gaps: tuple[ResearchGap, ...]
    evidence_gap_contract: EvidenceGapContract
    required_return: Decimal
    terminal_growth: Decimal
    market_bridge: MarketBridgeArtifact | None
    judgment_context: "JudgmentContextArtifact"
    reverse_dcf: ReverseDcfRequest | None = None
    equity_components: tuple[MarketEquityComponentReference, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.business_map) is not BusinessMapArtifact
            or type(self.driver_map) is not DriverMapArtifact
            or type(self.scenario_set) is not ScenarioSetArtifact
        ):
            raise CompanyResearchValidationError("model input artifacts must be typed")
        if not isinstance(self.scenario_bridges, tuple) or not all(
            type(item) is ScenarioFinancialBridge for item in self.scenario_bridges
        ):
            raise CompanyResearchValidationError(
                "model input scenario bridges must be typed"
            )
        if len({item.scenario_id for item in self.scenario_bridges}) != len(
            self.scenario_bridges
        ):
            raise CompanyResearchValidationError(
                "model input scenario bridges must not duplicate"
            )
        _artifact_refs(self.source_lineage, "model input source_lineage")
        if not isinstance(self.research_gaps, tuple) or not all(
            type(item) is ResearchGap for item in self.research_gaps
        ):
            raise CompanyResearchValidationError(
                "model input research gaps must be typed"
            )
        if type(self.evidence_gap_contract) is not EvidenceGapContract:
            raise CompanyResearchValidationError(
                "model input evidence gap contract must be typed"
            )
        if self.research_gaps != self.evidence_gap_contract.gaps:
            raise CompanyResearchValidationError(
                "model input research gaps must exactly match the evidence gap contract"
            )
        _artifact_decimal(self.required_return, "model input required_return")
        _artifact_decimal(self.terminal_growth, "model input terminal_growth")
        if self.required_return <= Decimal("0") or self.terminal_growth < Decimal("0"):
            raise CompanyResearchValidationError(
                "model input discount and terminal rates must be valid"
            )
        if (
            self.market_bridge is not None
            and type(self.market_bridge) is not MarketBridgeArtifact
        ):
            raise CompanyResearchValidationError(
                "model input market bridge must be typed"
            )
        if type(self.judgment_context) is not JudgmentContextArtifact:
            raise CompanyResearchValidationError(
                "model input judgment context must be typed"
            )
        if (
            self.reverse_dcf is not None
            and type(self.reverse_dcf) is not ReverseDcfRequest
        ):
            raise CompanyResearchValidationError(
                "model input reverse DCF must be typed"
            )
        if self.market_bridge is not None:
            if (
                not isinstance(self.equity_components, tuple)
                or not all(
                    type(item) is MarketEquityComponentReference
                    for item in self.equity_components
                )
                or tuple(item.component_key for item in self.equity_components)
                != ("class_a", "class_b", "class_c")
            ):
                raise CompanyResearchValidationError(
                    "model input requires exact Class A-B-C equity components"
                )
        elif self.equity_components:
            raise CompanyResearchValidationError(
                "model input cannot carry equity components without a market bridge"
            )


@dataclass(frozen=True, slots=True)
class ValueRange:
    minimum: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        _artifact_decimal(self.minimum, "value_range.minimum")
        _artifact_decimal(self.maximum, "value_range.maximum")
        if self.minimum > self.maximum:
            raise CompanyResearchValidationError("value_range must be ordered")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "minimum": canonical_decimal_string(self.minimum),
            "maximum": canonical_decimal_string(self.maximum),
        }


def canonical_decimal_string(value: Decimal) -> str:
    """Stable Decimal serialization for a later immutable-artifact boundary."""
    _artifact_decimal(value, "decimal")
    with localcontext(_COMPANY_RESEARCH_CANONICAL_DECIMAL_CONTEXT) as context:
        normalized = context.normalize(value)
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


@dataclass(frozen=True, slots=True)
class ScenarioDcfValue:
    scenario_id: str
    enterprise_value: Decimal

    def __post_init__(self) -> None:
        if self.scenario_id not in {"base", "bull", "bear"}:
            raise CompanyResearchValidationError(
                "scenario DCF value must name a scenario"
            )
        _artifact_decimal(self.enterprise_value, "scenario DCF enterprise value")


@dataclass(frozen=True, slots=True)
class SecurityValueRangeArtifact:
    security_external_key: str
    usd_per_share: ValueRange
    cny_return: ValueRange

    def __post_init__(self) -> None:
        _artifact_text(self.security_external_key, "security value security key")
        if (
            type(self.usd_per_share) is not ValueRange
            or type(self.cny_return) is not ValueRange
        ):
            raise CompanyResearchValidationError("security value ranges must be typed")


@dataclass(frozen=True, slots=True)
class ReverseDcfArtifact:
    driver_key: str
    implied_value: Decimal
    achieved_residual: Decimal
    iteration_count: int

    def __post_init__(self) -> None:
        if (
            self.driver_key != "fcff_multiplier"
            or type(self.iteration_count) is not int
            or self.iteration_count < 1
        ):
            raise CompanyResearchValidationError(
                "reverse DCF result must name a bounded driver"
            )
        _artifact_decimal(self.implied_value, "reverse DCF implied value")
        _artifact_decimal(self.achieved_residual, "reverse DCF achieved residual")


@dataclass(frozen=True, slots=True)
class RequiredReturnComparisonArtifact:
    """One Security's mechanism range compared against the mandated return."""

    security_external_key: str
    required_return: Decimal
    achieved_return_range: ValueRange
    meets_required_return: bool

    def __post_init__(self) -> None:
        _artifact_text(self.security_external_key, "required return security key")
        _artifact_decimal(
            self.required_return, "required return comparison required_return"
        )
        if self.required_return <= Decimal("0"):
            raise CompanyResearchValidationError(
                "required return comparison required_return must be positive"
            )
        if type(self.achieved_return_range) is not ValueRange:
            raise CompanyResearchValidationError(
                "required return comparison achieved return range must be typed"
            )
        if type(self.meets_required_return) is not bool:
            raise CompanyResearchValidationError(
                "required return comparison meeting condition must be a bool"
            )
        if self.meets_required_return != (
            self.achieved_return_range.minimum >= self.required_return
        ):
            raise CompanyResearchValidationError(
                "required return comparison meeting condition must be conservative"
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "security_external_key": self.security_external_key,
            "required_return": canonical_decimal_string(self.required_return),
            "achieved_return_range": self.achieved_return_range.canonical_payload(),
            "meets_required_return": self.meets_required_return,
        }


@dataclass(frozen=True, slots=True)
class ValuationSetArtifact:
    scenario_dcf_values: tuple[ScenarioDcfValue, ...]
    reverse_dcf: ReverseDcfArtifact | None
    security_value_ranges: tuple[SecurityValueRangeArtifact, ...]
    required_return: Decimal
    required_return_comparisons: tuple[RequiredReturnComparisonArtifact, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scenario_dcf_values, tuple)
            or {item.scenario_id for item in self.scenario_dcf_values}
            != {"base", "bull", "bear"}
            or not all(
                type(item) is ScenarioDcfValue for item in self.scenario_dcf_values
            )
        ):
            raise CompanyResearchValidationError(
                "valuation set must have one DCF value per scenario"
            )
        if (
            self.reverse_dcf is not None
            and type(self.reverse_dcf) is not ReverseDcfArtifact
        ):
            raise CompanyResearchValidationError(
                "valuation set reverse DCF must be typed"
            )
        if not isinstance(self.security_value_ranges, tuple) or not all(
            type(item) is SecurityValueRangeArtifact
            for item in self.security_value_ranges
        ):
            raise CompanyResearchValidationError(
                "valuation set security ranges must be typed"
            )
        if len(
            {item.security_external_key for item in self.security_value_ranges}
        ) != len(self.security_value_ranges):
            raise CompanyResearchValidationError(
                "valuation set security ranges must not duplicate"
            )
        _artifact_decimal(self.required_return, "valuation set required_return")
        if self.required_return <= Decimal("0"):
            raise CompanyResearchValidationError(
                "valuation set required_return must be positive"
            )
        if not isinstance(self.required_return_comparisons, tuple) or not all(
            type(item) is RequiredReturnComparisonArtifact
            for item in self.required_return_comparisons
        ):
            raise CompanyResearchValidationError(
                "valuation set required return comparisons must be typed"
            )
        if {
            item.security_external_key for item in self.required_return_comparisons
        } != {item.security_external_key for item in self.security_value_ranges}:
            raise CompanyResearchValidationError(
                "valuation set required return comparisons must cover each security exactly once"
            )
        if len(self.required_return_comparisons) != len(self.security_value_ranges):
            raise CompanyResearchValidationError(
                "valuation set required return comparisons must cover each security exactly once"
            )
        if any(
            item.required_return != self.required_return
            for item in self.required_return_comparisons
        ):
            raise CompanyResearchValidationError(
                "valuation set required return comparisons must preserve the mandate return"
            )
        ranges_by_security = {
            item.security_external_key: item.cny_return
            for item in self.security_value_ranges
        }
        for comparison in self.required_return_comparisons:
            if (
                comparison.achieved_return_range
                != ranges_by_security[comparison.security_external_key]
            ):
                raise CompanyResearchValidationError(
                    "valuation set required return comparisons must match security return ranges"
                )
            if comparison.meets_required_return != (
                comparison.achieved_return_range.minimum >= self.required_return
            ):
                raise CompanyResearchValidationError(
                    "valuation set required return comparisons must use the conservative return rule"
                )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "required_return": canonical_decimal_string(self.required_return),
            "required_return_comparisons": tuple(
                item.canonical_payload() for item in self.required_return_comparisons
            ),
        }


@dataclass(frozen=True, slots=True)
class JudgmentContextArtifact:
    operating_baseline_available: bool
    financial_bridge_closed: bool
    market_security_bridge_available: bool
    strongest_counterevidence: tuple[SourceLineageReference, ...]
    next_verification_events: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "operating_baseline_available",
            "financial_bridge_closed",
            "market_security_bridge_available",
        ):
            if type(getattr(self, name)) is not bool:
                raise CompanyResearchValidationError(
                    f"judgment context {name} must be a bool"
                )
        if not isinstance(self.strongest_counterevidence, tuple) or not all(
            type(item) is SourceLineageReference
            for item in self.strongest_counterevidence
        ):
            raise CompanyResearchValidationError(
                "judgment counterevidence must be typed lineage"
            )
        if not isinstance(self.next_verification_events, tuple) or not all(
            isinstance(item, str) and item.strip() == item and item
            for item in self.next_verification_events
        ):
            raise CompanyResearchValidationError(
                "judgment next verification events must be canonical text"
            )


@dataclass(frozen=True, slots=True)
class CompanyResearchAssessment:
    status: str
    direction: str | None
    confidence: str | None

    def __post_init__(self) -> None:
        if self.status not in {"not_answerable", "partially_answerable", "answerable"}:
            raise CompanyResearchValidationError("assessment status is invalid")
        if self.status == "not_answerable" and (
            self.direction is not None or self.confidence is not None
        ):
            raise CompanyResearchValidationError(
                "not_answerable must not have direction or confidence"
            )

    @classmethod
    def answerable(cls) -> "CompanyResearchAssessment":
        return cls("answerable", "provisional_neutral", "low")

    @classmethod
    def not_answerable(cls) -> "CompanyResearchAssessment":
        return cls("not_answerable", None, None)

    @classmethod
    def partially_answerable(cls) -> "CompanyResearchAssessment":
        return cls("partially_answerable", "provisional_neutral", "low")


_MEMO_ARTIFACT_KINDS = frozenset(
    {
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
    }
)


@dataclass(frozen=True, slots=True)
class CompanyResearchArtifactReference:
    """A typed content-addressed reference used by a machine memo candidate."""

    artifact_kind: str
    content_hash: str

    def __post_init__(self) -> None:
        if self.artifact_kind not in _MEMO_ARTIFACT_KINDS:
            raise CompanyResearchValidationError(
                "memo artifact reference kind is invalid"
            )
        if (
            not isinstance(self.content_hash, str)
            or _SHA256.fullmatch(self.content_hash) is None
        ):
            raise CompanyResearchValidationError(
                "memo artifact reference content_hash must be SHA-256"
            )

    def canonical_payload(self) -> dict[str, str]:
        return {
            "artifact_kind": self.artifact_kind,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class CompanyResearchMemoArtifact:
    """A structured research candidate, optionally closed by local human review."""

    assessment_status: str
    business_map_ref: CompanyResearchArtifactReference
    driver_map_ref: CompanyResearchArtifactReference
    financial_bridge_ref: CompanyResearchArtifactReference
    scenario_set_ref: CompanyResearchArtifactReference
    valuation_set_ref: CompanyResearchArtifactReference | None
    gap_keys: tuple[str, ...]
    strongest_counterevidence: tuple[SourceLineageReference, ...]
    next_verification_events: tuple[str, ...]
    research_gaps: tuple[ResearchGap, ...] = ()
    candidate_status: str = "machine_draft"
    reviewer: str | None = None
    markdown: str | None = None

    def __post_init__(self) -> None:
        if self.candidate_status == "machine_draft":
            if self.reviewer is not None or self.markdown is not None:
                raise CompanyResearchValidationError(
                    "machine_draft must not contain human confirmation"
                )
        elif self.candidate_status == "human_confirmed":
            if self.reviewer != "human:local-user":
                raise CompanyResearchValidationError(
                    "human_confirmed must name the local human reviewer"
                )
            if (
                not isinstance(self.markdown, str)
                or not self.markdown.strip()
                or len(self.markdown) > 100000
                or "\r" in self.markdown
            ):
                raise CompanyResearchValidationError(
                    "human_confirmed markdown must be non-blank canonical LF text"
                )
        else:
            raise CompanyResearchValidationError(
                "company research memo candidate status is invalid"
            )
        if self.assessment_status not in {
            "not_answerable",
            "partially_answerable",
            "answerable",
        }:
            raise CompanyResearchValidationError(
                "company research memo assessment status is invalid"
            )
        references = (
            (self.business_map_ref, "business_map"),
            (self.driver_map_ref, "driver_map"),
            (self.financial_bridge_ref, "financial_bridge"),
            (self.scenario_set_ref, "scenario_set"),
        )
        if any(
            type(reference) is not CompanyResearchArtifactReference
            or reference.artifact_kind != expected_kind
            for reference, expected_kind in references
        ):
            raise CompanyResearchValidationError(
                "company research memo references must match artifact kinds"
            )
        if self.valuation_set_ref is not None and (
            type(self.valuation_set_ref) is not CompanyResearchArtifactReference
            or self.valuation_set_ref.artifact_kind != "valuation_set"
        ):
            raise CompanyResearchValidationError(
                "company research memo valuation reference must be typed"
            )
        if (
            not isinstance(self.gap_keys, tuple)
            or tuple(sorted(self.gap_keys)) != self.gap_keys
            or len(set(self.gap_keys)) != len(self.gap_keys)
            or not all(_GAP_CODE.fullmatch(key) for key in self.gap_keys)
        ):
            raise CompanyResearchValidationError(
                "company research memo gap keys must be unique and canonical"
            )
        _artifact_optional_refs(
            self.strongest_counterevidence,
            "company research memo strongest counterevidence",
        )
        if not isinstance(self.next_verification_events, tuple) or not all(
            isinstance(item, str) and item and item == item.strip()
            for item in self.next_verification_events
        ):
            raise CompanyResearchValidationError(
                "company research memo verification events must be canonical text"
            )
        if (
            not isinstance(self.research_gaps, tuple)
            or not all(type(item) is ResearchGap for item in self.research_gaps)
            or tuple(sorted(item.code for item in self.research_gaps))
            != tuple(item.code for item in self.research_gaps)
            or len({item.code for item in self.research_gaps})
            != len(self.research_gaps)
        ):
            raise CompanyResearchValidationError(
                "company research memo gaps must be unique and canonical"
            )
