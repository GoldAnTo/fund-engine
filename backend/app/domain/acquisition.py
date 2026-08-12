"""Frozen public vocabulary for governed evidence acquisition."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Final
from uuid import UUID


B_SCOPE_POLICY_VERSION: Final = "b-scope-v1"
ACQUISITION_SOURCE_ROLES: Final = frozenset(
    {"company_disclosure", "licensed_provider"}
)


class EvidenceObjective(str, Enum):
    SUPPORT = "support"
    CONTRADICT = "contradict"
    ALTERNATIVE_EXPLANATION = "alternative_explanation"
    VERIFY_RULE = "verify_rule"


_OBJECTIVE_LINK_ROLES: Final = {
    EvidenceObjective.SUPPORT: frozenset({"supports"}),
    EvidenceObjective.CONTRADICT: frozenset({"contradicts"}),
    EvidenceObjective.ALTERNATIVE_EXPLANATION: frozenset({"contextualizes"}),
    EvidenceObjective.VERIFY_RULE: frozenset({"supports", "contradicts"}),
}


def _canonical_string(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _canonical_string_tuple(
    values: Iterable[str], field_name: str
) -> tuple[str, ...]:
    return tuple(
        _canonical_string(value, f"{field_name} element") for value in values
    )


def _canonical_iso_date(value: str, field_name: str) -> tuple[str, date]:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc
    return parsed.isoformat(), parsed


@dataclass(frozen=True, slots=True)
class AcquisitionPrincipal:
    tenant_id: str
    actor: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "tenant_id", _canonical_string(self.tenant_id, "tenant_id")
        )
        object.__setattr__(self, "actor", _canonical_string(self.actor, "actor"))


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    tenant_id: str
    case_id: UUID
    thesis_id: UUID
    research_run_id: UUID | None
    round: int
    objective: EvidenceObjective
    target_link_role: str
    thesis_statement: str
    entity_names: tuple[str, ...]
    security_codes: tuple[str, ...]
    metric_terms: tuple[str, ...]
    period_start: str
    period_end: str
    cutoff: datetime
    allowed_source_roles: frozenset[str]
    source_policy_version: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for field_name in (
            "tenant_id",
            "idempotency_key",
            "target_link_role",
            "thesis_statement",
            "period_start",
            "period_end",
            "source_policy_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _canonical_string(getattr(self, field_name), field_name),
            )

        for field_name in ("entity_names", "security_codes", "metric_terms"):
            object.__setattr__(
                self,
                field_name,
                _canonical_string_tuple(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "allowed_source_roles",
            frozenset(
                _canonical_string_tuple(
                    self.allowed_source_roles, "allowed_source_roles"
                )
            ),
        )

        period_start, parsed_start = _canonical_iso_date(
            self.period_start, "period_start"
        )
        period_end, parsed_end = _canonical_iso_date(self.period_end, "period_end")
        if parsed_start > parsed_end:
            raise ValueError("period_start must not be after period_end")
        object.__setattr__(self, "period_start", period_start)
        object.__setattr__(self, "period_end", period_end)

        if self.cutoff.tzinfo is None or self.cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        object.__setattr__(self, "cutoff", self.cutoff.astimezone(UTC))

        if not (self.entity_names or self.security_codes or self.metric_terms):
            raise ValueError("objective scope must not be empty")
        if not self.allowed_source_roles:
            raise ValueError("allowed_source_roles must not be empty")
        unknown_roles = self.allowed_source_roles - ACQUISITION_SOURCE_ROLES
        if unknown_roles:
            raise ValueError(f"unknown source roles: {sorted(unknown_roles)!r}")
        if self.source_policy_version != B_SCOPE_POLICY_VERSION:
            raise ValueError(
                "source_policy_version does not match the active B-scope policy"
            )

        allowed_link_roles = _OBJECTIVE_LINK_ROLES.get(self.objective)
        if allowed_link_roles is None or self.target_link_role not in allowed_link_roles:
            raise ValueError(
                "target_link_role is incompatible with the evidence objective"
            )


@dataclass(frozen=True, slots=True)
class PlannedQuery:
    adapter_key: str
    objective: EvidenceObjective
    query: str


@dataclass(frozen=True, slots=True)
class AcquisitionJobRef:
    id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class AcquisitionJobView:
    id: UUID
    status: str
    stage: str
    attempt: int


@dataclass(frozen=True, slots=True)
class AdmittedEvidenceRef:
    evidence_link_id: UUID
    source_statement_id: UUID
    document_version_id: UUID
