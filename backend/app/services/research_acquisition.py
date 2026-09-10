"""Translate one frozen event scope into governed acquisition requests.

This bridge owns no provider or acquisition persistence details.  Its only
acquisition mutation is :meth:`AcquisitionModule.request`; orchestration state
remains owned by :class:`ResearchOrchestrationService`.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY
from app.domain.acquisition import (
    ACQUISITION_PLANNER_VERSION,
    AcquisitionJobRef,
    AcquisitionJobView,
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
    QueryPlanExpansion,
)
from app.errors import ConflictError, ValidationFailedError
from app.models.event_research import EventResearchBrief, EventResearchScopeVersion
from app.models.ledger import ResearchCase, Thesis
from app.models.operational import ResearchRun
from app.models.research_orchestration import ResearchOrchestration
from app.models.research_protocol import MetricDefinitionVersion
from app.repositories.research_protocol import ResearchProtocolRepository
from app.services.acquisition import AcquisitionModule
from app.services.event_research_scope_evidence import canonical_scope_thesis_ids


TERMINAL_ACQUISITION_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "cancelled"}
)

_FINANCIAL_METRIC_ANCHORS = (
    "归属于上市公司股东的净利润",
    "归属于母公司所有者的净利润",
    "归属于母公司股东的净利润",
    "扣除非经常性损益的净利润",
    "营业总收入",
    "营业收入",
    "净利润",
    "研发投入",
    "研发费用",
    "电子化学品",
    "供应链",
)


def _metric_terms_for_statement(statement: str) -> tuple[str, ...]:
    anchors: list[str] = []
    for anchor in _FINANCIAL_METRIC_ANCHORS:
        if anchor in statement and not any(anchor in existing for existing in anchors):
            anchors.append(anchor)
    return tuple(dict.fromkeys((statement, *anchors)))


@dataclass(frozen=True, slots=True)
class DispatchedAcquisitionGoal:
    goal_id: str
    thesis_id: uuid.UUID
    objective: str
    target_link_role: str
    job_id: uuid.UUID

    def checkpoint_value(self) -> dict[str, str]:
        return {
            "goal_id": self.goal_id,
            "thesis_id": str(self.thesis_id),
            "objective": self.objective,
            "target_link_role": self.target_link_role,
            "job_id": str(self.job_id),
        }


@dataclass(frozen=True, slots=True)
class AcquisitionRoundDispatch:
    round: int
    goals: tuple[DispatchedAcquisitionGoal, ...]

    def checkpoint_value(self) -> dict[str, object]:
        return {
            "round": self.round,
            "goals": [goal.checkpoint_value() for goal in self.goals],
        }


class ResearchAcquisitionService:
    """Build deterministic goals exclusively from persisted frozen records."""

    def __init__(
        self,
        session: Session,
        *,
        module: AcquisitionModule | None = None,
    ) -> None:
        self._session = session
        self._module = module or AcquisitionModule(session)

    def dispatch_round(
        self,
        orchestration: ResearchOrchestration,
        *,
        principal,
    ) -> AcquisitionRoundDispatch:
        acquisition_principal = self._principal(orchestration, principal)
        frozen = self._frozen_context(orchestration)
        round_value = self._round(orchestration.checkpoint_json)
        all_bindings = self._expected_goal_bindings(orchestration, frozen)
        selected_ids = [binding["goal_id"] for binding in all_bindings]
        previous_by_goal: dict[str, dict] = {}
        expansion: QueryPlanExpansion | None = None
        if round_value > 1:
            raw_pending = orchestration.checkpoint_json.get("pending_goal_ids")
            if (
                not isinstance(raw_pending, list)
                or not raw_pending
                or any(not isinstance(value, str) for value in raw_pending)
            ):
                raise ValidationFailedError(
                    "later acquisition round has no unresolved goal set"
                )
            selected_ids = list(dict.fromkeys(raw_pending))
            allowed = {binding["goal_id"] for binding in all_bindings}
            if len(selected_ids) != len(raw_pending) or not set(selected_ids) <= allowed:
                raise ValidationFailedError("later acquisition goal set is corrupt")
            history = orchestration.checkpoint_json.get("acquisition_history")
            if not isinstance(history, list) or not history:
                raise ValidationFailedError("later acquisition round has no predecessor")
            previous = history[-1]
            previous_goals = previous.get("goals") if isinstance(previous, dict) else None
            if not isinstance(previous_goals, list):
                raise ValidationFailedError("acquisition predecessor is corrupt")
            previous_by_goal = {
                item["goal_id"]: item
                for item in previous_goals
                if isinstance(item, dict) and isinstance(item.get("goal_id"), str)
            }
            raw_expansion = orchestration.checkpoint_json.get("query_expansion")
            if not isinstance(raw_expansion, dict):
                raise ValidationFailedError("query expansion is missing")
            try:
                expansion = QueryPlanExpansion(
                    trigger=raw_expansion["trigger"],
                    reason=raw_expansion["reason"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailedError("query expansion is corrupt") from exc

        goals: list[DispatchedAcquisitionGoal] = []
        theses_by_id = {str(thesis.id): thesis for thesis in frozen["theses"]}
        brief = frozen["brief"]
        assert isinstance(brief, EventResearchBrief)
        for binding in all_bindings:
            if binding["goal_id"] not in selected_ids:
                continue
            thesis = theses_by_id[binding["thesis_id"]]
            objective = EvidenceObjective(binding["objective"])
            metric_periods, metric_units = self._metric_semantics(
                thesis,
                objective=objective,
            )
            previous_plan_id = None
            if round_value > 1:
                predecessor = previous_by_goal.get(binding["goal_id"])
                if predecessor is None:
                    raise ValidationFailedError(
                        "unresolved goal has no predecessor acquisition job"
                    )
                try:
                    predecessor_job_id = uuid.UUID(predecessor["job_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValidationFailedError(
                        "predecessor acquisition job ID is corrupt"
                    ) from exc
                previous_plan_id = self._module.query_plan(
                    predecessor_job_id, principal=acquisition_principal
                ).id
            goals.append(
                self._request_goal(
                    orchestration,
                    frozen=frozen,
                    thesis=thesis,
                    goal_id=binding["goal_id"],
                    objective=objective,
                    target_link_role=binding["target_link_role"],
                    metric_terms=(
                        _metric_terms_for_statement(brief.research_question)
                        if objective == EvidenceObjective.ALTERNATIVE_EXPLANATION
                        else _metric_terms_for_statement(thesis.statement)
                    ),
                    metric_periods=metric_periods,
                    metric_units=metric_units,
                    round_value=round_value,
                    principal=acquisition_principal,
                    previous_query_plan_id=previous_plan_id,
                    expansion=expansion,
                )
            )
        return AcquisitionRoundDispatch(round=round_value, goals=tuple(goals))

    def read_checkpointed_jobs(
        self,
        orchestration: ResearchOrchestration,
        *,
        principal,
        checkpoint: object | None = None,
    ) -> tuple[AcquisitionJobView, ...]:
        acquisition_principal = self._principal(orchestration, principal)
        frozen = self._frozen_context(orchestration)
        checkpoint_value = (
            orchestration.checkpoint_json
            if checkpoint is None
            else checkpoint
        )
        acquisition = self._checkpoint_acquisition(checkpoint_value)
        expected_round = self._round(checkpoint_value)
        if acquisition.get("round") != expected_round:
            raise ValidationFailedError("acquisition checkpoint round is stale")
        raw_goals = acquisition.get("goals")
        if not isinstance(raw_goals, list) or not raw_goals:
            raise ValidationFailedError("acquisition checkpoint has no goals")

        expected = self._expected_goal_bindings(orchestration, frozen)
        previous_by_goal: dict[str, dict] = {}
        query_expansion: QueryPlanExpansion | None = None
        if expected_round > 1:
            pending = checkpoint_value.get("pending_goal_ids")
            if not isinstance(pending, list):
                raise ValidationFailedError("later acquisition goal set is corrupt")
            expected = [item for item in expected if item["goal_id"] in pending]
            history = checkpoint_value.get("acquisition_history")
            previous = history[-1] if isinstance(history, list) and history else None
            previous_goals = previous.get("goals") if isinstance(previous, dict) else None
            expansion_value = checkpoint_value.get("query_expansion")
            if not isinstance(previous_goals, list) or not isinstance(
                expansion_value, dict
            ):
                raise ValidationFailedError("later acquisition lineage is corrupt")
            previous_by_goal = {
                item["goal_id"]: item
                for item in previous_goals
                if isinstance(item, dict) and isinstance(item.get("goal_id"), str)
            }
            try:
                query_expansion = QueryPlanExpansion(
                    trigger=expansion_value["trigger"],
                    reason=expansion_value["reason"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationFailedError("query expansion is corrupt") from exc
        if len(raw_goals) != len(expected):
            raise ValidationFailedError("acquisition checkpoint goal set is incomplete")
        views: list[AcquisitionJobView] = []
        seen: set[str] = set()
        theses_by_id = {
            str(thesis.id): thesis for thesis in frozen["theses"]
        }
        brief = frozen["brief"]
        assert isinstance(brief, EventResearchBrief)
        for item, binding in zip(raw_goals, expected, strict=True):
            if not isinstance(item, dict) or item != binding | {"job_id": item.get("job_id")}:
                raise ValidationFailedError("acquisition checkpoint goal binding is corrupt")
            job_id_value = item.get("job_id")
            try:
                job_id = uuid.UUID(job_id_value)
            except (TypeError, ValueError) as exc:
                raise ValidationFailedError(
                    "acquisition checkpoint job ID is corrupt"
                ) from exc
            if str(job_id) in seen:
                raise ValidationFailedError("acquisition checkpoint repeats a job")
            seen.add(str(job_id))
            thesis = theses_by_id[binding["thesis_id"]]
            objective = EvidenceObjective(binding["objective"])
            metric_periods, metric_units = self._metric_semantics(
                thesis,
                objective=objective,
            )
            previous_plan_id = None
            if expected_round > 1:
                predecessor = previous_by_goal.get(binding["goal_id"])
                try:
                    predecessor_job_id = uuid.UUID(predecessor["job_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValidationFailedError(
                        "predecessor acquisition job ID is corrupt"
                    ) from exc
                previous_plan_id = self._module.query_plan(
                    predecessor_job_id, principal=acquisition_principal
                ).id
            request = self._build_request(
                orchestration,
                frozen=frozen,
                thesis=thesis,
                goal_id=binding["goal_id"],
                objective=objective,
                target_link_role=binding["target_link_role"],
                metric_terms=(
                    _metric_terms_for_statement(brief.research_question)
                    if objective == EvidenceObjective.ALTERNATIVE_EXPLANATION
                    else _metric_terms_for_statement(thesis.statement)
                ),
                metric_periods=metric_periods,
                metric_units=metric_units,
                round_value=expected_round,
                previous_query_plan_id=previous_plan_id,
                expansion=query_expansion,
            )
            view = self._module.verify_existing(
                request,
                expected_job_id=job_id,
                principal=acquisition_principal,
            )
            if (
                view.research_case_id != orchestration.research_case_id
                or view.research_run_id != orchestration.current_research_run_id
                or view.scope_version_id != orchestration.current_scope_version_id
                or view.thesis_id != uuid.UUID(binding["thesis_id"])
                or view.goal_id != binding["goal_id"]
                or view.acquisition_round != expected_round
            ):
                raise ValidationFailedError(
                    "checkpointed acquisition job does not belong to current scope"
                )
            views.append(view)
        return tuple(views)

    def validate_frozen_checkpoint(
        self,
        orchestration: ResearchOrchestration,
        *,
        principal,
        checkpoint: object | None = None,
    ) -> None:
        """Validate recovery's immutable scope/run binding without mutation."""
        self._principal(orchestration, principal)
        self._frozen_context(orchestration)
        value = (
            orchestration.checkpoint_json
            if checkpoint is None
            else checkpoint
        )
        self._round(value)
        if not isinstance(value, dict):
            raise ValidationFailedError("orchestration checkpoint is corrupt")
        if (
            value.get("scope_version_id")
            != str(orchestration.current_scope_version_id)
            or value.get("research_run_id")
            != str(orchestration.current_research_run_id)
        ):
            raise ValidationFailedError("orchestration checkpoint binding is stale")

    def coverage_frozen_context(self, orchestration, *, principal) -> dict[str, object]:
        """Return the already-frozen context after tenant authorization."""
        self._principal(orchestration, principal)
        return self._frozen_context(orchestration)

    def expected_goal_bindings(
        self, orchestration, frozen: dict[str, object] | None = None
    ) -> list[dict[str, str]]:
        return self._expected_goal_bindings(
            orchestration,
            frozen if frozen is not None else self._frozen_context(orchestration),
        )

    def _request_goal(
        self,
        orchestration: ResearchOrchestration,
        *,
        frozen: dict[str, object],
        thesis: Thesis,
        goal_id: str,
        objective: EvidenceObjective,
        target_link_role: str,
        metric_terms: tuple[str, ...],
        metric_periods: tuple[str, ...],
        metric_units: tuple[str, ...],
        round_value: int,
        principal: AcquisitionPrincipal,
        previous_query_plan_id: uuid.UUID | None = None,
        expansion: QueryPlanExpansion | None = None,
    ) -> DispatchedAcquisitionGoal:
        request = self._build_request(
            orchestration,
            frozen=frozen,
            thesis=thesis,
            goal_id=goal_id,
            objective=objective,
            target_link_role=target_link_role,
            metric_terms=metric_terms,
            metric_periods=metric_periods,
            metric_units=metric_units,
            round_value=round_value,
            previous_query_plan_id=previous_query_plan_id,
            expansion=expansion,
        )
        ref: AcquisitionJobRef = self._module.request(
            request,
            principal=principal,
        )
        return DispatchedAcquisitionGoal(
            goal_id=goal_id,
            thesis_id=thesis.id,
            objective=objective.value,
            target_link_role=target_link_role,
            job_id=ref.id,
        )

    @staticmethod
    def _build_request(
        orchestration: ResearchOrchestration,
        *,
        frozen: dict[str, object],
        thesis: Thesis,
        goal_id: str,
        objective: EvidenceObjective,
        target_link_role: str,
        metric_terms: tuple[str, ...],
        metric_periods: tuple[str, ...],
        metric_units: tuple[str, ...],
        round_value: int,
        previous_query_plan_id: uuid.UUID | None = None,
        expansion: QueryPlanExpansion | None = None,
    ) -> AcquisitionRequest:
        scope = frozen["scope"]
        run = frozen["run"]
        assert isinstance(scope, EventResearchScopeVersion)
        assert isinstance(run, ResearchRun)
        key = (
            f"run:{run.id}:scope:{scope.id}:goal:{goal_id}:round:{round_value}"
        )
        return AcquisitionRequest(
            tenant_id=orchestration.tenant_id,
            case_id=orchestration.research_case_id,
            thesis_id=thesis.id,
            research_run_id=run.id,
            scope_version_id=scope.id,
            goal_id=goal_id,
            round=round_value,
            objective=objective,
            target_link_role=target_link_role,
            thesis_statement=thesis.statement,
            entity_names=frozen["entity_names"],
            security_codes=frozen["security_codes"],
            metric_terms=metric_terms,
            metric_periods=metric_periods,
            metric_units=metric_units,
            period_start=frozen["period_start"],
            period_end=frozen["period_end"],
            cutoff=frozen["cutoff"],
            allowed_source_roles=B_SCOPE_POLICY.allowed_source_roles,
            source_policy_version=B_SCOPE_POLICY.version,
            planner_version=ACQUISITION_PLANNER_VERSION,
            previous_query_plan_id=previous_query_plan_id,
            expansion=expansion,
            idempotency_key=key,
        )

    def _metric_semantics(
        self,
        thesis: Thesis,
        *,
        objective: EvidenceObjective,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if objective is EvidenceObjective.ALTERNATIVE_EXPLANATION:
            return (), ()
        binding = ResearchProtocolRepository(self._session).effective_binding(thesis.id)
        if binding is None or binding.state != "approved":
            return (), ()
        metric = self._session.get(
            MetricDefinitionVersion, binding.metric_definition_id
        )
        if metric is None or not metric.unit.strip() or not metric.period_semantics.strip():
            raise ValidationFailedError(
                "approved metric semantics are incomplete"
            )
        period = (
            f"{metric.period_semantics.strip()}:"
            f"{binding.horizon_start.isoformat()}/{binding.horizon_end.isoformat()}"
        )
        return (period,), (metric.unit.strip(),)

    def _frozen_context(self, orchestration: ResearchOrchestration) -> dict[str, object]:
        if (
            orchestration.current_scope_version_id is None
            or orchestration.current_research_run_id is None
        ):
            raise ValidationFailedError("orchestration has no frozen scope and run")
        case = self._session.get(ResearchCase, orchestration.research_case_id)
        scope = self._session.get(
            EventResearchScopeVersion, orchestration.current_scope_version_id
        )
        run = self._session.get(ResearchRun, orchestration.current_research_run_id)
        brief = self._session.scalar(
            select(EventResearchBrief)
            .where(EventResearchBrief.research_case_id == orchestration.research_case_id)
            .order_by(EventResearchBrief.created_at, EventResearchBrief.id)
            .limit(1)
        )
        if (
            case is None
            or scope is None
            or scope.research_case_id != case.id
            or run is None
            or run.research_case_id != case.id
            or brief is None
        ):
            raise ValidationFailedError("frozen event scope or run is stale")
        latest_scope_id = self._session.scalar(
            select(EventResearchScopeVersion.id)
            .where(EventResearchScopeVersion.research_case_id == case.id)
            .order_by(
                EventResearchScopeVersion.version.desc(),
                EventResearchScopeVersion.id.desc(),
            )
            .limit(1)
        )
        if latest_scope_id != scope.id:
            raise ValidationFailedError("orchestration scope is not current")
        if run.status != "prepared" or run.stage != "awaiting_acquisition":
            raise ConflictError("research run is not awaiting acquisition")
        thesis_ids = canonical_scope_thesis_ids(self._session, case.id, scope.id)
        if not thesis_ids or run.scope_thesis_ids != [str(value) for value in thesis_ids]:
            raise ValidationFailedError("research run thesis scope is stale")
        theses_by_id = {
            thesis.id: thesis
            for thesis in self._session.scalars(
                select(Thesis).where(Thesis.id.in_(thesis_ids))
            )
        }
        if len(theses_by_id) != len(thesis_ids):
            raise ValidationFailedError("frozen scope has missing active theses")
        theses = tuple(theses_by_id[value] for value in thesis_ids)

        entity = (brief.company_name or case.research_object or brief.event_title).strip()
        security_codes = self._security_codes(brief.ticker)
        fallback_date = (
            brief.event_at.date()
            if brief.event_at is not None
            else case.evidence_cutoff or scope.created_at.date()
        )
        end = case.period_end or case.evidence_cutoff or fallback_date
        # Event intake does not yet ask for an explicit research window. A
        # one-day fallback makes every prior disclosure semantically
        # inadmissible, so use a bounded trailing year until the user narrows
        # the dates through a frozen protocol revision.
        start = case.period_start or (end - timedelta(days=365))
        if start > end:
            raise ValidationFailedError("frozen research period is invalid")
        cutoff_date = case.evidence_cutoff or end
        cutoff = datetime.combine(cutoff_date, time.max, tzinfo=UTC)
        return {
            "case": case,
            "scope": scope,
            "run": run,
            "brief": brief,
            "theses": theses,
            "entity_names": (entity,),
            "security_codes": security_codes,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "cutoff": cutoff,
        }

    @staticmethod
    def _security_codes(ticker: str | None) -> tuple[str, ...]:
        if ticker is None:
            return ()
        value = ticker.strip()
        match = re.fullmatch(r"([A-Za-z0-9]+)(?:\.(?:SH|SZ|TW|HK))?", value, re.I)
        return (match.group(1),) if match is not None else ()

    @staticmethod
    def _round(checkpoint: object) -> int:
        if not isinstance(checkpoint, dict):
            raise ValidationFailedError("orchestration checkpoint is corrupt")
        value = checkpoint.get("acquisition_round")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationFailedError("orchestration acquisition round is corrupt")
        return value

    @staticmethod
    def _checkpoint_acquisition(checkpoint: object) -> dict:
        if not isinstance(checkpoint, dict) or not isinstance(
            checkpoint.get("acquisition"), dict
        ):
            raise ValidationFailedError("acquisition checkpoint is missing")
        return checkpoint["acquisition"]

    @staticmethod
    def _principal(orchestration, principal) -> AcquisitionPrincipal:
        if principal.tenant_id != orchestration.tenant_id:
            raise ValidationFailedError("orchestration tenant is unauthorized")
        return AcquisitionPrincipal(
            tenant_id=orchestration.tenant_id,
            actor=principal.actor,
        )

    @staticmethod
    def _expected_goal_bindings(orchestration, frozen) -> list[dict[str, str]]:
        expected: list[dict[str, str]] = []
        for thesis in frozen["theses"]:
            for objective, role in (
                ("support", "supports"),
                ("contradict", "contradicts"),
            ):
                expected.append(
                    {
                        "goal_id": f"thesis:{thesis.id}:{objective}",
                        "thesis_id": str(thesis.id),
                        "objective": objective,
                        "target_link_role": role,
                    }
                )
        first = frozen["theses"][0]
        expected.append(
            {
                "goal_id": (
                    f"event:{orchestration.research_case_id}:"
                    "alternative_explanation"
                ),
                "thesis_id": str(first.id),
                "objective": "alternative_explanation",
                "target_link_role": "contextualizes",
            }
        )
        return expected
