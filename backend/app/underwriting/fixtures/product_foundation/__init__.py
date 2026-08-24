"""Trusted bundled and checksum-validated custom identity foundation fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping
import unicodedata

from app.models.ledger import ValidationError
from app.underwriting.services.kernel import canonical_hash


_ROOT = Path(__file__).resolve().parent
_SCHEMA_VERSION = "product.foundation-identities.v1"
# SHA-256 over the exact bundled UTF-8 bytes, independent of its self-declared hash.
BUNDLED_MANIFEST_CONTENT_SHA256 = (
    "97cfba971bfbc995dfead4e76c4f2eacb1b76bdd2de18d2c51039c46324b8add"
)
_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "content_hash", "companies", "securities", "rights"}
)
_COMPANY_KEYS = frozenset({"external_key", "canonical_name", "effective_from"})
_SECURITY_KEYS = frozenset(
    {
        "external_key",
        "company_key",
        "canonical_name",
        "symbol",
        "exchange",
        "currency",
        "share_class",
        "effective_from",
    }
)
_RIGHTS_KEYS = frozenset(
    {"security_key", "economic_units", "votes_per_unit", "effective_from"}
)


def _exact_object(value: object, keys: frozenset[str], field: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValidationError(f"product foundation {field} has invalid fields")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"product foundation {field} must not be empty")
    if value != value.strip():
        raise ValidationError(
            f"product foundation {field} must not contain leading or trailing whitespace"
        )
    if unicodedata.normalize("NFC", value) != value:
        raise ValidationError(f"product foundation {field} must use NFC text")
    return value


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValidationError(
                f"product foundation manifest contains duplicate key {key!r}"
            )
        value[key] = item
    return value


def _timestamp(value: object, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(
            f"product foundation {field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"product foundation {field} must be timezone-aware")
    return parsed


def _decimal(value: object, field: str, *, positive: bool) -> Decimal:
    text = _text(value, field)
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"product foundation {field} must be a decimal") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValidationError(f"product foundation {field} must be {qualifier}")
    return parsed


@dataclass(frozen=True, slots=True)
class FoundationCompany:
    external_key: str
    canonical_name: str
    effective_from: datetime


@dataclass(frozen=True, slots=True)
class FoundationSecurity:
    external_key: str
    company_key: str
    canonical_name: str
    symbol: str
    exchange: str
    currency: str
    share_class: str
    effective_from: datetime


@dataclass(frozen=True, slots=True)
class FoundationRights:
    security_key: str
    economic_units: Decimal
    votes_per_unit: Decimal
    effective_from: datetime
    source_id: str
    raw_hash: str


@dataclass(frozen=True, slots=True)
class ProductFoundationFixture:
    schema_version: str
    content_hash: str
    companies: tuple[FoundationCompany, ...]
    securities: tuple[FoundationSecurity, ...]
    rights: tuple[FoundationRights, ...]
    raw: Mapping[str, object]
    manifest_path: Path


def _sequence(value: object, field: str) -> list:
    if not isinstance(value, list) or not value:
        raise ValidationError(f"product foundation {field} must be a nonempty array")
    return value


def load_product_foundation_fixture(
    manifest_path: Path | None = None,
) -> ProductFoundationFixture:
    """Load the trusted bundled fixture or a checksum-validated custom import."""

    path = manifest_path or (_ROOT / "manifest.json")
    try:
        manifest_bytes = path.read_bytes()
        if (
            manifest_path is None
            and hashlib.sha256(manifest_bytes).hexdigest()
            != BUNDLED_MANIFEST_CONTENT_SHA256
        ):
            raise ValidationError(
                "product foundation bundled manifest trusted content digest mismatch"
            )
        raw_value = json.loads(
            manifest_bytes.decode("utf-8"), object_pairs_hook=_strict_json_object
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("product foundation manifest is unreadable") from exc
    raw = _exact_object(raw_value, _TOP_LEVEL_KEYS, "manifest")
    if raw["schema_version"] != _SCHEMA_VERSION:
        raise ValidationError("product foundation schema version is unsupported")
    content_hash = _text(raw["content_hash"], "content_hash")
    payload = {key: raw[key] for key in raw if key != "content_hash"}
    if content_hash != canonical_hash(payload):
        raise ValidationError("product foundation content hash mismatch")

    companies = tuple(
        FoundationCompany(
            external_key=_text(item["external_key"], "company.external_key"),
            canonical_name=_text(item["canonical_name"], "company.canonical_name"),
            effective_from=_timestamp(item["effective_from"], "company.effective_from"),
        )
        for raw_item in _sequence(raw["companies"], "companies")
        for item in (_exact_object(raw_item, _COMPANY_KEYS, "company"),)
    )
    securities = tuple(
        FoundationSecurity(
            external_key=_text(item["external_key"], "security.external_key"),
            company_key=_text(item["company_key"], "security.company_key"),
            canonical_name=_text(item["canonical_name"], "security.canonical_name"),
            symbol=_text(item["symbol"], "security.symbol"),
            exchange=_text(item["exchange"], "security.exchange"),
            currency=_text(item["currency"], "security.currency"),
            share_class=_text(item["share_class"], "security.share_class"),
            effective_from=_timestamp(
                item["effective_from"], "security.effective_from"
            ),
        )
        for raw_item in _sequence(raw["securities"], "securities")
        for item in (_exact_object(raw_item, _SECURITY_KEYS, "security"),)
    )
    company_keys = {item.external_key for item in companies}
    security_by_key = {item.external_key: item for item in securities}
    if len(company_keys) != len(companies) or len(security_by_key) != len(securities):
        raise ValidationError("product foundation external keys must be unique")
    if any(item.company_key not in company_keys for item in securities):
        raise ValidationError("product foundation Security references unknown Company")
    if any(item.currency not in {"CNY", "USD"} for item in securities):
        raise ValidationError("product foundation Security currency is unsupported")

    rights: list[FoundationRights] = []
    for raw_item in _sequence(raw["rights"], "rights"):
        item = _exact_object(raw_item, _RIGHTS_KEYS, "rights")
        security_key = _text(item["security_key"], "rights.security_key")
        security = security_by_key.get(security_key)
        if security is None:
            raise ValidationError(
                "product foundation rights reference unknown Security"
            )
        date_text = _text(item["effective_from"], "rights.effective_from")
        try:
            effective_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValidationError(
                "product foundation rights.effective_from must be YYYY-MM-DD"
            ) from exc
        if effective_date.isoformat() != date_text:
            raise ValidationError(
                "product foundation rights.effective_from must be YYYY-MM-DD"
            )
        effective_from = datetime.combine(
            effective_date, time.min, tzinfo=security.effective_from.tzinfo
        ).astimezone(UTC)
        if effective_from < security.effective_from.astimezone(UTC):
            raise ValidationError(
                "product foundation rights cannot predate Security identity"
            )
        raw_hash = canonical_hash(item)
        rights.append(
            FoundationRights(
                security_key=security_key,
                economic_units=_decimal(
                    item["economic_units"], "rights.economic_units", positive=True
                ),
                votes_per_unit=_decimal(
                    item["votes_per_unit"], "rights.votes_per_unit", positive=False
                ),
                effective_from=effective_from,
                source_id=f"fixture:product-foundation:{security_key}",
                raw_hash=raw_hash,
            )
        )
    if len(rights) != len(securities) or {item.security_key for item in rights} != set(
        security_by_key
    ):
        raise ValidationError(
            "product foundation requires exactly one rights row per Security"
        )

    return ProductFoundationFixture(
        schema_version=_SCHEMA_VERSION,
        content_hash=content_hash,
        companies=companies,
        securities=securities,
        rights=tuple(rights),
        raw=MappingProxyType(raw),
        manifest_path=path,
    )


def validate_product_foundation_fixture(value: ProductFoundationFixture) -> None:
    """Reject values detached from their trusted or checksum-validated manifest."""
    if type(value) is not ProductFoundationFixture:
        raise ValidationError("fixture must be a ProductFoundationFixture")
    bundled_path = (_ROOT / "manifest.json").resolve()
    validated_import = load_product_foundation_fixture(
        None if value.manifest_path.resolve() == bundled_path else value.manifest_path
    )
    if (
        value.schema_version,
        value.content_hash,
        value.companies,
        value.securities,
        value.rights,
    ) != (
        validated_import.schema_version,
        validated_import.content_hash,
        validated_import.companies,
        validated_import.securities,
        validated_import.rights,
    ):
        raise ValidationError(
            "product foundation content hash no longer matches its "
            "checksum-validated manifest"
        )
