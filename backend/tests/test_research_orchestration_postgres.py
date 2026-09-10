"""PostgreSQL migration and immutability checks for orchestration records."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.test_research_orchestration_schema import (
    APPEND_ONLY_ORCHESTRATION_TABLES,
    ORCHESTRATION_TABLES,
)
from tests.test_sqlite_migration_bootstrap import (
    _seed_automatic_assignment_lineage,
)
from app.schemas.v1.research_workflow import WorkflowSystemActionDTO


def _schema_database_url(database_url: str, schema: str) -> str:
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}options=-csearch_path={schema}"


def _trigger_tables(connection: sa.Connection, schema: str) -> set[str]:
    return set(
        connection.execute(
            sa.text(
                "SELECT event_object_table FROM information_schema.triggers "
                "WHERE trigger_schema = :schema "
                "AND trigger_name LIKE 'no_update_%'"
            ),
            {"schema": schema},
        ).scalars()
    )


def _assert_0055_objects(connection: sa.Connection, schema: str) -> None:
    assert ORCHESTRATION_TABLES - {"acquisition_series"} <= set(
        sa.inspect(connection).get_table_names()
    )
    assert connection.execute(
        sa.text("SELECT version_num FROM alembic_version")
    ).scalar_one() == "0055"
    trigger_tables = _trigger_tables(connection, schema)
    assert APPEND_ONLY_ORCHESTRATION_TABLES - {"acquisition_series"} <= trigger_tables
    assert "research_orchestrations" not in trigger_tables
    assert "acquisition_goal_coverages" not in trigger_tables


def _run_migration(
    backend: Path, migration_url: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": migration_url},
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_trigger_rejection(error: sa.exc.DBAPIError, message: str) -> None:
    assert getattr(error.orig, "sqlstate", None) == "P0001"
    assert message in str(error.orig)


_AUTOMATIC_ASSIGNMENT_INSERT = sa.text(
    "INSERT INTO event_research_scope_evidence_assignments "
    "(id, scope_version_id, evidence_link_id, factor_statement, disposition, "
    "assignment_kind, research_run_id, acquisition_goal_id, "
    "automatic_admission_decision_id, automatic_provenance_json, created_at) "
    "VALUES (:id, :scope, :evidence, 'Revenue supports the event thesis', "
    "'mapped', 'automatic', :run, :goal, :decision, "
    "CAST(:provenance AS json), :created_at)"
)


@pytest.mark.pg_only
def test_0060_postgres_backfills_truthful_state_started_at() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0060_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    case_id = uuid.uuid4()
    scope_id = uuid.uuid4()
    orchestration_id = uuid.uuid4()
    now = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    event_at = now + timedelta(minutes=5)
    checkpoint_at = now + timedelta(minutes=15)
    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded = _run_migration(backend, migration_url, "upgrade", "0059")
        assert upgraded.returncode == 0, upgraded.stderr
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO research_cases "
                    "(id, title, industry_topic, created_at, created_by) "
                    "VALUES (:id, 'case', 'industry', :now, 'tester')"
                ),
                {"id": case_id, "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO event_research_scope_versions "
                    "(id, research_case_id, version, changed_by, "
                    "change_summary, created_at) VALUES "
                    "(:id, :case_id, 1, 'tester', 'legacy scope', :now)"
                ),
                {"id": scope_id, "case_id": case_id, "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestrations "
                    "(id, tenant_id, research_case_id, current_scope_version_id, "
                    "state, user_stage, checkpoint_json, recovery_status, "
                    "version, created_at, updated_at) VALUES "
                    "(:id, 'team-a', :case_id, :scope_id, 'acquiring', "
                    "'acquisition', '{}'::json, 'healthy', 3, :now, :now)"
                ),
                {
                    "id": orchestration_id,
                    "case_id": case_id,
                    "scope_id": scope_id,
                    "now": now,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestration_events "
                    "(id, orchestration_id, sequence, transition, actor, "
                    "message, payload_json, idempotency_key, created_at) "
                    "VALUES (:id, :orchestration_id, 1, "
                    "'acquisition_started', 'system', 'acquiring', "
                    "'{\"state\":\"acquiring\","
                    "\"from_state\":\"planning_acquisition\","
                    "\"to_state\":\"acquiring\"}'::json, 'state-start', "
                    ":event_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "orchestration_id": orchestration_id,
                    "event_at": event_at,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestration_events "
                    "(id, orchestration_id, sequence, transition, actor, "
                    "message, payload_json, idempotency_key, created_at) "
                    "VALUES (:id, :orchestration_id, 2, 'coverage_ready', "
                    "'system', 'checkpoint', "
                    "'{\"state\":\"acquiring\","
                    "\"from_state\":\"acquiring\","
                    "\"to_state\":\"acquiring\"}'::json, 'checkpoint', "
                    ":checkpoint_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "orchestration_id": orchestration_id,
                    "checkpoint_at": checkpoint_at,
                },
            )
        upgraded = _run_migration(backend, migration_url, "upgrade", "0060")
        assert upgraded.returncode == 0, upgraded.stderr
        with schema_engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT created_at, state_started_at "
                    "FROM research_orchestrations WHERE id = :id"
                ),
                {"id": orchestration_id},
            ).one()
            assert row.state_started_at == event_at
            assert connection.execute(
                sa.text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = :schema "
                    "AND table_name = 'research_orchestrations' "
                    "AND column_name = 'state_started_at'"
                ),
                {"schema": schema},
            ).scalar_one() == "NO"

        downgraded = _run_migration(backend, migration_url, "downgrade", "0059")
        assert downgraded.returncode == 0, downgraded.stderr
        upgraded = _run_migration(backend, migration_url, "upgrade", "0060")
        assert upgraded.returncode == 0, upgraded.stderr
        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT state_started_at FROM research_orchestrations "
                    "WHERE id = :id"
                ),
                {"id": orchestration_id},
            ).scalar_one() == event_at
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0060_postgres_uses_latest_true_reentry_and_created_at_for_legacy_events() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0060_reentry_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    created_at = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    reentry_at = created_at + timedelta(minutes=25)
    updated_at = created_at + timedelta(minutes=30)
    records = [
        {
            "case_id": uuid.uuid4(),
            "scope_id": uuid.uuid4(),
            "orchestration_id": uuid.uuid4(),
            "suffix": "typed",
        },
        {
            "case_id": uuid.uuid4(),
            "scope_id": uuid.uuid4(),
            "orchestration_id": uuid.uuid4(),
            "suffix": "legacy",
        },
    ]
    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded = _run_migration(backend, migration_url, "upgrade", "0059")
        assert upgraded.returncode == 0, upgraded.stderr
        with schema_engine.begin() as connection:
            for record in records:
                connection.execute(
                    sa.text(
                        "INSERT INTO research_cases "
                        "(id, title, industry_topic, created_at, created_by) "
                        "VALUES (:case_id, :suffix, 'industry', :created_at, 'tester')"
                    ),
                    {**record, "created_at": created_at},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO event_research_scope_versions "
                        "(id, research_case_id, version, changed_by, "
                        "change_summary, created_at) VALUES "
                        "(:scope_id, :case_id, 1, 'tester', 'legacy scope', "
                        ":created_at)"
                    ),
                    {**record, "created_at": created_at},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO research_orchestrations "
                        "(id, tenant_id, research_case_id, "
                        "current_scope_version_id, state, user_stage, "
                        "checkpoint_json, recovery_status, version, created_at, "
                        "updated_at) VALUES "
                        "(:orchestration_id, 'team-a', :case_id, :scope_id, "
                        "'acquiring', 'acquisition', '{}'::json, 'healthy', 3, "
                        ":created_at, :updated_at)"
                    ),
                    {
                        **record,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )

            typed = records[0]
            typed_events = [
                (
                    1,
                    "first-entry",
                    created_at + timedelta(minutes=5),
                    {
                        "state": "acquiring",
                        "from_state": "planning_acquisition",
                        "to_state": "acquiring",
                    },
                ),
                (
                    2,
                    "left-state",
                    created_at + timedelta(minutes=20),
                    {
                        "state": "recovering",
                        "from_state": "acquiring",
                        "to_state": "recovering",
                    },
                ),
                (
                    3,
                    "reentry",
                    reentry_at,
                    {
                        "state": "acquiring",
                        "from_state": "recovering",
                        "to_state": "acquiring",
                    },
                ),
            ]
            for sequence, key, occurred_at, payload in typed_events:
                connection.execute(
                    sa.text(
                        "INSERT INTO research_orchestration_events "
                        "(id, orchestration_id, sequence, transition, actor, "
                        "message, payload_json, idempotency_key, created_at) "
                        "VALUES (:id, :orchestration_id, :sequence, :transition, "
                        "'system', :message, CAST(:payload AS json), :idempotency_key, "
                        ":occurred_at)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "orchestration_id": typed["orchestration_id"],
                        "sequence": sequence,
                        "transition": key,
                        "message": key,
                        "idempotency_key": key,
                        "payload": json.dumps(payload),
                        "occurred_at": occurred_at,
                    },
                )

            legacy = records[1]
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestration_events "
                    "(id, orchestration_id, sequence, transition, actor, "
                    "message, payload_json, idempotency_key, created_at) "
                    "VALUES (:id, :orchestration_id, 1, 'coverage_ready', "
                    "'system', 'legacy checkpoint', "
                    "'{\"state\":\"acquiring\"}'::json, "
                    "'legacy-checkpoint', :occurred_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "orchestration_id": legacy["orchestration_id"],
                    "occurred_at": created_at + timedelta(minutes=15),
                },
            )

        upgraded = _run_migration(backend, migration_url, "upgrade", "0060")
        assert upgraded.returncode == 0, upgraded.stderr
        with schema_engine.connect() as connection:
            rows = dict(
                connection.execute(
                    sa.text(
                        "SELECT id, state_started_at FROM research_orchestrations"
                    )
                ).all()
            )
            assert rows[records[0]["orchestration_id"]] == reentry_at
            assert rows[records[1]["orchestration_id"]] == created_at
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        admin_engine.dispose()


@pytest.mark.pg_only
def test_workflow_wire_serializes_postgres_naive_and_aware_values_as_utc() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"workflow_utc_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "CREATE TABLE workflow_utc_probe ("
                    "naive_value timestamp without time zone NOT NULL, "
                    "aware_value timestamp with time zone NOT NULL)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO workflow_utc_probe "
                    "(naive_value, aware_value) VALUES "
                    "('2030-01-02 03:04:05', "
                    "'2030-01-02 11:04:06+08:00')"
                )
            )
        with schema_engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT naive_value, aware_value FROM workflow_utc_probe"
                )
            ).one()
        payload = json.loads(
            WorkflowSystemActionDTO(
                label="UTC probe",
                reason="PostgreSQL wire normalization",
                started_at=row.naive_value,
                heartbeat_at=row.aware_value,
                lease_expires_at=None,
                retry_at=None,
                recovery_status=None,
            ).model_dump_json()
        )
        assert payload["started_at"] == "2030-01-02T03:04:05Z"
        assert payload["heartbeat_at"] == "2030-01-02T03:04:06Z"
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
        admin_engine.dispose()


def _automatic_assignment_values(lineage: dict) -> dict:
    return {
        "id": uuid.uuid4(),
        "scope": lineage["scope"],
        "evidence": lineage["evidence"],
        "run": lineage["run"],
        "goal": lineage["goal"],
        "decision": lineage["decision"],
        "provenance": json.dumps(
            {
                "policy_version": "event-goal-coverage-v1",
                "mapping_scope": "factor",
                "goal_id": lineage["goal"],
                "job_id": str(lineage["job"]),
                "admission_decision_id": str(lineage["decision"]),
            }
        ),
        "created_at": datetime(2026, 8, 15, tzinfo=UTC),
    }


@contextmanager
def _postgres_0058_lineage_schema():
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0058_concurrency_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded = _run_migration(backend, migration_url, "upgrade", "0057")
        assert upgraded.returncode == 0, upgraded.stderr
        lineage = _seed_automatic_assignment_lineage(
            schema_engine, tenant_id="team-a"
        )
        upgraded = _run_migration(backend, migration_url, "upgrade", "0058")
        assert upgraded.returncode == 0, upgraded.stderr
        yield schema_engine, lineage
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _run_concurrent_statement(
    engine: sa.Engine,
    statement: sa.TextClause,
    parameters: dict,
    *,
    ready: threading.Event,
    start: threading.Event,
    done: threading.Event,
    state: dict,
) -> None:
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            state["pid"] = connection.execute(
                sa.text("SELECT pg_backend_pid()")
            ).scalar_one()
            ready.set()
            if not start.wait(5):
                raise AssertionError("concurrent statement was not released")
            try:
                connection.execute(statement, parameters)
                transaction.commit()
                state["committed"] = True
            except sa.exc.DBAPIError as exc:
                transaction.rollback()
                state["error"] = exc
    except BaseException as exc:  # pragma: no cover - surfaced in caller
        state["thread_error"] = exc
    finally:
        done.set()


def _wait_for_postgres_blocker(
    engine: sa.Engine,
    *,
    pid: int,
    done: threading.Event,
) -> bool:
    deadline = time.monotonic() + 5
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            blocked = observer.execute(
                sa.text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                {"pid": pid},
            ).scalar_one()
            if blocked:
                return True
            if done.is_set():
                return False
    raise AssertionError("concurrent statement neither blocked nor completed")


def _start_concurrent_statement(
    engine: sa.Engine,
    statement: sa.TextClause,
    parameters: dict,
) -> tuple[threading.Thread, threading.Event, dict]:
    ready = threading.Event()
    start = threading.Event()
    done = threading.Event()
    state: dict = {}
    thread = threading.Thread(
        target=_run_concurrent_statement,
        args=(engine, statement, parameters),
        kwargs={"ready": ready, "start": start, "done": done, "state": state},
        daemon=True,
    )
    thread.start()
    assert ready.wait(5), "concurrent database connection was not ready"
    start.set()
    return thread, done, state


def _join_concurrent_statement(
    thread: threading.Thread,
    done: threading.Event,
    state: dict,
) -> None:
    assert done.wait(10), "concurrent database statement hung"
    thread.join(timeout=1)
    assert not thread.is_alive(), "concurrent database thread did not stop"
    if "thread_error" in state:
        raise state["thread_error"]


def _job_snapshot_assignment_lineage_is_valid(engine: sa.Engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT NOT EXISTS ("
                "SELECT 1 FROM event_research_scope_evidence_assignments a "
                "JOIN automatic_admission_decisions d "
                "ON d.id = a.automatic_admission_decision_id "
                "JOIN acquisition_jobs j ON j.id = d.job_id "
                "WHERE a.assignment_kind = 'automatic' AND ("
                "j.request_snapshot->>'goal_id' IS DISTINCT FROM "
                "a.acquisition_goal_id OR "
                "j.request_snapshot->>'research_run_id' IS DISTINCT FROM "
                "a.research_run_id::text OR "
                "j.request_snapshot->>'scope_version_id' IS DISTINCT FROM "
                "a.scope_version_id::text))"
            )
        ).scalar_one()


def _seed_0055_acquisition_identity(
    connection: sa.Connection,
    *,
    ids: dict[str, uuid.UUID],
    now: datetime,
    job_names: tuple[str, ...],
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO research_cases "
            "(id, title, industry_topic, created_at, created_by) "
            "VALUES (:id, 'case', 'industry', :now, 'tester')"
        ),
        {"id": ids["case"], "now": now},
    )
    connection.execute(
        sa.text(
            "INSERT INTO theses "
            "(id, research_case_id, statement, research_protocol_required, "
            "created_at, created_by, creator_type, review_state) "
            "VALUES (:id, :case_id, 'thesis', false, :now, 'tester', "
            "'human', 'confirmed')"
        ),
        {"id": ids["thesis"], "case_id": ids["case"], "now": now},
    )
    connection.execute(
        sa.text(
            "INSERT INTO research_runs "
            "(id, research_case_id, status, stage, round, max_rounds, budget, "
            "budget_used, created_at, updated_at) "
            "VALUES (:id, :case_id, 'queued', 'planning', 0, 3, 100, 0, "
            ":now, :now)"
        ),
        {"id": ids["run"], "case_id": ids["case"], "now": now},
    )
    connection.execute(
        sa.text(
            "INSERT INTO event_research_scope_versions "
            "(id, research_case_id, version, changed_by, change_summary, "
            "created_at) VALUES (:id, :case_id, 1, 'tester', 'initial', :now)"
        ),
        {"id": ids["scope"], "case_id": ids["case"], "now": now},
    )
    connection.execute(
        sa.text(
            "INSERT INTO acquisition_jobs "
            "(id, tenant_id, research_case_id, thesis_id, research_run_id, "
            "idempotency_key, request_snapshot, policy_snapshot, status, stage, "
            "attempt, reference_count, fetched_count, frozen_count, "
            "admitted_count, exception_count, created_at, updated_at) "
            "VALUES (:id, 'tenant', :case_id, :thesis_id, :run_id, :key, "
            "'{}'::json, '{}'::json, 'queued', 'queued', 0, 0, 0, 0, 0, 0, "
            ":now, :now)"
        ),
        [
            {
                "id": ids[name],
                "case_id": ids["case"],
                "thesis_id": ids["thesis"],
                "run_id": ids["run"],
                "key": name,
                "now": now,
            }
            for name in job_names
        ],
    )


QUERY_PLAN_INSERT = sa.text(
    "INSERT INTO acquisition_query_plans "
    "(id, acquisition_job_id, acquisition_round, goal_id, planner_version, "
    "policy_version, frozen_inputs_json, ordered_queries_json, "
    "previous_query_plan_id, previous_acquisition_round, expansion_trigger, "
    "diff_json, created_at) "
    "VALUES (:id, :job_id, :round, 'goal-1', 'planner-v1', 'policy-v1', "
    "'{}'::json, '[]'::json, :previous_id, :previous_round, :trigger, "
    ":diff_json, :now)"
)


@pytest.mark.pg_only
def test_0055_postgres_lineage_immutability_and_migration_cycle() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0055_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    ids = {name: uuid.uuid4() for name in (
        "case",
        "thesis",
        "run",
        "scope",
        "job",
        "other_job",
        "orchestration",
        "event",
        "plan",
        "other_plan",
        "round_two_plan",
        "cross_job_plan",
        "self_plan",
        "skipped_plan",
        "mutable_orchestration",
        "mutable_coverage",
    )}
    now = datetime.now(UTC)

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))

        upgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0055"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert upgraded.returncode == 0, upgraded.stderr

        with schema_engine.connect() as connection:
            _assert_0055_objects(connection, schema)

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO research_cases "
                    "(id, title, industry_topic, created_at, created_by) "
                    "VALUES (:id, 'case', 'industry', :now, 'tester')"
                ),
                {"id": ids["case"], "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO theses "
                    "(id, research_case_id, statement, research_protocol_required, "
                    "created_at, created_by, creator_type, review_state) "
                    "VALUES (:id, :case_id, 'thesis', false, :now, 'tester', "
                    "'human', 'confirmed')"
                ),
                {"id": ids["thesis"], "case_id": ids["case"], "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_runs "
                    "(id, research_case_id, status, stage, round, max_rounds, "
                    "budget, budget_used, created_at, updated_at) "
                    "VALUES (:id, :case_id, 'queued', 'planning', 0, 3, 100, 0, "
                    ":now, :now)"
                ),
                {"id": ids["run"], "case_id": ids["case"], "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO event_research_scope_versions "
                    "(id, research_case_id, version, changed_by, change_summary, created_at) "
                    "VALUES (:id, :case_id, 1, 'tester', 'initial', :now)"
                ),
                {"id": ids["scope"], "case_id": ids["case"], "now": now},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO acquisition_jobs "
                    "(id, tenant_id, research_case_id, thesis_id, research_run_id, "
                    "idempotency_key, request_snapshot, policy_snapshot, status, "
                    "stage, attempt, reference_count, fetched_count, frozen_count, "
                    "admitted_count, exception_count, created_at, updated_at) "
                    "VALUES (:id, 'tenant', :case_id, :thesis_id, :run_id, :key, "
                    "'{}'::json, '{}'::json, 'queued', 'queued', 0, 0, 0, 0, 0, 0, "
                    ":now, :now)"
                ),
                [
                    {
                        "id": job_id,
                        "case_id": ids["case"],
                        "thesis_id": ids["thesis"],
                        "run_id": ids["run"],
                        "key": key,
                        "now": now,
                    }
                    for job_id, key in (
                        (ids["job"], "job-1"),
                        (ids["other_job"], "job-2"),
                    )
                ],
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestrations "
                    "(id, tenant_id, research_case_id, current_scope_version_id, "
                    "current_research_run_id, state, user_stage, checkpoint_json, "
                    "version, created_at, updated_at) "
                    "VALUES (:id, 'tenant', :case_id, :scope_id, :run_id, 'intake', "
                    "'intake', '{}'::json, 0, :now, :now)"
                ),
                {
                    "id": ids["orchestration"],
                    "case_id": ids["case"],
                    "scope_id": ids["scope"],
                    "run_id": ids["run"],
                    "now": now,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO acquisition_goal_coverages "
                    "(id, research_run_id, scope_version_id, thesis_id, goal_id, "
                    "objective, status, required_authority_count, "
                    "observed_authority_count, required_independent_source_count, "
                    "observed_independent_source_count, contrary_search_completed, "
                    "reason_codes_json, evidence_link_ids_json, created_at, updated_at) "
                    "VALUES (:id, :run_id, :scope_id, :thesis_id, 'goal-1', "
                    "'objective', 'unmet', 1, 0, 2, 0, false, '[]'::json, "
                    "'[]'::json, :now, :now)"
                ),
                {
                    "id": ids["mutable_coverage"],
                    "run_id": ids["run"],
                    "scope_id": ids["scope"],
                    "thesis_id": ids["thesis"],
                    "now": now,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestration_events "
                    "(id, orchestration_id, sequence, transition, actor, message, "
                    "payload_json, idempotency_key, created_at) "
                    "VALUES (:id, :orchestration_id, 1, 'created', 'system', "
                    "'created', '{}'::json, 'event-1', :now)"
                ),
                {
                    "id": ids["event"],
                    "orchestration_id": ids["orchestration"],
                    "now": now,
                },
            )
            connection.execute(
                QUERY_PLAN_INSERT,
                [
                    {
                        "id": ids["plan"],
                        "job_id": ids["job"],
                        "round": 1,
                        "previous_id": None,
                        "previous_round": None,
                        "trigger": None,
                        "diff_json": None,
                        "now": now,
                    },
                    {
                        "id": ids["other_plan"],
                        "job_id": ids["other_job"],
                        "round": 1,
                        "previous_id": None,
                        "previous_round": None,
                        "trigger": None,
                        "diff_json": None,
                        "now": now,
                    },
                    {
                        "id": ids["round_two_plan"],
                        "job_id": ids["job"],
                        "round": 2,
                        "previous_id": ids["plan"],
                        "previous_round": 1,
                        "trigger": "coverage gap",
                        "diff_json": "{}",
                        "now": now,
                    },
                ],
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestrations "
                    "(id, tenant_id, research_case_id, state, user_stage, "
                    "checkpoint_json, version, created_at, updated_at) "
                    "VALUES (:id, 'mutable-tenant', :case_id, 'intake', 'intake', "
                    "'{}'::json, 0, :now, :now)"
                ),
                {
                    "id": ids["mutable_orchestration"],
                    "case_id": ids["case"],
                    "now": now,
                },
            )

        invalid_lineages = (
            {
                "id": ids["cross_job_plan"],
                "job_id": ids["other_job"],
                "round": 2,
                "previous_id": ids["plan"],
                "previous_round": 1,
                "trigger": "coverage gap",
                "diff_json": "{}",
                "now": now,
            },
            {
                "id": ids["self_plan"],
                "job_id": ids["other_job"],
                "round": 2,
                "previous_id": ids["self_plan"],
                "previous_round": 1,
                "trigger": "coverage gap",
                "diff_json": "{}",
                "now": now,
            },
            {
                "id": ids["skipped_plan"],
                "job_id": ids["job"],
                "round": 3,
                "previous_id": ids["plan"],
                "previous_round": 2,
                "trigger": "coverage gap",
                "diff_json": "{}",
                "now": now,
            },
        )
        for invalid_lineage in invalid_lineages:
            with pytest.raises(sa.exc.IntegrityError):
                with schema_engine.begin() as connection:
                    connection.execute(QUERY_PLAN_INSERT, invalid_lineage)

        immutable_rows = {
            "research_orchestration_events": ids["event"],
            "acquisition_query_plans": ids["plan"],
        }
        for table_name, row_id in immutable_rows.items():
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f'UPDATE "{table_name}" SET id = id WHERE id = :id'),
                        {"id": row_id},
                    )
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f'DELETE FROM "{table_name}" WHERE id = :id'),
                        {"id": row_id},
                    )

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE research_orchestrations SET version = 1 WHERE id = :id"
                ),
                {"id": ids["mutable_orchestration"]},
            )
            connection.execute(
                sa.text("DELETE FROM research_orchestrations WHERE id = :id"),
                {"id": ids["mutable_orchestration"]},
            )
            connection.execute(
                sa.text(
                    "UPDATE acquisition_goal_coverages SET status = 'ready' "
                    "WHERE id = :id"
                ),
                {"id": ids["mutable_coverage"]},
            )
            connection.execute(
                sa.text("DELETE FROM acquisition_goal_coverages WHERE id = :id"),
                {"id": ids["mutable_coverage"]},
            )

        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT count(*) FROM research_orchestrations WHERE id = :id"),
                {"id": ids["mutable_orchestration"]},
            ).scalar_one() == 0
            assert connection.execute(
                sa.text(
                    "SELECT count(*) FROM acquisition_goal_coverages WHERE id = :id"
                ),
                {"id": ids["mutable_coverage"]},
            ).scalar_one() == 0

        downgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "0054"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert downgraded.returncode == 0, downgraded.stderr

        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0054"
            assert ORCHESTRATION_TABLES.isdisjoint(
                sa.inspect(connection).get_table_names()
            )
            assert APPEND_ONLY_ORCHESTRATION_TABLES.isdisjoint(
                _trigger_tables(connection, schema)
            )

        reupgraded = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "0055"],
            cwd=backend,
            env={**os.environ, "DATABASE_URL": migration_url},
            text=True,
            capture_output=True,
            check=False,
        )
        assert reupgraded.returncode == 0, reupgraded.stderr

        with schema_engine.connect() as connection:
            _assert_0055_objects(connection, schema)
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0056_postgres_cross_job_series_lineage_and_downgrade_guard() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0056_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    ids = {
        name: uuid.uuid4()
        for name in (
            "case",
            "thesis",
            "run",
            "scope",
            "job_one",
            "job_two",
            "job_cross_series",
            "series_one",
            "series_two",
            "plan_one",
            "plan_two",
            "plan_cross_series",
        )
    }
    now = datetime.now(UTC)
    plan_insert = sa.text(
        "INSERT INTO acquisition_query_plans "
        "(id, series_id, acquisition_job_id, acquisition_round, goal_id, "
        "planner_version, policy_version, frozen_inputs_json, "
        "ordered_queries_json, previous_query_plan_id, "
        "previous_acquisition_round, expansion_trigger, diff_json, created_at) "
        "VALUES (:id, :series_id, :job_id, :round, :goal_id, 'planner-v1', "
        "'policy-v1', '{}'::json, CAST(:queries AS json), :previous_id, "
        ":previous_round, :trigger, CAST(:diff_json AS json), :now)"
    )

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded_0055 = _run_migration(backend, migration_url, "upgrade", "0055")
        assert upgraded_0055.returncode == 0, upgraded_0055.stderr
        with schema_engine.begin() as connection:
            _seed_0055_acquisition_identity(
                connection,
                ids=ids,
                now=now,
                job_names=("job_one", "job_two", "job_cross_series"),
            )

        upgraded_0056 = _run_migration(backend, migration_url, "upgrade", "0056")
        assert upgraded_0056.returncode == 0, upgraded_0056.stderr
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO acquisition_series "
                    "(id, tenant_id, research_case_id, research_run_id, "
                    "scope_version_id, thesis_id, goal_id, created_at) "
                    "VALUES (:id, 'tenant', :case_id, :run_id, :scope_id, "
                    ":thesis_id, :goal_id, :now)"
                ),
                [
                    {
                        "id": ids["series_one"],
                        "case_id": ids["case"],
                        "run_id": ids["run"],
                        "scope_id": ids["scope"],
                        "thesis_id": ids["thesis"],
                        "goal_id": "goal-1",
                        "now": now,
                    },
                    {
                        "id": ids["series_two"],
                        "case_id": ids["case"],
                        "run_id": ids["run"],
                        "scope_id": ids["scope"],
                        "thesis_id": ids["thesis"],
                        "goal_id": "goal-2",
                        "now": now,
                    },
                ],
            )
            connection.execute(
                plan_insert,
                {
                    "id": ids["plan_one"],
                    "series_id": ids["series_one"],
                    "job_id": ids["job_one"],
                    "round": 1,
                    "goal_id": "goal-1",
                    "queries": '[{"query":"revenue"}]',
                    "previous_id": None,
                    "previous_round": None,
                    "trigger": None,
                    "diff_json": None,
                    "now": now,
                },
            )
            connection.execute(
                plan_insert,
                {
                    "id": ids["plan_two"],
                    "series_id": ids["series_one"],
                    "job_id": ids["job_two"],
                    "round": 2,
                    "goal_id": "goal-1",
                    "queries": '[{"query":"revenue"},{"query":"margin"}]',
                    "previous_id": ids["plan_one"],
                    "previous_round": 1,
                    "trigger": "coverage_gap",
                    "diff_json": (
                        '{"added_queries":[{"query":"margin"}],'
                        '"removed_queries":[],"reason":"missing margin"}'
                    ),
                    "now": now,
                },
            )
            lineage = connection.execute(
                sa.text(
                    "SELECT acquisition_job_id, previous_query_plan_id, "
                    "previous_acquisition_round FROM acquisition_query_plans "
                    "WHERE id = :id"
                ),
                {"id": ids["plan_two"]},
            ).one()
            assert lineage == (
                ids["job_two"],
                ids["plan_one"],
                1,
            )

        with pytest.raises(
            sa.exc.IntegrityError,
            match="fk_acquisition_query_plans_previous_plan_lineage",
        ):
            with schema_engine.begin() as connection:
                connection.execute(
                    plan_insert,
                    {
                        "id": ids["plan_cross_series"],
                        "series_id": ids["series_two"],
                        "job_id": ids["job_cross_series"],
                        "round": 2,
                        "goal_id": "goal-2",
                        "queries": '[{"query":"margin"}]',
                        "previous_id": ids["plan_one"],
                        "previous_round": 1,
                        "trigger": "coverage_gap",
                        "diff_json": (
                            '{"added_queries":[{"query":"margin"}],'
                            '"removed_queries":[],"reason":"missing margin"}'
                        ),
                        "now": now,
                    },
                )

        for table_name, row_id in (
            ("acquisition_series", ids["series_one"]),
            ("acquisition_query_plans", ids["plan_one"]),
        ):
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f'UPDATE "{table_name}" SET id = id WHERE id = :id'),
                        {"id": row_id},
                    )
            with pytest.raises(sa.exc.DBAPIError, match="append-only"):
                with schema_engine.begin() as connection:
                    connection.execute(
                        sa.text(f'DELETE FROM "{table_name}" WHERE id = :id'),
                        {"id": row_id},
                    )

        downgraded = _run_migration(backend, migration_url, "downgrade", "0055")
        assert downgraded.returncode != 0
        assert "cannot downgrade 0056 while cross-job" in downgraded.stderr
        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0056"
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0056_postgres_rejects_unverifiable_legacy_query_plan_rows() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0056_legacy_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]
    ids = {
        name: uuid.uuid4()
        for name in ("case", "thesis", "run", "scope", "job", "plan")
    }
    now = datetime.now(UTC)

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded_0055 = _run_migration(backend, migration_url, "upgrade", "0055")
        assert upgraded_0055.returncode == 0, upgraded_0055.stderr
        with schema_engine.begin() as connection:
            _seed_0055_acquisition_identity(
                connection,
                ids=ids,
                now=now,
                job_names=("job",),
            )
            connection.execute(
                QUERY_PLAN_INSERT,
                {
                    "id": ids["plan"],
                    "job_id": ids["job"],
                    "round": 1,
                    "previous_id": None,
                    "previous_round": None,
                    "trigger": None,
                    "diff_json": None,
                    "now": now,
                },
            )

        rejected = _run_migration(backend, migration_url, "upgrade", "0056")
        assert rejected.returncode != 0
        assert "cannot infer real run/scope" in rejected.stderr
        with schema_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0055"
            assert "acquisition_series" not in sa.inspect(connection).get_table_names()
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0057_postgres_adds_coverage_lineage_and_preserves_assignment_immutability() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0057_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded_0056 = _run_migration(backend, migration_url, "upgrade", "0056")
        assert upgraded_0056.returncode == 0, upgraded_0056.stderr
        upgraded_0057 = _run_migration(backend, migration_url, "upgrade", "0057")
        assert upgraded_0057.returncode == 0, upgraded_0057.stderr

        with schema_engine.connect() as connection:
            inspector = sa.inspect(connection)
            assignment_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "event_research_scope_evidence_assignments"
                )
            }
            assert {
                "assignment_kind",
                "research_run_id",
                "acquisition_goal_id",
                "automatic_admission_decision_id",
                "automatic_provenance_json",
            } <= assignment_columns
            coverage_columns = {
                column["name"]
                for column in inspector.get_columns("acquisition_goal_coverages")
            }
            assert {
                "policy_version",
                "required_authority_levels_json",
                "observed_authority_levels_json",
                "independent_source_identities_json",
                "conflict_details_json",
                "unknown_details_json",
                "evaluation_round",
                "zero_new_independent_source_rounds",
            } <= coverage_columns
            assert (
                "event_research_scope_evidence_assignments"
                in _trigger_tables(connection, schema)
            )
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0057"

        downgraded = _run_migration(backend, migration_url, "downgrade", "0056")
        assert downgraded.returncode == 0, downgraded.stderr
        with schema_engine.connect() as connection:
            assignment_columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns(
                    "event_research_scope_evidence_assignments"
                )
            }
            assert "acquisition_goal_id" not in assignment_columns
            assert (
                "event_research_scope_evidence_assignments"
                in _trigger_tables(connection, schema)
            )
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0058_postgres_enforces_assignment_lineage_and_refuses_populated_downgrade() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0058_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded = _run_migration(backend, migration_url, "upgrade", "0057")
        assert upgraded.returncode == 0, upgraded.stderr
        first = _seed_automatic_assignment_lineage(schema_engine, tenant_id="team-a")
        second = _seed_automatic_assignment_lineage(schema_engine, tenant_id="team-b")
        upgraded = _run_migration(backend, migration_url, "upgrade", "0058")
        assert upgraded.returncode == 0, upgraded.stderr

        insert_sql = sa.text(
            "INSERT INTO event_research_scope_evidence_assignments "
            "(id, scope_version_id, evidence_link_id, factor_statement, disposition, "
            "assignment_kind, research_run_id, acquisition_goal_id, "
            "automatic_admission_decision_id, automatic_provenance_json, created_at) "
            "VALUES (:id, :scope, :evidence, 'Revenue supports the event thesis', "
            "'mapped', 'automatic', :run, :goal, :decision, "
            "CAST(:provenance AS json), :created_at)"
        )

        def values(**overrides):
            payload = {
                "id": uuid.uuid4(),
                "scope": first["scope"],
                "evidence": first["evidence"],
                "run": first["run"],
                "goal": first["goal"],
                "decision": first["decision"],
                "provenance": json.dumps(
                    {
                        "policy_version": "event-goal-coverage-v1",
                        "mapping_scope": "factor",
                        "goal_id": first["goal"],
                        "job_id": str(first["job"]),
                        "admission_decision_id": str(first["decision"]),
                    }
                ),
                "created_at": datetime(2026, 8, 15, tzinfo=UTC),
            }
            payload.update(overrides)
            return payload

        invalid = [
            {"decision": second["decision"]},
            {"evidence": second["evidence"]},
            {"run": second["run"]},
            {"scope": second["scope"]},
            {"goal": second["goal"]},
            {
                "decision": second["decision"],
                "evidence": second["evidence"],
            },
        ]
        for overrides in invalid:
            with pytest.raises(sa.exc.DBAPIError):
                with schema_engine.begin() as connection:
                    connection.execute(insert_sql, values(**overrides))

        valid = values()
        with schema_engine.begin() as connection:
            connection.execute(insert_sql, valid)
        with pytest.raises(sa.exc.IntegrityError):
            with schema_engine.begin() as connection:
                connection.execute(insert_sql, values())
        with pytest.raises(sa.exc.DBAPIError):
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE event_research_scope_evidence_assignments "
                        "SET disposition = 'unmapped' WHERE id = :id"
                    ),
                    {"id": valid["id"]},
                )

        parent_mutations = [
            (
                "UPDATE acquisition_jobs SET request_snapshot = jsonb_set("
                "request_snapshot::jsonb, '{goal_id}', "
                "to_jsonb(CAST(:value AS text)), false)::json WHERE id = :job",
                {
                    "job": first["job"],
                    "value": "forged-goal",
                },
                "automatic evidence assignment parent lineage mismatch",
            ),
            (
                "UPDATE acquisition_jobs SET request_snapshot = jsonb_set("
                "request_snapshot::jsonb, '{research_run_id}', "
                "to_jsonb(CAST(:value AS text)), false)::json WHERE id = :job",
                {
                    "job": first["job"],
                    "value": str(second["run"]),
                },
                "automatic evidence assignment parent lineage mismatch",
            ),
            (
                "UPDATE acquisition_jobs SET request_snapshot = jsonb_set("
                "request_snapshot::jsonb, '{scope_version_id}', "
                "to_jsonb(CAST(:value AS text)), false)::json WHERE id = :job",
                {
                    "job": first["job"],
                    "value": str(second["scope"]),
                },
                "automatic evidence assignment parent lineage mismatch",
            ),
            (
                "UPDATE acquisition_series SET goal_id = 'forged-goal' "
                "WHERE goal_id = :goal",
                {"goal": first["goal"]},
                "table acquisition_series is append-only",
            ),
            (
                "UPDATE automatic_admission_decisions SET job_id = :other_job "
                "WHERE id = :decision",
                {
                    "other_job": second["job"],
                    "decision": first["decision"],
                },
                "table automatic_admission_decisions is append-only",
            ),
            (
                "DELETE FROM acquisition_query_plans "
                "WHERE acquisition_job_id = :job",
                {"job": first["job"]},
                "table acquisition_query_plans is append-only",
            ),
        ]
        for statement, parameters, expected_message in parent_mutations:
            with pytest.raises(sa.exc.DBAPIError) as raised:
                with schema_engine.begin() as connection:
                    connection.execute(sa.text(statement), parameters)
            _assert_trigger_rejection(raised.value, expected_message)

        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE acquisition_jobs SET status = 'running', "
                    "stage = 'searching', attempt = attempt + 1, "
                    "error_code = 'retryable', error_detail = 'retry planned' "
                    "WHERE id = :job"
                ),
                {"job": second["job"]},
            )
        with schema_engine.connect() as connection:
            mutable = connection.execute(
                sa.text(
                    "SELECT status, stage, attempt, error_code "
                    "FROM acquisition_jobs WHERE id = :job"
                ),
                {"job": second["job"]},
            ).one()
        assert mutable == ("running", "searching", 2, "retryable")
        with pytest.raises(sa.exc.DBAPIError):
            with schema_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "DELETE FROM event_research_scope_evidence_assignments "
                        "WHERE id = :id"
                    ),
                    {"id": valid["id"]},
                )

        downgraded = _run_migration(
            backend, migration_url, "downgrade", "0057"
        )
        assert downgraded.returncode != 0
        assert "automatic evidence provenance" in downgraded.stderr
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0058_assignment_insert_serializes_before_job_snapshot_mutation() -> None:
    with _postgres_0058_lineage_schema() as (schema_engine, lineage):
        assignment = _automatic_assignment_values(lineage)
        first = schema_engine.connect()
        first_transaction = first.begin()
        try:
            first.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            first.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            first.execute(_AUTOMATIC_ASSIGNMENT_INSERT, assignment)
            thread, done, state = _start_concurrent_statement(
                schema_engine,
                sa.text(
                    "UPDATE acquisition_jobs SET request_snapshot = jsonb_set("
                    "request_snapshot::jsonb, '{goal_id}', "
                    "to_jsonb(CAST(:value AS text)), false)::json "
                    "WHERE id = :job"
                ),
                {"job": lineage["job"], "value": "forged-goal"},
            )
            blocked = _wait_for_postgres_blocker(
                schema_engine, pid=state["pid"], done=done
            )
            first_transaction.commit()
            _join_concurrent_statement(thread, done, state)
        finally:
            if first_transaction.is_active:
                first_transaction.rollback()
            first.close()

        assert blocked is True
        assert state.get("committed") is not True
        assert isinstance(state.get("error"), sa.exc.DBAPIError)
        _assert_trigger_rejection(
            state["error"],
            "automatic evidence assignment parent lineage mismatch",
        )
        assert _job_snapshot_assignment_lineage_is_valid(schema_engine)


@pytest.mark.pg_only
def test_0058_job_snapshot_mutation_serializes_before_assignment_insert() -> None:
    with _postgres_0058_lineage_schema() as (schema_engine, lineage):
        first = schema_engine.connect()
        first_transaction = first.begin()
        try:
            first.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            first.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            first.execute(
                sa.text(
                    "UPDATE acquisition_jobs SET request_snapshot = jsonb_set("
                    "request_snapshot::jsonb, '{goal_id}', "
                    "to_jsonb(CAST(:value AS text)), false)::json "
                    "WHERE id = :job"
                ),
                {"job": lineage["job"], "value": "forged-goal"},
            )
            thread, done, state = _start_concurrent_statement(
                schema_engine,
                _AUTOMATIC_ASSIGNMENT_INSERT,
                _automatic_assignment_values(lineage),
            )
            blocked = _wait_for_postgres_blocker(
                schema_engine, pid=state["pid"], done=done
            )
            first_transaction.commit()
            _join_concurrent_statement(thread, done, state)
        finally:
            if first_transaction.is_active:
                first_transaction.rollback()
            first.close()

        assert blocked is True
        assert state.get("committed") is not True
        assert isinstance(state.get("error"), sa.exc.DBAPIError)
        _assert_trigger_rejection(
            state["error"], "automatic evidence assignment lineage mismatch"
        )
        assert _job_snapshot_assignment_lineage_is_valid(schema_engine)


@pytest.mark.pg_only
def test_0058_assignment_insert_locks_every_lineage_parent_row() -> None:
    with _postgres_0058_lineage_schema() as (schema_engine, lineage):
        parent_updates = {
            "scope": (
                "UPDATE event_research_scope_versions SET research_case_id = "
                "research_case_id WHERE id = :scope",
                {"scope": lineage["scope"]},
            ),
            "run": (
                "UPDATE research_runs SET research_case_id = research_case_id "
                "WHERE id = :run",
                {"run": lineage["run"]},
            ),
            "evidence": (
                "UPDATE evidence_links SET review_state = review_state "
                "WHERE id = :evidence",
                {"evidence": lineage["evidence"]},
            ),
            "admission": (
                "UPDATE automatic_admission_decisions SET outcome = outcome "
                "WHERE id = :decision",
                {"decision": lineage["decision"]},
            ),
            "job": (
                "UPDATE acquisition_jobs SET request_snapshot = request_snapshot "
                "WHERE id = :job",
                {"job": lineage["job"]},
            ),
            "plan": (
                "UPDATE acquisition_query_plans SET goal_id = goal_id "
                "WHERE acquisition_job_id = :job",
                {"job": lineage["job"]},
            ),
            "series": (
                "UPDATE acquisition_series SET goal_id = goal_id "
                "WHERE goal_id = :goal",
                {"goal": lineage["goal"]},
            ),
            "thesis": (
                "UPDATE theses SET statement = statement WHERE id = :thesis",
                {"thesis": lineage["thesis"]},
            ),
            "tenant": (
                "UPDATE case_tenant_admissions SET tenant_id = tenant_id "
                "WHERE research_case_id = :case AND tenant_id = 'team-a'",
                {"case": lineage["case"]},
            ),
            "factor": (
                "UPDATE event_research_scope_factors SET statement = statement "
                "WHERE scope_version_id = :scope",
                {"scope": lineage["scope"]},
            ),
        }
        blocked_parents: set[str] = set()
        for parent, (statement, parameters) in parent_updates.items():
            first = schema_engine.connect()
            first_transaction = first.begin()
            try:
                first.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
                first.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
                first.execute(
                    _AUTOMATIC_ASSIGNMENT_INSERT,
                    _automatic_assignment_values(lineage),
                )
                thread, done, state = _start_concurrent_statement(
                    schema_engine, sa.text(statement), parameters
                )
                if _wait_for_postgres_blocker(
                    schema_engine, pid=state["pid"], done=done
                ):
                    blocked_parents.add(parent)
                first_transaction.rollback()
                _join_concurrent_statement(thread, done, state)
            finally:
                if first_transaction.is_active:
                    first_transaction.rollback()
                first.close()

        assert blocked_parents == set(parent_updates)


@pytest.mark.pg_only
def test_0058_postgres_rejects_lineage_without_case_tenant_admission() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0058_no_tenant_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        upgraded = _run_migration(backend, migration_url, "upgrade", "0057")
        assert upgraded.returncode == 0, upgraded.stderr
        lineage = _seed_automatic_assignment_lineage(
            schema_engine, tenant_id="team-a", admit_tenant=False
        )
        with schema_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO event_research_scope_evidence_assignments "
                    "(id, scope_version_id, evidence_link_id, factor_statement, "
                    "disposition, assignment_kind, research_run_id, "
                    "acquisition_goal_id, automatic_admission_decision_id, "
                    "automatic_provenance_json, created_at) VALUES "
                    "(:id, :scope, :evidence, 'Revenue supports the event thesis', "
                    "'mapped', 'automatic', :run, :goal, :decision, "
                    "CAST(:provenance AS json), :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "scope": lineage["scope"],
                    "evidence": lineage["evidence"],
                    "run": lineage["run"],
                    "goal": lineage["goal"],
                    "decision": lineage["decision"],
                    "provenance": json.dumps(
                        {
                            "policy_version": "event-goal-coverage-v1",
                            "mapping_scope": "factor",
                            "goal_id": lineage["goal"],
                            "job_id": str(lineage["job"]),
                            "admission_decision_id": str(lineage["decision"]),
                        }
                    ),
                    "now": datetime(2026, 8, 15, tzinfo=UTC),
                },
            )

        rejected = _run_migration(backend, migration_url, "upgrade", "0058")

        assert rejected.returncode != 0
        assert "automatic evidence lineage is invalid" in rejected.stderr
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.mark.pg_only
def test_0058_postgres_empty_upgrade_downgrade_upgrade_round_trip() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"research_orchestration_0058_empty_{uuid.uuid4().hex}"
    migration_url = _schema_database_url(database_url, schema)
    admin_engine = sa.create_engine(database_url, future=True)
    schema_engine = sa.create_engine(migration_url, future=True)
    backend = Path(__file__).parents[1]

    try:
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        for arguments, expected_revision in (
            (("upgrade", "0058"), "0058"),
            (("downgrade", "0057"), "0057"),
            (("upgrade", "0058"), "0058"),
        ):
            result = _run_migration(backend, migration_url, *arguments)
            assert result.returncode == 0, result.stderr
            with schema_engine.connect() as connection:
                assert connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one() == expected_revision
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
