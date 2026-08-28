"""Canonical JSON codec seam for typed company-research artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError as PydanticValidationError

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


_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "business_map": TypeAdapter(BusinessMapArtifact),
    "driver_map": TypeAdapter(DriverMapArtifact),
    "financial_bridge": TypeAdapter(FinancialBridgeArtifact),
    "scenario_set": TypeAdapter(ScenarioSetArtifact),
    "valuation_set": TypeAdapter(ValuationSetArtifact),
    "judgment_context": TypeAdapter(JudgmentContextArtifact),
    "research_gaps": TypeAdapter(tuple[ResearchGap, ...]),
    "memo": TypeAdapter(CompanyResearchMemoArtifact),
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
    def decode(cls, kind: str, payload: Mapping[str, object]) -> object:
        adapter = cls._adapter(kind)
        if not isinstance(payload, Mapping):
            raise ValidationError(f"{kind} payload is invalid")
        candidate = dict(payload)
        try:
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
    def validate_payload(
        cls, kind: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        candidate = dict(payload) if isinstance(payload, Mapping) else payload
        decoded = cls.decode(kind, candidate)
        if kind == "research_gaps":
            canonical: object = {"gaps": _canonical_json(decoded)}
        else:
            canonical = _canonical_json(decoded)
        if not isinstance(canonical, dict) or canonical != candidate:
            raise ValidationError(f"{kind} payload is invalid")
        return canonical


MODEL_ARTIFACT_KINDS = frozenset(_ADAPTERS)
