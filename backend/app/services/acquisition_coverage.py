"""Versioned, goal-bound evidence coverage for event research.

Coverage deliberately consumes frozen provenance facts rather than document
counts or model confidence.  Database orchestration is added below the pure
policy types so the decision rules remain independently testable.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.acquisition import AcquisitionPrincipal
from app.domain.coverage_decision import (
    BOUNDARY_DECISION_ALTERNATIVES,
    BOUNDARY_DECISION_REASON,
    BOUNDARY_DECISION_RECOMMENDATION,
    BOUNDARY_REASON_CODES,
)
from app.errors import ValidationFailedError
from app.models.ledger import Thesis
from app.models.operational import ResearchRun
from app.models.research_orchestration import (
    AcquisitionGoalCoverage,
    ResearchOrchestration,
)
from app.repositories.research_orchestration import OrchestrationTransitionCommand
from app.services.acquisition import AcquisitionModule
from app.services.event_research_scope_evidence import (
    append_automatic_scope_evidence_assignment,
)


CoverageDecision = Literal["ready", "continue", "needs_decision", "exhausted"]


def _normalize(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(
        r"[^0-9a-z\u3400-\u9fff]+",
        " ",
        unicodedata.normalize("NFKC", value).casefold(),
    ).strip()


def _period_range(value: str | None) -> tuple[date, date] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().upper()
    if match := re.fullmatch(r"(\d{4})-Q([1-4])", raw):
        year = int(match.group(1))
        start_month = (int(match.group(2)) - 1) * 3 + 1
        end_month = start_month + 2
        return (
            date(year, start_month, 1),
            date(year, end_month, monthrange(year, end_month)[1]),
        )
    if match := re.fullmatch(r"(\d{4})-(\d{2})", raw):
        year = int(match.group(1))
        month = int(match.group(2))
        try:
            return date(year, month, 1), date(
                year, month, monthrange(year, month)[1]
            )
        except ValueError:
            return None
    if match := re.fullmatch(r"(\d{4})", raw):
        year = int(match.group(1))
        return date(year, 1, 1), date(year, 12, 31)
    if match := re.fullmatch(
        r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})", raw
    ):
        try:
            start = date.fromisoformat(match.group(1))
            end = date.fromisoformat(match.group(2))
        except ValueError:
            return None
        return (start, end) if start <= end else None
    try:
        observed = date.fromisoformat(raw)
    except ValueError:
        return None
    return observed, observed


def _period_matches(observed: str | None, expected: str) -> bool:
    if _normalize(observed) == _normalize(expected):
        return True
    match = re.fullmatch(
        r"([^:]+):(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})",
        expected.strip(),
    )
    if match is None:
        return False
    try:
        expected_start = date.fromisoformat(match.group(2))
        expected_end = date.fromisoformat(match.group(3))
    except ValueError:
        return False
    if expected_start > expected_end:
        return False
    observed_range = _period_range(observed)
    if observed_range is None:
        return False
    semantics = _normalize(match.group(1))
    if semantics == "period end":
        return observed_range[1] == expected_end
    if semantics == "period start":
        return observed_range[0] == expected_start
    if semantics in {"flow", "fiscal year"}:
        return observed_range == (expected_start, expected_end)
    return False


def _canonical_period_key(
    observed: str | None, expected_periods: tuple[str, ...]
) -> tuple[str, ...]:
    """Return the frozen semantic period represented by an observed value."""
    for expected in expected_periods:
        if not _period_matches(observed, expected):
            continue
        match = re.fullmatch(
            r"([^:]+):(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})",
            expected.strip(),
        )
        if match is not None:
            return (
                _normalize(match.group(1)),
                match.group(2),
                match.group(3),
            )
        return ("exact", _normalize(expected))
    return ("unmatched", _normalize(observed))


@dataclass(frozen=True, slots=True)
class CoverageSearchOperation:
    job_id: uuid.UUID
    query_index: int
    adapter_key: str
    query: str
    outcome: Literal["succeeded", "failed", "missing"]
    error_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageGoal:
    goal_id: str
    thesis_id: uuid.UUID
    objective: str
    job_id: uuid.UUID
    job_status: str
    cutoff: datetime
    entity_names: tuple[str, ...]
    security_codes: tuple[str, ...]
    metric_terms: tuple[str, ...]
    metric_periods: tuple[str, ...]
    metric_units: tuple[str, ...]
    period_start: str
    period_end: str
    search_operations: tuple[CoverageSearchOperation, ...]
    related_job_ids: tuple[uuid.UUID, ...] = ()
    related_job_statuses: tuple[str, ...] = ()
    require_explicit_metric_semantics: bool = True


@dataclass(frozen=True, slots=True)
class CoverageEvidence:
    evidence_link_id: uuid.UUID
    automatic_admission_decision_id: uuid.UUID
    source_statement_id: uuid.UUID
    document_version_id: uuid.UUID
    job_id: uuid.UUID
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

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GoalCoverageResult:
    goal_id: str
    thesis_id: uuid.UUID
    objective: str
    status: Literal["ready", "unmet", "exhausted"]
    policy_version: str
    required_authority_levels: tuple[str, ...]
    observed_authority_levels: tuple[str, ...]
    required_authority_count: int
    observed_authority_count: int
    required_independent_source_count: int
    observed_independent_source_count: int
    search_completed: bool
    contrary_search_completed: bool
    blocking_reasons: tuple[str, ...]
    qualifying_evidence_ids: tuple[uuid.UUID, ...]
    independent_source_identities: tuple[str, ...]
    conflict_details: tuple[dict[str, object], ...]
    unknown_details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageOutcome:
    kind: CoverageDecision
    unresolved_goal_ids: tuple[str, ...]
    unknown_goal_ids: tuple[str, ...]
    expansion_trigger: str | None = None
    expansion_reason: str | None = None
    decision_action: str | None = None
    boundary_changes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageRule:
    required_authority_count: int
    required_independent_source_count: int
    requires_successful_terminal_search: bool = True


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    version: str
    authority_levels: frozenset[str]
    rules: dict[str, CoverageRule]
    zero_new_independent_source_limit: int

    def evaluate_goal(
        self,
        goal: CoverageGoal,
        evidence: tuple[CoverageEvidence, ...],
    ) -> GoalCoverageResult:
        rule = self.rules.get(goal.objective)
        if rule is None:
            raise ValueError("unsupported acquisition coverage objective")
        allowed_job_ids = {goal.job_id, *goal.related_job_ids}
        successful_terminal = (
            all(
                status in {"succeeded", "partial"}
                for status in (*goal.related_job_statuses, goal.job_status)
            )
            and bool(goal.search_operations)
            and all(
                operation.job_id in allowed_job_ids
                and operation.outcome == "succeeded"
                for operation in goal.search_operations
            )
        )
        contrary_completed = (
            successful_terminal if goal.objective == "contradict" else False
        )
        reasons: set[str] = set()
        if rule.requires_successful_terminal_search and not successful_terminal:
            reasons.add("search_not_successfully_completed")

        matching: list[CoverageEvidence] = []
        rejected_reasons: set[str] = set()
        for item in evidence:
            if item.job_id not in allowed_job_ids or item.goal_id != goal.goal_id:
                continue
            if item.review_state != "automatically_admitted":
                rejected_reasons.add("untraceable_admission")
                continue
            item_reasons = self._semantic_reasons(goal, item)
            if item_reasons:
                rejected_reasons.update(item_reasons)
                continue
            matching.append(item)

        event_level_alternative = goal.objective == "alternative_explanation"
        periods = (
            set()
            if event_level_alternative
            else {
                _canonical_period_key(item.observed_period, goal.metric_periods)
                for item in matching
            }
        )
        units = (
            set()
            if event_level_alternative
            else {_normalize(item.unit) for item in matching}
        )
        semantic_conflict = False
        semantic_conflicts: list[dict[str, object]] = []
        if len(periods) > 1:
            reasons.add("conflicting_evidence_periods")
            semantic_conflicts.append(
                {"kind": "period_semantics", "variant_count": len(periods)}
            )
            semantic_conflict = True
        if len(units) > 1:
            reasons.add("conflicting_evidence_units")
            semantic_conflicts.append(
                {"kind": "unit_semantics", "variant_count": len(units)}
            )
            semantic_conflict = True
        if semantic_conflict:
            matching = []

        conflict_keys: set[str] = set()
        conflict_urls: set[str] = set()
        publication_hashes: dict[str, set[str]] = {}
        for item in matching:
            publication_hashes.setdefault(item.publication_key, set()).add(
                item.content_sha256
            )
        conflicts: list[dict[str, object]] = list(semantic_conflicts)
        for publication_key, hashes in sorted(publication_hashes.items()):
            if len(hashes) <= 1:
                continue
            conflict_keys.add(publication_key)
            conflicts.append(
                {
                    "publication_key": publication_key,
                    "variant_count": len(hashes),
                }
            )
        canonical_hashes: dict[str, set[str]] = {}
        for item in matching:
            if item.publication_key in conflict_keys:
                continue
            canonical_hashes.setdefault(item.canonical_url, set()).add(
                item.content_sha256
            )
        for canonical_url, hashes in sorted(canonical_hashes.items()):
            if len(hashes) <= 1:
                continue
            conflict_urls.add(canonical_url)
            conflicts.append(
                {
                    "canonical_url": canonical_url,
                    "variant_count": len(hashes),
                }
            )
        if conflicts:
            reasons.add("conflicting_document_variants")

        unique: dict[tuple[str, str, str], CoverageEvidence] = {}
        seen_publications: set[str] = set()
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        for item in sorted(matching, key=lambda value: str(value.evidence_link_id)):
            if (
                item.publication_key in conflict_keys
                or item.canonical_url in conflict_urls
            ):
                continue
            if (
                item.publication_key in seen_publications
                or item.canonical_url in seen_urls
                or item.content_sha256 in seen_hashes
            ):
                continue
            key = (item.publication_key, item.canonical_url, item.content_sha256)
            unique[key] = item
            seen_publications.add(item.publication_key)
            seen_urls.add(item.canonical_url)
            seen_hashes.add(item.content_sha256)

        qualifying = tuple(unique.values())
        authority_count = sum(
            item.authority in self.authority_levels for item in qualifying
        )
        observed_authority_levels = tuple(
            sorted({item.authority for item in qualifying if item.authority})
        )
        identities = tuple(sorted({item.source_identity for item in qualifying}))
        if authority_count < rule.required_authority_count:
            reasons.add("authority_requirement_unmet")
        if len(identities) < rule.required_independent_source_count:
            reasons.add("independent_source_requirement_unmet")
        if (
            authority_count < rule.required_authority_count
            or len(identities) < rule.required_independent_source_count
        ):
            reasons.update(rejected_reasons)

        status: Literal["ready", "unmet", "exhausted"] = (
            "ready" if not reasons else "unmet"
        )
        unknowns = (
            ()
            if status == "ready"
            else tuple(
                sorted(
                    {
                        "goal_fact_unresolved",
                        *(
                            reason
                            for reason in reasons
                            if reason
                            in {
                                "expected_period_unspecified",
                                "expected_unit_unspecified",
                                "unit_unknown",
                                "search_not_successfully_completed",
                            }
                        ),
                    }
                )
            )
        )
        return GoalCoverageResult(
            goal_id=goal.goal_id,
            thesis_id=goal.thesis_id,
            objective=goal.objective,
            status=status,
            policy_version=self.version,
            required_authority_levels=(
                tuple(sorted(self.authority_levels))
                if rule.required_authority_count > 0
                else ()
            ),
            observed_authority_levels=observed_authority_levels,
            required_authority_count=rule.required_authority_count,
            observed_authority_count=authority_count,
            required_independent_source_count=rule.required_independent_source_count,
            observed_independent_source_count=len(identities),
            search_completed=successful_terminal,
            contrary_search_completed=contrary_completed,
            blocking_reasons=tuple(sorted(reasons)),
            qualifying_evidence_ids=tuple(
                item.evidence_link_id for item in qualifying
            ),
            independent_source_identities=identities,
            conflict_details=tuple(conflicts),
            unknown_details=unknowns,
        )

    @staticmethod
    def _semantic_reasons(
        goal: CoverageGoal, item: CoverageEvidence
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        available_at = item.available_at
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            reasons.append("cutoff_violation")
        elif available_at.astimezone(UTC) > goal.cutoff.astimezone(UTC):
            reasons.append("cutoff_violation")
        entities = {_normalize(value) for value in goal.entity_names}
        if entities and _normalize(item.subject) not in entities:
            reasons.append("entity_mismatch")
        securities = {_normalize(value) for value in goal.security_codes}
        if securities and _normalize(item.security_code) not in securities:
            reasons.append("security_mismatch")
        if goal.objective != "alternative_explanation":
            metrics = {_normalize(value) for value in goal.metric_terms}
            if metrics and _normalize(item.metric) not in metrics:
                reasons.append("metric_mismatch")
            if not goal.metric_periods and goal.require_explicit_metric_semantics:
                reasons.append("expected_period_unspecified")
            elif goal.metric_periods and not any(
                _period_matches(item.observed_period, expected)
                for expected in goal.metric_periods
            ):
                reasons.append("period_mismatch")
            expected_units = {_normalize(value) for value in goal.metric_units}
            if not expected_units and goal.require_explicit_metric_semantics:
                reasons.append("expected_unit_unspecified")
            elif expected_units and not _normalize(item.unit):
                reasons.append("unit_unknown")
            elif expected_units and _normalize(item.unit) not in expected_units:
                reasons.append("unit_mismatch")
        expected_role = {
            "support": "supports",
            "contradict": "contradicts",
            "alternative_explanation": "contextualizes",
        }.get(goal.objective)
        if expected_role is None or item.role != expected_role:
            reasons.append("role_mismatch")
        for value, code in (
            (item.source_identity, "source_identity_unknown"),
            (item.publication_key, "publication_identity_unknown"),
            (item.canonical_url, "canonical_identity_unknown"),
            (item.content_sha256, "content_identity_unknown"),
        ):
            if not isinstance(value, str) or not value.strip():
                reasons.append(code)
        return tuple(reasons)


COVERAGE_POLICY: Final = CoveragePolicy(
    version="event-goal-coverage-v1",
    authority_levels=frozenset({"primary_disclosure"}),
    rules={
        "support": CoverageRule(1, 1),
        "contradict": CoverageRule(0, 0),
        "alternative_explanation": CoverageRule(0, 0),
    },
    zero_new_independent_source_limit=2,
)


def decide_coverage(
    results: tuple[GoalCoverageResult, ...],
    *,
    current_round: int,
    max_rounds: int,
    zero_new_rounds: int,
    allow_unknown_completion: bool = False,
) -> CoverageOutcome:
    unresolved = tuple(result.goal_id for result in results if result.status != "ready")
    if not unresolved:
        return CoverageOutcome("ready", (), ())
    if (
        allow_unknown_completion
        and all(result.search_completed for result in results)
        and any(result.qualifying_evidence_ids for result in results)
    ):
        return CoverageOutcome("ready", unresolved, unresolved)
    if (
        current_round >= max_rounds
        or zero_new_rounds >= COVERAGE_POLICY.zero_new_independent_source_limit
    ):
        return CoverageOutcome("exhausted", unresolved, unresolved)
    frozen_boundary_changes = tuple(
        code
        for code, reason_codes in BOUNDARY_REASON_CODES.items()
        if any(
            reason_codes.intersection(result.blocking_reasons)
            and result.search_completed
            and (
                code != "source_policy"
                or bool(result.observed_authority_levels)
            )
            for result in results
            if result.status != "ready"
        )
    )
    if frozen_boundary_changes:
        return CoverageOutcome(
            "needs_decision",
            unresolved,
            unresolved,
            decision_action="revise_frozen_research_boundary",
            boundary_changes=tuple(sorted(set(frozen_boundary_changes))),
        )
    return CoverageOutcome(
        "continue",
        unresolved,
        unresolved,
        expansion_trigger="coverage_unmet",
        expansion_reason=(
            "当前冻结范围内仍有目标未满足覆盖规则；仅为未解决目标扩展下一轮查询"
        ),
    )


def build_boundary_decision_context(
    results: tuple[GoalCoverageResult, ...],
    *,
    outcome: CoverageOutcome,
    attempted_rounds: int,
) -> dict[str, object]:
    """Build a decision payload only from typed coverage results and outcome."""
    if (
        outcome.kind != "needs_decision"
        or outcome.decision_action != "revise_frozen_research_boundary"
        or not outcome.boundary_changes
    ):
        raise ValidationFailedError("typed boundary decision outcome is invalid")
    unresolved = [
        result for result in results if result.goal_id in outcome.unresolved_goal_ids
    ]
    selected_reasons = set().union(
        *(BOUNDARY_REASON_CODES[code] for code in outcome.boundary_changes)
    )
    affected = [
        {
            "goal_id": result.goal_id,
            "reason_codes": [
                reason
                for reason in result.blocking_reasons
                if reason in selected_reasons
            ],
        }
        for result in unresolved
        if result.search_completed
        and selected_reasons.intersection(result.blocking_reasons)
    ]
    return {
        "action": outcome.decision_action,
        "attempted_rounds": attempted_rounds,
        "boundary_codes": list(outcome.boundary_changes),
        "affected_goals": affected,
        "missing": [
            {
                "goal_id": result.goal_id,
                "reason_codes": list(result.blocking_reasons),
            }
            for result in unresolved
        ],
        "attempted": [
            {
                "goal_id": result.goal_id,
                "search_completed": result.search_completed,
            }
            for result in unresolved
        ],
        "cannot_continue_reason": BOUNDARY_DECISION_REASON,
        "recommendation": dict(BOUNDARY_DECISION_RECOMMENDATION),
        "alternatives": [
            dict(alternative) for alternative in BOUNDARY_DECISION_ALTERNATIVES
        ],
    }


class AcquisitionCoverageService:
    """Reconcile one locked ``assessing_coverage`` orchestration."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._module = AcquisitionModule(session)

    def reconcile(self, orchestration: ResearchOrchestration, *, principal):
        from app.services.research_acquisition import ResearchAcquisitionService

        if orchestration.state != "assessing_coverage":
            return orchestration
        if (
            orchestration.current_research_run_id is None
            or orchestration.current_scope_version_id is None
            or not isinstance(orchestration.checkpoint_json, dict)
        ):
            raise ValidationFailedError("coverage orchestration binding is incomplete")
        run = self._session.get(ResearchRun, orchestration.current_research_run_id)
        if run is None or run.research_case_id != orchestration.research_case_id:
            raise ValidationFailedError("coverage research run is stale")
        bridge = ResearchAcquisitionService(self._session, module=self._module)
        frozen = bridge.coverage_frozen_context(
            orchestration, principal=principal
        )
        checkpoint = dict(orchestration.checkpoint_json)
        round_number = checkpoint.get("acquisition_round")
        if not isinstance(round_number, int) or isinstance(round_number, bool):
            raise ValidationFailedError("coverage acquisition round is corrupt")
        rounds = self._round_checkpoints(checkpoint)
        expected_bindings = bridge.expected_goal_bindings(orchestration, frozen)
        acquisition_principal = AcquisitionPrincipal(
            tenant_id=orchestration.tenant_id,
            actor=principal.actor,
        )
        coverage_snapshots = self._module.coverage_snapshots(
            self._checkpoint_job_ids(rounds),
            principal=acquisition_principal,
        )
        prior_rows = {
            row.goal_id: row
            for row in self._session.scalars(
                select(AcquisitionGoalCoverage).where(
                    AcquisitionGoalCoverage.research_run_id == run.id,
                    AcquisitionGoalCoverage.scope_version_id
                    == orchestration.current_scope_version_id,
                )
            )
        }
        results: list[GoalCoverageResult] = []
        evidence_by_id: dict[uuid.UUID, object] = {}
        all_current_identities: set[str] = set()
        for binding in expected_bindings:
            goal_snapshots = self._goal_snapshots(
                rounds,
                binding["goal_id"],
                orchestration=orchestration,
                snapshots=coverage_snapshots,
            )
            if not goal_snapshots:
                raise ValidationFailedError("coverage goal has no acquisition job")
            jobs = [snapshot.job for snapshot in goal_snapshots]
            plans = [snapshot.plan for snapshot in goal_snapshots]
            frozen_inputs = plans[-1].frozen_inputs
            metric_terms = self._frozen_string_tuple(
                frozen_inputs, "metric_terms"
            )
            metric_periods = self._frozen_string_tuple(
                frozen_inputs, "metric_periods", allow_empty=True
            )
            metric_units = self._frozen_string_tuple(
                frozen_inputs, "metric_units", allow_empty=True
            )
            if any(
                self._frozen_string_tuple(plan.frozen_inputs, "metric_terms")
                != metric_terms
                or self._frozen_string_tuple(
                    plan.frozen_inputs, "metric_periods", allow_empty=True
                )
                != metric_periods
                or self._frozen_string_tuple(
                    plan.frozen_inputs, "metric_units", allow_empty=True
                )
                != metric_units
                for plan in plans
            ):
                raise ValidationFailedError(
                    "coverage metric semantics changed across frozen rounds"
                )
            search_operations = tuple(
                CoverageSearchOperation(
                    job_id=value.job_id,
                    query_index=value.query_index,
                    adapter_key=value.adapter_key,
                    query=value.query,
                    outcome=value.outcome,
                    error_codes=value.error_codes,
                )
                for snapshot in goal_snapshots
                for value in snapshot.search_operations
            )
            evidence_views = []
            for snapshot in goal_snapshots:
                evidence_views.extend(snapshot.evidence)
                evidence_by_id.update(
                    (value.evidence_link_id, value) for value in snapshot.evidence
                )
            thesis = self._session.get(Thesis, uuid.UUID(binding["thesis_id"]))
            if thesis is None or thesis.research_case_id != orchestration.research_case_id:
                raise ValidationFailedError("coverage thesis binding is stale")
            goal = CoverageGoal(
                goal_id=binding["goal_id"],
                thesis_id=thesis.id,
                objective=binding["objective"],
                job_id=jobs[-1].id,
                related_job_ids=tuple(job.id for job in jobs[:-1]),
                related_job_statuses=tuple(job.status for job in jobs[:-1]),
                job_status=jobs[-1].status,
                cutoff=frozen["cutoff"],
                entity_names=frozen["entity_names"],
                security_codes=frozen["security_codes"],
                metric_terms=metric_terms,
                metric_periods=metric_periods,
                metric_units=metric_units,
                period_start=frozen["period_start"],
                period_end=frozen["period_end"],
                search_operations=search_operations,
                require_explicit_metric_semantics=thesis.research_protocol_required,
            )
            evidence = tuple(
                CoverageEvidence(
                    evidence_link_id=value.evidence_link_id,
                    automatic_admission_decision_id=(
                        value.automatic_admission_decision_id
                    ),
                    source_statement_id=value.source_statement_id,
                    document_version_id=value.document_version_id,
                    job_id=value.job_id,
                    goal_id=value.goal_id,
                    review_state=value.review_state,
                    role=value.role,
                    authority=value.authority,
                    source_identity=value.source_identity,
                    publication_key=value.publication_key,
                    canonical_url=value.canonical_url,
                    content_sha256=value.content_sha256,
                    available_at=value.available_at,
                    subject=value.subject,
                    security_code=value.security_code,
                    metric=value.metric,
                    observed_period=value.observed_period,
                    unit=value.unit,
                )
                for value in evidence_views
                if value.policy_version == COVERAGE_POLICY.version
                or value.policy_version == "b-scope-v2"
            )
            result = COVERAGE_POLICY.evaluate_goal(goal, evidence)
            results.append(result)
            all_current_identities.update(result.independent_source_identities)
            self._persist_result(
                orchestration,
                result,
                round_number=round_number,
                prior=prior_rows.get(result.goal_id),
            )
            for evidence_id in result.qualifying_evidence_ids:
                value = evidence_by_id[evidence_id]
                append_automatic_scope_evidence_assignment(
                    self._session,
                    case_id=orchestration.research_case_id,
                    scope_version_id=orchestration.current_scope_version_id,
                    research_run_id=run.id,
                    goal_id=result.goal_id,
                    evidence_link_id=evidence_id,
                    automatic_admission_decision_id=(
                        value.automatic_admission_decision_id
                    ),
                    factor_statement=(
                        None
                        if result.objective == "alternative_explanation"
                        else thesis.statement
                    ),
                    provenance={
                        "policy_version": COVERAGE_POLICY.version,
                        "mapping_scope": (
                            "event"
                            if result.objective == "alternative_explanation"
                            else "factor"
                        ),
                    },
                    created_at=datetime.now(UTC),
                )

        history = checkpoint.get("coverage_history")
        prior_identities: set[str] = set()
        prior_zero = 0
        if isinstance(history, list) and history:
            latest = history[-1]
            if isinstance(latest, dict):
                prior_identities = {
                    value
                    for value in latest.get("independent_source_identities", [])
                    if isinstance(value, str)
                }
                value = latest.get("zero_new_independent_source_rounds", 0)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    prior_zero = value
        zero_new = 0 if all_current_identities - prior_identities else prior_zero + 1
        for row in self._session.scalars(
            select(AcquisitionGoalCoverage).where(
                AcquisitionGoalCoverage.research_run_id == run.id,
                AcquisitionGoalCoverage.scope_version_id
                == orchestration.current_scope_version_id,
            )
        ):
            row.zero_new_independent_source_rounds = zero_new
        outcome = decide_coverage(
            tuple(results),
            current_round=round_number,
            max_rounds=run.max_rounds,
            zero_new_rounds=zero_new,
            allow_unknown_completion=all(
                isinstance(value, Thesis) and not value.research_protocol_required
                for value in frozen["theses"]
            ),
        )
        return self._apply_outcome(
            orchestration,
            principal=principal,
            checkpoint=checkpoint,
            results=tuple(results),
            outcome=outcome,
            identities=tuple(sorted(all_current_identities)),
            zero_new=zero_new,
        )

    @staticmethod
    def _frozen_string_tuple(
        frozen_inputs: dict[str, object],
        key: str,
        *,
        allow_empty: bool = False,
    ) -> tuple[str, ...]:
        value = frozen_inputs.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValidationFailedError(f"coverage frozen {key} is corrupt")
        if not value and not allow_empty:
            raise ValidationFailedError(f"coverage frozen {key} is empty")
        return tuple(item.strip() for item in value)

    @staticmethod
    def _round_checkpoints(checkpoint: dict) -> list[dict]:
        history = checkpoint.get("acquisition_history", [])
        rounds = [value for value in history if isinstance(value, dict)] if isinstance(history, list) else []
        current = checkpoint.get("acquisition")
        if isinstance(current, dict):
            rounds.append(current)
        if not rounds:
            raise ValidationFailedError("coverage acquisition checkpoint is missing")
        return rounds

    @staticmethod
    def _checkpoint_job_ids(rounds) -> tuple[uuid.UUID, ...]:
        job_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for round_value in rounds:
            goals = round_value.get("goals")
            if not isinstance(goals, list):
                raise ValidationFailedError("coverage acquisition goals are corrupt")
            for item in goals:
                if not isinstance(item, dict):
                    raise ValidationFailedError("coverage acquisition goal is corrupt")
                try:
                    job_id = uuid.UUID(item["job_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValidationFailedError(
                        "coverage acquisition job ID is corrupt"
                    ) from exc
                if job_id not in seen:
                    seen.add(job_id)
                    job_ids.append(job_id)
        return tuple(job_ids)

    @staticmethod
    def _goal_snapshots(rounds, goal_id, *, orchestration, snapshots):
        result = []
        seen: set[uuid.UUID] = set()
        for round_value in rounds:
            goals = round_value.get("goals")
            if not isinstance(goals, list):
                raise ValidationFailedError("coverage acquisition goals are corrupt")
            for item in goals:
                if not isinstance(item, dict) or item.get("goal_id") != goal_id:
                    continue
                try:
                    job_id = uuid.UUID(item["job_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValidationFailedError("coverage acquisition job ID is corrupt") from exc
                if job_id in seen:
                    continue
                seen.add(job_id)
                snapshot = snapshots.get(job_id)
                if snapshot is None:
                    raise ValidationFailedError("coverage job binding is stale")
                view = snapshot.job
                if (
                    view.research_case_id != orchestration.research_case_id
                    or view.research_run_id != orchestration.current_research_run_id
                    or view.scope_version_id != orchestration.current_scope_version_id
                    or view.goal_id != goal_id
                ):
                    raise ValidationFailedError("coverage job binding is stale")
                plan = snapshot.plan
                if (
                    plan.job_id != job_id
                    or plan.goal_id != goal_id
                    or plan.acquisition_round != view.acquisition_round
                ):
                    raise ValidationFailedError(
                        "coverage query-plan binding is stale"
                    )
                result.append(snapshot)
        return sorted(
            result, key=lambda value: value.job.acquisition_round or 0
        )

    def _persist_result(self, orchestration, result, *, round_number, prior):
        now = datetime.now(UTC)
        row = prior
        if row is None:
            row = AcquisitionGoalCoverage(
                research_run_id=orchestration.current_research_run_id,
                scope_version_id=orchestration.current_scope_version_id,
                thesis_id=result.thesis_id,
                goal_id=result.goal_id,
                objective=result.objective,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        row.policy_version = result.policy_version
        row.required_authority_levels_json = list(result.required_authority_levels)
        row.observed_authority_levels_json = list(result.observed_authority_levels)
        row.status = result.status
        row.required_authority_count = result.required_authority_count
        row.observed_authority_count = result.observed_authority_count
        row.required_independent_source_count = result.required_independent_source_count
        row.observed_independent_source_count = result.observed_independent_source_count
        row.contrary_search_completed = result.contrary_search_completed
        row.reason_codes_json = list(result.blocking_reasons)
        row.evidence_link_ids_json = [str(value) for value in result.qualifying_evidence_ids]
        row.independent_source_identities_json = list(result.independent_source_identities)
        row.conflict_details_json = list(result.conflict_details)
        row.unknown_details_json = list(result.unknown_details)
        row.evaluation_round = round_number
        row.updated_at = now
        self._session.flush()

    def _apply_outcome(
        self,
        orchestration,
        *,
        principal,
        checkpoint,
        results,
        outcome,
        identities,
        zero_new,
    ):
        from app.repositories.research_orchestration import ResearchOrchestrationRepository

        round_number = checkpoint["acquisition_round"]
        coverage_summary = {
            "decision": outcome.kind,
            "policy_version": COVERAGE_POLICY.version,
            "acquisition_round": round_number,
            "unresolved_goal_ids": list(outcome.unresolved_goal_ids),
            "unknown_goal_ids": list(outcome.unknown_goal_ids),
            "independent_source_identities": list(identities),
            "zero_new_independent_source_rounds": zero_new,
        }
        key = (
            f"coverage:{orchestration.current_research_run_id}:"
            f"{orchestration.current_scope_version_id}:round:{round_number}:"
            f"{outcome.kind}"
        )
        repository = ResearchOrchestrationRepository(self._session)
        if outcome.kind == "ready":
            ready_checkpoint = dict(checkpoint)
            coverage_history = list(ready_checkpoint.get("coverage_history", []))
            if not coverage_history or coverage_history[-1] != coverage_summary:
                coverage_history.append(coverage_summary)
            ready_checkpoint["coverage_history"] = coverage_history
            ready_checkpoint["coverage_decision"] = coverage_summary
            return repository.record_checkpoint_event(
                orchestration,
                OrchestrationTransitionCommand(
                    expected_version=orchestration.version,
                    target_state="assessing_coverage",
                    user_stage="acquisition",
                    action="资料目标覆盖评估已完成",
                    reason="所有必要资料目标均满足冻结覆盖策略",
                    checkpoint=ready_checkpoint,
                    next_action_kind=None,
                    next_action_label=None,
                    next_action_payload=None,
                    recovery_status="healthy",
                    heartbeat=datetime.now(UTC),
                    current_scope_version_id=orchestration.current_scope_version_id,
                    current_research_run_id=orchestration.current_research_run_id,
                    actor=principal.actor,
                    event_transition="coverage_ready",
                    event_message="所有必要资料目标已满足覆盖规则",
                    event_payload=coverage_summary,
                    idempotency_key=key,
                ),
            )

        new_checkpoint = dict(checkpoint)
        coverage_history = list(new_checkpoint.get("coverage_history", []))
        coverage_history.append(coverage_summary)
        new_checkpoint["coverage_history"] = coverage_history
        new_checkpoint["coverage_decision"] = coverage_summary
        if outcome.kind == "continue":
            acquisition_history = list(new_checkpoint.get("acquisition_history", []))
            acquisition_history.append(new_checkpoint["acquisition"])
            new_checkpoint["acquisition_history"] = acquisition_history
            new_checkpoint.pop("acquisition", None)
            new_checkpoint["acquisition_round"] = round_number + 1
            new_checkpoint["pending_goal_ids"] = list(outcome.unresolved_goal_ids)
            new_checkpoint["query_expansion"] = {
                "trigger": outcome.expansion_trigger,
                "reason": outcome.expansion_reason,
            }
            return repository.transition(
                orchestration,
                OrchestrationTransitionCommand(
                    expected_version=orchestration.version,
                    target_state="planning_acquisition",
                    user_stage="acquisition",
                    action="正在为未解决目标规划下一轮资料获取",
                    reason=outcome.expansion_reason or "覆盖不足",
                    checkpoint=new_checkpoint,
                    next_action_kind=None,
                    next_action_label=None,
                    next_action_payload=None,
                    recovery_status="healthy",
                    heartbeat=datetime.now(UTC),
                    current_scope_version_id=orchestration.current_scope_version_id,
                    current_research_run_id=orchestration.current_research_run_id,
                    actor=principal.actor,
                    event_transition="coverage_continue",
                    event_message="覆盖不足，仅扩展未解决目标的下一轮查询",
                    event_payload=coverage_summary,
                    idempotency_key=key,
                ),
            )
        if outcome.kind == "needs_decision":
            payload = build_boundary_decision_context(
                results,
                outcome=outcome,
                attempted_rounds=round_number,
            )
            return repository.transition(
                orchestration,
                OrchestrationTransitionCommand(
                    expected_version=orchestration.version,
                    target_state="needs_scope_decision",
                    user_stage="scope_confirmation",
                    action="等待研究边界决定",
                    reason="继续补证需要改变冻结的研究边界",
                    checkpoint=new_checkpoint,
                    next_action_kind="scope_decision",
                    next_action_label="决定是否调整研究边界",
                    next_action_payload=payload,
                    recovery_status="healthy",
                    heartbeat=datetime.now(UTC),
                    current_scope_version_id=orchestration.current_scope_version_id,
                    current_research_run_id=orchestration.current_research_run_id,
                    actor=principal.actor,
                    event_transition="coverage_needs_decision",
                    event_message="覆盖不足且继续需要改变冻结边界",
                    event_payload=coverage_summary,
                    idempotency_key=key,
                ),
            )
        for row in self._session.scalars(
            select(AcquisitionGoalCoverage).where(
                AcquisitionGoalCoverage.research_run_id
                == orchestration.current_research_run_id,
                AcquisitionGoalCoverage.goal_id.in_(outcome.unresolved_goal_ids),
            )
        ):
            row.status = "exhausted"
            row.unknown_details_json = ["goal_fact_unresolved"]
            row.updated_at = datetime.now(UTC)
        return repository.transition(
            orchestration,
            OrchestrationTransitionCommand(
                expected_version=orchestration.version,
                target_state="exhausted",
                user_stage="acquisition",
                action="资料获取已达到停止条件",
                reason="达到最大轮次或连续未发现新的独立来源",
                checkpoint=new_checkpoint,
                next_action_kind=None,
                next_action_label=None,
                next_action_payload=None,
                recovery_status="healthy",
                heartbeat=datetime.now(UTC),
                current_scope_version_id=orchestration.current_scope_version_id,
                current_research_run_id=orchestration.current_research_run_id,
                actor=principal.actor,
                event_transition="coverage_exhausted",
                event_message="覆盖评估停止并将未解决事实保留为未知",
                event_payload=coverage_summary,
                idempotency_key=key,
            ),
        )
