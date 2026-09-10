"""Frozen public vocabulary for governed evidence acquisition."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Final, Literal
from uuid import UUID


B_SCOPE_POLICY_VERSION: Final = "b-scope-v2"
ACQUISITION_PLANNER_VERSION: Final = "goal-query-v1"
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
class QueryPlanExpansion:
    trigger: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger", _canonical_string(self.trigger, "trigger"))
        object.__setattr__(self, "reason", _canonical_string(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    tenant_id: str
    case_id: UUID
    thesis_id: UUID
    research_run_id: UUID
    scope_version_id: UUID
    goal_id: str
    round: int
    objective: EvidenceObjective
    target_link_role: str
    thesis_statement: str
    entity_names: tuple[str, ...]
    security_codes: tuple[str, ...]
    metric_terms: tuple[str, ...]
    metric_periods: tuple[str, ...]
    metric_units: tuple[str, ...]
    period_start: str
    period_end: str
    cutoff: datetime
    allowed_source_roles: frozenset[str]
    source_policy_version: str
    planner_version: str
    previous_query_plan_id: UUID | None
    expansion: QueryPlanExpansion | None
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
            "planner_version",
            "goal_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _canonical_string(getattr(self, field_name), field_name),
            )

        for field_name in (
            "entity_names",
            "security_codes",
            "metric_terms",
            "metric_periods",
            "metric_units",
        ):
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
        if self.planner_version != ACQUISITION_PLANNER_VERSION:
            raise ValueError("planner_version does not match the active query planner")
        for field_name in (
            "case_id",
            "thesis_id",
            "research_run_id",
            "scope_version_id",
        ):
            if not isinstance(getattr(self, field_name), UUID):
                raise ValueError(f"{field_name} must be a UUID")
        if not isinstance(self.round, int) or isinstance(self.round, bool) or self.round < 1:
            raise ValueError("round must be a positive integer")
        if self.round == 1:
            if self.previous_query_plan_id is not None or self.expansion is not None:
                raise ValueError("round 1 cannot declare query-plan expansion")
        elif self.previous_query_plan_id is None or self.expansion is None:
            raise ValueError(
                "round greater than 1 requires previous_query_plan_id and expansion"
            )
        expected_idempotency_key = (
            f"run:{self.research_run_id}:scope:{self.scope_version_id}:"
            f"goal:{self.goal_id}:round:{self.round}"
        )
        if self.idempotency_key != expected_idempotency_key:
            raise ValueError("idempotency_key does not match canonical acquisition identity")

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
    tenant_id: str | None = None
    research_case_id: UUID | None = None
    thesis_id: UUID | None = None
    research_run_id: UUID | None = None
    scope_version_id: UUID | None = None
    goal_id: str | None = None
    acquisition_round: int | None = None


@dataclass(frozen=True, slots=True)
class AdmittedEvidenceRef:
    evidence_link_id: UUID
    source_statement_id: UUID
    document_version_id: UUID


@dataclass(frozen=True, slots=True)
class AcquisitionEvidenceView:
    evidence_link_id: UUID
    automatic_admission_decision_id: UUID
    source_statement_id: UUID
    document_version_id: UUID
    job_id: UUID | None
    goal_id: str
    review_state: str
    role: str
    authority: str
    source_identity: str
    publication_key: str
    canonical_url: str
    content_sha256: str
    available_at: datetime
    subject: str | None
    security_code: str | None
    metric: str | None
    observed_period: str | None
    unit: str | None
    policy_version: str


WORKFLOW_LEDGER_STATUSES = (
    "search_candidate",
    "fetching",
    "frozen",
    "deduplicated",
    "conflicted",
    "admitted",
    "quarantined",
    "skipped",
)
WorkflowLedgerStatus = Literal[*WORKFLOW_LEDGER_STATUSES]
AcquisitionWorkflowLedgerKey = tuple[datetime, str, str]


@dataclass(frozen=True, slots=True)
class AcquisitionWorkflowLedgerRecord:
    """One durable acquisition fact exposed through the public read seam."""

    record_id: UUID
    record_type: str
    status: WorkflowLedgerStatus
    reason: str
    reason_code: str
    job_id: UUID | None
    recorded_at: datetime
    adapter_key: str | None = None
    attempt_id: UUID | None = None
    source_url: str | None = None
    final_url: str | None = None
    retrieved_at: datetime | None = None
    content_sha256: str | None = None
    publication_key: str | None = None
    dedup_relation: str | None = None
    admission_outcome: str | None = None
    review_state: str | None = None
    evidence_link_id: UUID | None = None
    role: str | None = None
    source_role: str | None = None
    mapping_disposition: str | None = None
    mapping_kind: str | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionWorkflowLedgerCounts:
    """Database-aggregated counts for one frozen-scope ledger snapshot."""

    total: int
    reviewed: int
    automatically_admitted: int
    by_status: dict[WorkflowLedgerStatus, int]


@dataclass(frozen=True, slots=True)
class AcquisitionWorkflowLedgerPage:
    """A bounded keyset page plus counts for its immutable high-watermark."""

    records: tuple[AcquisitionWorkflowLedgerRecord, ...]
    counts: AcquisitionWorkflowLedgerCounts
    high_watermark: AcquisitionWorkflowLedgerKey | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class AcquisitionQueryPlanView:
    id: UUID
    job_id: UUID
    series_id: UUID
    goal_id: str
    acquisition_round: int
    frozen_inputs: dict[str, object]
    ordered_queries: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class AcquisitionSearchOperationView:
    job_id: UUID
    query_index: int
    adapter_key: str
    query: str
    outcome: Literal["succeeded", "failed", "missing"]
    attempt_count: int
    error_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcquisitionCoverageSnapshot:
    job: AcquisitionJobView
    plan: AcquisitionQueryPlanView
    search_operations: tuple[AcquisitionSearchOperationView, ...]
    evidence: tuple[AcquisitionEvidenceView, ...]
