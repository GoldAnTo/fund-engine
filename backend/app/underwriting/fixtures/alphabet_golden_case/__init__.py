"""Authenticated, bounded primary-source extracts for the Alphabet golden case.

The package stores no source documents.  It only preserves the audit-safe facts,
their locators and digests, and explicit gaps that prevent this fixture from
turning into invented market data or a forward model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Literal
from unicodedata import normalize

from app.models.ledger import ValidationError
from app.underwriting.hashing import canonical_hash


_ROOT = Path(__file__).resolve().parent
_MANIFEST_SCHEMA = "alphabet.golden-case.manifest.v1"
_BUSINESS_MAP_SCHEMA = "alphabet.golden-case.business-map.v1"
_SOURCE_FACTS_SCHEMA = "alphabet.golden-case.source-facts.v1"
_MARKET_INPUTS_SCHEMA = "alphabet.golden-case.market-inputs.v1"
_STRATEGY_ASSUMPTIONS_SCHEMA = "alphabet.golden-case.strategy-assumptions.v1"
BUNDLED_MANIFEST_CONTENT_SHA256 = "43a6d4e13d1dddbf2c24e097b4bc05dbe52bd9424a60e1233a054de138a0631a"
# The first published evidence-only fixture.  Existing projects may retain
# this manifest even though later fixture revisions added market/model files.
LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256 = (
    "632f9e40fb2707a16b3cc104910b45ab9d71b916bcc666e141e56b2d2046200b"
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = frozenset({"schema_version", "content_hash", "cutoff", "files"})
_JSON_FILE_KEYS = frozenset({"name", "content_hash"})
_RAW_FILE_KEYS = frozenset({"name", "content_hash", "raw_hash", "raw_size"})
_BUSINESS_MAP_KEYS = frozenset({"schema_version", "content_hash", "modules"})
_MODULE_KEYS = frozenset({"key", "label"})
_SOURCE_FACTS_KEYS = frozenset(
    {"schema_version", "content_hash", "company_external_key", "security_external_keys", "facts", "research_gaps"}
)
_FACT_KEYS = frozenset(
    {
        "fact_key", "company_external_key", "business_module", "metric_key", "value",
        "value_kind", "currency", "unit", "period_start", "period_end", "published_at",
        "available_at", "source_role", "source_url", "source_locator", "raw_hash",
    }
)
_GAP_KEYS = frozenset({"gap_key", "business_module", "reason"})
_MARKET_INPUT_KEYS = frozenset(
    {
        "schema_version", "content_hash", "company_external_key",
        "security_external_keys", "prices", "fx", "capital_structure",
        "security_rights", "class_b_rights",
    }
)
_PROVENANCE_KEYS = frozenset(
    {
        "source_url", "source_locator", "raw_hash", "provider_policy_version",
        "raw_components",
    }
)
_RAW_COMPONENT_KEYS = frozenset(
    {"raw_file", "raw_hash", "source_url", "source_locator"}
)
_PRICE_KEYS = frozenset(
    {
        "kind", "security_external_key", "value", "currency", "price_type",
        "adjustment_basis", "market_at", "available_at", *_PROVENANCE_KEYS,
    }
)
_FX_KEYS = frozenset(
    {
        "kind", "base_currency", "quote_currency", "rate", "quote_direction",
        "market_at", "available_at", *_PROVENANCE_KEYS,
    }
)
_CAPITAL_KEYS = frozenset(
    {
        "kind", "company_external_key", "currency", "cash", "debt",
        "minority_interest", "investments", "pension_liabilities",
        "other_adjustments", "basic_shares", "diluted_shares",
        "potential_dilution_descriptors", "report_period_start",
        "report_period_end", "market_at", "available_at",
        "capital_bridge_policy_version", "policy_excluded_adjustments",
        *_PROVENANCE_KEYS,
    }
)
_RIGHTS_KEYS = frozenset(
    {
        "kind", "security_external_key", "economic_units", "votes_per_unit",
        "conversion_ratio", "adr_ratio", "dividend_rights_per_unit",
        "effective_from", "effective_to", "available_at", *_PROVENANCE_KEYS,
    }
)
_CLASS_B_RIGHTS_KEYS = frozenset(
    {
        "kind", "component_key", "economic_units", "votes_per_unit",
        "conversion_to_security_external_key", "conversion_ratio",
        "dividend_rights_per_unit", "economic_rights_per_unit",
        "effective_from", "effective_to", "price_proxy_security_external_key",
        "price_proxy_policy_version", "available_at", "legal_provenance",
        "unit_provenance",
    }
)
_STRATEGY_KEYS = frozenset(
    {
        "schema_version", "content_hash", "strategy_version", "first_fiscal_year",
        "driver_paths", "scenario_overrides", "terminal_growth",
    }
)
_ASSUMPTION_METADATA_KEYS = frozenset(
    {"state", "assumption_key", "rationale", "equation"}
)
_ASSUMPTION_NUMBER_KEYS = frozenset({"value", *_ASSUMPTION_METADATA_KEYS})
_DRIVER_PATH_KEYS = frozenset({"driver_key", "values", *_ASSUMPTION_METADATA_KEYS})
_SCENARIO_KEYS = frozenset({"scenario_id", "mechanism_id", "driver_overrides"})
_OVERRIDE_KEYS = frozenset({"driver_key", *_ASSUMPTION_NUMBER_KEYS})
_FINANCIAL_DRIVER_KEYS = (
    "revenue", "operating_margin", "cash_tax_rate", "depreciation", "capex",
    "working_capital_change",
)
_BUSINESS_MODULES = frozenset(
    {
        "search_and_other_ads", "youtube_ads_and_subscriptions", "google_cloud",
        "other_google_services", "other_bets", "corporate_capital_allocation",
    }
)
_ALLOWED_CURRENCIES = frozenset({"USD"})
_ALLOWED_UNITS = frozenset({"million"})
_VALUE_KINDS = frozenset({"reported", "derived", "management_guidance"})
_SOURCE_ROLES = frozenset({"regulatory_filing", "company_material", "official_regulator"})


class AlphabetGoldenCaseFixtureError(ValidationError):
    """The authenticated fixture cannot safely provide source evidence."""


@dataclass(frozen=True, slots=True)
class CompanySourceFact:
    fact_key: str
    company_external_key: str
    business_module: str
    metric_key: str
    value: Decimal | str
    value_kind: Literal["reported", "derived", "management_guidance"]
    currency: str | None
    unit: str | None
    period_start: date | None
    period_end: date | None
    published_at: datetime
    available_at: datetime
    source_role: Literal["regulatory_filing", "company_material", "official_regulator"]
    source_url: str
    source_locator: str
    raw_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "fact_key": self.fact_key,
            "company_external_key": self.company_external_key,
            "business_module": self.business_module,
            "metric_key": self.metric_key,
            "value": format(self.value, "f") if isinstance(self.value, Decimal) else self.value,
            "value_kind": self.value_kind,
            "currency": self.currency,
            "unit": self.unit,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "published_at": self.published_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "source_role": self.source_role,
            "source_url": self.source_url,
            "source_locator": self.source_locator,
            "raw_hash": self.raw_hash,
        }


@dataclass(frozen=True, slots=True)
class ResearchGap:
    gap_key: str
    business_module: str
    reason: str

    def payload(self) -> dict[str, str]:
        return {"gap_key": self.gap_key, "business_module": self.business_module, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CapturedRawComponent:
    raw_file: str
    raw_hash: str
    source_url: str
    source_locator: str

    def payload(self) -> dict[str, str]:
        return {
            "raw_file": self.raw_file,
            "raw_hash": self.raw_hash,
            "source_url": self.source_url,
            "source_locator": self.source_locator,
        }


@dataclass(frozen=True, slots=True)
class CapturedProvenance:
    source_url: str
    source_locator: str
    raw_hash: str
    provider_policy_version: str
    raw_components: tuple[CapturedRawComponent, ...] = ()


@dataclass(frozen=True, slots=True)
class CapturedMarketPrice:
    security_external_key: str
    value: Decimal
    currency: str
    price_type: str
    adjustment_basis: str
    market_at: datetime
    available_at: datetime
    provenance: CapturedProvenance


@dataclass(frozen=True, slots=True)
class CapturedMarketFX:
    base_currency: str
    quote_currency: str
    rate: Decimal
    quote_direction: str
    market_at: datetime
    available_at: datetime
    provenance: CapturedProvenance


@dataclass(frozen=True, slots=True)
class CapturedCapitalStructure:
    company_external_key: str
    currency: str
    values: tuple[tuple[str, Decimal], ...]
    potential_dilution_descriptors: tuple[str, ...]
    report_period_start: datetime
    report_period_end: datetime
    market_at: datetime
    available_at: datetime
    provenance: CapturedProvenance
    capital_bridge_policy_version: str
    policy_excluded_adjustments: tuple[str, ...]

    def value(self, name: str) -> Decimal:
        return dict(self.values)[name]


@dataclass(frozen=True, slots=True)
class CapturedSecurityRights:
    security_external_key: str
    values: tuple[tuple[str, Decimal], ...]
    effective_from: datetime
    effective_to: datetime | None
    available_at: datetime
    provenance: CapturedProvenance

    def value(self, name: str) -> Decimal:
        return dict(self.values)[name]


@dataclass(frozen=True, slots=True)
class CapturedNonListedSecurityRights:
    component_key: str
    economic_units: Decimal
    votes_per_unit: Decimal
    conversion_to_security_external_key: str
    conversion_ratio: Decimal
    dividend_rights_per_unit: Decimal
    economic_rights_per_unit: Decimal
    effective_from: datetime
    effective_to: datetime | None
    price_proxy_security_external_key: str
    price_proxy_policy_version: str
    available_at: datetime
    legal_provenance: CapturedProvenance
    unit_provenance: CapturedProvenance


@dataclass(frozen=True, slots=True)
class AlphabetMarketInputBundle:
    content_hash: str
    company_external_key: str
    security_external_keys: tuple[str, ...]
    prices: tuple[CapturedMarketPrice, ...]
    fx: CapturedMarketFX
    capital_structure: CapturedCapitalStructure
    security_rights: tuple[CapturedSecurityRights, ...]
    class_b_rights: CapturedNonListedSecurityRights


@dataclass(frozen=True, slots=True)
class CapturedAssumptionNumber:
    value: Decimal
    state: str
    assumption_key: str
    rationale: str
    equation: str


@dataclass(frozen=True, slots=True)
class CapturedStrategyDriverPath:
    driver_key: str
    values: tuple[Decimal, ...]
    state: str
    assumption_key: str
    rationale: str
    equation: str


@dataclass(frozen=True, slots=True)
class CapturedStrategyScenario:
    scenario_id: str
    mechanism_id: str
    driver_overrides: tuple[tuple[str, CapturedAssumptionNumber], ...]


@dataclass(frozen=True, slots=True)
class AlphabetStrategyAssumptionBundle:
    content_hash: str
    strategy_version: str
    first_fiscal_year: int
    driver_paths: tuple[CapturedStrategyDriverPath, ...]
    scenario_overrides: tuple[CapturedStrategyScenario, ...]
    terminal_growth: CapturedAssumptionNumber


@dataclass(frozen=True, slots=True)
class AlphabetGoldenCaseFixture:
    cutoff: datetime
    content_hash: str
    company_external_key: str
    security_external_keys: tuple[str, ...]
    business_modules: tuple[object, ...]
    facts: tuple[CompanySourceFact, ...]
    research_gaps: tuple[ResearchGap, ...]
    market_inputs: AlphabetMarketInputBundle | None = None
    strategy_assumptions: AlphabetStrategyAssumptionBundle | None = None


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture contains duplicate key {key!r}")
        result[key] = value
    return result


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {path.name} is unreadable") from exc


def _read_json(path: Path, contents: bytes) -> dict[str, object]:
    try:
        raw = json.loads(contents.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {path.name} is unreadable") from exc
    if not isinstance(raw, dict):
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {path.name} must be an object")
    return raw


def _exact_object(value: object, keys: frozenset[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} has invalid fields")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must not be empty")
    if value != value.strip():
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must not contain leading or trailing whitespace")
    if normalize("NFC", value) != value:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must use NFC text")
    return value


def _hash(value: object, field: str) -> str:
    text = _text(value, field)
    if _HASH.fullmatch(text) is None:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must be a SHA-256 hash")
    return text


def _canonical_decimal(value: object, field: str) -> Decimal:
    text = _text(value, field)
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} must be a canonical Decimal string"
        ) from exc
    from app.underwriting.domain.company_research import canonical_decimal_string

    if not parsed.is_finite() or canonical_decimal_string(parsed) != text:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} must be a canonical Decimal string"
        )
    return parsed


def _http_url(value: object, field: str) -> str:
    text = _text(value, field)
    if not (text.startswith("https://") or text.startswith("http://")):
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} must be an HTTP source URL"
        )
    return text


def _timestamp(value: object, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != text:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must round-trip exactly")
    return parsed.astimezone(UTC)


def _date(value: object, field: str) -> date | None:
    if value is None:
        return None
    text = _text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must round-trip exactly") from exc
    if parsed.isoformat() != text:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} must round-trip exactly")
    return parsed


def _file_hash(path: Path, contents: bytes, expected: str) -> None:
    if hashlib.sha256(contents).hexdigest() != expected:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {path.name} content hash mismatch")


def _raw_file_name(value: object, field: str) -> str:
    name = _text(value, field)
    path = Path(name)
    if path.is_absolute() or path.parts != ("raw", path.name) or not name.endswith(".gz"):
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} must be a local raw/*.gz sidecar"
        )
    return name


def _verified_raw_sidecar(
    path: Path,
    contents: bytes,
    *,
    expected_raw_hash: str,
    expected_raw_size: object,
) -> str:
    if type(expected_raw_size) is not int or not 0 < expected_raw_size <= 10_000_000:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {path.name} raw_size is invalid"
        )
    if len(contents) > 10_000_000:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {path.name} compressed size is invalid"
        )
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(contents)) as stream:
            raw = stream.read(expected_raw_size + 1)
    except (OSError, EOFError) as exc:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {path.name} gzip sidecar is invalid"
        ) from exc
    if len(raw) != expected_raw_size:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {path.name} decompressed size mismatch"
        )
    actual_raw_hash = hashlib.sha256(raw).hexdigest()
    if actual_raw_hash != expected_raw_hash:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {path.name} decompressed raw hash mismatch"
        )
    return actual_raw_hash


def _content_hash(raw: dict[str, object], field: str) -> str:
    declared = _hash(raw.get("content_hash"), f"{field}.content_hash")
    payload = {key: value for key, value in raw.items() if key != "content_hash"}
    if declared != canonical_hash(payload):
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {field} content hash mismatch")
    return declared


def _modules(raw: dict[str, object]) -> tuple[object, ...]:
    if raw.get("schema_version") != _BUSINESS_MAP_SCHEMA:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture business map schema is unsupported")
    _exact_object(raw, _BUSINESS_MAP_KEYS, "business map")
    _content_hash(raw, "business map")
    values = raw.get("modules")
    if not isinstance(values, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture business map modules must be an array")
    from app.underwriting.domain.company_research import CompanyResearchModule

    modules = tuple(
        CompanyResearchModule(
            key=_text(_exact_object(item, _MODULE_KEYS, "business module").get("key"), "business module.key"),
            label=_text(_exact_object(item, _MODULE_KEYS, "business module").get("label"), "business module.label"),
        )
        for item in values
    )
    if {item.key for item in modules} != _BUSINESS_MODULES or len(modules) != len(_BUSINESS_MODULES):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture business modules are incomplete")
    return modules


def _fact(raw: object, *, cutoff: datetime, company_key: str) -> CompanySourceFact:
    item = _exact_object(raw, _FACT_KEYS, "source fact")
    fact_key = _text(item.get("fact_key"), "fact.fact_key")
    external_key = _text(item.get("company_external_key"), "fact.company_external_key")
    if external_key != company_key:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact company key is invalid")
    module = _text(item.get("business_module"), "fact.business_module")
    if module not in _BUSINESS_MODULES:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact business module is invalid")
    value_kind = _text(item.get("value_kind"), "fact.value_kind")
    if value_kind not in _VALUE_KINDS:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact value_kind is invalid")
    raw_value = item.get("value")
    if not isinstance(raw_value, str):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact value must be a string")
    try:
        value: Decimal | str = Decimal(raw_value)
    except InvalidOperation:
        value = _text(raw_value, "fact.value")
    if isinstance(value, Decimal) and not value.is_finite():
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact value must be finite")
    currency = item.get("currency")
    unit = item.get("unit")
    if currency is not None and _text(currency, "fact.currency") not in _ALLOWED_CURRENCIES:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact currency is unsupported")
    if unit is not None and _text(unit, "fact.unit") not in _ALLOWED_UNITS:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact unit is unsupported")
    period_start = _date(item.get("period_start"), "fact.period_start")
    period_end = _date(item.get("period_end"), "fact.period_end")
    if period_start is not None and period_end is not None and period_start > period_end:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact period is invalid")
    available_at = _timestamp(item.get("available_at"), "fact.available_at")
    if available_at > cutoff:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact is available after fixture cutoff")
    return CompanySourceFact(
        fact_key=fact_key, company_external_key=external_key, business_module=module,
        metric_key=_text(item.get("metric_key"), "fact.metric_key"), value=value,
        value_kind=value_kind, currency=currency if isinstance(currency, str) else None,
        unit=unit if isinstance(unit, str) else None, period_start=period_start, period_end=period_end,
        published_at=_timestamp(item.get("published_at"), "fact.published_at"), available_at=available_at,
        source_role=_source_role(item.get("source_role")), source_url=_text(item.get("source_url"), "fact.source_url"),
        source_locator=_text(item.get("source_locator"), "fact.source_locator"), raw_hash=_hash(item.get("raw_hash"), "fact.raw_hash"),
    )


def _source_role(value: object) -> Literal["regulatory_filing", "company_material", "official_regulator"]:
    result = _text(value, "fact.source_role")
    if result not in _SOURCE_ROLES:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact source_role is invalid")
    return result  # type: ignore[return-value]


def _gap(raw: object) -> ResearchGap:
    item = _exact_object(raw, _GAP_KEYS, "research gap")
    module = _text(item.get("business_module"), "research gap.business_module")
    if module not in _BUSINESS_MODULES:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture research gap business module is invalid")
    return ResearchGap(_text(item.get("gap_key"), "research gap.gap_key"), module, _text(item.get("reason"), "research gap.reason"))


def _provenance(
    item: dict[str, object],
    field: str,
    *,
    raw_sidecars: dict[str, str],
) -> CapturedProvenance:
    source_url = _http_url(item.get("source_url"), f"{field}.source_url")
    source_locator = _text(item.get("source_locator"), f"{field}.source_locator")
    raw_hash = _hash(item.get("raw_hash"), f"{field}.raw_hash")
    values = item.get("raw_components")
    if not isinstance(values, list) or not values:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field}.raw_components must not be empty"
        )
    components: list[CapturedRawComponent] = []
    for index, value in enumerate(values):
        component = _exact_object(
            value, _RAW_COMPONENT_KEYS, f"{field}.raw_components[{index}]"
        )
        raw_file = _raw_file_name(
            component.get("raw_file"), f"{field}.raw_components[{index}].raw_file"
        )
        component_hash = _hash(
            component.get("raw_hash"), f"{field}.raw_components[{index}].raw_hash"
        )
        if raw_sidecars.get(raw_file) != component_hash:
            raise AlphabetGoldenCaseFixtureError(
                f"Alphabet fixture {field} references an unverified raw sidecar"
            )
        components.append(
            CapturedRawComponent(
                raw_file=raw_file,
                raw_hash=component_hash,
                source_url=_http_url(
                    component.get("source_url"),
                    f"{field}.raw_components[{index}].source_url",
                ),
                source_locator=_text(
                    component.get("source_locator"),
                    f"{field}.raw_components[{index}].source_locator",
                ),
            )
        )
    if len({item.raw_file for item in components}) != len(components):
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} raw component identity must be unique"
        )
    expected_capture_hash = (
        components[0].raw_hash
        if len(components) == 1
        else canonical_hash([component.payload() for component in components])
    )
    if raw_hash != expected_capture_hash:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} composite raw hash mismatch"
        )
    if len(components) == 1 and (
        components[0].source_url != source_url
        or components[0].source_locator != source_locator
    ):
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} singular raw lineage is inconsistent"
        )
    return CapturedProvenance(
        source_url=source_url,
        source_locator=source_locator,
        raw_hash=raw_hash,
        provider_policy_version=_text(
            item.get("provider_policy_version"),
            f"{field}.provider_policy_version",
        ),
        raw_components=tuple(components),
    )


def _market_inputs(
    raw: dict[str, object],
    *,
    cutoff: datetime,
    raw_sidecars: dict[str, str],
) -> AlphabetMarketInputBundle:
    _exact_object(raw, _MARKET_INPUT_KEYS, "market inputs")
    if raw.get("schema_version") != _MARKET_INPUTS_SCHEMA:
        raise AlphabetGoldenCaseFixtureError(
            "Alphabet fixture market inputs schema is unsupported"
        )
    content_hash = _content_hash(raw, "market inputs")
    company_key = _text(raw.get("company_external_key"), "market inputs.company_external_key")
    if company_key != "US:ALPHABET:COMPANY":
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture market inputs Company is invalid")
    security_values = raw.get("security_external_keys")
    if (
        not isinstance(security_values, list)
        or not all(isinstance(item, str) for item in security_values)
        or tuple(security_values) != ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    ):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture market input Securities are invalid")

    prices_raw = raw.get("prices")
    if not isinstance(prices_raw, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture market input prices must be an array")
    prices: list[CapturedMarketPrice] = []
    for index, value in enumerate(prices_raw):
        item = _exact_object(value, _PRICE_KEYS, f"market inputs.prices[{index}]")
        if item.get("kind") != "price" or item.get("currency") != "USD":
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture market price kind or currency is invalid")
        market_at = _timestamp(item.get("market_at"), f"market price[{index}].market_at")
        available_at = _timestamp(item.get("available_at"), f"market price[{index}].available_at")
        if market_at > available_at or available_at > cutoff:
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture market price is outside the cutoff")
        prices.append(
            CapturedMarketPrice(
                security_external_key=_text(item.get("security_external_key"), f"market price[{index}].security_external_key"),
                value=_canonical_decimal(item.get("value"), f"market price[{index}].value"),
                currency="USD",
                price_type=_text(item.get("price_type"), f"market price[{index}].price_type"),
                adjustment_basis=_text(item.get("adjustment_basis"), f"market price[{index}].adjustment_basis"),
                market_at=market_at,
                available_at=available_at,
                provenance=_provenance(
                    item, f"market price[{index}]", raw_sidecars=raw_sidecars
                ),
            )
        )
    if tuple(item.security_external_key for item in prices) != tuple(security_values):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture market prices must exactly cover Securities")

    fx_item = _exact_object(raw.get("fx"), _FX_KEYS, "market inputs.fx")
    fx_market_at = _timestamp(fx_item.get("market_at"), "market fx.market_at")
    fx_available_at = _timestamp(fx_item.get("available_at"), "market fx.available_at")
    if (
        fx_item.get("kind") != "fx"
        or fx_item.get("base_currency") != "USD"
        or fx_item.get("quote_currency") != "CNY"
        or fx_item.get("quote_direction") != "quote_per_base"
        or fx_market_at > fx_available_at
        or fx_available_at > cutoff
    ):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture USD/CNY FX is invalid")
    fx = CapturedMarketFX(
        base_currency="USD",
        quote_currency="CNY",
        rate=_canonical_decimal(fx_item.get("rate"), "market fx.rate"),
        quote_direction="quote_per_base",
        market_at=fx_market_at,
        available_at=fx_available_at,
        provenance=_provenance(fx_item, "market fx", raw_sidecars=raw_sidecars),
    )

    capital_item = _exact_object(
        raw.get("capital_structure"), _CAPITAL_KEYS, "market inputs.capital_structure"
    )
    capital_times = {
        name: _timestamp(capital_item.get(name), f"market capital_structure.{name}")
        for name in (
            "report_period_start", "report_period_end", "market_at", "available_at"
        )
    }
    descriptors = capital_item.get("potential_dilution_descriptors")
    exclusions = capital_item.get("policy_excluded_adjustments")
    if (
        capital_item.get("kind") != "capital_structure"
        or capital_item.get("company_external_key") != company_key
        or capital_item.get("currency") != "USD"
        or not isinstance(descriptors, list)
        or not all(isinstance(item, str) and item and item == item.strip() for item in descriptors)
        or len(set(descriptors)) != len(descriptors)
        or not isinstance(exclusions, list)
        or tuple(exclusions) != ("pension_liabilities",)
        or capital_times["report_period_start"] > capital_times["report_period_end"]
        or capital_times["market_at"] > capital_times["available_at"]
        or capital_times["available_at"] > cutoff
    ):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture capital structure is invalid")
    capital_names = (
        "cash", "debt", "minority_interest", "investments", "pension_liabilities",
        "other_adjustments", "basic_shares", "diluted_shares",
    )
    capital_values = tuple(
        (name, _canonical_decimal(capital_item.get(name), f"market capital_structure.{name}"))
        for name in capital_names
    )
    if dict(capital_values)["basic_shares"] <= 0 or dict(capital_values)["diluted_shares"] < dict(capital_values)["basic_shares"]:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture capital structure share counts are invalid")
    capital = CapturedCapitalStructure(
        company_external_key=company_key,
        currency="USD",
        values=capital_values,
        potential_dilution_descriptors=tuple(descriptors),
        report_period_start=capital_times["report_period_start"],
        report_period_end=capital_times["report_period_end"],
        market_at=capital_times["market_at"],
        available_at=capital_times["available_at"],
        provenance=_provenance(
            capital_item, "market capital_structure", raw_sidecars=raw_sidecars
        ),
        capital_bridge_policy_version=_text(
            capital_item.get("capital_bridge_policy_version"),
            "market capital_structure.capital_bridge_policy_version",
        ),
        policy_excluded_adjustments=tuple(exclusions),
    )

    rights_raw = raw.get("security_rights")
    if not isinstance(rights_raw, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture security rights must be an array")
    rights: list[CapturedSecurityRights] = []
    rights_names = (
        "economic_units", "votes_per_unit", "conversion_ratio", "adr_ratio",
        "dividend_rights_per_unit",
    )
    for index, value in enumerate(rights_raw):
        item = _exact_object(value, _RIGHTS_KEYS, f"market inputs.security_rights[{index}]")
        effective_from = _timestamp(item.get("effective_from"), f"market rights[{index}].effective_from")
        effective_to = (
            _timestamp(item.get("effective_to"), f"market rights[{index}].effective_to")
            if item.get("effective_to") is not None else None
        )
        available_at = _timestamp(
            item.get("available_at"), f"market rights[{index}].available_at"
        )
        values = tuple(
            (name, _canonical_decimal(item.get(name), f"market rights[{index}].{name}"))
            for name in rights_names
        )
        numbers = dict(values)
        if (
            item.get("kind") != "security_rights"
            or effective_from > cutoff
            or available_at > cutoff
            or (effective_to is not None and effective_to <= effective_from)
            or numbers["economic_units"] <= 0
            or numbers["conversion_ratio"] <= 0
            or numbers["adr_ratio"] <= 0
            or numbers["votes_per_unit"] < 0
            or numbers["dividend_rights_per_unit"] < 0
        ):
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture security rights are invalid")
        rights.append(
            CapturedSecurityRights(
                security_external_key=_text(item.get("security_external_key"), f"market rights[{index}].security_external_key"),
                values=values,
                effective_from=effective_from,
                effective_to=effective_to,
                available_at=available_at,
                provenance=_provenance(
                    item, f"market rights[{index}]", raw_sidecars=raw_sidecars
                ),
            )
        )
    if tuple(item.security_external_key for item in rights) != tuple(security_values):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture rights must exactly cover Securities")

    class_b_item = _exact_object(
        raw.get("class_b_rights"), _CLASS_B_RIGHTS_KEYS, "market inputs.class_b_rights"
    )
    class_b_effective_from = _timestamp(
        class_b_item.get("effective_from"), "market class_b_rights.effective_from"
    )
    class_b_effective_to = (
        _timestamp(
            class_b_item.get("effective_to"), "market class_b_rights.effective_to"
        )
        if class_b_item.get("effective_to") is not None
        else None
    )
    class_b = CapturedNonListedSecurityRights(
        component_key=_text(class_b_item.get("component_key"), "market class_b_rights.component_key"),
        economic_units=_canonical_decimal(class_b_item.get("economic_units"), "market class_b_rights.economic_units"),
        votes_per_unit=_canonical_decimal(class_b_item.get("votes_per_unit"), "market class_b_rights.votes_per_unit"),
        conversion_to_security_external_key=_text(class_b_item.get("conversion_to_security_external_key"), "market class_b_rights.conversion_to_security_external_key"),
        conversion_ratio=_canonical_decimal(class_b_item.get("conversion_ratio"), "market class_b_rights.conversion_ratio"),
        dividend_rights_per_unit=_canonical_decimal(class_b_item.get("dividend_rights_per_unit"), "market class_b_rights.dividend_rights_per_unit"),
        economic_rights_per_unit=_canonical_decimal(class_b_item.get("economic_rights_per_unit"), "market class_b_rights.economic_rights_per_unit"),
        effective_from=class_b_effective_from,
        effective_to=class_b_effective_to,
        price_proxy_security_external_key=_text(class_b_item.get("price_proxy_security_external_key"), "market class_b_rights.price_proxy_security_external_key"),
        price_proxy_policy_version=_text(class_b_item.get("price_proxy_policy_version"), "market class_b_rights.price_proxy_policy_version"),
        available_at=_timestamp(
            class_b_item.get("available_at"), "market class_b_rights.available_at"
        ),
        legal_provenance=_provenance(
            _exact_object(class_b_item.get("legal_provenance"), _PROVENANCE_KEYS, "market class_b_rights.legal_provenance"),
            "market class_b_rights.legal_provenance",
            raw_sidecars=raw_sidecars,
        ),
        unit_provenance=_provenance(
            _exact_object(class_b_item.get("unit_provenance"), _PROVENANCE_KEYS, "market class_b_rights.unit_provenance"),
            "market class_b_rights.unit_provenance",
            raw_sidecars=raw_sidecars,
        ),
    )
    if (
        class_b_item.get("kind") != "nonlisted_security_rights"
        or class_b.component_key != "class_b"
        or class_b.economic_units <= 0
        or class_b.votes_per_unit != Decimal("10")
        or class_b.conversion_to_security_external_key != "NASDAQ:GOOGL"
        or class_b.conversion_ratio != Decimal("1")
        or class_b.dividend_rights_per_unit != Decimal("1")
        or class_b.economic_rights_per_unit != Decimal("1")
        or class_b.price_proxy_security_external_key != "NASDAQ:GOOGL"
        or class_b.price_proxy_policy_version != "alphabet_class_b_googl_proxy.v1"
        or class_b.effective_from > cutoff
        or class_b.available_at > cutoff
        or (class_b.effective_to is not None and class_b.effective_to <= class_b.effective_from)
    ):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture Class B rights are invalid")
    listed_units = sum(item.value("economic_units") for item in rights)
    if class_b.economic_units != capital.value("basic_shares") - listed_units:
        raise AlphabetGoldenCaseFixtureError(
            "Alphabet fixture Class B units must close basic shares"
        )

    referenced_raw_files = {
        component.raw_file
        for capture in (
            *prices, fx, capital, *rights,
            class_b.legal_provenance, class_b.unit_provenance,
        )
        for component in (
            capture.raw_components
            if type(capture) is CapturedProvenance
            else capture.provenance.raw_components
        )
    }
    if referenced_raw_files != set(raw_sidecars):
        raise AlphabetGoldenCaseFixtureError(
            "Alphabet fixture raw sidecars do not exactly cover captures"
        )
    return AlphabetMarketInputBundle(
        content_hash=content_hash,
        company_external_key=company_key,
        security_external_keys=tuple(security_values),
        prices=tuple(prices),
        fx=fx,
        capital_structure=capital,
        security_rights=tuple(rights),
        class_b_rights=class_b,
    )


def _assumption_metadata(item: dict[str, object], field: str) -> tuple[str, str, str, str]:
    state = _text(item.get("state"), f"{field}.state")
    key = _text(item.get("assumption_key"), f"{field}.assumption_key")
    rationale = _text(item.get("rationale"), f"{field}.rationale")
    equation = _text(item.get("equation"), f"{field}.equation")
    if state != "assumption" or ".v" not in key:
        raise AlphabetGoldenCaseFixtureError(
            f"Alphabet fixture {field} must be an explicit versioned assumption"
        )
    return state, key, rationale, equation


def _assumption_number(raw: object, field: str) -> CapturedAssumptionNumber:
    item = _exact_object(raw, _ASSUMPTION_NUMBER_KEYS, field)
    state, key, rationale, equation = _assumption_metadata(item, field)
    return CapturedAssumptionNumber(
        value=_canonical_decimal(item.get("value"), f"{field}.value"),
        state=state,
        assumption_key=key,
        rationale=rationale,
        equation=equation,
    )


def _strategy_assumptions(raw: dict[str, object]) -> AlphabetStrategyAssumptionBundle:
    _exact_object(raw, _STRATEGY_KEYS, "strategy assumptions")
    if raw.get("schema_version") != _STRATEGY_ASSUMPTIONS_SCHEMA:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy schema is unsupported")
    content_hash = _content_hash(raw, "strategy assumptions")
    strategy_version = _text(raw.get("strategy_version"), "strategy.strategy_version")
    if ".v" not in strategy_version:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy version is invalid")
    first_year = raw.get("first_fiscal_year")
    if type(first_year) is not int or first_year < 1900:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy first fiscal year is invalid")
    paths_raw = raw.get("driver_paths")
    if not isinstance(paths_raw, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy driver paths must be an array")
    paths: list[CapturedStrategyDriverPath] = []
    for index, value in enumerate(paths_raw):
        item = _exact_object(value, _DRIVER_PATH_KEYS, f"strategy driver_paths[{index}]")
        state, key, rationale, equation = _assumption_metadata(item, f"strategy driver_paths[{index}]")
        values = item.get("values")
        if not isinstance(values, list) or len(values) != 5:
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy paths require five values")
        paths.append(
            CapturedStrategyDriverPath(
                driver_key=_text(item.get("driver_key"), f"strategy driver_paths[{index}].driver_key"),
                values=tuple(
                    _canonical_decimal(number, f"strategy driver_paths[{index}].values")
                    for number in values
                ),
                state=state,
                assumption_key=key,
                rationale=rationale,
                equation=equation,
            )
        )
    if tuple(item.driver_key for item in paths) != _FINANCIAL_DRIVER_KEYS:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy paths are incomplete")
    if any(not item.assumption_key.startswith(f"{strategy_version}:") for item in paths):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy assumption keys are not versioned")

    scenarios_raw = raw.get("scenario_overrides")
    if not isinstance(scenarios_raw, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy scenarios must be an array")
    scenarios: list[CapturedStrategyScenario] = []
    for index, value in enumerate(scenarios_raw):
        item = _exact_object(value, _SCENARIO_KEYS, f"strategy scenario[{index}]")
        overrides_raw = item.get("driver_overrides")
        if not isinstance(overrides_raw, list):
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy overrides must be an array")
        overrides = tuple(
            (
                _text(
                    _exact_object(override, _OVERRIDE_KEYS, "strategy override").get("driver_key"),
                    "strategy override.driver_key",
                ),
                _assumption_number(
                    {key: value for key, value in override.items() if key != "driver_key"},
                    "strategy override",
                ),
            )
            for override in overrides_raw
            if isinstance(override, dict)
        )
        if len(overrides) != len(overrides_raw) or tuple(key for key, _ in overrides) != _FINANCIAL_DRIVER_KEYS:
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy overrides are incomplete")
        if any(not number.assumption_key.startswith(f"{strategy_version}:") for _, number in overrides):
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy override keys are not versioned")
        scenarios.append(
            CapturedStrategyScenario(
                scenario_id=_text(item.get("scenario_id"), f"strategy scenario[{index}].scenario_id"),
                mechanism_id=_text(item.get("mechanism_id"), f"strategy scenario[{index}].mechanism_id"),
                driver_overrides=overrides,
            )
        )
    if tuple(item.scenario_id for item in scenarios) != ("base", "bull", "bear") or len({item.mechanism_id for item in scenarios}) != 3:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture strategy scenarios are not exact")
    terminal = _assumption_number(raw.get("terminal_growth"), "strategy terminal_growth")
    if not terminal.assumption_key.startswith(f"{strategy_version}:"):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture terminal assumption key is not versioned")
    return AlphabetStrategyAssumptionBundle(
        content_hash=content_hash,
        strategy_version=strategy_version,
        first_fiscal_year=first_year,
        driver_paths=tuple(paths),
        scenario_overrides=tuple(scenarios),
        terminal_growth=terminal,
    )


def load_alphabet_golden_case_fixture(root: Path | None = None) -> AlphabetGoldenCaseFixture:
    """Read a fully authenticated fixture; custom roots still verify all JSON hashes."""
    fixture_root = root or _ROOT
    manifest_path = fixture_root / "manifest.json"
    try:
        manifest_bytes = _read_bytes(manifest_path)
    except AlphabetGoldenCaseFixtureError as exc:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest is unavailable") from exc
    if root is None and hashlib.sha256(manifest_bytes).hexdigest() != BUNDLED_MANIFEST_CONTENT_SHA256:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture bundled manifest trusted content digest mismatch")
    manifest = _read_json(manifest_path, manifest_bytes)
    _exact_object(manifest, _MANIFEST_KEYS, "manifest")
    if manifest.get("schema_version") != _MANIFEST_SCHEMA:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest schema is unsupported")
    manifest_hash = _content_hash(manifest, "manifest")
    cutoff = _timestamp(manifest.get("cutoff"), "manifest.cutoff")
    files = manifest.get("files")
    allowed_json_names = {
        "business_map.json",
        "source_facts.json",
        "market_inputs.json",
        "strategy_assumptions.json",
    }
    if not isinstance(files, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest files are invalid")
    file_names: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest files are invalid")
        name_value = entry.get("name")
        if not isinstance(name_value, str):
            raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest files are invalid")
        if name_value not in allowed_json_names:
            _raw_file_name(name_value, "manifest file.name")
        file_names.append(name_value)
    if (
        not {"business_map.json", "source_facts.json"}.issubset(file_names)
        or len(set(file_names)) != len(files)
        or (("market_inputs.json" in file_names) != any(name.startswith("raw/") for name in file_names))
    ):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest files are invalid")
    parsed_files: dict[str, dict[str, object]] = {}
    raw_sidecars: dict[str, str] = {}
    for entry in files:
        assert isinstance(entry, dict)
        name_value = entry.get("name")
        assert isinstance(name_value, str)
        is_raw = name_value.startswith("raw/")
        item = _exact_object(
            entry, _RAW_FILE_KEYS if is_raw else _JSON_FILE_KEYS, "manifest file"
        )
        name = _text(item.get("name"), "manifest file.name")
        path = fixture_root / name
        contents = _read_bytes(path)
        if is_raw:
            _file_hash(path, contents, _hash(item.get("content_hash"), "manifest file.content_hash"))
            raw_hash = _hash(item.get("raw_hash"), "manifest file.raw_hash")
            raw_sidecars[name] = _verified_raw_sidecar(
                path,
                contents,
                expected_raw_hash=raw_hash,
                expected_raw_size=item.get("raw_size"),
            )
        else:
            parsed_files[name] = _read_json(path, contents)
            _file_hash(path, contents, _hash(item.get("content_hash"), "manifest file.content_hash"))
    business_map = _modules(parsed_files["business_map.json"])
    facts_raw = parsed_files["source_facts.json"]
    _exact_object(facts_raw, _SOURCE_FACTS_KEYS, "source facts")
    if facts_raw.get("schema_version") != _SOURCE_FACTS_SCHEMA:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture source facts schema is unsupported")
    _content_hash(facts_raw, "source facts")
    company_key = _text(facts_raw.get("company_external_key"), "source facts.company_external_key")
    if company_key != "US:ALPHABET:COMPANY":
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture company key is unsupported")
    security_keys_raw = facts_raw.get("security_external_keys")
    if (
        not isinstance(security_keys_raw, list)
        or not all(isinstance(key, str) for key in security_keys_raw)
        or tuple(sorted(security_keys_raw)) != ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    ):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture securities must preserve GOOGL and GOOG")
    facts_values = facts_raw.get("facts")
    gaps_values = facts_raw.get("research_gaps")
    if not isinstance(facts_values, list) or not isinstance(gaps_values, list):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture evidence arrays are invalid")
    facts = tuple(_fact(item, cutoff=cutoff, company_key=company_key) for item in facts_values)
    gaps = tuple(_gap(item) for item in gaps_values)
    if len({item.fact_key for item in facts}) != len(facts):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture fact identity must be unique")
    if len({item.gap_key for item in gaps}) != len(gaps):
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture research gap identity must be unique")
    coverage = {item.business_module for item in facts} | {item.business_module for item in gaps}
    if coverage != _BUSINESS_MODULES:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture every business module needs a fact or gap")
    if {item.source_role for item in facts} < {"regulatory_filing", "company_material"}:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture primary source roles are incomplete")
    market_inputs = (
        _market_inputs(
            parsed_files["market_inputs.json"],
            cutoff=cutoff,
            raw_sidecars=raw_sidecars,
        )
        if "market_inputs.json" in parsed_files
        else None
    )
    strategy_assumptions = (
        _strategy_assumptions(parsed_files["strategy_assumptions.json"])
        if "strategy_assumptions.json" in parsed_files
        else None
    )
    return AlphabetGoldenCaseFixture(
        cutoff=cutoff, content_hash=manifest_hash, company_external_key=company_key,
        security_external_keys=tuple(sorted(security_keys_raw)), business_modules=business_map,
        facts=facts, research_gaps=gaps, market_inputs=market_inputs,
        strategy_assumptions=strategy_assumptions,
    )
