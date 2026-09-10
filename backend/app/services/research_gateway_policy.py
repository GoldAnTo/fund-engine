"""The fixed, server-owned execution capabilities available to Gateway P0."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

ROLE_MANIFEST_VERSION = "fundclaw-roles.v1"
CAPABILITY_MANIFEST_VERSION = "fundclaw-capabilities.v1"
UNSUPPORTED_FOLLOWUP_MESSAGE = "This request is not supported in Gateway P0."


@dataclass(frozen=True, slots=True)
class RoleCapabilityManifest:
    role_key: str
    capabilities: tuple[str, ...]
    outputs: tuple[str, ...]


_MANIFEST = MappingProxyType(
    {
        role.role_key: role
        for role in (
            RoleCapabilityManifest(
                "scope_identity", ("resolve_scope",), ("identity_decision", "blocked")
            ),
            RoleCapabilityManifest(
                "sources_evidence",
                ("request_acquisition", "read_frozen_evidence"),
                ("artifact_ref", "rejected_source"),
            ),
            RoleCapabilityManifest(
                "analysis_counter_evidence",
                ("propose_candidate_claim", "propose_counter_evidence_task"),
                ("candidate", "gap"),
            ),
            RoleCapabilityManifest(
                "compilation_checks",
                ("compile_provisional_draft", "validate_lineage"),
                ("draft_ref", "validation"),
            ),
        )
    }
)


def require_role(role_key: str) -> RoleCapabilityManifest:
    try:
        return _MANIFEST[role_key]
    except (KeyError, TypeError) as exc:
        raise ValueError("unsupported Gateway role") from exc


def supported_command(command: str) -> bool:
    """No native command has a transaction-safe Gateway adapter in P0 yet."""
    return False


def frozen_manifest() -> Mapping[str, RoleCapabilityManifest]:
    return _MANIFEST
