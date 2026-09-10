"""Behavioral contract for goal-bound, immutable acquisition query plans."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Barrier, Event as ThreadEvent

import app.domain.acquisition as acquisition_domain
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.acquisition.policy import B_SCOPE_POLICY
from app.domain.acquisition import (
    ACQUISITION_PLANNER_VERSION,
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
    PlannedQuery,
    QueryPlanExpansion,
)
from app.models.acquisition import AcquisitionJob, AcquisitionJobEvent
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import Base
from app.models.operational import ResearchRun
from app.models.research_orchestration import AcquisitionQueryPlan, AcquisitionSeries
from app.repositories.acquisition import AcquisitionRepository
from app.services.acquisition import AcquisitionModule
from tests.tenant_admission import admit_case


NOW = datetime(2026, 8, 15, tzinfo=UTC)


class CountingPlanner:
    version = ACQUISITION_PLANNER_VERSION

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, request: AcquisitionRequest, policy) -> tuple[PlannedQuery, ...]:
        self.calls += 1
        suffix = " ".join(request.metric_terms)
        return tuple(
            PlannedQuery(
                adapter_key=adapter,
                objective=request.objective,
                query=f"{request.goal_id} {suffix} {adapter}",
            )
            for adapter in sorted(policy.enabled_adapter_keys)
        )


class StalePlanner(CountingPlanner):
    version = "goal-query-v0"


class SynchronizedCreateRepository(AcquisitionRepository):
    """Pause after the post-planning idempotency lookup observes no Job."""

    def __init__(
        self,
        session,
        *,
        second_lookup_barrier: Barrier,
        second_lookup_completed: ThreadEvent,
    ) -> None:
        super().__init__(session)
        self._second_lookup_barrier = second_lookup_barrier
        self._second_lookup_completed = second_lookup_completed
        self.idempotency_lookup_count = 0

    def _by_idempotency(self, tenant_id, idempotency_key):
        self.idempotency_lookup_count += 1
        existing = super()._by_idempotency(tenant_id, idempotency_key)
        if self.idempotency_lookup_count == 2:
            assert existing is None
            self._second_lookup_completed.set()
            self._second_lookup_barrier.wait(timeout=10)
        return existing


def _scope_and_run(session, research_case, thesis):
    scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=1,
        changed_by="system:test",
        change_summary="confirmed test scope",
        created_at=NOW,
    )
    run = ResearchRun(
        research_case_id=research_case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=100,
        budget_used=0,
        scope_thesis_ids=[str(thesis.id)],
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((scope, run))
    session.flush()
    return scope, run


def _request(research_case, thesis, scope, run, **overrides) -> AcquisitionRequest:
    goal_id = str(overrides.pop("goal_id", f"thesis:{thesis.id}:support"))
    round_value = int(overrides.pop("round", 1))
    values = {
        "tenant_id": "team-a",
        "case_id": research_case.id,
        "thesis_id": thesis.id,
        "research_run_id": run.id,
        "scope_version_id": scope.id,
        "goal_id": goal_id,
        "round": round_value,
        "objective": EvidenceObjective.SUPPORT,
        "target_link_role": "supports",
        "thesis_statement": thesis.statement,
        "entity_names": ("Example Corp",),
        "security_codes": ("600001",),
        "metric_terms": ("Revenue",),
        "metric_periods": ("2026-Q2",),
        "metric_units": ("USD",),
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "cutoff": NOW,
        "allowed_source_roles": frozenset(B_SCOPE_POLICY.allowed_source_roles),
        "source_policy_version": B_SCOPE_POLICY.version,
        "planner_version": ACQUISITION_PLANNER_VERSION,
        "previous_query_plan_id": None,
        "expansion": None,
        "idempotency_key": (
            f"run:{run.id}:scope:{scope.id}:goal:{goal_id}:round:{round_value}"
        ),
    }
    values.update(overrides)
    return AcquisitionRequest(**values)


def test_acquisition_series_owns_query_plan_lineage() -> None:
    assert "acquisition_series" in Base.metadata.tables

    series = Base.metadata.tables["acquisition_series"]
    plans = Base.metadata.tables["acquisition_query_plans"]

    assert {
        "tenant_id",
        "research_case_id",
        "research_run_id",
        "scope_version_id",
        "thesis_id",
        "goal_id",
    } <= set(series.c.keys())
    assert "series_id" in plans.c

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in plans.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("acquisition_job_id",) in unique_columns
    assert ("series_id", "acquisition_round") in unique_columns
    assert ("id", "series_id", "acquisition_round") in unique_columns

    lineage = next(
        constraint
        for constraint in plans.constraints
        if getattr(constraint, "name", None)
        == "fk_acquisition_query_plans_previous_plan_lineage"
    )
    assert tuple(column.name for column in lineage.columns) == (
        "previous_query_plan_id",
        "series_id",
        "previous_acquisition_round",
    )


def test_migration_0056_moves_query_plan_lineage_to_series() -> None:
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0056_acquisition_series.py"
    )
    assert migration.exists()
    source = migration.read_text(encoding="utf-8")
    assert 'revision: str = "0056"' in source
    assert 'down_revision: Union[str, None] = "0055"' in source
    assert '"acquisition_series"' in source
    assert '"series_id"' in source


def test_acquisition_request_exposes_stable_goal_plan_identity() -> None:
    assert hasattr(acquisition_domain, "QueryPlanExpansion")
    request_fields = {field.name for field in fields(acquisition_domain.AcquisitionRequest)}
    assert {
        "scope_version_id",
        "goal_id",
        "planner_version",
        "previous_query_plan_id",
        "expansion",
    } <= request_fields


def test_module_freezes_one_plan_before_queue_and_replays_without_replanning(
    session, research_case, thesis, document
) -> None:
    session.autoflush = False
    admit_case(
        session, research_case.id, tenant_id="team-a", document_version_id=document.id
    )
    scope, run = _scope_and_run(session, research_case, thesis)
    planner = CountingPlanner()
    module = AcquisitionModule(session, planner=planner)
    request = _request(research_case, thesis, scope, run)
    principal = AcquisitionPrincipal(tenant_id="team-a", actor="system:test")

    first = module.request(request, principal=principal)
    first_bytes = json.dumps(
        session.scalar(select(AcquisitionQueryPlan)).ordered_queries_json,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    replay = module.request(request, principal=principal)

    assert replay == first
    assert planner.calls == 1
    assert session.scalar(select(func.count()).select_from(AcquisitionSeries)) == 1
    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 1
    assert session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 1
    job = session.get(AcquisitionJob, first.id)
    plan = session.scalar(select(AcquisitionQueryPlan))
    assert job is not None and plan is not None
    assert job.request_snapshot["query_plan_id"] == str(plan.id)
    assert plan.acquisition_job_id == job.id
    assert plan.series_id == session.scalar(select(AcquisitionSeries.id))
    assert json.dumps(
        plan.ordered_queries_json,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode() == first_bytes
    assert [event.message for event in session.scalars(
        select(AcquisitionJobEvent)
        .where(AcquisitionJobEvent.job_id == job.id)
        .order_by(AcquisitionJobEvent.seq)
    )] == ["acquisition queued", "query_plan_frozen"]


def test_module_rejects_a_request_bound_to_a_different_planner_version(
    session, research_case, thesis, document
) -> None:
    admit_case(
        session, research_case.id, tenant_id="team-a", document_version_id=document.id
    )
    scope, run = _scope_and_run(session, research_case, thesis)

    with pytest.raises(ValueError, match="planner version is not active"):
        AcquisitionModule(session, planner=StalePlanner()).request(
            _request(research_case, thesis, scope, run),
            principal=AcquisitionPrincipal(
                tenant_id="team-a", actor="system:test"
            ),
        )

    assert session.scalar(select(func.count()).select_from(AcquisitionSeries)) == 0
    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0
    assert session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 0


def test_round_two_is_a_new_job_in_same_series_with_explicit_diff(
    session, research_case, thesis, document
) -> None:
    admit_case(
        session, research_case.id, tenant_id="team-a", document_version_id=document.id
    )
    scope, run = _scope_and_run(session, research_case, thesis)
    module = AcquisitionModule(session, planner=CountingPlanner())
    principal = AcquisitionPrincipal(tenant_id="team-a", actor="system:test")
    first_job = module.request(
        _request(research_case, thesis, scope, run), principal=principal
    )
    first_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == first_job.id
        )
    )
    assert first_plan is not None

    second_request = _request(
        research_case,
        thesis,
        scope,
        run,
        round=2,
        metric_terms=("Revenue", "Operating margin"),
        previous_query_plan_id=first_plan.id,
        expansion=QueryPlanExpansion(
            trigger="coverage_gap",
            reason="Missing margin evidence from every permitted authority",
        ),
    )
    second_job = module.request(second_request, principal=principal)
    second_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == second_job.id
        )
    )

    assert second_job.id != first_job.id
    assert second_plan is not None
    assert second_plan.series_id == first_plan.series_id
    assert second_plan.previous_query_plan_id == first_plan.id
    assert second_plan.previous_acquisition_round == 1
    assert second_plan.expansion_trigger == "coverage_gap"
    assert second_plan.diff_json == {
        "added_queries": second_plan.ordered_queries_json,
        "removed_queries": first_plan.ordered_queries_json,
        "reason": "Missing margin evidence from every permitted authority",
    }
    assert [event.message for event in session.scalars(
        select(AcquisitionJobEvent)
        .where(AcquisitionJobEvent.job_id == second_job.id)
        .order_by(AcquisitionJobEvent.seq)
    )] == ["acquisition queued", "query_plan_expanded"]


def test_plan_persistence_failure_rolls_back_series_job_and_events(
    session, research_case, thesis, document
) -> None:
    admit_case(
        session, research_case.id, tenant_id="team-a", document_version_id=document.id
    )
    scope, run = _scope_and_run(session, research_case, thesis)
    module = AcquisitionModule(session, planner=CountingPlanner())

    def fail_plan_flush(_session, _flush_context, _instances) -> None:
        if any(isinstance(value, AcquisitionQueryPlan) for value in _session.new):
            raise RuntimeError("forced plan persistence failure")

    event.listen(session, "before_flush", fail_plan_flush)
    try:
        with pytest.raises(RuntimeError, match="forced plan persistence failure"):
            module.request(
                _request(research_case, thesis, scope, run),
                principal=AcquisitionPrincipal(
                    tenant_id="team-a", actor="system:test"
                ),
            )
    finally:
        event.remove(session, "before_flush", fail_plan_flush)

    assert session.scalar(select(func.count()).select_from(AcquisitionSeries)) == 0
    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0
    assert session.scalar(select(func.count()).select_from(AcquisitionQueryPlan)) == 0
    assert session.scalar(select(func.count()).select_from(AcquisitionJobEvent)) == 0


@pytest.mark.pg_only
@pytest.mark.parametrize("_repetition", range(10))
def test_postgres_concurrent_idempotent_create_replays_one_frozen_aggregate(
    engine, session, research_case, thesis, document, _repetition
) -> None:
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    scope, run = _scope_and_run(session, research_case, thesis)
    request = _request(research_case, thesis, scope, run)
    principal = AcquisitionPrincipal("team-a", "system:concurrent-test")
    session.commit()
    sessions = sessionmaker(bind=engine, future=True)
    second_lookup_barrier = Barrier(2)
    second_lookup_completed = (ThreadEvent(), ThreadEvent())

    def create(worker_index: int) -> dict[str, object]:
        with sessions() as command:
            savepoint_conflict_count = 0

            def track_savepoint_conflict(_session, previous_transaction) -> None:
                nonlocal savepoint_conflict_count
                parent = previous_transaction.parent
                if (
                    not previous_transaction.nested
                    and parent is not None
                    and parent.nested
                ):
                    savepoint_conflict_count += 1

            event.listen(command, "after_soft_rollback", track_savepoint_conflict)
            command.execute(text("SET LOCAL lock_timeout = '10s'"))
            repository = SynchronizedCreateRepository(
                command,
                second_lookup_barrier=second_lookup_barrier,
                second_lookup_completed=second_lookup_completed[worker_index],
            )
            result = AcquisitionModule(
                command,
                repository=repository,
            ).request(request, principal=principal)
            loser_visible_jobs = None
            if savepoint_conflict_count:
                loser_visible_jobs = command.scalar(
                    select(func.count()).select_from(AcquisitionJob)
                )
                command.commit()
            else:
                command.commit()
            committed_jobs = command.scalar(
                select(func.count()).select_from(AcquisitionJob)
            )
            command.commit()
            event.remove(command, "after_soft_rollback", track_savepoint_conflict)
            assert committed_jobs == 1
            return {
                "job_id": result.id,
                "savepoint_conflict_count": savepoint_conflict_count,
                "loser_visible_jobs": loser_visible_jobs,
                "idempotency_lookup_count": repository.idempotency_lookup_count,
            }

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(create, index) for index in range(2)]
        results = [future.result(timeout=20) for future in futures]
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    assert all(completed.wait(timeout=1) for completed in second_lookup_completed)
    assert len({result["job_id"] for result in results}) == 1
    losers = [result for result in results if result["savepoint_conflict_count"]]
    assert len(losers) == 1
    assert losers[0]["savepoint_conflict_count"] == 1
    assert losers[0]["loser_visible_jobs"] == 1
    assert sorted(
        result["idempotency_lookup_count"] for result in results
    ) == [2, 3]
    with sessions() as verification:
        assert verification.scalar(
            select(func.count()).select_from(AcquisitionSeries)
        ) == 1
        assert verification.scalar(
            select(func.count()).select_from(AcquisitionJob)
        ) == 1
        assert verification.scalar(
            select(func.count()).select_from(AcquisitionQueryPlan)
        ) == 1
