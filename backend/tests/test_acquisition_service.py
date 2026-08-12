"""Authorization and frozen-snapshot contract for ``AcquisitionModule``."""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.domain.acquisition import (
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.models.acquisition import AcquisitionJob
from app.models.ledger import CaseTenantAdmission, ResearchCase, Thesis
from app.models.operational import ResearchRun
from app.services.acquisition import AcquisitionModule


def _request(research_case, thesis, **overrides) -> AcquisitionRequest:
    values = {
        "tenant_id": "team-a",
        "case_id": research_case.id,
        "thesis_id": thesis.id,
        "research_run_id": None,
        "round": 2,
        "objective": EvidenceObjective.CONTRADICT,
        "target_link_role": "contradicts",
        "thesis_statement": "公司收入将在 2026 年增长",
        "entity_names": ("示例公司",),
        "security_codes": ("600000",),
        "metric_terms": ("营业收入",),
        "period_start": "2026-01-01",
        "period_end": "2026-12-31",
        "cutoff": datetime(2026, 8, 12, 16, tzinfo=UTC),
        "allowed_source_roles": frozenset(
            {"licensed_provider", "company_disclosure"}
        ),
        "source_policy_version": B_SCOPE_POLICY.version,
        "idempotency_key": uuid.uuid4().hex,
    }
    values.update(overrides)
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
        "objective": "contradict",
        "period_end": "2026-12-31",
        "period_start": "2026-01-01",
        "research_run_id": None,
        "round": 2,
        "security_codes": ["600000"],
        "source_policy_version": "b-scope-v1",
        "target_link_role": "contradicts",
        "tenant_id": "team-a",
        "thesis_id": str(thesis.id),
        "thesis_statement": "公司收入将在 2026 年增长",
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


def test_request_requires_thesis_and_optional_run_to_belong_to_case(
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
    assert [event.seq for event in module.events(created.id, principal=principal)] == [1]
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
