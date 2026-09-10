"""Canonical external-source records for company-research artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import SourceLineageReference

SOURCE_REF_FIELDS = (
    "source_role",
    "source_url",
    "source_locator",
    "raw_hash",
)
_SOURCE_REF_FIELD_SET = frozenset(SOURCE_REF_FIELDS)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def canonical_source_refs(
    values: object,
    *,
    field_name: str = "source_refs",
) -> tuple[dict[str, str], ...]:
    """Validate, deduplicate, and deterministically order source records."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValidationError(f"{field_name} must be an array")
    records: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for value in values:
        if not isinstance(value, Mapping) or set(value) != _SOURCE_REF_FIELD_SET:
            raise ValidationError(f"{field_name} contain invalid fields")
        record: dict[str, str] = {}
        for key in SOURCE_REF_FIELDS:
            item = value.get(key)
            if not isinstance(item, str) or not item.strip() or item != item.strip():
                raise ValidationError(f"{field_name}.{key} is invalid")
            record[key] = item
        if _SHA256.fullmatch(record["raw_hash"]) is None:
            raise ValidationError(f"{field_name}.raw_hash is invalid")
        identity = tuple(record[key] for key in SOURCE_REF_FIELDS)
        records.setdefault(identity, record)
    return tuple(records[key] for key in sorted(records))


def source_record(reference: SourceLineageReference) -> dict[str, str]:
    """Project fact-level lineage onto its external source-record identity."""
    if type(reference) is not SourceLineageReference:
        raise ValidationError("source lineage reference is invalid")
    return {
        "source_role": reference.source_role,
        "source_url": reference.source_url,
        "source_locator": reference.source_locator,
        "raw_hash": reference.raw_hash,
    }


def evidence_payload_source_refs(
    payload: object,
) -> tuple[dict[str, str], ...]:
    """Derive source records from the exact governed facts in an evidence index."""
    facts = payload.get("facts") if isinstance(payload, Mapping) else None
    if not isinstance(facts, list) or not facts:
        raise ValidationError("evidence index source refs are invalid")
    refs: list[dict[str, object]] = []
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise ValidationError("evidence index source refs are invalid")
        refs.append({key: fact.get(key) for key in SOURCE_REF_FIELDS})
    return canonical_source_refs(refs, field_name="evidence index source refs")


def critical_inputs_payload_source_refs(
    payload: object,
) -> tuple[dict[str, str], ...]:
    """Derive artifact-level source identities from typed critical inputs."""

    inputs = payload.get("inputs") if isinstance(payload, Mapping) else None
    if not isinstance(inputs, list):
        raise ValidationError("critical inputs source refs are invalid")
    refs: list[dict[str, object]] = []
    for value in inputs:
        if not isinstance(value, Mapping):
            raise ValidationError("critical inputs source refs are invalid")
        reference = value.get("source_ref")
        if reference is None:
            continue
        if not isinstance(reference, Mapping) or set(reference) != {
            "fact_key",
            *SOURCE_REF_FIELDS,
        }:
            raise ValidationError("critical inputs source refs are invalid")
        fact_key = reference.get("fact_key")
        if (
            not isinstance(fact_key, str)
            or not fact_key
            or fact_key != fact_key.strip()
        ):
            raise ValidationError("critical inputs source refs are invalid")
        refs.append({key: reference.get(key) for key in SOURCE_REF_FIELDS})
    return canonical_source_refs(refs, field_name="critical inputs source refs")
