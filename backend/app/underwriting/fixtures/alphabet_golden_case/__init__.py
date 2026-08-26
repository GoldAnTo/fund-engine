"""Authenticated, bounded primary-source extracts for the Alphabet golden case.

The package stores no source documents.  It only preserves the audit-safe facts,
their locators and digests, and explicit gaps that prevent this fixture from
turning into invented market data or a forward model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
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
BUNDLED_MANIFEST_CONTENT_SHA256 = "52657a2393f6e59f16cde6e342998462055d40f224e0766e585139c9c5b1eb2f"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_KEYS = frozenset({"schema_version", "content_hash", "cutoff", "files"})
_FILE_KEYS = frozenset({"name", "content_hash"})
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
class AlphabetGoldenCaseFixture:
    cutoff: datetime
    content_hash: str
    company_external_key: str
    security_external_keys: tuple[str, ...]
    business_modules: tuple[object, ...]
    facts: tuple[CompanySourceFact, ...]
    research_gaps: tuple[ResearchGap, ...]


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture contains duplicate key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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


def _file_hash(path: Path, expected: str) -> None:
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise AlphabetGoldenCaseFixtureError(f"Alphabet fixture {path.name} content hash mismatch")


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


def load_alphabet_golden_case_fixture(root: Path | None = None) -> AlphabetGoldenCaseFixture:
    """Read a fully authenticated fixture; custom roots still verify all JSON hashes."""
    fixture_root = root or _ROOT
    manifest_path = fixture_root / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest is unavailable") from exc
    if root is None and hashlib.sha256(manifest_bytes).hexdigest() != BUNDLED_MANIFEST_CONTENT_SHA256:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture bundled manifest trusted content digest mismatch")
    manifest = _read_json(manifest_path)
    _exact_object(manifest, _MANIFEST_KEYS, "manifest")
    if manifest.get("schema_version") != _MANIFEST_SCHEMA:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest schema is unsupported")
    manifest_hash = _content_hash(manifest, "manifest")
    cutoff = _timestamp(manifest.get("cutoff"), "manifest.cutoff")
    files = manifest.get("files")
    if not isinstance(files, list) or {item.get("name") for item in files if isinstance(item, dict)} != {"business_map.json", "source_facts.json"}:
        raise AlphabetGoldenCaseFixtureError("Alphabet fixture manifest files are invalid")
    parsed_files: dict[str, dict[str, object]] = {}
    for entry in files:
        item = _exact_object(entry, _FILE_KEYS, "manifest file")
        name = _text(item.get("name"), "manifest file.name")
        parsed_files[name] = _read_json(fixture_root / name)
        _file_hash(fixture_root / name, _hash(item.get("content_hash"), "manifest file.content_hash"))
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
    return AlphabetGoldenCaseFixture(
        cutoff=cutoff, content_hash=manifest_hash, company_external_key=company_key,
        security_external_keys=tuple(sorted(security_keys_raw)), business_modules=business_map,
        facts=facts, research_gaps=gaps,
    )
