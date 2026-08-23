"""Canonical, persisted seals for special research-version parent sets."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.models.ledger import ValidationError
from app.underwriting.services.kernel import canonical_hash


CATL_VERSION_KIND = "catl_economic_model_evidence_only"
CATL_PARENT_SET_FAMILY = "research_revision:catl_economic_model_evidence_only:parent_set"
CATL_PARENT_SET_ENTRY_TYPE = "research_revision_parent_set_seal"
PARENT_SET_SCHEMA = "underwriting.revision-parent-set-seal.v1"


def answerability_content_hash(
    *,
    object_id: object,
    basis_id: object,
    version: int,
    state: str,
    blockers: Iterable[str],
    research_debt_keys: Iterable[str],
    resolvable_within_mandate: bool,
    allowed_action: str,
    resolution_requirements: Iterable[str],
) -> str:
    # Object and basis are independently scope-checked by the revision reader.
    # Leaving their generated UUIDs out retains deterministic cross-database
    # fixture identities while retaining every answerability semantic field.
    del object_id, basis_id
    return canonical_hash({
        "version": version, "state": state, "blockers": list(blockers),
        "research_debt_keys": list(research_debt_keys),
        "resolvable_within_mandate": resolvable_within_mandate,
        "allowed_action": allowed_action,
        "resolution_requirements": list(resolution_requirements),
    })


def canonical_parent_refs(refs: Iterable[Mapping[str, object]]) -> tuple[dict[str, str], ...]:
    values: list[dict[str, str]] = []
    for ref in refs:
        reference = ref.get("reference")
        artifact_type = ref.get("artifact_type")
        content_hash = ref.get("content_hash")
        if not all(isinstance(value, str) and value for value in (reference, artifact_type, content_hash)):
            raise ValidationError("revision parent seal reference is malformed")
        values.append({
            "reference": reference,
            "artifact_type": artifact_type,
            "content_hash": content_hash,
        })
    values.sort(key=lambda item: (item["artifact_type"], item["reference"], item["content_hash"]))
    if len({(item["reference"], item["artifact_type"]) for item in values}) != len(values):
        raise ValidationError("revision parent seal references are duplicated")
    return tuple(values)


def catl_parent_set_seal_payload(
    *, semantic_snapshot_token: str, refs: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": PARENT_SET_SCHEMA,
        "version_kind": CATL_VERSION_KIND,
        "semantic_snapshot_token": semantic_snapshot_token,
        "parent_refs": list(canonical_parent_refs(refs)),
    }


def parse_catl_parent_set_seal(payload: object) -> tuple[str, tuple[dict[str, str], ...]]:
    if not isinstance(payload, Mapping):
        raise ValidationError("CATL revision parent seal payload is malformed")
    if payload.get("schema_version") != PARENT_SET_SCHEMA or payload.get("version_kind") != CATL_VERSION_KIND:
        raise ValidationError("CATL revision parent seal schema is invalid")
    token = payload.get("semantic_snapshot_token")
    refs = payload.get("parent_refs")
    if not isinstance(token, str) or not isinstance(refs, list):
        raise ValidationError("CATL revision parent seal payload is malformed")
    normalized = canonical_parent_refs(refs)
    if list(normalized) != refs:
        raise ValidationError("CATL revision parent seal references are not canonical")
    return token, normalized


def parent_set_semantic_hash(refs: Iterable[Mapping[str, object]]) -> str:
    """Stable set identity independent of database-generated UUIDs."""
    normalized = canonical_parent_refs(refs)
    semantic = sorted(
        (
            {"artifact_type": ref["artifact_type"], "content_hash": ref["content_hash"]}
            for ref in normalized
        ),
        key=lambda value: (value["artifact_type"], value["content_hash"]),
    )
    return canonical_hash(tuple(semantic))


def catl_revision_content_hash(*, semantic_snapshot_token: str, parent_set_semantic_hash: str) -> str:
    return canonical_hash({
        "semantic_snapshot_token": semantic_snapshot_token,
        "version_kind": CATL_VERSION_KIND,
        "parent_set_semantic_hash": parent_set_semantic_hash,
    })
