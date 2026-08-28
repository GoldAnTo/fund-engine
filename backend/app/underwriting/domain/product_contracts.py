"""Immutable input contracts for the independent investment research product.

These value objects preserve caller-supplied timestamps as supplied.  Services
are responsible for normalizing timestamps to UTC before they cross a storage
or external-system boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from uuid import UUID

from app.underwriting.hashing import canonical_hash

from .types import AnswerabilityState


_CURRENCY_CODE = re.compile(r"[A-Z]{3}")
_SUPPORTED_PRODUCT_CURRENCIES = frozenset({"CNY", "USD"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
PRODUCT_HISTORICAL_BASIS_SCHEMA = "product.historical-basis.v1"


class ValueNature(StrEnum):
    ACTUAL = "actual"
    FORECAST = "forecast"
    DERIVED = "derived"
    ASSUMPTION = "assumption"


class Provenance(StrEnum):
    COMPANY_GUIDANCE = "company_guidance"
    CONSENSUS = "consensus"
    HOUSE = "house"
    MARKET_IMPLIED = "market_implied"
    THIRD_PARTY = "third_party"
    SOURCE_REPORTED = "source_reported"


class ScenarioKey(StrEnum):
    BASE = "base"
    BULL = "bull"
    BEAR = "bear"


class EpistemicStatus(StrEnum):
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    CANDIDATE = "candidate"
    SELF_REVIEWED = "self_reviewed"
    ADOPTED = "adopted"
    CHALLENGED = "challenged"
    SUPERSEDED = "superseded"


class AssessmentDirection(StrEnum):
    PROVISIONAL_BULLISH = "provisional_bullish"
    PROVISIONAL_NEUTRAL = "provisional_neutral"
    PROVISIONAL_CAUTIOUS = "provisional_cautious"


class AssessmentConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    USER_FROZEN = "user_frozen"
    SUPERSEDED = "superseded"


class AgendaGenerationMethod(StrEnum):
    DETERMINISTIC_TEMPLATE = "deterministic_template"
    AI_GENERATED = "ai_generated"


class FxQuoteDirection(StrEnum):
    """The rate is the number of quote-currency units per one base unit."""

    QUOTE_PER_BASE = "quote_per_base"


def _require_uuid(value: object, field_name: str) -> None:
    if type(value) is not UUID:
        raise ValueError(f"{field_name} must be a UUID")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_sha256(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256")


def agenda_items_hash(items: tuple[str, ...]) -> str:
    """Return the stable digest of the agenda item sequence, preserving its order."""
    return hashlib.sha256(
        json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_aware_datetime(value: object, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


def _is_later(left: datetime, right: datetime) -> bool:
    """Compare aware datetimes as instants without mutating stored values."""
    return left.astimezone(UTC) > right.astimezone(UTC)


def _require_currency(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _CURRENCY_CODE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a three-letter uppercase currency code")
    if value not in _SUPPORTED_PRODUCT_CURRENCIES:
        raise ValueError(f"{field_name} must be a supported product currency")


def _require_positive_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a positive finite Decimal")


def _require_nonnegative_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a non-negative finite Decimal")


def _require_finite_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal")


def _require_enum(value: object, enum_type: type[StrEnum], field_name: str) -> None:
    if type(value) is not enum_type:
        raise ValueError(f"{field_name} is invalid")


def _require_text_tuple(
    value: object, field_name: str, *, nonempty: bool = False
) -> None:
    if not isinstance(value, tuple) or (nonempty and not value):
        suffix = " a non-empty tuple" if nonempty else " a tuple"
        raise ValueError(f"{field_name} must be{suffix}")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must not contain duplicates")


def _require_uuid_tuple(
    value: object, field_name: str, *, nonempty: bool = False
) -> None:
    if not isinstance(value, tuple) or (nonempty and not value):
        suffix = " a non-empty tuple" if nonempty else " a tuple"
        raise ValueError(f"{field_name} must be{suffix}")
    if not all(type(item) is UUID for item in value):
        raise ValueError(f"{field_name} must contain UUIDs")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must not contain duplicates")


def _validate_market_times(market_at: datetime, available_at: datetime) -> None:
    _require_aware_datetime(market_at, "market_at")
    _require_aware_datetime(available_at, "available_at")
    if _is_later(market_at, available_at):
        raise ValueError("market_at must not be later than available_at")


@dataclass(frozen=True, slots=True)
class ResearchScopeInput:
    primary_company_id: UUID
    target_security_ids: tuple[UUID, ...]
    industry_ids: tuple[UUID, ...]
    covered_segments: tuple[str, ...]
    user_focus: str | None
    exclusions: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.primary_company_id, "primary_company_id")
        _require_uuid_tuple(
            self.target_security_ids, "target_security_ids", nonempty=True
        )
        _require_uuid_tuple(self.industry_ids, "industry_ids")
        _require_text_tuple(self.covered_segments, "covered_segments")
        if self.user_focus is not None:
            _require_text(self.user_focus, "user_focus")
        _require_text_tuple(self.exclusions, "exclusions")


@dataclass(frozen=True, slots=True)
class AgendaGeneratorInput:
    """Frozen provenance for an agenda generated by rules or an AI model."""

    method: AgendaGenerationMethod
    template_key: str | None
    template_version: str | None
    model_name: str | None
    prompt_template_version: str | None
    input_summary_hash: str
    output_hash: str

    def __post_init__(self) -> None:
        _require_enum(self.method, AgendaGenerationMethod, "agenda generation method")
        _require_sha256(self.input_summary_hash, "input_summary_hash")
        _require_sha256(self.output_hash, "output_hash")
        if self.method is AgendaGenerationMethod.DETERMINISTIC_TEMPLATE:
            _require_text(self.template_key, "template_key")
            _require_text(self.template_version, "template_version")
            if self.model_name is not None or self.prompt_template_version is not None:
                raise ValueError(
                    "deterministic agenda provenance must not include AI fields"
                )
            return
        if any(
            value is None or not isinstance(value, str) or not value.strip()
            for value in (self.model_name, self.prompt_template_version)
        ):
            raise ValueError(
                "AI agenda provenance requires model, prompt, and input summary hashes"
            )
        _require_sha256(self.input_summary_hash, "input_summary_hash")
        if self.template_key is not None or self.template_version is not None:
            raise ValueError(
                "AI agenda provenance must not include deterministic template fields"
            )


@dataclass(frozen=True, slots=True)
class ResearchAgendaInput:
    scope_id: UUID
    items: tuple[str, ...]
    generator: AgendaGeneratorInput

    def __post_init__(self) -> None:
        _require_uuid(self.scope_id, "scope_id")
        _require_text_tuple(self.items, "agenda items", nonempty=True)
        if type(self.generator) is not AgendaGeneratorInput:
            raise ValueError("agenda generator is invalid")
        if self.generator.output_hash != agenda_items_hash(self.items):
            raise ValueError("output_hash must match agenda items hash")


@dataclass(frozen=True, slots=True)
class ProductHistoricalBasisInput:
    cutoff_at: datetime
    source_manifest_hash: str
    definition_bundle_hash: str
    parser_bundle_hash: str

    def __post_init__(self) -> None:
        _require_aware_datetime(self.cutoff_at, "cutoff_at")
        _require_sha256(self.source_manifest_hash, "source_manifest_hash")
        _require_sha256(self.definition_bundle_hash, "definition_bundle_hash")
        _require_sha256(self.parser_bundle_hash, "parser_bundle_hash")


def product_historical_basis_payload_and_hash(
    value: ProductHistoricalBasisInput,
) -> tuple[dict[str, str], str]:
    """Normalize one product basis into its canonical immutable payload/hash."""
    if type(value) is not ProductHistoricalBasisInput:
        raise ValueError("value must be a ProductHistoricalBasisInput")
    cutoff = value.cutoff_at.astimezone(UTC)
    payload = {
        "schema_version": PRODUCT_HISTORICAL_BASIS_SCHEMA,
        "cutoff_at": cutoff.isoformat(),
        "source_manifest_hash": value.source_manifest_hash,
        "definition_bundle_hash": value.definition_bundle_hash,
        "parser_bundle_hash": value.parser_bundle_hash,
    }
    return payload, canonical_hash(payload)


@dataclass(frozen=True, slots=True)
class PriceSnapshotInput:
    security_identity_id: UUID
    price: Decimal
    currency: str
    price_type: str
    adjustment_basis: str
    market_at: datetime
    available_at: datetime
    source_id: str
    raw_hash: str

    def __post_init__(self) -> None:
        _require_uuid(self.security_identity_id, "security_identity_id")
        _require_positive_decimal(self.price, "price")
        _require_currency(self.currency, "currency")
        _require_text(self.price_type, "price_type")
        _require_text(self.adjustment_basis, "adjustment_basis")
        _validate_market_times(self.market_at, self.available_at)
        _require_text(self.source_id, "source_id")
        _require_sha256(self.raw_hash, "raw_hash")


@dataclass(frozen=True, slots=True)
class FXSnapshotInput:
    base_currency: str
    quote_currency: str
    rate: Decimal
    quote_direction: FxQuoteDirection
    market_at: datetime
    available_at: datetime
    source_id: str
    raw_hash: str

    def __post_init__(self) -> None:
        _require_currency(self.base_currency, "base_currency")
        _require_currency(self.quote_currency, "quote_currency")
        if self.base_currency == self.quote_currency:
            raise ValueError("fx base and quote currencies must differ")
        _require_positive_decimal(self.rate, "fx rate")
        _require_enum(self.quote_direction, FxQuoteDirection, "fx quote direction")
        _validate_market_times(self.market_at, self.available_at)
        _require_text(self.source_id, "source_id")
        _require_sha256(self.raw_hash, "raw_hash")


@dataclass(frozen=True, slots=True)
class CapitalStructureSnapshotInput:
    company_id: UUID
    currency: str
    cash: Decimal
    debt: Decimal
    minority_interest: Decimal
    investments: Decimal
    pension_liabilities: Decimal
    other_adjustments: Decimal
    basic_shares: Decimal
    diluted_shares: Decimal
    potential_dilution_descriptors: tuple[str, ...]
    report_period_start: datetime
    report_period_end: datetime
    market_at: datetime
    available_at: datetime
    source_id: str
    raw_hash: str

    def __post_init__(self) -> None:
        _require_uuid(self.company_id, "company_id")
        _require_currency(self.currency, "currency")
        for field_name in (
            "cash",
            "debt",
            "minority_interest",
            "investments",
            "pension_liabilities",
            "other_adjustments",
        ):
            _require_finite_decimal(getattr(self, field_name), field_name)
        _require_positive_decimal(self.basic_shares, "basic_shares")
        _require_positive_decimal(self.diluted_shares, "diluted_shares")
        if self.diluted_shares < self.basic_shares:
            raise ValueError("diluted_shares must not be less than basic_shares")
        _require_text_tuple(
            self.potential_dilution_descriptors,
            "potential_dilution_descriptors",
        )
        _require_aware_datetime(self.report_period_start, "report_period_start")
        _require_aware_datetime(self.report_period_end, "report_period_end")
        if _is_later(self.report_period_start, self.report_period_end):
            raise ValueError("report period is invalid")
        _validate_market_times(self.market_at, self.available_at)
        _require_text(self.source_id, "source_id")
        _require_sha256(self.raw_hash, "raw_hash")


@dataclass(frozen=True, slots=True)
class SecurityRightsInput:
    security_identity_id: UUID
    economic_units: Decimal
    votes_per_unit: Decimal
    conversion_ratio: Decimal
    adr_ratio: Decimal
    dividend_rights_per_unit: Decimal
    effective_from: datetime
    effective_to: datetime | None
    source_id: str
    raw_hash: str

    def __post_init__(self) -> None:
        _require_uuid(self.security_identity_id, "security_identity_id")
        for field_name in ("economic_units", "conversion_ratio", "adr_ratio"):
            _require_positive_decimal(getattr(self, field_name), field_name)
        for field_name in ("votes_per_unit", "dividend_rights_per_unit"):
            _require_nonnegative_decimal(getattr(self, field_name), field_name)
        _require_aware_datetime(self.effective_from, "effective_from")
        if self.effective_to is not None:
            _require_aware_datetime(self.effective_to, "effective_to")
            if not _is_later(self.effective_to, self.effective_from):
                raise ValueError("effective interval is invalid")
        _require_text(self.source_id, "source_id")
        _require_sha256(self.raw_hash, "raw_hash")


@dataclass(frozen=True, slots=True)
class AssessmentState:
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus

    def __post_init__(self) -> None:
        _require_enum(self.answerability, AnswerabilityState, "answerability")
        if self.direction is not None:
            _require_enum(self.direction, AssessmentDirection, "assessment direction")
        if self.confidence is not None:
            _require_enum(
                self.confidence, AssessmentConfidence, "assessment confidence"
            )
        _require_enum(self.publication_status, PublicationStatus, "publication status")
        if self.answerability is AnswerabilityState.NOT_ANSWERABLE and (
            self.direction is not None or self.confidence is not None
        ):
            raise ValueError(
                "direction and confidence must be null when not_answerable"
            )


@dataclass(frozen=True, slots=True)
class RevisionBoundaryInput:
    historical_basis_id: UUID
    mandate_id: UUID
    scope_id: UUID
    agenda_id: UUID
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    parent_revision_id: UUID | None

    def __post_init__(self) -> None:
        for field_name in (
            "historical_basis_id",
            "mandate_id",
            "scope_id",
            "agenda_id",
            "capital_structure_snapshot_id",
        ):
            _require_uuid(getattr(self, field_name), field_name)
        _require_uuid_tuple(
            self.price_snapshot_ids, "price_snapshot_ids", nonempty=True
        )
        _require_uuid_tuple(self.fx_snapshot_ids, "fx_snapshot_ids")
        _require_uuid_tuple(
            self.security_rights_ids, "security_rights_ids", nonempty=True
        )
        if self.parent_revision_id is not None:
            _require_uuid(self.parent_revision_id, "parent_revision_id")


@dataclass(frozen=True, slots=True)
class ProductRevisionView:
    id: UUID
    project_id: UUID
    boundary_id: UUID
    manifest_hash: str
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus

    def __post_init__(self) -> None:
        _require_uuid(self.id, "id")
        _require_uuid(self.project_id, "project_id")
        _require_uuid(self.boundary_id, "boundary_id")
        _require_sha256(self.manifest_hash, "manifest_hash")
        _require_uuid_tuple(
            self.price_snapshot_ids, "price_snapshot_ids", nonempty=True
        )
        _require_uuid_tuple(self.fx_snapshot_ids, "fx_snapshot_ids")
        _require_uuid(
            self.capital_structure_snapshot_id, "capital_structure_snapshot_id"
        )
        _require_uuid_tuple(
            self.security_rights_ids, "security_rights_ids", nonempty=True
        )
        for value, field_name in (
            (self.price_snapshot_ids, "price_snapshot_ids"),
            (self.fx_snapshot_ids, "fx_snapshot_ids"),
            (self.security_rights_ids, "security_rights_ids"),
        ):
            if value != tuple(sorted(value, key=str)):
                raise ValueError(f"{field_name} must be canonical")
        AssessmentState(
            answerability=self.answerability,
            direction=self.direction,
            confidence=self.confidence,
            publication_status=self.publication_status,
        )
        if self.publication_status is PublicationStatus.DRAFT:
            raise ValueError("formal product revision must not be draft")
