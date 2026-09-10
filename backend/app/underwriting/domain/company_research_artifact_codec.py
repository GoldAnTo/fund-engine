"""Canonical JSON codec seam for typed company-research artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    CompanyResearchMemoArtifact,
    CompanyResearchValidationError,
    DriverMapArtifact,
    FinancialBridgeArtifact,
    JudgmentContextArtifact,
    ResearchGap,
    ScenarioSetArtifact,
    ValuationSetArtifact,
    canonical_decimal_string,
)
from app.underwriting.domain.company_research_critical_inputs import CriticalInputSet

_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "business_map": TypeAdapter(BusinessMapArtifact),
    "driver_map": TypeAdapter(DriverMapArtifact),
    "financial_bridge": TypeAdapter(FinancialBridgeArtifact),
    "scenario_set": TypeAdapter(ScenarioSetArtifact),
    "valuation_set": TypeAdapter(ValuationSetArtifact),
    "judgment_context": TypeAdapter(JudgmentContextArtifact),
    "research_gaps": TypeAdapter(tuple[ResearchGap, ...]),
    "memo": TypeAdapter(CompanyResearchMemoArtifact),
    "critical_inputs": TypeAdapter(CriticalInputSet),
}


def _canonical_json(value: object) -> object:
    if isinstance(value, Decimal):
        return canonical_decimal_string(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_json(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_json(item) for item in value]
    if value is None or type(value) in {bool, int, str}:
        return value
    raise TypeError("company research artifact contains a non-JSON domain value")


def _canonical_memo_payload(payload: dict[str, object]) -> dict[str, object]:
    """Keep legacy machine-draft payloads byte-for-byte unchanged."""
    if payload.get("candidate_status") == "machine_draft":
        payload.pop("reviewer", None)
        payload.pop("markdown", None)
    if payload.get("narrative") is None:
        payload.pop("narrative", None)
    elif isinstance(payload["narrative"], dict):
        narrative = payload["narrative"]
        for key in (
            "provider",
            "model",
            "prompt_hash",
            "provider_model_identifier",
        ):
            if narrative.get(key) is None:
                narrative.pop(key, None)
    return payload


def _critical_input_scalar_type(value: Decimal | str | None) -> str:
    if type(value) is Decimal:
        return "decimal"
    if type(value) is str:
        return "text"
    if value is None:
        return "none"
    raise TypeError("critical input scalar type is invalid")


def _tag_critical_input_scalar(
    payload: dict[str, object], value: Decimal | str | None
) -> None:
    payload["value_type"] = _critical_input_scalar_type(value)


def _tag_critical_input_payload(
    payload: dict[str, object], artifact: CriticalInputSet
) -> dict[str, object]:
    values = payload.get("inputs")
    if not isinstance(values, list) or len(values) != len(artifact.inputs):
        raise TypeError("critical input payload shape is invalid")
    for item, typed in zip(values, artifact.inputs, strict=True):
        if not isinstance(item, dict):
            raise TypeError("critical input payload shape is invalid")
        _tag_critical_input_scalar(item, typed.value)
        replacement = item.get("replacement")
        if typed.replacement is None:
            if replacement is not None:
                raise TypeError("critical input replacement shape is invalid")
            continue
        if not isinstance(replacement, dict):
            raise TypeError("critical input replacement shape is invalid")
        _tag_critical_input_scalar(replacement, typed.replacement.value)
    return payload


def _decode_critical_input_scalar(payload: dict[str, object]) -> None:
    value_type = payload.pop("value_type", None)
    value = payload.get("value")
    if value_type == "decimal":
        if type(value) is not str:
            raise TypeError("critical input decimal scalar is invalid")
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise TypeError("critical input decimal scalar is invalid") from exc
        if not decimal.is_finite() or canonical_decimal_string(decimal) != value:
            raise TypeError("critical input decimal scalar is not canonical")
        payload["value"] = decimal
        return
    if value_type == "text":
        if type(value) is not str:
            raise TypeError("critical input text scalar is invalid")
        return
    if value_type == "none":
        if value is not None:
            raise TypeError("critical input none scalar is invalid")
        return
    raise TypeError("critical input scalar type is invalid")


def _typed_critical_input_payload(payload: dict[str, object]) -> dict[str, object]:
    """Decode only explicitly tagged critical-input scalar values."""
    candidate = deepcopy(payload)
    values = candidate.get("inputs")
    if not isinstance(values, list):
        return candidate
    for value in values:
        if not isinstance(value, dict):
            continue
        for item in (value, value.get("replacement")):
            if not isinstance(item, dict):
                continue
            _decode_critical_input_scalar(item)
    return candidate


def _upgrade_legacy_valuation_payload(
    payload: dict[str, object],
) -> dict[str, object]:
    """Read v1 Alphabet value keys while all new writes use currency-neutral v2."""
    candidate = deepcopy(payload)
    values = candidate.get("security_value_ranges")
    if not isinstance(values, list):
        return candidate
    for item in values:
        if not isinstance(item, dict) or "usd_per_share" not in item:
            continue
        if "value_per_share" in item or "base_currency_return" in item:
            raise TypeError("valuation payload mixes legacy and current value fields")
        item["value_per_share"] = item.pop("usd_per_share")
        item["value_currency"] = "USD"
        item["base_currency_return"] = item.pop("cny_return", None)
        item["schema_version"] = "company-research.security-value.v2"
    return candidate


class CompanyResearchArtifactCodec:
    """Rebuild domain objects and require their single canonical JSON encoding."""

    @staticmethod
    def _adapter(kind: str) -> TypeAdapter[Any]:
        adapter = _ADAPTERS.get(kind)
        if adapter is None:
            raise ValidationError("company research artifact kind is invalid")
        return adapter

    @classmethod
    def encode(cls, kind: str, artifact: object) -> dict[str, object]:
        adapter = cls._adapter(kind)
        try:
            decoded = adapter.validate_python(artifact)
            if kind == "research_gaps":
                values = _canonical_json(decoded)
                payload: object = {"gaps": values}
            else:
                payload = _canonical_json(decoded)
            if not isinstance(payload, dict):
                raise TypeError
            if kind == "critical_inputs":
                if type(decoded) is not CriticalInputSet:
                    raise TypeError
                payload = _tag_critical_input_payload(payload, decoded)
            if kind == "memo":
                payload = _canonical_memo_payload(payload)
            return cls.validate_payload(kind, payload)
        except (
            AttributeError,
            CompanyResearchValidationError,
            PydanticValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValidationError(f"{kind} payload is invalid") from exc

    @classmethod
    def decode_for_read(cls, kind: str, payload: Mapping[str, object]) -> object:
        adapter = cls._adapter(kind)
        if not isinstance(payload, Mapping):
            raise ValidationError(f"{kind} payload is invalid")
        candidate = dict(payload)
        try:
            if kind == "critical_inputs":
                candidate = _typed_critical_input_payload(candidate)
            if kind == "valuation_set":
                candidate = _upgrade_legacy_valuation_payload(candidate)
            if kind == "research_gaps":
                if set(candidate) != {"gaps"} or not isinstance(
                    candidate["gaps"], list
                ):
                    raise TypeError
                return adapter.validate_python(candidate["gaps"])
            return adapter.validate_python(candidate)
        except (
            CompanyResearchValidationError,
            PydanticValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValidationError(f"{kind} payload is invalid") from exc

    @classmethod
    def decode(cls, kind: str, payload: Mapping[str, object]) -> object:
        """Compatibility name for the authenticated read decoder."""
        return cls.decode_for_read(kind, payload)

    @classmethod
    def validate_for_write(
        cls, kind: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        candidate = dict(payload) if isinstance(payload, Mapping) else payload
        decoded = cls.decode_for_read(kind, candidate)
        if kind == "research_gaps":
            canonical: object = {"gaps": _canonical_json(decoded)}
        else:
            canonical = _canonical_json(decoded)
        if kind == "critical_inputs":
            if not isinstance(canonical, dict) or type(decoded) is not CriticalInputSet:
                raise ValidationError(f"{kind} payload is invalid")
            canonical = _tag_critical_input_payload(canonical, decoded)
        if (
            kind == "memo"
            and isinstance(candidate, dict)
            and isinstance(canonical, dict)
        ):
            _canonical_memo_payload(canonical)
            if "research_gaps" not in candidate:
                canonical.pop("research_gaps", None)
        if not isinstance(canonical, dict) or canonical != candidate:
            raise ValidationError(f"{kind} payload is invalid")
        return canonical

    @classmethod
    def validate_payload(
        cls, kind: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        """Compatibility name for strict canonical v2 write validation."""
        return cls.validate_for_write(kind, payload)

    @classmethod
    def normalize_payload_for_read(
        cls, kind: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        """Return a validated neutral projection without mutating stored bytes."""
        if kind != "valuation_set" or not isinstance(payload, Mapping):
            return cls.validate_for_write(kind, payload)
        candidate = _upgrade_legacy_valuation_payload(dict(payload))
        candidate.setdefault("sensitivity_analyses", [])
        return cls.validate_for_write(kind, candidate)


MODEL_ARTIFACT_KINDS = frozenset(_ADAPTERS)
