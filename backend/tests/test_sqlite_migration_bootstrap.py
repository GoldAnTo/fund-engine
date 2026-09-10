"""SQLite is the supported self-contained demonstration database.

This is intentionally an end-to-end migration test rather than a metadata
test: a freshly created demo database must be able to replay the same Alembic
history used by the application and retain a durable revision marker.
"""
from __future__ import annotations

import os
import subprocess
import sys
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
import pytest


def _run_sqlite_migration(backend, environment, command, revision):
    return subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _seed_automatic_assignment_lineage(
    engine, *, tenant_id="team-a", admit_tenant: bool = True
) -> dict:
    import app.models  # noqa: F401
    from sqlalchemy.orm import Session

    from app.models.acquisition import (
        AcquisitionAttempt,
        AcquisitionJob,
        AutomaticAdmissionDecision,
        RetrievalArtifact,
        RetrievalArtifactDocument,
        SourceReference,
    )
    from app.models.event_research import (
        EventResearchScopeFactor,
        EventResearchScopeVersion,
    )
    from app.models.ledger import (
        AtomicClaimCandidate,
        CaseTenantAdmission,
        DocumentVersion,
        EvidenceLink,
        ResearchCase,
        SourceSpan,
        SourceStatement,
        Thesis,
    )
    from app.models.operational import ResearchRun
    from app.models.research_orchestration import (
        AcquisitionQueryPlan,
        AcquisitionSeries,
    )

    now = datetime(2026, 8, 15, tzinfo=UTC)
    with Session(engine) as session:
        case = ResearchCase(
            title="0058 lineage",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(case)
        session.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="Revenue supports the event thesis",
            research_protocol_required=False,
            created_by="tester",
            creator_type="human",
            review_state="confirmed",
            created_at=now,
        )
        scope = EventResearchScopeVersion(
            research_case_id=case.id,
            version=1,
            changed_by="tester",
            change_summary="0058 fixture",
            created_at=now,
        )
        session.add_all((thesis, scope))
        session.flush()
        session.add(
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement=thesis.statement,
                position=1,
            )
        )
        run = ResearchRun(
            research_case_id=case.id,
            status="prepared",
            stage="awaiting_acquisition",
            round=0,
            max_rounds=3,
            budget=10,
            budget_used=0,
            scope_thesis_ids=[str(thesis.id)],
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.flush()
        goal_id = f"thesis:{thesis.id}:support"
        request_snapshot = {
            "tenant_id": tenant_id,
            "case_id": str(case.id),
            "thesis_id": str(thesis.id),
            "research_run_id": str(run.id),
            "scope_version_id": str(scope.id),
            "goal_id": goal_id,
            "round": 1,
            "metric_terms": ["Revenue"],
            "metric_periods": ["quarter:2026-04-01/2026-06-30"],
            "metric_units": ["USD"],
        }
        job = AcquisitionJob(
            tenant_id=tenant_id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            research_run_id=run.id,
            idempotency_key=uuid.uuid4().hex,
            request_snapshot=request_snapshot,
            policy_snapshot={"version": "b-scope-v2"},
            status="succeeded",
            stage="succeeded",
            attempt=1,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.flush()
        series = AcquisitionSeries(
            tenant_id=tenant_id,
            research_case_id=case.id,
            research_run_id=run.id,
            scope_version_id=scope.id,
            thesis_id=thesis.id,
            goal_id=goal_id,
            created_at=now,
        )
        session.add(series)
        session.flush()
        session.add(
            AcquisitionQueryPlan(
                series_id=series.id,
                acquisition_job_id=job.id,
                acquisition_round=1,
                goal_id=goal_id,
                planner_version="goal-query-v1",
                policy_version="b-scope-v2",
                frozen_inputs_json=request_snapshot,
                ordered_queries_json=[
                    {
                        "adapter_key": "sse",
                        "objective": "support",
                        "query": "Revenue",
                    }
                ],
                created_at=now,
            )
        )
        reference = SourceReference(
            job_id=job.id,
            adapter_key="sse",
            external_record_id=uuid.uuid4().hex,
            external_version="v1",
            canonical_url="https://www.sse.com.cn/0058.pdf",
            title="0058",
            published_at=now,
            source_role="company_disclosure",
            metadata_json={"provider_identity": "SSE"},
            created_at=now,
        )
        session.add(reference)
        session.flush()
        attempt = AcquisitionAttempt(
            job_id=job.id,
            adapter_key="sse",
            operation="fetch",
            attempt_no=1,
            started_at=now,
            finished_at=now,
            outcome="succeeded",
            retryable=False,
            safe_metadata={},
        )
        session.add(attempt)
        session.flush()
        raw = uuid.uuid4().bytes
        digest = hashlib.sha256(raw).hexdigest()
        artifact = RetrievalArtifact(
            source_reference_id=reference.id,
            attempt_id=attempt.id,
            content_sha256=digest,
            raw_bytes=raw,
            mime_type="application/pdf",
            byte_size=len(raw),
            final_url=reference.canonical_url,
            retrieved_at=now,
        )
        document = DocumentVersion(
            content_sha256=digest,
            source_url=reference.canonical_url,
            available_at=now,
            acquired_at=now,
            parser_version="0058-fixture",
            source_authority="primary_disclosure",
        )
        session.add_all((artifact, document))
        session.flush()
        if admit_tenant:
            session.add(
                CaseTenantAdmission(
                    research_case_id=case.id,
                    tenant_id=tenant_id,
                    initial_document_version_id=document.id,
                    admitted_by="tester",
                    admission_reason="0058 lineage fixture",
                    admitted_at=now,
                )
            )
        session.add(
            RetrievalArtifactDocument(
                retrieval_artifact_id=artifact.id,
                document_version_id=document.id,
                relation="created",
                publication_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                created_at=now,
            )
        )
        span = SourceSpan(
            document_version_id=document.id,
            locator={"page": 1},
            verbatim_text="Revenue 100 USD",
        )
        session.add(span)
        session.flush()
        candidate = AtomicClaimCandidate(
            source_span_id=span.id,
            canonical_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            quote="Revenue 100 USD",
            quote_start=0,
            quote_end=15,
            quote_sha256=hashlib.sha256(b"Revenue 100 USD").hexdigest(),
            normalized_text="Revenue 100 USD",
            claim_type="disclosed_fact",
            authority_level="primary_disclosure",
            structured_fields={
                "subject": "Example",
                "predicate": "Revenue",
                "unit": "USD",
                "observed_period": "quarter:2026-04-01/2026-06-30",
            },
            validation_result={"normalizer_version": "fixture"},
            created_at=now,
        )
        session.add(candidate)
        session.flush()
        decision = AutomaticAdmissionDecision(
            job_id=job.id,
            candidate_id=candidate.id,
            retrieval_artifact_id=artifact.id,
            outcome="admitted",
            gate_version="b-scope-auto-admission-v1",
            policy_version="b-scope-v2",
            gate_results={"passed": True},
            created_at=now,
        )
        session.add(decision)
        session.flush()
        statement = SourceStatement(
            source_span_id=span.id,
            kind="fact",
            normalized_text="Revenue 100 USD",
            atomic_claim_candidate_id=candidate.id,
            automatic_admission_decision_id=decision.id,
            created_at=now,
        )
        session.add(statement)
        session.flush()
        evidence = EvidenceLink(
            thesis_id=thesis.id,
            source_statement_id=statement.id,
            role="supports",
            reason="automatic",
            scope={"period": "event"},
            available_at=now,
            creator_type="ai",
            review_state="automatically_admitted",
            automatic_admission_decision_id=decision.id,
            created_at=now,
        )
        session.add(evidence)
        session.commit()
        return {
            "case": case.id,
            "thesis": thesis.id,
            "scope": scope.id,
            "run": run.id,
            "goal": goal_id,
            "job": job.id,
            "decision": decision.id,
            "evidence": evidence.id,
        }


def test_fresh_sqlite_database_upgrades_to_alembic_head(tmp_path) -> None:
    database_path = tmp_path / "research-demo.db"
    backend = Path(__file__).parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0062"
        job_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("jobs")
        }
        assert {"failure_count", "next_retry_at", "retry_policy_version"} <= job_columns
        orchestration_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "research_orchestrations"
            )
        }
        assert "state_started_at" in orchestration_columns
        assert "acquisition_series" in sa.inspect(connection).get_table_names()
        assessment_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("ai_assessments")
        }
        assert {
            "research_protocol_status",
            "effective_binding_id",
            "mechanism_template_version_id",
            "verification_rule_ids",
        }.issubset(assessment_columns)
        assert {"key_factor_candidate_runs", "key_factor_candidates"}.issubset(
            sa.inspect(connection).get_table_names()
        )
        research_source_type = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("source_contracts")
        }["research_source_type"]
        assert research_source_type["nullable"] is False
        trigger_count = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one()
        assert trigger_count == 1


def test_research_job_retry_migration_round_trips_existing_jobs(tmp_path) -> None:
    database_path = tmp_path / "research-job-retry.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0059")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    job_id = uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO jobs "
                "(id, kind, status, progress, attempt, failure_count, "
                "cancel_requested, created_at, retry_policy_version) "
                "VALUES (:id, 'propose', 'failed', 0, 1, 2, 0, :created, :policy)"
            ),
            {
                "id": job_id,
                "created": datetime.now(UTC),
                "policy": "research-synthesis-retry-v1",
            },
        )

    downgraded = _run_sqlite_migration(backend, environment, "downgrade", "0058")
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0059")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT failure_count, next_retry_at, retry_policy_version "
                "FROM jobs WHERE id = :id"
            ),
            {"id": job_id},
        ).one()
        assert row == (0, None, None)


def test_state_started_at_migration_backfills_existing_orchestrations(
    tmp_path,
) -> None:
    database_path = tmp_path / "orchestration-state-started-at.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0059")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    now = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    case_id = uuid.uuid4().hex
    scope_id = uuid.uuid4().hex
    orchestration_id = uuid.uuid4().hex
    event_at = now + timedelta(minutes=5)
    checkpoint_at = now + timedelta(minutes=15)
    with engine.begin() as connection:
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
                "(id, research_case_id, version, changed_by, change_summary, "
                "created_at) VALUES "
                "(:id, :case_id, 1, 'tester', 'legacy scope', :now)"
            ),
            {"id": scope_id, "case_id": case_id, "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO research_orchestrations "
                "(id, tenant_id, research_case_id, current_scope_version_id, "
                "state, user_stage, checkpoint_json, recovery_status, version, "
                "created_at, updated_at) VALUES "
                "(:id, 'team-a', :case_id, :scope_id, 'acquiring', "
                "'acquisition', '{}', 'healthy', 3, :now, :now)"
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
                "(id, orchestration_id, sequence, transition, actor, message, "
                "payload_json, idempotency_key, created_at) VALUES "
                "(:id, :orchestration_id, 1, 'acquisition_started', 'system', "
                "'acquiring', '{\"state\":\"acquiring\","
                "\"from_state\":\"planning_acquisition\","
                "\"to_state\":\"acquiring\"}', 'state-start', "
                ":event_at)"
            ),
            {
                "id": uuid.uuid4().hex,
                "orchestration_id": orchestration_id,
                "event_at": event_at,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO research_orchestration_events "
                "(id, orchestration_id, sequence, transition, actor, message, "
                "payload_json, idempotency_key, created_at) VALUES "
                "(:id, :orchestration_id, 2, 'coverage_ready', 'system', "
                "'checkpoint', '{\"state\":\"acquiring\","
                "\"from_state\":\"acquiring\","
                "\"to_state\":\"acquiring\"}', 'checkpoint', "
                ":checkpoint_at)"
            ),
            {
                "id": uuid.uuid4().hex,
                "orchestration_id": orchestration_id,
                "checkpoint_at": checkpoint_at,
            },
        )

    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0060")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT created_at, state_started_at "
                "FROM research_orchestrations WHERE id = :id"
            ),
            {"id": orchestration_id},
        ).one()
        assert datetime.fromisoformat(row.state_started_at) == event_at
        state_column = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns(
                "research_orchestrations"
            )
        }["state_started_at"]
        assert state_column["nullable"] is False

    downgraded = _run_sqlite_migration(backend, environment, "downgrade", "0059")
    assert downgraded.returncode == 0, downgraded.stderr
    with engine.connect() as connection:
        assert "state_started_at" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "research_orchestrations"
            )
        }

    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0060")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        restarted_at = connection.execute(
            sa.text(
                "SELECT state_started_at FROM research_orchestrations "
                "WHERE id = :id"
            ),
            {"id": orchestration_id},
        ).scalar_one()
        assert datetime.fromisoformat(restarted_at) == event_at


def test_state_started_at_migration_uses_latest_true_reentry_and_honest_legacy_fallback(
    tmp_path,
) -> None:
    database_path = tmp_path / "orchestration-state-reentry.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0059")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    created_at = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    first_entry_at = created_at + timedelta(minutes=5)
    reentry_at = created_at + timedelta(minutes=25)
    legacy_checkpoint_at = created_at + timedelta(minutes=15)
    updated_at = created_at + timedelta(minutes=30)
    records = [
        {
            "case_id": uuid.uuid4().hex,
            "scope_id": uuid.uuid4().hex,
            "orchestration_id": uuid.uuid4().hex,
            "suffix": "typed",
        },
        {
            "case_id": uuid.uuid4().hex,
            "scope_id": uuid.uuid4().hex,
            "orchestration_id": uuid.uuid4().hex,
            "suffix": "legacy",
        },
    ]
    with engine.begin() as connection:
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
                    "(id, research_case_id, version, changed_by, change_summary, "
                    "created_at) VALUES "
                    "(:scope_id, :case_id, 1, 'tester', 'legacy scope', :created_at)"
                ),
                {**record, "created_at": created_at},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestrations "
                    "(id, tenant_id, research_case_id, current_scope_version_id, "
                    "state, user_stage, checkpoint_json, recovery_status, version, "
                    "created_at, updated_at) VALUES "
                    "(:orchestration_id, 'team-a', :case_id, :scope_id, "
                    "'acquiring', 'acquisition', '{}', 'healthy', 3, "
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
                first_entry_at,
                '{"state":"acquiring","from_state":"planning_acquisition",'
                '"to_state":"acquiring"}',
            ),
            (
                2,
                "left-state",
                created_at + timedelta(minutes=20),
                '{"state":"recovering","from_state":"acquiring",'
                '"to_state":"recovering"}',
            ),
            (
                3,
                "reentry",
                reentry_at,
                '{"state":"acquiring","from_state":"recovering",'
                '"to_state":"acquiring"}',
            ),
        ]
        for sequence, key, occurred_at, payload in typed_events:
            connection.execute(
                sa.text(
                    "INSERT INTO research_orchestration_events "
                    "(id, orchestration_id, sequence, transition, actor, message, "
                    "payload_json, idempotency_key, created_at) VALUES "
                    "(:id, :orchestration_id, :sequence, :key, 'system', :key, "
                    ":payload, :key, :occurred_at)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "orchestration_id": typed["orchestration_id"],
                    "sequence": sequence,
                    "key": key,
                    "payload": payload,
                    "occurred_at": occurred_at,
                },
            )

        legacy = records[1]
        connection.execute(
            sa.text(
                "INSERT INTO research_orchestration_events "
                "(id, orchestration_id, sequence, transition, actor, message, "
                "payload_json, idempotency_key, created_at) VALUES "
                "(:id, :orchestration_id, 1, 'coverage_ready', 'system', "
                "'legacy checkpoint', '{\"state\":\"acquiring\"}', "
                "'legacy-checkpoint', :occurred_at)"
            ),
            {
                "id": uuid.uuid4().hex,
                "orchestration_id": legacy["orchestration_id"],
                "occurred_at": legacy_checkpoint_at,
            },
        )

    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0060")
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        rows = dict(
            connection.execute(
                sa.text(
                    "SELECT id, state_started_at FROM research_orchestrations"
                )
            ).all()
        )
        assert datetime.fromisoformat(
            rows[records[0]["orchestration_id"]]
        ) == reentry_at
        assert datetime.fromisoformat(
            rows[records[1]["orchestration_id"]]
        ) == created_at


def test_0058_sqlite_enforces_automatic_assignment_lineage_and_immutability(
    tmp_path,
) -> None:
    database_path = tmp_path / "assignment-lineage.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0057")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    first = _seed_automatic_assignment_lineage(engine, tenant_id="team-a")
    second = _seed_automatic_assignment_lineage(engine, tenant_id="team-b")

    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0058")
    assert upgraded.returncode == 0, upgraded.stderr

    insert_sql = sa.text(
        "INSERT INTO event_research_scope_evidence_assignments "
        "(id, scope_version_id, evidence_link_id, factor_statement, disposition, "
        "assignment_kind, research_run_id, acquisition_goal_id, "
        "automatic_admission_decision_id, automatic_provenance_json, created_at) "
        "VALUES (:id, :scope, :evidence, 'Revenue supports the event thesis', "
        "'mapped', 'automatic', :run, :goal, :decision, :provenance, :created_at)"
    )

    def values(**overrides):
        payload = {
            "id": uuid.uuid4().hex,
            "scope": first["scope"].hex,
            "evidence": first["evidence"].hex,
            "run": first["run"].hex,
            "goal": first["goal"],
            "decision": first["decision"].hex,
            "provenance": json.dumps(
                {
                    "policy_version": "event-goal-coverage-v1",
                    "mapping_scope": "factor",
                    "goal_id": first["goal"],
                    "job_id": str(first["job"]),
                    "admission_decision_id": str(first["decision"]),
                }
            ),
            "created_at": "2026-08-15 00:00:00",
        }
        payload.update(overrides)
        return payload

    invalid = [
        {"decision": second["decision"].hex},
        {"evidence": second["evidence"].hex},
        {"run": second["run"].hex},
        {"scope": second["scope"].hex},
        {"goal": second["goal"]},
        {
            "decision": second["decision"].hex,
            "evidence": second["evidence"].hex,
        },
    ]
    with engine.connect() as connection:
        for overrides in invalid:
            transaction = connection.begin()
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(insert_sql, values(**overrides))
            transaction.rollback()

        valid_values = values()
        with connection.begin():
            connection.execute(insert_sql, valid_values)
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin():
                connection.execute(insert_sql, values())
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin():
                connection.execute(
                    sa.text(
                        "UPDATE event_research_scope_evidence_assignments "
                        "SET disposition = 'unmapped' WHERE id = :id"
                    ),
                    {"id": valid_values["id"]},
                )

        parent_mutations = [
            (
                "UPDATE acquisition_jobs SET request_snapshot = "
                "json_set(request_snapshot, '$.goal_id', 'forged-goal') "
                "WHERE id = :job",
                {"job": first["job"].hex},
            ),
            (
                "UPDATE acquisition_jobs SET request_snapshot = "
                "json_set(request_snapshot, '$.research_run_id', :run) "
                "WHERE id = :job",
                {"job": first["job"].hex, "run": str(second["run"])},
            ),
            (
                "UPDATE acquisition_jobs SET request_snapshot = "
                "json_set(request_snapshot, '$.scope_version_id', :scope) "
                "WHERE id = :job",
                {"job": first["job"].hex, "scope": str(second["scope"])},
            ),
            (
                "UPDATE acquisition_series SET goal_id = 'forged-goal' "
                "WHERE goal_id = :goal",
                {"goal": first["goal"]},
            ),
            (
                "UPDATE automatic_admission_decisions SET job_id = :other_job "
                "WHERE id = :decision",
                {
                    "other_job": second["job"].hex,
                    "decision": first["decision"].hex,
                },
            ),
            (
                "DELETE FROM acquisition_query_plans "
                "WHERE acquisition_job_id = :job",
                {"job": first["job"].hex},
            ),
        ]
        for statement, parameters in parent_mutations:
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin():
                    connection.execute(sa.text(statement), parameters)

        with connection.begin():
            connection.execute(
                sa.text(
                    "UPDATE acquisition_jobs SET status = 'running', "
                    "stage = 'searching', attempt = attempt + 1, "
                    "error_code = 'retryable', error_detail = 'retry planned' "
                    "WHERE id = :job"
                ),
                {"job": second["job"].hex},
            )
        mutable = connection.execute(
            sa.text(
                "SELECT status, stage, attempt, error_code "
                "FROM acquisition_jobs WHERE id = :job"
            ),
            {"job": second["job"].hex},
        ).one()
        assert mutable == ("running", "searching", 2, "retryable")
        connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin():
                connection.execute(
                    sa.text(
                        "DELETE FROM event_research_scope_evidence_assignments "
                        "WHERE id = :id"
                    ),
                    {"id": valid_values["id"]},
                )

    engine.dispose()
    downgraded = _run_sqlite_migration(
        backend, environment, "downgrade", "0057"
    )
    assert downgraded.returncode != 0
    assert "automatic evidence provenance" in downgraded.stderr


def test_0058_sqlite_empty_round_trip(tmp_path) -> None:
    database_path = tmp_path / "assignment-lineage-empty.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0058")
    assert upgraded.returncode == 0, upgraded.stderr
    downgraded = _run_sqlite_migration(
        backend, environment, "downgrade", "0057"
    )
    assert downgraded.returncode == 0, downgraded.stderr
    upgraded_again = _run_sqlite_migration(
        backend, environment, "upgrade", "0058"
    )
    assert upgraded_again.returncode == 0, upgraded_again.stderr


def test_0058_sqlite_rejects_lineage_without_case_tenant_admission(tmp_path) -> None:
    database_path = tmp_path / "assignment-lineage-no-tenant.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    upgraded = _run_sqlite_migration(backend, environment, "upgrade", "0057")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    lineage = _seed_automatic_assignment_lineage(
        engine, tenant_id="team-a", admit_tenant=False
    )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO event_research_scope_evidence_assignments "
                "(id, scope_version_id, evidence_link_id, factor_statement, "
                "disposition, assignment_kind, research_run_id, "
                "acquisition_goal_id, automatic_admission_decision_id, "
                "automatic_provenance_json, created_at) VALUES "
                "(:id, :scope, :evidence, 'Revenue supports the event thesis', "
                "'mapped', 'automatic', :run, :goal, :decision, :provenance, :now)"
            ),
            {
                "id": uuid.uuid4().hex,
                "scope": lineage["scope"].hex,
                "evidence": lineage["evidence"].hex,
                "run": lineage["run"].hex,
                "goal": lineage["goal"],
                "decision": lineage["decision"].hex,
                "provenance": json.dumps(
                    {
                        "policy_version": "event-goal-coverage-v1",
                        "mapping_scope": "factor",
                        "goal_id": lineage["goal"],
                        "job_id": str(lineage["job"]),
                        "admission_decision_id": str(lineage["decision"]),
                    }
                ),
                "now": "2026-08-15 00:00:00",
            },
        )
    engine.dispose()

    rejected = _run_sqlite_migration(backend, environment, "upgrade", "0058")

    assert rejected.returncode != 0
    assert "automatic evidence lineage is invalid" in rejected.stderr


def test_0051_preserves_legacy_assessment_and_downgrades_cleanly(tmp_path) -> None:
    database_path = tmp_path / "assessment-provenance.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    upgraded_to_0050 = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0050"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded_to_0050.returncode == 0, upgraded_to_0050.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    ids = {
        "case": "00000000000000000000000000000001",
        "thesis": "00000000000000000000000000000002",
        "snapshot": "00000000000000000000000000000003",
        "assessment": "00000000000000000000000000000004",
    }
    now = "2026-08-12 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO research_cases "
                "(id, title, industry_topic, created_at, created_by) "
                "VALUES (:id, 'legacy case', 'test', :now, 'tester')"
            ),
            {"id": ids["case"], "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO theses "
                "(id, research_case_id, statement, created_at, created_by, "
                "creator_type, review_state, research_protocol_required) "
                "VALUES (:id, :case_id, 'legacy strict thesis', :now, 'tester', "
                "'human', 'confirmed', 1)"
            ),
            {"id": ids["thesis"], "case_id": ids["case"], "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO evidence_snapshots "
                "(id, thesis_id, cutoff, evidence_link_ids, created_at) "
                "VALUES (:id, :thesis_id, :now, '[]', :now)"
            ),
            {"id": ids["snapshot"], "thesis_id": ids["thesis"], "now": now},
        )
        connection.execute(
            sa.text(
                "INSERT INTO ai_assessments "
                "(id, snapshot_id, conclusion, rationale, gaps, "
                "displayed_as_provisional, creator_type, created_at) "
                "VALUES (:id, :snapshot_id, 'insufficient_evidence', "
                "'legacy row', '[]', 1, 'ai', :now)"
            ),
            {
                "id": ids["assessment"],
                "snapshot_id": ids["snapshot"],
                "now": now,
            },
        )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        legacy = connection.execute(
            sa.text(
                "SELECT research_protocol_status, effective_binding_id, "
                "mechanism_template_version_id, verification_rule_ids "
                "FROM ai_assessments WHERE id = :id"
            ),
            {"id": ids["assessment"]},
        ).one()
        assert tuple(legacy) == (None, None, None, None)
        assert connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one() == 1
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO ai_assessments "
                    "(id, snapshot_id, conclusion, rationale, gaps, "
                    "research_protocol_status, effective_binding_id, "
                    "mechanism_template_version_id, verification_rule_ids, "
                    "displayed_as_provisional, creator_type, created_at) "
                    "VALUES ('00000000000000000000000000000005', :snapshot_id, "
                    "'insufficient_evidence', 'invalid typed import', '[]', "
                    "'ready', '00000000000000000000000000000006', "
                    "'00000000000000000000000000000007', '[]', 1, 'ai', :now)"
                ),
                {"snapshot_id": ids["snapshot"], "now": now},
            )

    downgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0050"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM ai_assessments WHERE id = :id"),
            {"id": ids["assessment"]},
        ).scalar_one() == 1
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("ai_assessments")
        }
        assert "research_protocol_status" not in columns
        assert connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' "
                "AND name = 'trg_ai_assessments_protocol_scope'"
            )
        ).scalar_one() == 0


def test_0051_migration_trigger_enforces_typed_protocol_scope(tmp_path) -> None:
    import app.models  # noqa: F401 - register protocol mappings
    from app.models.ledger import AIAssessment, EvidenceSnapshot, ResearchCase, Thesis
    from app.models.research_protocol import (
        CaseMechanismSelectionVersion,
        MechanismEdgeVersion,
        OutcomeBindingVersion,
        VerificationRuleVersion,
    )
    from sqlalchemy.orm import Session
    from tests.protocol_provenance import seed_protocol_footprint

    database_path = tmp_path / "assessment-protocol-scope.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}
    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert migrated.returncode == 0, migrated.stderr
    engine = sa.create_engine(environment["DATABASE_URL"])
    now = datetime.now(UTC)

    def assessment(snapshot, *, conclusion="insufficient_evidence", **protocol):
        return AIAssessment(
            snapshot_id=snapshot.id,
            conclusion=conclusion,
            rationale="migration trigger probe",
            gaps=[],
            displayed_as_provisional=True,
            creator_type="ai",
            created_at=now,
            **protocol,
        )

    with Session(engine) as session:
        first_case = ResearchCase(
            title="first migrated scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        other_case = ResearchCase(
            title="other migrated scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add_all([first_case, other_case])
        session.flush()
        first_thesis = Thesis(
            research_case_id=first_case.id,
            statement="first strict thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        other_thesis = Thesis(
            research_case_id=other_case.id,
            statement="other strict thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add_all([first_thesis, other_thesis])
        session.flush()
        first_snapshot = EvidenceSnapshot(
            thesis_id=first_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        other_snapshot = EvidenceSnapshot(
            thesis_id=other_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add_all([first_snapshot, other_snapshot])
        session.flush()
        first = seed_protocol_footprint(session, first_thesis, status="ready")
        other = seed_protocol_footprint(session, other_thesis, status="ready")
        valid_protocol = {
            "research_protocol_status": "ready",
            "effective_binding_id": first.binding.id,
            "mechanism_template_version_id": first.template.id,
            "verification_rule_ids": [str(rule.id) for rule in first.rules],
        }
        session.add(assessment(first_snapshot, **valid_protocol))
        session.flush()

        invalid_protocols = [
            {"research_protocol_status": "ready"},
            {**valid_protocol, "research_protocol_status": None},
            {**valid_protocol, "research_protocol_status": "blocked"},
            {**valid_protocol, "effective_binding_id": other.binding.id},
            {
                **valid_protocol,
                "mechanism_template_version_id": other.template.id,
            },
            {
                **valid_protocol,
                "verification_rule_ids": [str(rule.id) for rule in other.rules],
            },
            {**valid_protocol, "verification_rule_ids": ["not-a-uuid"]},
            {**valid_protocol, "verification_rule_ids": {"not": "an array"}},
            {
                **valid_protocol,
                "research_protocol_status": "single_metric_monitoring",
            },
            {
                **valid_protocol,
                "verification_rule_ids": valid_protocol[
                    "verification_rule_ids"
                ][:-1],
            },
            {
                **valid_protocol,
                "verification_rule_ids": [
                    *valid_protocol["verification_rule_ids"],
                    valid_protocol["verification_rule_ids"][0],
                ],
            },
        ]
        for invalid_protocol in invalid_protocols:
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(assessment(first_snapshot, **invalid_protocol))
                session.flush()

        monitoring_case = ResearchCase(
            title="migrated monitoring scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(monitoring_case)
        session.flush()
        monitoring_thesis = Thesis(
            research_case_id=monitoring_case.id,
            statement="migrated monitoring thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add(monitoring_thesis)
        session.flush()
        monitoring_snapshot = EvidenceSnapshot(
            thesis_id=monitoring_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add(monitoring_snapshot)
        session.flush()
        monitoring = seed_protocol_footprint(session, monitoring_thesis)
        monitoring_protocol = {
            "research_protocol_status": "single_metric_monitoring",
            "effective_binding_id": monitoring.binding.id,
            "mechanism_template_version_id": monitoring.template.id,
            "verification_rule_ids": [str(rule.id) for rule in monitoring.rules],
        }
        for invalid_assessment in (
            assessment(
                monitoring_snapshot,
                conclusion="supported",
                **monitoring_protocol,
            ),
            assessment(
                monitoring_snapshot,
                **{**monitoring_protocol, "research_protocol_status": "ready"},
            ),
        ):
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(invalid_assessment)
                session.flush()

        non_string_case = ResearchCase(
            title="migrated non-string truthy business line",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(non_string_case)
        session.flush()
        non_string_thesis = Thesis(
            research_case_id=non_string_case.id,
            statement="migrated integer business line thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add(non_string_thesis)
        session.flush()
        non_string_snapshot = EvidenceSnapshot(
            thesis_id=non_string_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add(non_string_snapshot)
        session.flush()
        non_string = seed_protocol_footprint(
            session,
            non_string_thesis,
            business_line=1,
        )
        non_string_ready = {
            "research_protocol_status": "ready",
            "effective_binding_id": non_string.binding.id,
            "mechanism_template_version_id": non_string.template.id,
            "verification_rule_ids": [str(rule.id) for rule in non_string.rules],
        }
        for claimed_status, conclusion in (
            ("ready", "insufficient_evidence"),
            ("single_metric_monitoring", "supported"),
        ):
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(
                    assessment(
                        non_string_snapshot,
                        conclusion=conclusion,
                        **{
                            **non_string_ready,
                            "research_protocol_status": claimed_status,
                        },
                    )
                )
                session.flush()

        scoped_rule = first.rules[0]
        legacy_rule = VerificationRuleVersion(
            research_case_id=None,
            mechanism_edge_id=scoped_rule.mechanism_edge_id,
            metric_definition_id=scoped_rule.metric_definition_id,
            expected_direction=scoped_rule.expected_direction,
            support_predicate=scoped_rule.support_predicate,
            contradiction_predicate=scoped_rule.contradiction_predicate,
            allowed_source_roles=list(scoped_rule.allowed_source_roles),
            observed_period_start=scoped_rule.observed_period_start,
            observed_period_end=scoped_rule.observed_period_end,
            available_at_deadline=scoped_rule.available_at_deadline,
            next_verification_event=scoped_rule.next_verification_event,
            reviewer="legacy",
            reason="migrated pre-case-scope rule",
            created_at=datetime.now(UTC),
        )
        session.add(legacy_rule)
        session.flush()
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(
                assessment(
                    first_snapshot,
                    **{
                        **valid_protocol,
                        "verification_rule_ids": [
                            *valid_protocol["verification_rule_ids"],
                            str(legacy_rule.id),
                        ],
                    },
                )
            )
            session.flush()

        old_binding = first.binding
        current_binding = OutcomeBindingVersion(
            thesis_id=old_binding.thesis_id,
            metric_definition_id=old_binding.metric_definition_id,
            entity_scope=dict(old_binding.entity_scope),
            direction=old_binding.direction,
            baseline=dict(old_binding.baseline),
            horizon_start=old_binding.horizon_start,
            horizon_end=old_binding.horizon_end,
            state="approved",
            supersedes_id=old_binding.id,
            reviewer="tester",
            reason="migrated current binding",
            created_at=datetime.now(UTC),
        )
        old_rule = first.rules[0]
        current_rule = VerificationRuleVersion(
            research_case_id=old_rule.research_case_id,
            mechanism_edge_id=old_rule.mechanism_edge_id,
            metric_definition_id=old_rule.metric_definition_id,
            expected_direction=old_rule.expected_direction,
            support_predicate=old_rule.support_predicate,
            contradiction_predicate=old_rule.contradiction_predicate,
            allowed_source_roles=list(old_rule.allowed_source_roles),
            observed_period_start=old_rule.observed_period_start,
            observed_period_end=old_rule.observed_period_end,
            available_at_deadline=old_rule.available_at_deadline,
            next_verification_event=old_rule.next_verification_event,
            supersedes_id=old_rule.id,
            reviewer="tester",
            reason="migrated current rule",
            created_at=datetime.now(UTC),
        )
        session.add_all([current_binding, current_rule])
        session.flush()
        current_rule_ids = [
            str(current_rule.id) if rule.id == old_rule.id else str(rule.id)
            for rule in first.rules
        ]
        stale_protocols = [
            {
                **valid_protocol,
                "effective_binding_id": old_binding.id,
                "verification_rule_ids": current_rule_ids,
            },
            {
                **valid_protocol,
                "effective_binding_id": current_binding.id,
            },
        ]
        for stale_protocol in stale_protocols:
            with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
                session.add(assessment(first_snapshot, **stale_protocol))
                session.flush()

        first_edge = session.get(
            MechanismEdgeVersion,
            first.rules[0].mechanism_edge_id,
        )
        session.add(
            MechanismEdgeVersion(
                template_version_id=first.template.id,
                edge_key="migrated-unruled-required-edge",
                source_node_id=first_edge.source_node_id,
                target_node_id=first_edge.target_node_id,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(
                assessment(
                    first_snapshot,
                    **{
                        **valid_protocol,
                        "effective_binding_id": current_binding.id,
                        "verification_rule_ids": current_rule_ids,
                    },
                )
            )
            session.flush()

        no_counter_case = ResearchCase(
            title="migrated no-counter scope",
            industry_topic="test",
            created_by="tester",
            created_at=now,
        )
        session.add(no_counter_case)
        session.flush()
        no_counter_thesis = Thesis(
            research_case_id=no_counter_case.id,
            statement="migrated no-counter thesis",
            research_protocol_required=True,
            created_by="tester",
            created_at=now,
        )
        session.add(no_counter_thesis)
        session.flush()
        no_counter_snapshot = EvidenceSnapshot(
            thesis_id=no_counter_thesis.id,
            cutoff=now,
            evidence_link_ids=[],
            created_at=now,
        )
        session.add(no_counter_snapshot)
        session.flush()
        no_counter = seed_protocol_footprint(
            session,
            no_counter_thesis,
            status="ready",
            counter_hypothesis=False,
        )
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(
                assessment(
                    no_counter_snapshot,
                    research_protocol_status="ready",
                    effective_binding_id=no_counter.binding.id,
                    mechanism_template_version_id=no_counter.template.id,
                    verification_rule_ids=[
                        str(rule.id) for rule in no_counter.rules
                    ],
                )
            )
            session.flush()

        session.add(
            CaseMechanismSelectionVersion(
                research_case_id=first_case.id,
                template_version_id=other.template.id,
                reviewer="tester",
                reason="new current template",
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
        with pytest.raises(sa.exc.IntegrityError), session.begin_nested():
            session.add(assessment(first_snapshot, **valid_protocol))
            session.flush()


def test_upgrade_recovers_when_0048_columns_exist_but_revision_is_stale(tmp_path) -> None:
    database_path = tmp_path / "stale-0047.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0047"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "ALTER TABLE fund_disclosure_sync_config_versions "
                "ADD COLUMN report_period DATE"
            )
        )
        connection.execute(
            sa.text(
                "ALTER TABLE fund_disclosure_sync_runs ADD COLUMN report_period DATE"
            )
        )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0062"


def test_live_case_runner_bootstraps_its_database_before_materializing(
    monkeypatch, tmp_path
) -> None:
    from app.scripts import run_industrial_foxconn_forecast_case as runner

    database_url = f"sqlite:///{tmp_path / 'industrial-foxconn.db'}"
    calls: list[str] = []
    monkeypatch.setattr(runner, "upgrade_database_to_head", calls.append)
    monkeypatch.setattr(runner, "load_local_env", lambda: None)
    monkeypatch.setattr(runner.GildataMCPClient, "from_env", lambda: object())
    monkeypatch.setattr(runner, "load_industrial_foxconn_sources", lambda _: object())
    monkeypatch.setattr(
        runner,
        "materialize_live_industrial_foxconn_case",
        lambda *_args, **_kwargs: SimpleNamespace(
            case_id="case", verdict_id="verdict", baseline_value=1,
            expected_value=2, actual_value=3, outcome="supported",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_industrial_foxconn_forecast_case", "--database-url", database_url],
    )

    assert runner.main() == 0
    assert calls == [database_url]


def test_adopts_a_complete_legacy_orm_database_without_losing_rows(tmp_path) -> None:
    import app.models  # noqa: F401 - register the complete metadata
    from app.db_migrations import upgrade_database_to_head
    from app.models.ledger import Base, ResearchCase
    from sqlalchemy.orm import Session

    database_url = f"sqlite:///{tmp_path / 'legacy-demo.db'}"
    engine = sa.create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            ResearchCase(
                title="existing evidence", industry_topic="semiconductor",
                created_by="tester", created_at=datetime.now(UTC),
            )
        )
        session.commit()

    upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert connection.execute(sa.text("SELECT COUNT(*) FROM research_cases")).scalar_one() == 1
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0062"


def test_upgrade_from_0051_backfills_source_contract_research_type(tmp_path) -> None:
    database_path = tmp_path / "source-contracts-0051.db"
    backend = Path(__file__).parents[1]
    environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0051"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert initial.returncode == 0, initial.stderr
    engine = sa.create_engine(f"sqlite:///{database_path}")
    contract_id = "11111111111111111111111111111111"
    document_id = "22222222222222222222222222222222"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO source_contracts (
                    id, document_version_id, source_type, provider_or_tenant,
                    allow_ai_processing, allow_display, allow_export, allow_api,
                    region, retention_policy, deletion_policy,
                    downstream_restrictions, intake_metadata, declared_by, created_at
                ) VALUES (
                    :id, :document_id, 'licensed_provider', 'legacy-provider',
                    1, 1, 0, 0, 'CN', 'case_retained', 'not_recorded',
                    '[]', '{}', 'legacy-user', :created_at
                )
                """
            ),
            {
                "id": contract_id,
                "document_id": document_id,
                "created_at": datetime.now(UTC),
            },
        )

    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert upgraded.returncode == 0, upgraded.stderr
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0062"
        assert connection.execute(
            sa.text(
                "SELECT research_source_type FROM source_contracts WHERE id = :id"
            ),
            {"id": contract_id},
        ).scalar_one() == "licensed_provider"


def test_refuses_to_stamp_an_incomplete_unmanaged_database(tmp_path) -> None:
    from app.db_migrations import (
        UnmanagedDatabaseSchemaError,
        upgrade_database_to_head,
    )

    database_url = f"sqlite:///{tmp_path / 'incomplete-demo.db'}"
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE incomplete_legacy_schema (id INTEGER)"))

    with pytest.raises(UnmanagedDatabaseSchemaError, match="missing_tables"):
        upgrade_database_to_head(database_url)

    with engine.connect() as connection:
        assert "alembic_version" not in sa.inspect(connection).get_table_names()
