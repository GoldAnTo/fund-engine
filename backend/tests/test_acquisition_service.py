"""Authorization and frozen-snapshot contract for ``AcquisitionModule``."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import event as sqlalchemy_event, func, select
from sqlalchemy.orm import object_session

from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.domain.acquisition import (
    ACQUISITION_PLANNER_VERSION,
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.acquisition import AcquisitionAttempt, AcquisitionJob, SourceReference
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import CaseTenantAdmission, ResearchCase, Thesis
from app.models.operational import ResearchRun
from app.services.acquisition import AcquisitionModule


def _request(research_case, thesis, **overrides) -> AcquisitionRequest:
    session = object_session(research_case)
    assert session is not None
    version = (
        session.scalar(
            select(func.max(EventResearchScopeVersion.version)).where(
                EventResearchScopeVersion.research_case_id == research_case.id
            )
        )
        or 0
    ) + 1
    scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=version,
        changed_by="system:test",
        change_summary="service test scope",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    run = ResearchRun(
        research_case_id=research_case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=10,
        budget_used=0,
        scope_thesis_ids=[str(thesis.id)],
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        updated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    session.add_all((scope, run))
    session.flush()
    goal_id = f"thesis:{thesis.id}:contradict"
    values = {
        "tenant_id": "team-a",
        "case_id": research_case.id,
        "thesis_id": thesis.id,
        "research_run_id": run.id,
        "scope_version_id": scope.id,
        "goal_id": goal_id,
        "round": 1,
        "objective": EvidenceObjective.CONTRADICT,
        "target_link_role": "contradicts",
        "thesis_statement": "公司收入将在 2026 年增长",
        "entity_names": ("示例公司",),
        "security_codes": ("600000",),
        "metric_terms": ("营业收入",),
        "metric_periods": ("2026-Q2",),
        "metric_units": ("亿元",),
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "cutoff": datetime(2026, 8, 12, 16, tzinfo=UTC),
        "allowed_source_roles": frozenset(
            {"licensed_provider", "company_disclosure"}
        ),
        "source_policy_version": B_SCOPE_POLICY.version,
        "planner_version": ACQUISITION_PLANNER_VERSION,
        "previous_query_plan_id": None,
        "expansion": None,
    }
    values.update(overrides)
    values["idempotency_key"] = overrides.get(
        "idempotency_key",
        f"run:{values['research_run_id']}:scope:{values['scope_version_id']}:"
        f"goal:{values['goal_id']}:round:{values['round']}",
    )
    return AcquisitionRequest(**values)


@pytest.fixture
def principal() -> AcquisitionPrincipal:
    return AcquisitionPrincipal(tenant_id="team-a", actor="system:test")


@pytest.fixture
def admitted_case(session, research_case, document):
    session.add(
        CaseTenantAdmission(
            research_case_id=research_case.id,
            tenant_id="team-a",
            initial_document_version_id=document.id,
            admitted_by="test",
            admitted_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
    )
    session.flush()
    return research_case


@pytest.fixture
def module(session) -> AcquisitionModule:
    return AcquisitionModule(session)


def test_request_is_idempotent_and_persists_complete_deterministic_snapshots(
    module, session, admitted_case, thesis, principal
):
    request = _request(admitted_case, thesis)

    first = module.request(request, principal=principal)
    second = module.request(request, principal=principal)

    assert first == second
    job = session.get(AcquisitionJob, first.id)
    assert job is not None
    assert job.request_snapshot == {
        "allowed_source_roles": ["company_disclosure", "licensed_provider"],
        "case_id": str(admitted_case.id),
        "cutoff": "2026-08-12T16:00:00Z",
        "entity_names": ["示例公司"],
        "idempotency_key": request.idempotency_key,
        "metric_terms": ["营业收入"],
        "metric_periods": ["2026-Q2"],
        "metric_units": ["亿元"],
        "objective": "contradict",
        "period_end": "2026-12-31",
        "period_start": "2026-01-01",
        "research_run_id": str(request.research_run_id),
        "scope_version_id": str(request.scope_version_id),
        "goal_id": request.goal_id,
        "round": 1,
        "security_codes": ["600000"],
        "source_policy_version": B_SCOPE_POLICY.version,
        "planner_version": ACQUISITION_PLANNER_VERSION,
        "previous_query_plan_id": None,
        "expansion": None,
        "target_link_role": "contradicts",
        "tenant_id": "team-a",
        "thesis_id": str(thesis.id),
        "thesis_statement": "公司收入将在 2026 年增长",
        "query_plan_id": job.request_snapshot["query_plan_id"],
    }
    assert set(job.policy_snapshot) == {
        "allowed_source_roles",
        "enabled_adapter_keys",
        "exact_hosts",
        "max_response_bytes",
        "per_adapter_page_limit",
        "permission_declarations",
        "suffix_hosts",
        "version",
    }
    assert job.policy_snapshot["enabled_adapter_keys"] == ["gildata", "sse", "szse"]
    assert job.policy_snapshot["permission_declarations"] == [
        ["gildata", "licensed-provider-contract"],
        ["sse", "official-public-disclosure"],
        ["szse", "official-public-disclosure"],
    ]
    assert "actor" not in job.request_snapshot
    assert module.events(first.id, principal=principal)[0].payload_json == {
        "actor": "system:test"
    }


def test_public_coverage_provenance_reports_each_frozen_search_operation(
    module, session, admitted_case, thesis, principal
) -> None:
    request = _request(admitted_case, thesis)
    job = module.request(request, principal=principal)
    plan = module.query_plan(job.id, principal=principal)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    session.add_all(
        [
            AcquisitionAttempt(
                job_id=job.id,
                adapter_key=plan.ordered_queries[0]["adapter_key"],
                operation="search",
                attempt_no=1,
                started_at=now,
                finished_at=now,
                outcome="succeeded",
                retryable=False,
                safe_metadata={"query_index": 0},
            ),
            AcquisitionAttempt(
                job_id=job.id,
                adapter_key=plan.ordered_queries[1]["adapter_key"],
                operation="search",
                attempt_no=1,
                started_at=now,
                finished_at=now,
                outcome="failed",
                retryable=False,
                error_code="SourceUnavailable",
                safe_metadata={"query_index": 1},
            ),
        ]
    )
    session.flush()

    operations = module.coverage_search_operations(job.id, principal=principal)

    assert [item.query_index for item in operations] == [0, 1, 2]
    assert [item.outcome for item in operations] == [
        "succeeded",
        "failed",
        "missing",
    ]
    assert operations[1].error_codes == ("SourceUnavailable",)
    assert operations[2].attempt_count == 0


def test_public_coverage_snapshot_batch_has_fixed_select_count(
    module, session, admitted_case, thesis, principal
) -> None:
    first = module.request(_request(admitted_case, thesis), principal=principal)
    second = module.request(_request(admitted_case, thesis), principal=principal)
    engine = session.get_bind()

    def load(job_ids):
        statements: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        sqlalchemy_event.listen(engine, "before_cursor_execute", record)
        try:
            snapshots = module.coverage_snapshots(job_ids, principal=principal)
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", record)
        return snapshots, len(statements)

    one, one_count = load((first.id,))
    two, two_count = load((first.id, second.id))

    assert set(one) == {first.id}
    assert set(two) == {first.id, second.id}
    assert two[first.id].plan.job_id == first.id
    assert two[second.id].plan.job_id == second.id
    assert two_count == one_count
    assert two_count <= 5


def test_request_same_key_with_changed_frozen_request_conflicts(
    module, admitted_case, thesis, principal
):
    request = _request(admitted_case, thesis)
    module.request(request, principal=principal)

    with pytest.raises(ConflictError, match="idempotency"):
        module.request(
            replace(
                request,
                objective=EvidenceObjective.SUPPORT,
                target_link_role="supports",
            ),
            principal=principal,
        )


def test_http_replay_rejects_an_internally_mismatched_frozen_policy(
    module, session, admitted_case, thesis, principal
):
    request = _request(admitted_case, thesis)
    created = module.request(request, principal=principal)
    job = session.get(AcquisitionJob, created.id)
    assert job is not None
    job.policy_snapshot = {**job.policy_snapshot, "version": "b-scope-v0"}
    session.flush()

    with pytest.raises(ConflictError, match="idempotency"):
        module.replay_existing(
            tenant_id=request.tenant_id,
            idempotency_key=request.idempotency_key,
            case_id=request.case_id,
            thesis_id=request.thesis_id,
            objective=request.objective.value,
            principal=principal,
        )


def test_request_requires_matching_principal_tenant(
    module, admitted_case, thesis
):
    request = _request(admitted_case, thesis)

    with pytest.raises(PermissionDeniedError):
        module.request(
            request,
            principal=AcquisitionPrincipal(tenant_id="team-b", actor="system:test"),
        )


def test_request_requires_case_access_and_does_not_persist(
    module, session, research_case, thesis, principal
):
    request = _request(research_case, thesis)

    with pytest.raises(NotFoundError):
        module.request(request, principal=principal)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_request_requires_thesis_run_and_scope_to_belong_to_case(
    module, session, admitted_case, thesis, principal
):
    other_case = ResearchCase(
        title="other",
        industry_topic="other",
        created_by="test",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    session.add(other_case)
    session.flush()
    other_thesis = Thesis(
        research_case_id=other_case.id,
        statement="other thesis",
        created_by="test",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    wrong_run = ResearchRun(
        research_case_id=other_case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=10,
        budget_used=0,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        updated_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    session.add_all([other_thesis, wrong_run])
    session.flush()

    with pytest.raises(NotFoundError):
        module.request(
            _request(admitted_case, other_thesis),
            principal=principal,
        )
    with pytest.raises(NotFoundError):
        module.request(
            _request(admitted_case, thesis, research_run_id=wrong_run.id),
            principal=principal,
        )


def test_request_validates_active_policy_version_and_role_subset(
    session, admitted_case, thesis, principal
):
    future_policy = SourcePolicy(
        version="future-v2",
        enabled_adapter_keys=B_SCOPE_POLICY.enabled_adapter_keys,
        allowed_source_roles=frozenset({"company_disclosure"}),
        exact_hosts=B_SCOPE_POLICY.exact_hosts,
        suffix_hosts=B_SCOPE_POLICY.suffix_hosts,
        max_response_bytes=B_SCOPE_POLICY.max_response_bytes,
        per_adapter_page_limit=B_SCOPE_POLICY.per_adapter_page_limit,
        permission_declarations=B_SCOPE_POLICY.permission_declarations,
    )

    with pytest.raises(ValueError, match="policy version"):
        AcquisitionModule(session, policy=future_policy).request(
            _request(admitted_case, thesis), principal=principal
        )
    roles_policy = replace(
        B_SCOPE_POLICY,
        allowed_source_roles=frozenset({"company_disclosure"}),
    )
    with pytest.raises(ValueError, match="policy roles"):
        AcquisitionModule(session, policy=roles_policy).request(
            _request(admitted_case, thesis), principal=principal
        )


def test_request_applies_sensitive_persistence_validator_to_snapshot_and_actor(
    module, admitted_case, thesis
):
    with pytest.raises(ValueError, match="sensitive"):
        module.request(
            _request(
                admitted_case,
                thesis,
                thesis_statement="https://example.test/a?api_key=leak",
            ),
            principal=AcquisitionPrincipal(tenant_id="team-a", actor="system:test"),
        )
    with pytest.raises(ValueError, match="sensitive"):
        module.request(
            _request(admitted_case, thesis),
            principal=AcquisitionPrincipal(
                tenant_id="team-a",
                actor="https://example.test/a?access_token=leak",
            ),
        )


def test_get_events_and_admitted_evidence_authorize_tenant_and_case_before_read(
    module, admitted_case, thesis, principal
):
    created = module.request(_request(admitted_case, thesis), principal=principal)

    assert module.get(created.id, principal=principal).id == created.id
    assert [event.seq for event in module.events(created.id, principal=principal)] == [1, 2]
    assert module.admitted_evidence(created.id, principal=principal) == ()

    other = AcquisitionPrincipal(tenant_id="team-b", actor="system:other")
    for read in (module.get, module.events, module.admitted_evidence):
        with pytest.raises(NotFoundError):
            read(created.id, principal=other)
        with pytest.raises(NotFoundError):
            read(uuid.uuid4(), principal=principal)


def test_reads_recheck_case_admission_instead_of_trusting_job_tenant(
    module, session, admitted_case, thesis, principal
):
    created = module.request(_request(admitted_case, thesis), principal=principal)
    admission = session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == admitted_case.id
        )
    )
    assert admission is not None
    # The immutable-table guard prevents an UPDATE, but expunging the object and
    # issuing a core DELETE is likewise forbidden. Exercise the service boundary
    # with a fresh repository record carrying an inaccessible case instead.
    inaccessible = ResearchCase(
        title="inaccessible",
        industry_topic="other",
        created_by="test",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    session.add(inaccessible)
    session.flush()
    job = session.get(AcquisitionJob, created.id)
    assert job is not None
    session.expunge(job)
    session.execute(
        AcquisitionJob.__table__.update()
        .where(AcquisitionJob.id == created.id)
        .values(research_case_id=inaccessible.id)
    )
    session.expire_all()

    with pytest.raises(NotFoundError):
        module.get(created.id, principal=principal)


def test_module_documents_caller_owned_transaction_boundary():
    docs = AcquisitionModule.__doc__ or ""
    assert "caller" in docs.casefold()
    assert "commit" in docs.casefold()
    assert "external work" in docs.casefold()


def test_workflow_ledger_reads_independent_linear_batches(
    module, session, admitted_case, thesis, principal
):
    request = _request(admitted_case, thesis)
    created = module.request(request, principal=principal)
    now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    session.add_all(
        [
            AcquisitionAttempt(
                job_id=created.id,
                adapter_key="linear",
                operation="fetch",
                attempt_no=index,
                started_at=now,
                finished_at=None,
                outcome="running",
                retryable=False,
                safe_metadata={},
            )
            for index in range(1, 4)
        ]
        + [
            SourceReference(
                job_id=created.id,
                adapter_key="linear",
                external_record_id=f"reference-{index}",
                external_version="v1",
                canonical_url=f"https://example.test/reference-{index}",
                title=f"Reference {index}",
                published_at=now,
                source_role="company_disclosure",
                metadata_json={},
                created_at=now,
            )
            for index in range(1, 5)
        ]
    )
    session.flush()

    captured: list[tuple[str, object]] = []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        lowered = statement.casefold()
        if statement.lstrip().upper().startswith("SELECT") and any(
            table in lowered
            for table in (
                "acquisition_attempts",
                "source_references",
                "automatic_admission_decisions",
                "acquisition_exceptions",
            )
        ):
            captured.append((statement, parameters))

    engine = session.get_bind()
    sqlalchemy_event.listen(engine, "before_cursor_execute", capture)
    try:
        records = module.workflow_ledger(
            case_id=admitted_case.id,
            scope_version_id=request.scope_version_id,
            principal=principal,
        )
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", capture)

    assert len(records) == 3 + 4
    replayed_input_rows = sum(
        len(session.connection().exec_driver_sql(statement, parameters).all())
        for statement, parameters in captured
    )
    assert replayed_input_rows <= len(records) + 2
    compiled = [statement.casefold() for statement, _parameters in captured]
    assert not any(
        "acquisition_attempts" in statement
        and "source_references" in statement
        for statement in compiled
    )
    assert not any(
        "automatic_admission_decisions" in statement
        and "acquisition_exceptions" in statement
        for statement in compiled
    )


def test_bounded_workflow_ledger_reads_are_authorized_by_case_and_scope(
    module, admitted_case, thesis, principal
):
    request = _request(admitted_case, thesis)
    module.request(request, principal=principal)
    other_tenant = AcquisitionPrincipal("team-b", "system:other")

    page = module.workflow_ledger_page(
        case_id=admitted_case.id,
        scope_version_id=request.scope_version_id,
        principal=principal,
        status=None,
        after=None,
        high_watermark=None,
        limit=20,
    )
    assert page.records == ()
    assert page.counts.total == 0
    assert module.workflow_ledger_for_evidence_links(
        case_id=admitted_case.id,
        scope_version_id=request.scope_version_id,
        evidence_link_ids=(),
        principal=principal,
    ) == ()

    with pytest.raises(NotFoundError):
        module.workflow_ledger_page(
            case_id=admitted_case.id,
            scope_version_id=request.scope_version_id,
            principal=other_tenant,
            status=None,
            after=None,
            high_watermark=None,
            limit=20,
        )
    with pytest.raises(NotFoundError):
        module.workflow_ledger_for_evidence_links(
            case_id=admitted_case.id,
            scope_version_id=request.scope_version_id,
            evidence_link_ids=(),
            principal=other_tenant,
        )
    with pytest.raises(NotFoundError):
        module.workflow_ledger_page(
            case_id=admitted_case.id,
            scope_version_id=uuid.uuid4(),
            principal=principal,
            status=None,
            after=None,
            high_watermark=None,
            limit=20,
        )
