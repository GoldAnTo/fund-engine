"""Persistence contract for event-research orchestration records."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

import app.models  # noqa: F401 - register all models on shared metadata
from app.models.ledger import Base, IMMUTABLE_TABLES, ImmutableLedgerError


ORCHESTRATION_TABLES = {
    "research_orchestrations",
    "research_orchestration_events",
    "acquisition_series",
    "acquisition_query_plans",
    "acquisition_goal_coverages",
}
APPEND_ONLY_ORCHESTRATION_TABLES = {
    "research_orchestration_events",
    "acquisition_series",
    "acquisition_query_plans",
}

EXPECTED_COLUMNS = {
    "research_orchestrations": {
        "id",
        "tenant_id",
        "research_case_id",
        "current_scope_version_id",
        "current_research_run_id",
        "state",
        "user_stage",
        "current_system_action",
        "system_action_reason",
        "checkpoint_json",
        "next_action_kind",
        "next_action_label",
        "next_action_payload",
        "last_heartbeat_at",
        "recovery_status",
        "state_started_at",
        "version",
        "created_at",
        "updated_at",
    },
    "research_orchestration_events": {
        "id",
        "orchestration_id",
        "sequence",
        "transition",
        "actor",
        "message",
        "payload_json",
        "idempotency_key",
        "created_at",
    },
    "acquisition_query_plans": {
        "id",
        "series_id",
        "acquisition_job_id",
        "acquisition_round",
        "goal_id",
        "planner_version",
        "policy_version",
        "frozen_inputs_json",
        "ordered_queries_json",
        "previous_query_plan_id",
        "previous_acquisition_round",
        "expansion_trigger",
        "diff_json",
        "created_at",
    },
    "acquisition_series": {
        "id",
        "tenant_id",
        "research_case_id",
        "research_run_id",
        "scope_version_id",
        "thesis_id",
        "goal_id",
        "created_at",
    },
    "acquisition_goal_coverages": {
        "id",
        "research_run_id",
        "scope_version_id",
        "thesis_id",
        "goal_id",
        "objective",
        "policy_version",
        "required_authority_levels_json",
        "observed_authority_levels_json",
        "status",
        "required_authority_count",
        "observed_authority_count",
        "required_independent_source_count",
        "observed_independent_source_count",
        "contrary_search_completed",
        "reason_codes_json",
        "evidence_link_ids_json",
        "independent_source_identities_json",
        "conflict_details_json",
        "unknown_details_json",
        "evaluation_round",
        "zero_new_independent_source_rounds",
        "created_at",
        "updated_at",
    },
}

EXPECTED_UNIQUES = {
    "research_orchestrations": {
        "uq_research_orchestrations_tenant_case": (
            "tenant_id",
            "research_case_id",
        )
    },
    "research_orchestration_events": {
        "uq_research_orchestration_events_sequence": (
            "orchestration_id",
            "sequence",
        ),
        "uq_research_orchestration_events_idempotency": (
            "orchestration_id",
            "idempotency_key",
        ),
    },
    "acquisition_query_plans": {
        "uq_acquisition_query_plans_job": ("acquisition_job_id",),
        "uq_acquisition_query_plans_series_round": (
            "series_id", "acquisition_round"
        ),
        "uq_acquisition_query_plans_lineage_target": (
            "id",
            "series_id",
            "acquisition_round",
        ),
    },
    "acquisition_series": {
        "uq_acquisition_series_goal_identity": (
            "tenant_id",
            "research_case_id",
            "research_run_id",
            "scope_version_id",
            "thesis_id",
            "goal_id",
        )
    },
    "acquisition_goal_coverages": {
        "uq_acquisition_goal_coverages_run_scope_goal": (
            "research_run_id",
            "scope_version_id",
            "goal_id",
        )
    },
}

EXPECTED_FOREIGN_KEYS = {
    "research_orchestrations": {
        "fk_research_orchestrations_research_case_id": (
            ("research_case_id",),
            "research_cases",
            ("id",),
        ),
        "fk_research_orchestrations_current_scope_version_id": (
            ("current_scope_version_id",),
            "event_research_scope_versions",
            ("id",),
        ),
        "fk_research_orchestrations_current_research_run_id": (
            ("current_research_run_id",),
            "research_runs",
            ("id",),
        ),
    },
    "research_orchestration_events": {
        "fk_research_orchestration_events_orchestration_id": (
            ("orchestration_id",),
            "research_orchestrations",
            ("id",),
        )
    },
    "acquisition_query_plans": {
        "fk_acquisition_query_plans_acquisition_job_id": (
            ("acquisition_job_id",),
            "acquisition_jobs",
            ("id",),
        ),
        "fk_acquisition_query_plans_series_id": (
            ("series_id",),
            "acquisition_series",
            ("id",),
        ),
        "fk_acquisition_query_plans_previous_plan_lineage": (
            (
                "previous_query_plan_id",
                "series_id",
                "previous_acquisition_round",
            ),
            "acquisition_query_plans",
            ("id", "series_id", "acquisition_round"),
        ),
    },
    "acquisition_series": {
        "fk_acquisition_series_research_case_id": (
            ("research_case_id",), "research_cases", ("id",)
        ),
        "fk_acquisition_series_research_run_id": (
            ("research_run_id",), "research_runs", ("id",)
        ),
        "fk_acquisition_series_scope_version_id": (
            ("scope_version_id",), "event_research_scope_versions", ("id",)
        ),
        "fk_acquisition_series_thesis_id": (
            ("thesis_id",), "theses", ("id",)
        ),
    },
    "acquisition_goal_coverages": {
        "fk_acquisition_goal_coverages_research_run_id": (
            ("research_run_id",),
            "research_runs",
            ("id",),
        ),
        "fk_acquisition_goal_coverages_scope_version_id": (
            ("scope_version_id",),
            "event_research_scope_versions",
            ("id",),
        ),
        "fk_acquisition_goal_coverages_thesis_id": (
            ("thesis_id",),
            "theses",
            ("id",),
        ),
    },
}

EXPECTED_CHECK_NAMES = {
    "research_orchestrations": {
        "ck_research_orchestrations_tenant_nonblank",
        "ck_research_orchestrations_state",
        "ck_research_orchestrations_user_stage",
        "ck_research_orchestrations_next_action",
        "ck_research_orchestrations_recovery_status",
        "ck_research_orchestrations_version_non_negative",
    },
    "research_orchestration_events": {
        "ck_research_orchestration_events_sequence_positive",
        "ck_research_orchestration_events_transition_nonblank",
        "ck_research_orchestration_events_actor_nonblank",
        "ck_research_orchestration_events_message_nonblank",
        "ck_research_orchestration_events_idempotency_key_nonblank",
    },
    "acquisition_query_plans": {
        "ck_acquisition_query_plans_round_positive",
        "ck_acquisition_query_plans_goal_id_nonblank",
        "ck_acquisition_query_plans_planner_version_nonblank",
        "ck_acquisition_query_plans_policy_version_nonblank",
        "ck_acquisition_query_plans_round_lineage",
    },
    "acquisition_series": {
        "ck_acquisition_series_tenant_nonblank",
        "ck_acquisition_series_goal_nonblank",
    },
    "acquisition_goal_coverages": {
        "ck_acquisition_goal_coverages_goal_id_nonblank",
        "ck_acquisition_goal_coverages_objective_nonblank",
        "ck_acquisition_goal_coverages_status",
        "ck_acquisition_goal_coverages_counts_non_negative",
        "ck_acquisition_goal_coverages_policy_version_nonblank",
        "ck_acquisition_goal_coverages_rounds_non_negative",
    },
}


def _unique_constraints(table: sa.Table) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _foreign_keys(
    table: sa.Table,
) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        constraint.name: (
            tuple(column.name for column in constraint.columns),
            next(iter(constraint.elements)).column.table.name,
            tuple(element.column.name for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }


def _checks(table: sa.Table) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _default_value(column: sa.Column):
    assert column.default is not None
    value = column.default.arg
    return value(None) if callable(value) else value


def _query_plan_values(
    *,
    plan_id: uuid.UUID,
    series_id: uuid.UUID,
    job_id: uuid.UUID,
    acquisition_round: int,
    previous_query_plan_id: uuid.UUID | None,
    previous_acquisition_round: int | None,
    expansion_trigger: str | None,
    now: datetime,
) -> dict:
    return {
        "id": plan_id,
        "series_id": series_id,
        "acquisition_job_id": job_id,
        "acquisition_round": acquisition_round,
        "goal_id": "goal-1",
        "planner_version": "planner-v1",
        "policy_version": "policy-v1",
        "frozen_inputs_json": {},
        "ordered_queries_json": [],
        "previous_query_plan_id": previous_query_plan_id,
        "previous_acquisition_round": previous_acquisition_round,
        "expansion_trigger": expansion_trigger,
        "diff_json": None if acquisition_round == 1 else {},
        "created_at": now,
    }


def test_metadata_registers_all_orchestration_tables_and_exact_columns() -> None:
    assert ORCHESTRATION_TABLES <= set(Base.metadata.tables)
    for table_name, expected in EXPECTED_COLUMNS.items():
        assert set(Base.metadata.tables[table_name].columns.keys()) == expected


def test_metadata_declares_named_unique_check_and_foreign_key_constraints() -> None:
    for table_name in sorted(ORCHESTRATION_TABLES):
        table = Base.metadata.tables[table_name]
        assert _unique_constraints(table) == EXPECTED_UNIQUES[table_name]
        assert _foreign_keys(table) == EXPECTED_FOREIGN_KEYS[table_name]
        assert set(_checks(table)) == EXPECTED_CHECK_NAMES[table_name]


def test_orchestration_vocabulary_and_cross_field_checks_are_frozen() -> None:
    orchestration_checks = _checks(Base.metadata.tables["research_orchestrations"])
    assert all(
        value in orchestration_checks["ck_research_orchestrations_state"]
        for value in (
            "intake",
            "awaiting_scope_confirmation",
            "planning_acquisition",
            "acquiring",
            "freezing_sources",
            "assessing_coverage",
            "synthesizing_evidence",
            "adjudicating_thesis",
            "generating_report",
            "monitoring",
            "retry_wait",
            "recovering",
            "needs_scope_decision",
            "exhausted",
            "cancelled",
            "failed",
        )
    )
    assert all(
        value in orchestration_checks["ck_research_orchestrations_user_stage"]
        for value in (
            "intake",
            "scope_confirmation",
            "acquisition",
            "evidence_synthesis",
            "thesis_adjudication",
            "report_monitoring",
        )
    )
    assert "next_action_kind IS NULL AND next_action_label IS NULL" in (
        orchestration_checks["ck_research_orchestrations_next_action"]
    )
    assert "next_action_kind IS NOT NULL AND next_action_label IS NOT NULL" in (
        orchestration_checks["ck_research_orchestrations_next_action"]
    )

    plan_checks = _checks(Base.metadata.tables["acquisition_query_plans"])
    lineage = plan_checks["ck_acquisition_query_plans_round_lineage"]
    assert "acquisition_round = 1" in lineage
    assert "previous_query_plan_id IS NULL" in lineage
    assert "previous_acquisition_round IS NULL" in lineage
    assert "acquisition_round > 1" in lineage
    assert "previous_query_plan_id IS NOT NULL" in lineage
    assert "previous_acquisition_round IS NOT NULL" in lineage
    assert "previous_acquisition_round = acquisition_round - 1" in lineage
    assert "expansion_trigger IS NOT NULL" in lineage
    assert "trim(expansion_trigger) <> ''" in lineage

    coverage_checks = _checks(Base.metadata.tables["acquisition_goal_coverages"])
    assert all(
        status in coverage_checks["ck_acquisition_goal_coverages_status"]
        for status in ("ready", "unmet", "exhausted")
    )
    assert "<=" not in coverage_checks[
        "ck_acquisition_goal_coverages_counts_non_negative"
    ]


def test_model_defaults_match_the_persistence_contract() -> None:
    orchestrations = Base.metadata.tables["research_orchestrations"].c
    assert _default_value(orchestrations.state) == "intake"
    assert _default_value(orchestrations.user_stage) == "intake"
    assert _default_value(orchestrations.checkpoint_json) == {}
    assert orchestrations.checkpoint_json.server_default is not None
    assert _default_value(orchestrations.version) == 0
    assert orchestrations.version.server_default is not None

    events = Base.metadata.tables["research_orchestration_events"].c
    plans = Base.metadata.tables["acquisition_query_plans"].c
    coverage = Base.metadata.tables["acquisition_goal_coverages"].c
    assert _default_value(events.payload_json) == {}
    assert _default_value(plans.frozen_inputs_json) == {}
    assert _default_value(plans.ordered_queries_json) == []
    assert _default_value(coverage.required_authority_count) == 0
    assert _default_value(coverage.observed_authority_count) == 0
    assert _default_value(coverage.required_independent_source_count) == 0
    assert _default_value(coverage.observed_independent_source_count) == 0
    assert _default_value(coverage.contrary_search_completed) is False
    assert _default_value(coverage.reason_codes_json) == []
    assert _default_value(coverage.evidence_link_ids_json) == []


def _new_query_plan_schema() -> tuple[
    sa.Engine,
    sa.Table,
    tuple[uuid.UUID, uuid.UUID],
    tuple[uuid.UUID, uuid.UUID],
    datetime,
]:
    engine = sa.create_engine("sqlite://", future=True)
    with engine.connect() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)

    now = datetime.now(UTC)
    case_id = uuid.uuid4()
    thesis_id = uuid.uuid4()
    run_id = uuid.uuid4()
    scope_id = uuid.uuid4()
    job_ids = (uuid.uuid4(), uuid.uuid4())
    series_ids = (uuid.uuid4(), uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["research_cases"].insert(),
            {
                "id": case_id,
                "title": "case",
                "industry_topic": "industry",
                "created_at": now,
                "created_by": "tester",
            },
        )
        connection.execute(
            Base.metadata.tables["event_research_scope_versions"].insert(),
            {
                "id": scope_id,
                "research_case_id": case_id,
                "version": 1,
                "changed_by": "tester",
                "change_summary": "scope",
                "created_at": now,
            },
        )
        connection.execute(
            Base.metadata.tables["theses"].insert(),
            {
                "id": thesis_id,
                "research_case_id": case_id,
                "statement": "thesis",
                "created_at": now,
                "created_by": "tester",
            },
        )
        connection.execute(
            Base.metadata.tables["research_runs"].insert(),
            {
                "id": run_id,
                "research_case_id": case_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            Base.metadata.tables["acquisition_jobs"].insert(),
            [
                {
                    "id": job_id,
                    "tenant_id": "tenant",
                    "research_case_id": case_id,
                    "thesis_id": thesis_id,
                    "research_run_id": run_id,
                    "idempotency_key": f"job-{position}",
                    "created_at": now,
                    "updated_at": now,
                }
                for position, job_id in enumerate(job_ids, start=1)
            ],
        )
        connection.execute(
            Base.metadata.tables["acquisition_series"].insert(),
            [
                {
                    "id": series_id,
                    "tenant_id": "tenant",
                    "research_case_id": case_id,
                    "research_run_id": run_id,
                    "scope_version_id": scope_id,
                    "thesis_id": thesis_id,
                    "goal_id": f"goal-{position}",
                    "created_at": now,
                }
                for position, series_id in enumerate(series_ids, start=1)
            ],
        )
    return (
        engine,
        Base.metadata.tables["acquisition_query_plans"],
        job_ids,
        series_ids,
        now,
    )


def _insert_first_query_plan(
    engine: sa.Engine,
    table: sa.Table,
    *,
    job_id: uuid.UUID,
    series_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID:
    first_plan_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            _query_plan_values(
                plan_id=first_plan_id,
                series_id=series_id,
                job_id=job_id,
                acquisition_round=1,
                previous_query_plan_id=None,
                previous_acquisition_round=None,
                expansion_trigger=None,
                now=now,
            ),
        )
    return first_plan_id


@pytest.mark.parametrize("expansion_trigger", [None, "", "   "])
def test_round_two_query_plan_requires_nonblank_expansion_trigger(
    expansion_trigger: str | None,
) -> None:
    engine, table, job_ids, series_ids, now = _new_query_plan_schema()
    first_plan_id = _insert_first_query_plan(
        engine, table, job_id=job_ids[0], series_id=series_ids[0], now=now
    )

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                table.insert(),
                _query_plan_values(
                    plan_id=uuid.uuid4(),
                    series_id=series_ids[0],
                    job_id=job_ids[1],
                    acquisition_round=2,
                    previous_query_plan_id=first_plan_id,
                    previous_acquisition_round=1,
                    expansion_trigger=expansion_trigger,
                    now=now,
                ),
            )


def test_round_two_query_plan_accepts_nonblank_expansion_trigger() -> None:
    engine, table, job_ids, series_ids, now = _new_query_plan_schema()
    first_plan_id = _insert_first_query_plan(
        engine, table, job_id=job_ids[0], series_id=series_ids[0], now=now
    )
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            _query_plan_values(
                plan_id=uuid.uuid4(),
                series_id=series_ids[0],
                job_id=job_ids[1],
                acquisition_round=2,
                previous_query_plan_id=first_plan_id,
                previous_acquisition_round=1,
                expansion_trigger="coverage gap",
                now=now,
            ),
        )


@pytest.mark.parametrize(
    "lineage_case",
    [
        "cross_series",
        "self_reference",
        "skipped_round",
        "later_round",
        "missing_previous_round",
    ],
)
def test_query_plan_rejects_invalid_predecessor_lineage(lineage_case: str) -> None:
    engine, table, job_ids, series_ids, now = _new_query_plan_schema()
    first_plan_id = _insert_first_query_plan(
        engine, table, job_id=job_ids[0], series_id=series_ids[0], now=now
    )
    candidate_id = uuid.uuid4()
    values = _query_plan_values(
        plan_id=candidate_id,
        series_id=series_ids[0],
        job_id=job_ids[1],
        acquisition_round=2,
        previous_query_plan_id=first_plan_id,
        previous_acquisition_round=1,
        expansion_trigger="coverage gap",
        now=now,
    )
    if lineage_case == "cross_series":
        values["series_id"] = series_ids[1]
    elif lineage_case == "self_reference":
        values["previous_query_plan_id"] = candidate_id
    elif lineage_case == "skipped_round":
        values["acquisition_round"] = 3
        values["previous_acquisition_round"] = 2
    elif lineage_case == "later_round":
        values["previous_acquisition_round"] = 3
    else:
        values["previous_acquisition_round"] = None

    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.execute(table.insert(), values)


def test_append_only_guard_covers_only_orchestration_history_tables() -> None:
    assert APPEND_ONLY_ORCHESTRATION_TABLES <= IMMUTABLE_TABLES
    assert "research_orchestrations" not in IMMUTABLE_TABLES
    assert "acquisition_goal_coverages" not in IMMUTABLE_TABLES

    engine = sa.create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table_name in APPEND_ONLY_ORCHESTRATION_TABLES:
            table = Base.metadata.tables[table_name]
            with pytest.raises(ImmutableLedgerError, match="append-only"):
                connection.execute(table.update().values(id=table.c.id))
            with pytest.raises(ImmutableLedgerError, match="append-only"):
                connection.execute(table.delete())

        orchestration = Base.metadata.tables["research_orchestrations"]
        coverage = Base.metadata.tables["acquisition_goal_coverages"]
        connection.execute(orchestration.update().values(version=1))
        connection.execute(orchestration.delete())
        connection.execute(coverage.update().values(status="ready"))
        connection.execute(coverage.delete())
