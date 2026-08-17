"""Protected HTTP contract for standalone governed acquisition jobs."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.acquisition.policy import B_SCOPE_POLICY
from app.acquisition.sources import (
    RetrievedEnvelope,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
)
from app.models.acquisition import (
    AcquisitionException,
    AcquisitionJob,
    AcquisitionJobEvent,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    SourceReference,
)
from app.models.ledger import ResearchCase
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MetricDefinitionVersion,
    VerificationRuleVersion,
)
from app.repositories.acquisition import AcquisitionRepository
from app.repositories.research_protocol import ResearchProtocolRepository
from app.services.acquisition_runner import AcquisitionRunner
from app.services.mechanism_templates import seed_ai_capex_template
from app.services.research_protocol import (
    MetricDefinitionInput,
    OutcomeBindingInput,
    ResearchProtocolService,
    VerificationRuleInput,
)
from tests.tenant_admission import admit_case


RUNNER_NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class APIExtractionClient:
    model_version = "fake-task9-extractor-v1"

    def chat_json(self, messages, schema_hint=""):
        assert schema_hint == "extract"
        extraction_input = json.loads(messages[-1]["content"])
        span = next(
            item
            for item in extraction_input["spans"]
            if "Revenue was 100 USD" in item["verbatim_text"]
        )
        quote = span["verbatim_text"]
        return {
            "statements": [
                {
                    "span_id": span["span_id"],
                    "kind": "disclosed_fact",
                    "quote": quote,
                    "quote_start": 0,
                    "quote_end": len(quote),
                    "normalized_text": quote,
                    "assertion_actor": "Example Corp",
                    "subject": "Example Corp",
                    "predicate": "Revenue",
                    "object_text": "100 USD",
                    "numeric_value": "100",
                    "unit": "USD",
                    "observed_period": "2026-08-12",
                    "scope": {"company": "Example Corp", "metric": "Revenue"},
                }
            ]
        }


class EmptyAPIAdapter(SourceAdapter):
    def __init__(self, adapter_key: str) -> None:
        self._descriptor = SourceDescriptor(
            adapter_key=adapter_key,
            provider_identity=f"Task9 {adapter_key}",
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=frozenset({"www.sse.com.cn"}),
        )

    @property
    def descriptor(self):
        return self._descriptor

    def search(self, query: str, cutoff: datetime):
        return ()

    def fetch(self, reference):
        raise AssertionError("empty adapter must not fetch")

    def close(self):
        return None


class EvidenceAPIAdapter(SourceAdapter):
    _descriptor = SourceDescriptor(
        adapter_key="sse",
        provider_identity="Shanghai Stock Exchange",
        allowed_schemes=frozenset({"https"}),
        allowed_hosts=frozenset({"www.sse.com.cn", "static.sse.com.cn"}),
        allowed_source_roles=frozenset({"company_disclosure"}),
    )

    @property
    def descriptor(self):
        return self._descriptor

    def search(self, query: str, cutoff: datetime):
        assert "Example Corp" in query
        assert "600001" in query
        return (
            SourceReferenceValue(
                adapter_key="sse",
                external_record_id="task9-report-1",
                external_version="v1",
                canonical_url="https://www.sse.com.cn/task9-report.txt",
                title="Example Corp annual report",
                published_at=RUNNER_NOW,
                source_role="company_disclosure",
                fetch_locator={
                    "canonical_url": "https://www.sse.com.cn/task9-report.txt"
                },
                metadata={
                    "provider_identity": "Shanghai Stock Exchange",
                    "security_code": "600001",
                    "source_publication": "2026-08-13",
                },
            ),
        )

    def fetch(self, reference):
        return RetrievedEnvelope(
            content=b"Example Corp 2026-08-12 Revenue was 100 USD.",
            mime_type="text/plain; charset=utf-8",
            final_url="https://static.sse.com.cn/task9-report.txt",
            etag='"task9-v1"',
            last_modified="Thu, 13 Aug 2026 12:00:00 GMT",
            provider_request_id="task9-request-1",
            metadata={
                "adapter_key": "sse",
                "external_record_id": reference.external_record_id,
                "provider_identity": "Shanghai Stock Exchange",
            },
        )

    def close(self):
        return None


def _run_next_job(session, *, adapters):
    repository = AcquisitionRepository(session, clock=lambda: RUNNER_NOW)
    claim = repository.claim_next(
        worker_id="system:acquisition-worker@task9-v1#api-runner",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    factory = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    AcquisitionRunner(
        factory,
        adapters=adapters,
        llm_client=APIExtractionClient(),
        clock=lambda: RUNNER_NOW,
    ).run_claim(claim)
    session.expire_all()
    return claim.job_id


@dataclass(frozen=True)
class AcquisitionAPIScope:
    case_id: uuid.UUID
    thesis_id: uuid.UUID
    verification_rule_id: uuid.UUID


@pytest.fixture
def acquisition_api_scope(
    session,
    document,
    research_service,
    instrument_repository,
) -> AcquisitionAPIScope:
    """Create only the persisted research inputs consumed by the API."""
    research_case = research_service.add_case(
        title="Example Corp revenue verification",
        industry_topic="enterprise_software",
        created_by="human:test",
        research_object="Example Corp (600001)",
        phenomenon="Revenue growth needs fresh primary evidence",
        core_question="Will Example Corp revenue grow in 2026?",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        evidence_cutoff=date(2026, 8, 13),
    )
    thesis = research_service.add_thesis(
        research_case.id,
        statement="Example Corp revenue will grow in 2026",
        created_by="human:test",
        title="2026 revenue growth",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 12, 31),
        support_condition="Reported revenue grows year over year",
        falsification_condition="Reported revenue does not grow year over year",
        next_verification_event="2026 annual report",
        # The acquisition API still resolves the approved protocol below.
        # Keeping the strict baseline-contract gate off avoids coupling this
        # route test to unrelated source-contract setup.
        research_protocol_required=False,
    )
    admit_case(
        session,
        research_case.id,
        tenant_id="test-team",
        document_version_id=document.id,
    )

    company = instrument_repository.add_company(
        code="EXAMPLE",
        name="Example Corp",
        type="listed_company",
    )
    instrument_repository.add_stock(
        company_id=company.id,
        code="600001.SH",
        name="Example Corp",
        market="SSE",
    )

    protocol = ResearchProtocolService(session)
    metric = protocol.add_metric_version(
        MetricDefinitionInput(
            metric_id="revenue",
            display_name="Revenue",
            canonical_definition="Reported operating revenue",
            entity_scope="company",
            unit="USD",
            frequency="annual",
            period_semantics="flow",
            allowed_source_roles=["company_disclosure", "licensed_provider"],
            role_eligibility=["outcome"],
        ),
        approved_by="human:reviewer",
        reason="Freeze the outcome metric",
    )
    draft = protocol.create_outcome_binding(
        thesis.id,
        OutcomeBindingInput(
            metric_definition_id=metric.id,
            entity_scope={
                "company_id": str(company.id),
                "company": company.name,
            },
            direction="increase",
            baseline={
                "source_ref": f"document:{document.id}",
                "value": "100",
                "unit": "USD",
                "observed_period": "2025",
                "available_at": document.available_at.isoformat(),
            },
            horizon_start=date(2026, 1, 1),
            horizon_end=date(2026, 12, 31),
            reviewer="human:reviewer",
            reason="Bind the thesis to the persisted company and metric",
        ),
    )
    protocol.approve_outcome_binding(
        draft.id,
        reviewer="human:reviewer",
        reason="Approve acquisition scope",
    )
    template = seed_ai_capex_template(session)
    protocol.select_template(
        research_case.id,
        template.id,
        reviewer="human:reviewer",
        reason="Use the reviewed mechanism template",
    )
    edge = session.scalar(
        select(MechanismEdgeVersion)
        .where(MechanismEdgeVersion.template_version_id == template.id)
        .order_by(MechanismEdgeVersion.edge_key)
        .limit(1)
    )
    assert edge is not None
    verification_rule = protocol.add_verification_rule(
        research_case.id,
        edge.id,
        VerificationRuleInput(
            metric_definition_id=metric.id,
            expected_direction="increase",
            support_predicate="Reported revenue grows year over year",
            contradiction_predicate="Reported revenue does not grow year over year",
            allowed_source_roles=["company_disclosure"],
            observed_period_start=date(2026, 4, 1),
            observed_period_end=date(2026, 6, 30),
            available_at_deadline=date(2026, 8, 1),
            next_verification_event="2026 interim report",
            reviewer="human:reviewer",
            reason="Freeze the revenue verification rule",
        ),
    )
    session.flush()
    return AcquisitionAPIScope(
        case_id=research_case.id,
        thesis_id=thesis.id,
        verification_rule_id=verification_rule.id,
    )


def _create_path(scope: AcquisitionAPIScope) -> str:
    return (
        f"/api/v1/research-cases/{scope.case_id}/theses/{scope.thesis_id}"
        "/acquisition-jobs"
    )


def _strip_request_id(payload: dict) -> dict:
    copied = json.loads(json.dumps(payload))
    copied.get("error", {}).pop("request_id", None)
    return copied


def _raw_update(
    session,
    *,
    table: str,
    record_id: uuid.UUID,
    assignments: dict[str, object],
) -> None:
    """Corrupt persisted public-facing fields below the ORM safety boundary."""
    cursor = session.connection().connection.cursor()
    if session.get_bind().dialect.name == "postgresql":
        cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER no_update_{table}")
        clauses: list[str] = []
        values: list[object] = []
        for column, value in assignments.items():
            if isinstance(value, (dict, list)):
                clauses.append(f"{column} = CAST(%s AS json)")
                values.append(json.dumps(value))
            else:
                clauses.append(f"{column} = %s")
                values.append(value)
        values.append(str(record_id))
        cursor.execute(
            f"UPDATE {table} SET {', '.join(clauses)} WHERE id = %s",
            tuple(values),
        )
    else:
        clauses = [f"{column} = ?" for column in assignments]
        values = [
            json.dumps(value)
            if isinstance(value, (dict, list))
            else value
            for column, value in assignments.items()
        ]
        values.append(record_id.hex)
        cursor.execute(
            f"UPDATE {table} SET {', '.join(clauses)} WHERE id = ?",
            tuple(values),
        )
    assert cursor.rowcount == 1
    session.expire_all()


def test_post_acquisition_job_returns_202_without_client_identity_fields(
    api_client,
    acquisition_api_scope,
):
    response = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-post-1"},
        json={"objective": "support"},
    )

    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["status"] == "queued"
    serialized = json.dumps(body).casefold()
    assert "actor" not in serialized
    assert "reviewer" not in serialized


def test_post_requires_idempotency_key(api_client, acquisition_api_scope):
    response = api_client.post(
        _create_path(acquisition_api_scope),
        json={"objective": "support"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_post_is_idempotent_for_same_frozen_request_and_conflicts_on_change(
    api_client,
    acquisition_api_scope,
):
    headers = {"Idempotency-Key": "api-task9-idempotent-1"}
    first = api_client.post(
        _create_path(acquisition_api_scope),
        headers=headers,
        json={"objective": "support"},
    )
    repeated = api_client.post(
        _create_path(acquisition_api_scope),
        headers=headers,
        json={"objective": "support"},
    )
    conflicting = api_client.post(
        _create_path(acquisition_api_scope),
        headers=headers,
        json={"objective": "contradict"},
    )

    assert first.status_code == repeated.status_code == 202
    assert repeated.json() == first.json()
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "conflict"


def test_post_replay_bypasses_scope_resolution_after_scope_records_change(
    api_client,
    acquisition_api_scope,
    session,
    monkeypatch,
):
    headers = {"Idempotency-Key": "api-task9-scope-drift-1"}
    first = api_client.post(
        _create_path(acquisition_api_scope),
        headers=headers,
        json={"objective": "support"},
    )
    assert first.status_code == 202

    protocol_repo = ResearchProtocolRepository(session)
    binding = protocol_repo.effective_binding(acquisition_api_scope.thesis_id)
    assert binding is not None
    protocol = ResearchProtocolService(session)
    replacement = protocol.create_outcome_binding(
        acquisition_api_scope.thesis_id,
        OutcomeBindingInput(
            metric_definition_id=binding.metric_definition_id,
            entity_scope=dict(binding.entity_scope),
            direction=binding.direction,
            baseline=dict(binding.baseline),
            horizon_start=date(2026, 2, 1),
            horizon_end=date(2026, 11, 30),
            reviewer="human:replacement-reviewer",
            reason="Change the mutable effective binding after job creation",
        ),
    )
    protocol.approve_outcome_binding(
        replacement.id,
        reviewer="human:replacement-reviewer",
        reason="Approve the later binding",
    )
    prior_rule = session.get(
        VerificationRuleVersion, acquisition_api_scope.verification_rule_id
    )
    assert prior_rule is not None
    protocol.add_verification_rule(
        acquisition_api_scope.case_id,
        prior_rule.mechanism_edge_id,
        VerificationRuleInput(
            metric_definition_id=prior_rule.metric_definition_id,
            expected_direction=prior_rule.expected_direction,
            support_predicate=prior_rule.support_predicate,
            contradiction_predicate=prior_rule.contradiction_predicate,
            allowed_source_roles=list(prior_rule.allowed_source_roles),
            observed_period_start=date(2026, 5, 1),
            observed_period_end=date(2026, 7, 31),
            available_at_deadline=date(2026, 8, 5),
            next_verification_event="Later protocol event",
            reviewer="human:replacement-reviewer",
            reason="Change the effective rule after job creation",
        ),
    )
    research_case = session.get(ResearchCase, acquisition_api_scope.case_id)
    metric = session.get(MetricDefinitionVersion, binding.metric_definition_id)
    assert research_case is not None and metric is not None
    _raw_update(
        session,
        table="research_cases",
        record_id=research_case.id,
        assignments={"evidence_cutoff": "2026-07-01"},
    )
    _raw_update(
        session,
        table="metric_definition_versions",
        record_id=metric.id,
        assignments={"allowed_source_roles": []},
    )

    def fail_if_scope_is_resolved(*args, **kwargs):
        raise AssertionError("idempotent replay must not resolve mutable scope")

    monkeypatch.setattr(
        "app.api.v1.acquisition._scope_request", fail_if_scope_is_resolved
    )
    repeated = api_client.post(
        _create_path(acquisition_api_scope),
        headers=headers,
        json={"objective": "support"},
    )

    assert repeated.status_code == 202
    assert repeated.json() == first.json()


def test_post_same_key_with_different_path_conflicts_without_resolving_new_scope(
    api_client,
    acquisition_api_scope,
):
    headers = {"Idempotency-Key": "api-task9-path-conflict-1"}
    first = api_client.post(
        _create_path(acquisition_api_scope),
        headers=headers,
        json={"objective": "support"},
    )
    assert first.status_code == 202

    response = api_client.post(
        (
            f"/api/v1/research-cases/{uuid.uuid4()}/theses/{uuid.uuid4()}"
            "/acquisition-jobs"
        ),
        headers=headers,
        json={"objective": "support"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_post_freezes_scope_and_actor_only_from_server_records(
    api_client,
    acquisition_api_scope,
    session,
):
    response = api_client.post(
        _create_path(acquisition_api_scope),
        headers={
            "Idempotency-Key": "api-task9-frozen-scope-1",
            "X-Actor": "human:attacker",
        },
        json={"objective": "contradict"},
    )
    assert response.status_code == 202

    job = session.get(AcquisitionJob, uuid.UUID(response.json()["id"]))
    assert job is not None
    assert job.request_snapshot == {
        "allowed_source_roles": ["company_disclosure", "licensed_provider"],
        "acquisition_kind": "external_gap",
        "case_id": str(acquisition_api_scope.case_id),
        "cutoff": "2026-08-13T23:59:59.999999Z",
        "document_version_id": None,
        "entity_names": ["Example Corp"],
        "idempotency_key": "api-task9-frozen-scope-1",
        "metric_terms": ["Revenue"],
        "objective": "contradict",
        "period_end": "2026-12-31",
        "period_start": "2026-01-01",
        "research_run_id": None,
        "round": 1,
        "security_codes": ["600001"],
        "source_policy_version": B_SCOPE_POLICY.version,
        "target_link_role": "contradicts",
        "tenant_id": "test-team",
        "thesis_id": str(acquisition_api_scope.thesis_id),
        "thesis_statement": "Example Corp revenue will grow in 2026",
    }
    assert job.policy_snapshot["enabled_adapter_keys"] == ["gildata", "sse", "szse"]
    events = api_client.get(f"/api/v1/acquisition-jobs/{job.id}/events")
    assert events.status_code == 200
    assert events.json()[0]["payload"] == {}


def test_post_fails_closed_when_required_protocol_scope_is_missing(
    api_client,
    session,
    document,
    research_service,
):
    research_case = research_service.add_case(
        title="Incomplete acquisition scope",
        industry_topic="test",
        created_by="human:test",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        evidence_cutoff=date(2026, 8, 13),
    )
    thesis = research_service.add_thesis(
        research_case.id,
        statement="A thesis without a frozen protocol",
        created_by="human:test",
    )
    admit_case(
        session,
        research_case.id,
        tenant_id="test-team",
        document_version_id=document.id,
    )

    response = api_client.post(
        f"/api/v1/research-cases/{research_case.id}/theses/{thesis.id}/acquisition-jobs",
        headers={"Idempotency-Key": "api-task9-incomplete-1"},
        json={"objective": "support"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert session.scalar(
        select(AcquisitionJob).where(
            AcquisitionJob.idempotency_key == "api-task9-incomplete-1"
        )
    ) is None


def test_verify_rule_resolves_case_rule_and_freezes_support_role(
    api_client,
    acquisition_api_scope,
    session,
):
    response = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-rule-1"},
        json={"objective": "verify_rule"},
    )

    assert response.status_code == 202
    job = session.get(AcquisitionJob, uuid.UUID(response.json()["id"]))
    assert job is not None
    assert job.request_snapshot["objective"] == "verify_rule"
    assert job.request_snapshot["target_link_role"] == "supports"
    assert job.request_snapshot["metric_terms"] == ["Revenue"]
    assert job.request_snapshot["period_start"] == "2026-04-01"
    assert job.request_snapshot["period_end"] == "2026-06-30"
    assert job.request_snapshot["cutoff"] == "2026-08-01T23:59:59.999999Z"
    assert job.request_snapshot["allowed_source_roles"] == ["company_disclosure"]


def test_verify_rule_rejects_client_selected_rule_id(
    api_client,
    acquisition_api_scope,
):
    response = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-rule-client-id-1"},
        json={
            "objective": "verify_rule",
            "verification_rule_id": str(acquisition_api_scope.verification_rule_id),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_verify_rule_rejects_multiple_compatible_effective_rules(
    api_client,
    acquisition_api_scope,
    session,
):
    existing = session.get(
        VerificationRuleVersion, acquisition_api_scope.verification_rule_id
    )
    assert existing is not None
    existing_edge = session.get(MechanismEdgeVersion, existing.mechanism_edge_id)
    assert existing_edge is not None
    second_edge = session.scalar(
        select(MechanismEdgeVersion)
        .where(
            MechanismEdgeVersion.template_version_id
            == existing_edge.template_version_id,
            MechanismEdgeVersion.id != existing_edge.id,
        )
        .order_by(MechanismEdgeVersion.edge_key, MechanismEdgeVersion.id)
        .limit(1)
    )
    assert second_edge is not None
    ResearchProtocolService(session).add_verification_rule(
        acquisition_api_scope.case_id,
        second_edge.id,
        VerificationRuleInput(
            metric_definition_id=existing.metric_definition_id,
            expected_direction="increase",
            support_predicate="Second compatible support predicate",
            contradiction_predicate="Second compatible contradiction predicate",
            allowed_source_roles=["company_disclosure"],
            observed_period_start=date(2026, 4, 1),
            observed_period_end=date(2026, 6, 30),
            available_at_deadline=date(2026, 8, 1),
            next_verification_event="Second compatible event",
            reviewer="human:reviewer",
            reason="Create deliberate ambiguity for the HTTP boundary",
        ),
    )

    response = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-rule-ambiguous-1"},
        json={"objective": "verify_rule"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == (
        "verify_rule requires exactly one compatible effective rule"
    )


def test_acquisition_job_read_is_non_disclosing_for_another_tenant(
    api_client,
    acquisition_api_scope,
    monkeypatch,
):
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        json.dumps(
            {
                "test-tenant-token": "test-team",
                "other-tenant-token": "other-team",
            }
        ),
    )
    created = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-tenant-1"},
        json={"objective": "support"},
    )
    assert created.status_code == 202

    api_client.headers["Authorization"] = "Bearer other-tenant-token"
    hidden = api_client.get(f"/api/v1/acquisition-jobs/{created.json()['id']}")
    missing = api_client.get(f"/api/v1/acquisition-jobs/{uuid.uuid4()}")

    assert hidden.status_code == missing.status_code == 404
    assert _strip_request_id(hidden.json()) == _strip_request_id(missing.json())


def test_runner_results_are_exposed_only_through_safe_status_reads(
    api_client,
    acquisition_api_scope,
    session,
):
    created = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-runner-1"},
        json={"objective": "support"},
    )
    assert created.status_code == 202
    job_id = _run_next_job(
        session,
        adapters={
            "gildata": EmptyAPIAdapter("gildata"),
            "sse": EvidenceAPIAdapter(),
            "szse": EmptyAPIAdapter("szse"),
        },
    )
    assert str(job_id) == created.json()["id"]

    detail = api_client.get(f"/api/v1/acquisition-jobs/{job_id}")
    events = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/events")
    evidence = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/evidence")
    exceptions = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/exceptions")

    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["stage"] == "succeeded"
    assert detail.json()["attempt"] == 1
    assert detail.json()["counters"] == {
        "references": 1,
        "fetched": 1,
        "frozen": 1,
        "admitted": 1,
        "exceptions": 0,
    }
    assert detail.json()["retry_at"] is None
    assert detail.json()["error"] is None

    assert events.status_code == 200
    event_items = events.json()
    assert [item["seq"] for item in event_items] == sorted(
        item["seq"] for item in event_items
    )
    assert {"seq", "status", "stage", "message", "payload", "created_at"} == set(
        event_items[0]
    )
    tail = api_client.get(
        f"/api/v1/acquisition-jobs/{job_id}/events",
        params={"after_seq": event_items[-2]["seq"]},
    )
    assert tail.status_code == 200
    assert [item["seq"] for item in tail.json()] == [event_items[-1]["seq"]]
    invalid_cursor = api_client.get(
        f"/api/v1/acquisition-jobs/{job_id}/events", params={"after_seq": -1}
    )
    assert invalid_cursor.status_code == 422

    assert evidence.status_code == 200
    assert evidence.json() == [
        {
            "evidence_link_id": evidence.json()[0]["evidence_link_id"],
            "source_statement_id": evidence.json()[0]["source_statement_id"],
            "document_version_id": evidence.json()[0]["document_version_id"],
            "source_title": "Example Corp annual report",
            "source_url": "https://www.sse.com.cn/task9-report.txt",
            "gate_results": {
                "locator": {"passed": True, "reason_code": "passed"},
                "semantic": {"passed": True, "reason_code": "passed"},
                "source": {"passed": True, "reason_code": "passed"},
                "temporal": {"passed": True, "reason_code": "passed"},
            },
            "review_state": "automatically_admitted",
        }
    ]
    assert exceptions.status_code == 200
    assert exceptions.json() == []

    serialized = json.dumps(
        {
            "detail": detail.json(),
            "events": event_items,
            "evidence": evidence.json(),
            "exceptions": exceptions.json(),
        }
    ).casefold()
    for forbidden in (
        "raw_bytes",
        "request_snapshot",
        "policy_snapshot",
        "request_headers",
        "response_headers",
        "stack_trace",
        "credential",
        "lease_token",
        "policy_secret",
        '"facts"',
    ):
        assert forbidden not in serialized


def test_runner_exceptions_and_failure_summary_are_safely_projected(
    api_client,
    acquisition_api_scope,
    session,
):
    created = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-exception-1"},
        json={"objective": "support"},
    )
    assert created.status_code == 202
    job_id = _run_next_job(session, adapters={"sse": EvidenceAPIAdapter()})

    detail = api_client.get(f"/api/v1/acquisition-jobs/{job_id}")
    exceptions = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/exceptions")

    assert detail.status_code == 200
    assert detail.json()["status"] == "failed"
    assert detail.json()["error"] == {
        "code": "adapter_configuration_invalid",
        "summary": "acquisition job failed",
    }
    assert exceptions.status_code == 200
    exception_items = sorted(
        exceptions.json(), key=lambda item: item["detail"]["adapter_key"]
    )
    assert exception_items == [
        {
            "reason": "configured_adapter_unavailable",
            "detail": {"adapter_key": "gildata"},
            "created_at": exception_items[0]["created_at"],
        },
        {
            "reason": "configured_adapter_unavailable",
            "detail": {"adapter_key": "szse"},
            "created_at": exception_items[1]["created_at"],
        },
    ]


def test_public_reads_redact_nested_persisted_pollution_without_losing_safe_gate_data(
    api_client,
    acquisition_api_scope,
    session,
):
    created = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": "api-task9-pollution-1"},
        json={"objective": "support"},
    )
    assert created.status_code == 202
    job_id = _run_next_job(
        session,
        adapters={
            "gildata": EmptyAPIAdapter("gildata"),
            "sse": EvidenceAPIAdapter(),
            "szse": EmptyAPIAdapter("szse"),
        },
    )
    assert str(job_id) == created.json()["id"]

    job = session.get(AcquisitionJob, job_id)
    event = session.scalar(
        select(AcquisitionJobEvent)
        .where(AcquisitionJobEvent.job_id == job_id)
        .order_by(AcquisitionJobEvent.seq)
        .limit(1)
    )
    reference = session.scalar(
        select(SourceReference).where(SourceReference.job_id == job_id)
    )
    artifact = session.scalar(
        select(RetrievalArtifact)
        .join(
            SourceReference,
            SourceReference.id == RetrievalArtifact.source_reference_id,
        )
        .where(SourceReference.job_id == job_id)
    )
    decision = session.scalar(
        select(AutomaticAdmissionDecision).where(
            AutomaticAdmissionDecision.job_id == job_id
        )
    )
    assert all(value is not None for value in (job, event, reference, artifact, decision))
    assert job is not None
    assert event is not None
    assert reference is not None
    assert artifact is not None
    assert decision is not None

    job.error_code = "Traceback Authorization: Bearer detail-secret"
    session.add(
        AcquisitionException(
            job_id=job_id,
            source_reference_id=reference.id,
            retrieval_artifact_id=artifact.id,
            candidate_id=decision.candidate_id,
            reason_code="Traceback exception-secret",
            detail_json={
                "adapter_key": "sse",
                "candidate_id": str(decision.candidate_id),
                "provider_status": 401,
                "failed_gates": ["source", "temporal", "Traceback bad-gate"],
                "reason_codes": {
                    "source": "source_not_allowed",
                    "temporal": "Bearer nested-secret",
                    "Traceback bad-gate": "exception-secret",
                },
                "gate_version": "automatic-admission-gates-v1",
                "policy_version": "b-scope-v1",
                "nested": {
                    "Authorization": "Bearer exception-nested-secret"
                },
            },
            created_at=RUNNER_NOW,
        )
    )
    session.flush()
    _raw_update(
        session,
        table="acquisition_job_events",
        record_id=event.id,
        assignments={
            "message": "Traceback\nAuthorization: Bearer event-secret",
            "payload_json": {
                "actor": "Bearer actor-secret",
                "attempt": 2,
                "adapter_key": "sse",
                "operation": "Traceback operation-secret",
                "outcome": "failed",
                "reason_code": "Bearer event-reason-secret",
                "retryable": True,
                "nested": {"token": "event-nested-secret"},
            },
        },
    )
    _raw_update(
        session,
        table="source_references",
        record_id=reference.id,
        assignments={
            "title": "Traceback Authorization: Bearer title-secret",
            "canonical_url": (
                "https://user:password@www.sse.com.cn/report"
                "?access_token=url-secret"
            ),
        },
    )
    _raw_update(
        session,
        table="automatic_admission_decisions",
        record_id=decision.id,
        assignments={
            "gate_results": {
                "source": {
                    "passed": False,
                    "reason_code": "source_not_allowed",
                    "facts": {"Authorization": "Bearer gate-nested-secret"},
                },
                "temporal": {
                    "passed": False,
                    "reason_code": "Bearer gate-secret",
                },
                "locator": {
                    "passed": False,
                    "reason_code": "Traceback\nlocator-secret",
                },
                "semantic": {"passed": True, "reason_code": "passed"},
                "Traceback bad-gate": {
                    "passed": False,
                    "reason_code": "gate-name-secret",
                },
            }
        },
    )

    detail = api_client.get(f"/api/v1/acquisition-jobs/{job_id}")
    events = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/events")
    evidence = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/evidence")
    exceptions = api_client.get(f"/api/v1/acquisition-jobs/{job_id}/exceptions")

    assert detail.status_code == events.status_code == evidence.status_code == 200
    assert exceptions.status_code == 200
    assert detail.json()["error"]["code"] == "redacted"
    polluted_event = next(
        item for item in events.json() if item["seq"] == event.seq
    )
    assert polluted_event["message"] == "redacted"
    assert polluted_event["payload"] == {
        "attempt": 2,
        "adapter_key": "sse",
        "operation": "redacted",
        "outcome": "failed",
        "reason_code": "redacted",
        "retryable": True,
    }
    evidence_item = evidence.json()[0]
    assert evidence_item["source_title"] == "redacted"
    assert evidence_item["source_url"] is None
    assert evidence_item["gate_results"] == {
        "source": {"passed": False, "reason_code": "source_not_allowed"},
        "temporal": {"passed": False, "reason_code": "redacted"},
        "locator": {"passed": False, "reason_code": "redacted"},
        "semantic": {"passed": True, "reason_code": "passed"},
    }
    exception_item = exceptions.json()[0]
    assert exception_item["reason"] == "redacted"
    assert exception_item["detail"]["adapter_key"] == "sse"
    assert exception_item["detail"]["candidate_id"] == str(decision.candidate_id)
    assert exception_item["detail"]["provider_status"] == 401
    assert exception_item["detail"]["failed_gates"] == ["source", "temporal"]
    assert exception_item["detail"]["reason_codes"] == {
        "source": "source_not_allowed",
        "temporal": "redacted",
    }
    assert exception_item["detail"]["gate_version"] == (
        "automatic-admission-gates-v1"
    )
    assert exception_item["detail"]["policy_version"] == "b-scope-v1"

    serialized = json.dumps(
        {
            "detail": detail.json(),
            "events": events.json(),
            "evidence": evidence.json(),
            "exceptions": exceptions.json(),
        }
    ).casefold()
    for forbidden in (
        "secret",
        "traceback",
        "authorization",
        "bearer",
        "password",
        "access_token",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor", "tenant:attacker"),
        ("reviewer", "human:attacker"),
        ("adapter", "arbitrary-web"),
        ("url", "https://attacker.invalid/source"),
        ("host", "attacker.invalid"),
        ("policy_snapshot", {"version": "attacker-policy"}),
        ("source_permissions", ["arbitrary_web"]),
        ("allowed_source_roles", ["arbitrary_web"]),
        ("tenant_id", "other-team"),
        ("cutoff", "2099-01-01T00:00:00Z"),
        ("target_link_role", "supports"),
        ("entity_names", ["Attacker Corp"]),
        ("security_codes", ["000001"]),
        ("metric_terms", ["Attacker metric"]),
        ("period_start", "2000-01-01"),
        ("period_end", "2099-12-31"),
    ],
)
def test_post_acquisition_job_forbids_server_owned_fields(
    api_client,
    acquisition_api_scope,
    field,
    value,
):
    response = api_client.post(
        _create_path(acquisition_api_scope),
        headers={"Idempotency-Key": f"api-task9-forbid-{field}"},
        json={"objective": "support", field: value},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
