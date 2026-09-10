"""Private content reads expose only freshly authorized native evidence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text

from app.models.acquisition import AutomaticAdmissionDecision
from app.models.ledger import AtomicClaimCandidate, SourceSpan, SourceStatement, Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun, ResearchTask
from app.models.source_governance import SourceContract
from tests import test_research_gateway_api as gateway_api
from tests.test_research_gateway_artifact_authorization import (
    _copy_row,
    _evidence_document,
    complete_authorized_evidence,
)

BASE = gateway_api.BASE
gateway_client = gateway_api.gateway_client

SUMMARY_FIELDS = {
    "evidence_link_id",
    "task_id",
    "thesis_id",
    "thesis_statement",
    "statement",
    "document_version_id",
    "title",
    "source_authority",
    "source_url",
    "published_at",
    "observed_period",
    "relationship",
    "review_state",
}
CONTENT_FIELDS = {
    "conversation_id",
    "run_spec_id",
    "state",
    "reason_code",
    "draft_id",
    "result",
    "assessment_review",
    "evidence",
    "total_evidence",
    "truncated",
    "warnings",
}


def _path(spec, suffix="research"):
    return f"{BASE}/{spec.conversation_id}/runs/{spec.id}/{suffix}"


def _contract(session, link):
    document = _evidence_document(session, link)
    return session.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document.id)
    )


def _pending(client):
    response = client.post(
        BASE,
        headers={"Idempotency-Key": "content-pending"},
        json={"initial_message": "研究设备验证"},
    )
    assert response.status_code == 201
    return response.json()


def test_research_content_returns_exact_contract_and_native_result(
    gateway_client, cmd_session
):
    client, _ = gateway_client
    spec, run, links = complete_authorized_evidence(cmd_session)
    response = client.get(_path(spec))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == CONTENT_FIELDS
    assert body["conversation_id"] == str(spec.conversation_id)
    assert body["run_spec_id"] == str(spec.id)
    assert body["state"] == "available"
    assert body["reason_code"] is None
    assert body["draft_id"] == str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"fund-engine:event-research:automatic:{run.id}")
    )
    assert body["result"]["label"] == "系统生成，未经人工审核"
    assert body["result"]["human_reviewed"] is False
    assert body["result"]["conclusion"]
    assert body["total_evidence"] == len(links)
    assert body["truncated"] is False
    assert {row["evidence_link_id"] for row in body["evidence"]} == {
        str(link.id) for link in links
    }
    assert all(set(row) == SUMMARY_FIELDS for row in body["evidence"])
    assert all(
        row["source_authority"] == "primary_disclosure" for row in body["evidence"]
    )
    assert all(
        isinstance(row["title"], str) and row["title"] for row in body["evidence"]
    )
    assert any("未经人工审核" in warning for warning in body["warnings"])
    assert any("不等于已证实反证" in warning for warning in body["warnings"])
    assert any(
        "来自检索任务方向" in warning and "不代表已经验证" in warning
        for warning in body["warnings"]
    )
    assert any(
        "用户材料" in warning and "独立核验" in warning for warning in body["warnings"]
    )


def test_evidence_detail_is_exact_admitted_quote_and_bounded_locator(
    gateway_client, cmd_session
):
    client, _ = gateway_client
    spec, run, links = complete_authorized_evidence(cmd_session)
    link = links[0]
    statement = cmd_session.get(SourceStatement, link.source_statement_id)
    span = cmd_session.get(SourceSpan, statement.source_span_id)
    candidate = cmd_session.get(
        AtomicClaimCandidate, statement.atomic_claim_candidate_id
    )
    document = _evidence_document(cmd_session, link)
    decision = cmd_session.get(
        AutomaticAdmissionDecision, link.automatic_admission_decision_id
    )
    task = next(
        task
        for task in cmd_session.scalars(
            select(ResearchTask).where(ResearchTask.run_id == run.id)
        )
        if isinstance(task.result, dict)
        and task.result.get("acquisition_job_id") == str(decision.job_id)
    )

    response = client.get(_path(spec, f"evidence/{link.id}"))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == SUMMARY_FIELDS | {
        "conversation_id",
        "run_spec_id",
        "quote",
        "source_span_id",
        "locator",
        "content_sha256",
        "acquired_at",
    }
    assert body["quote"] == candidate.quote
    assert body["statement"] == statement.normalized_text
    assert body["task_id"] == str(task.id)
    assert body["thesis_statement"] == cmd_session.get(Thesis, link.thesis_id).statement
    assert body["source_span_id"] == str(span.id)
    assert body["content_sha256"] == document.content_sha256
    assert body["locator"] == {"page": 1, "paragraph": None}
    assert body["source_authority"] == "primary_disclosure"
    assert body["observed_period"] == "2026-01-31"
    assert body["review_state"] == "automatically_admitted"
    assert body["relationship"] == "supports"


def test_result_task_cannot_impersonate_the_authorized_source_task(
    gateway_client, cmd_session
):
    client, _ = gateway_client
    spec, run, links = complete_authorized_evidence(cmd_session)
    decision = cmd_session.get(
        AutomaticAdmissionDecision, links[0].automatic_admission_decision_id
    )
    source_task = next(
        task
        for task in cmd_session.scalars(
            select(ResearchTask).where(ResearchTask.run_id == run.id)
        )
        if task.task_type != "result"
        and isinstance(task.result, dict)
        and task.result.get("acquisition_job_id") == str(decision.job_id)
    )
    result_task = cmd_session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id, ResearchTask.task_type == "result"
        )
    )
    result_task.result = {
        **result_task.result,
        "acquisition_job_id": str(decision.job_id),
    }
    cmd_session.flush()
    # Fix the result row's index ordering so the pre-fix unrestricted mapping
    # deterministically visits it after the legitimate source task.
    cmd_session.execute(
        text("UPDATE research_tasks SET id = :new WHERE id = :old"),
        {
            "new": "ffffffffffffffffffffffffffffffff",
            "old": result_task.id.hex,
        },
    )
    cmd_session.commit()

    body = client.get(_path(spec)).json()
    summary = next(
        row for row in body["evidence"] if row["evidence_link_id"] == str(links[0].id)
    )
    assert summary["task_id"] == str(source_task.id)
    detail = client.get(_path(spec, f"evidence/{links[0].id}"))
    assert detail.status_code == 200
    assert detail.json()["task_id"] == str(source_task.id)


@pytest.mark.parametrize("token", ["bob-token", "foreign-token"])
def test_content_reads_keep_private_owner_and_tenant_boundary(
    gateway_client, cmd_session, token
):
    client, _ = gateway_client
    spec, _, links = complete_authorized_evidence(cmd_session)
    for suffix in ("research", f"evidence/{links[0].id}"):
        response = client.get(
            _path(spec, suffix), headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "research conversation not found"


def test_content_reads_reject_path_mismatch_and_cross_run_evidence(
    gateway_client, cmd_session
):
    client, _ = gateway_client
    spec, _, links = complete_authorized_evidence(cmd_session)
    other = _pending(client)
    for suffix in ("research", f"evidence/{links[0].id}"):
        path = f"{BASE}/{other['conversation_id']}/runs/{spec.id}/{suffix}"
        assert client.get(path).status_code == 404
    path = f"{BASE}/{other['conversation_id']}/runs/{other['run_spec_id']}/evidence/{links[0].id}"
    assert client.get(path).status_code == 404
    assert client.get(_path(spec, f"evidence/{uuid.uuid4()}")).status_code == 404


@pytest.mark.parametrize(
    "mutation", ["revoked", "expired", "tampered", "future", "binding"]
)
def test_every_read_reauthorizes_source_and_withholds_dependent_result(
    gateway_client, cmd_session, mutation
):
    client, _ = gateway_client
    spec, _, links = complete_authorized_evidence(cmd_session)
    assert client.get(_path(spec)).status_code == 200
    link = links[0]
    document = _evidence_document(cmd_session, link)
    if mutation in {"revoked", "expired"}:
        contract = _contract(cmd_session, link)
        column = "allow_display" if mutation == "revoked" else "effective_until"
        value = (
            False
            if mutation == "revoked"
            else (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        )
        cmd_session.execute(
            text(f"UPDATE source_contracts SET {column} = :value WHERE id = :id"),
            {"value": value, "id": contract.id.hex},
        )
    elif mutation == "tampered":
        cmd_session.execute(
            text(
                "UPDATE automatic_admission_decisions SET gate_results = '{}' WHERE id = :id"
            ),
            {"id": link.automatic_admission_decision_id.hex},
        )
    elif mutation == "future":
        cmd_session.execute(
            text("UPDATE document_versions SET published_at = :value WHERE id = :id"),
            {
                "value": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "id": document.id.hex,
            },
        )
    else:
        cmd_session.execute(
            text(
                "UPDATE research_run_specs SET frozen_source_policy_json = '{}' WHERE id = :id"
            ),
            {"id": spec.id.hex},
        )
    cmd_session.commit()
    response = client.get(_path(spec))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "withheld"
    assert body["reason_code"] == "result_not_authorized"
    assert body["result"] is None and body["draft_id"] is None
    assert str(link.id) not in {row["evidence_link_id"] for row in body["evidence"]}
    assert client.get(_path(spec, f"evidence/{link.id}")).status_code == 404


@pytest.mark.parametrize(
    "status,expected,reason",
    [
        ("queued", "pending", "result_pending"),
        ("running", "pending", "result_pending"),
        ("failed", "failed", "execution_failed"),
        ("cancelled", "cancelled", "execution_cancelled"),
        ("succeeded", "withheld", "result_not_authorized"),
    ],
)
def test_empty_result_state_uses_safe_native_status(
    gateway_client, cmd_session, status, expected, reason
):
    client, _ = gateway_client
    receipt = _pending(client)
    run = cmd_session.get(ResearchRun, uuid.UUID(receipt["native_run_id"]))
    run.status = status
    run.stop_reason = "SECRET provider token=abc prompt lease exception"
    cmd_session.commit()
    response = client.get(
        f"{BASE}/{receipt['conversation_id']}/runs/{receipt['run_spec_id']}/research"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == expected and body["reason_code"] == reason
    assert body["result"] is None and body["draft_id"] is None
    assert body["evidence"] == [] and body["total_evidence"] == 0
    assert response.headers["cache-control"] == "no-store"
    assert "SECRET" not in response.text


def test_historical_run_never_projects_current_case_result(gateway_client, cmd_session):
    client, _ = gateway_client
    spec, run, _ = complete_authorized_evidence(cmd_session)
    other_run = _copy_row(run)
    cmd_session.add(other_run)
    cmd_session.flush()
    lifecycle = cmd_session.get(EventResearchLifecycle, run.research_case_id)
    lifecycle.active_run_id = other_run.id
    cmd_session.commit()
    response = client.get(_path(spec))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "withheld" and body["result"] is None
    assert body["draft_id"] is None and body["reason_code"] == "result_not_authorized"


def test_result_rechecks_run_identity_after_artifact_authorization(
    gateway_client, cmd_session, monkeypatch
):
    from app.queries.automatic_research import AutomaticResearchQueries

    client, _ = gateway_client
    spec, _, _ = complete_authorized_evidence(cmd_session)
    native_get = AutomaticResearchQueries.get
    calls = []

    def current_case_switched(queries, case_id, tenant_id):
        view = native_get(queries, case_id, tenant_id)
        calls.append(view.run_id)
        return (
            view
            if len(calls) == 1
            else view.model_copy(update={"run_id": str(uuid.uuid4())})
        )

    monkeypatch.setattr(AutomaticResearchQueries, "get", current_case_switched)
    response = client.get(_path(spec))
    assert response.status_code == 200
    assert len(calls) == 2
    assert response.json()["state"] == "withheld"
    assert response.json()["result"] is None


def test_result_query_is_not_called_without_authorized_draft(
    gateway_client, cmd_session, monkeypatch
):
    from app.queries.automatic_research import AutomaticResearchQueries

    client, _ = gateway_client
    spec, _, links = complete_authorized_evidence(cmd_session)
    contract = _contract(cmd_session, links[0])
    cmd_session.execute(
        text("UPDATE source_contracts SET allow_display = 0 WHERE id = :id"),
        {"id": contract.id.hex},
    )
    cmd_session.commit()

    def forbidden_query(*args, **kwargs):
        pytest.fail("result query ran without a currently authorized draft")

    monkeypatch.setattr(AutomaticResearchQueries, "get", forbidden_query)
    response = client.get(_path(spec))
    assert response.status_code == 200
    assert response.json()["state"] == "withheld"


def test_wire_contract_caps_evidence_at_two_hundred(gateway_client, cmd_session):
    from pydantic import ValidationError

    from app.schemas.v1.research_gateway_content import ResearchContentDTO

    client, _ = gateway_client
    spec, _, _ = complete_authorized_evidence(cmd_session)
    body = client.get(_path(spec)).json()
    body["evidence"] = body["evidence"][:1] * 201
    with pytest.raises(ValidationError, match="at most 200"):
        ResearchContentDTO.model_validate(body)


def test_research_summary_limits_orm_rows_in_sql_with_more_than_two_hundred_admissions(
    gateway_client, cmd_session
):
    from app.acquisition.policy import B_SCOPE_POLICY
    from app.models.acquisition import AcquisitionJob
    from app.models.event_research import EventResearchScopeEvidenceAssignment
    from app.services.atomic_claims import AtomicClaimService
    from app.services.automatic_admission import (
        B_SCOPE_GATE_VERSION,
        AdmissionContext,
        AutomaticAdmissionGate,
    )
    from tests.test_automatic_admission import LEASE_TOKEN

    client, _ = gateway_client
    spec, _run, links = complete_authorized_evidence(cmd_session)
    original_decision = cmd_session.get(
        AutomaticAdmissionDecision, links[0].automatic_admission_decision_id
    )
    original_candidate = cmd_session.get(
        AtomicClaimCandidate, original_decision.candidate_id
    )
    assignment = cmd_session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.evidence_link_id == links[0].id
        )
    )
    job = cmd_session.get(AcquisitionJob, original_decision.job_id)
    job.status, job.stage = "running", "admitting"
    job.lease_owner = "system:acquisition-worker@gateway-test#bounded-content"
    job.lease_token = LEASE_TOKEN
    job.lease_expires_at = datetime.now(UTC) + timedelta(hours=1)
    cmd_session.flush()
    context = AdmissionContext(
        job_id=job.id,
        retrieval_artifact_id=original_decision.retrieval_artifact_id,
        thesis_id=job.thesis_id,
        cutoff=datetime.fromisoformat(job.request_snapshot["cutoff"]),
        objective=job.request_snapshot["objective"],
        target_link_role=links[0].role,
        gate_version=B_SCOPE_GATE_VERSION,
        policy_version=B_SCOPE_POLICY.version,
        allowed_source_roles=frozenset(job.request_snapshot["allowed_source_roles"]),
        metric_terms=tuple(job.request_snapshot["metric_terms"]),
        lease_token=LEASE_TOKEN,
    )
    for _ in range(201 - len(links)):
        candidate = _copy_row(original_candidate, canonical_key=uuid.uuid4().hex * 2)
        cmd_session.add(candidate)
        cmd_session.flush()
        decision = AutomaticAdmissionGate(cmd_session).evaluate(candidate, context)
        assert decision.outcome == "admitted"
        _, admitted_link = AtomicClaimService(cmd_session).publish_automatically(
            candidate.id, decision.id, lease_token=LEASE_TOKEN
        )
        cmd_session.add(_copy_row(assignment, evidence_link_id=admitted_link.id))
    job.status = job.stage = "succeeded"
    job.lease_owner = job.lease_token = job.lease_expires_at = None
    cmd_session.commit()

    statements = []

    def record_query(connection, cursor, statement, parameters, context, executemany):
        if (
            "JOIN source_references" in statement
            and "JOIN atomic_claim_candidates" in statement
        ):
            statements.append(statement)

    engine = cmd_session.get_bind()
    event.listen(engine, "before_cursor_execute", record_query)
    try:
        response = client.get(_path(spec))
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_evidence"] == 201
    assert len(body["evidence"]) == 200 and body["truncated"] is True
    assert statements and all("LIMIT" in statement for statement in statements)


@pytest.mark.parametrize("failure", ["binding", "row"])
@pytest.mark.parametrize("content", ["research", "evidence"])
def test_mid_read_source_projection_failure_is_withheld_or_not_found(
    gateway_client, cmd_session, monkeypatch, failure, content
):
    from app.services import research_gateway_content as reader

    client, _ = gateway_client
    spec, _, links = complete_authorized_evidence(cmd_session)
    if failure == "binding":

        def changed_binding(*args, **kwargs):
            raise ValueError("PRIVATE source mutation SECRET lease/prompt")

        monkeypatch.setattr(
            reader, "validated_gateway_source_bindings", changed_binding
        )
    else:
        # Simulate a row no longer present between native authorization and
        # the narrower projection query; the read must not keep the old draft.
        monkeypatch.setattr(
            reader.ResearchGatewayContent, "_evidence_rows", lambda *args, **kwargs: []
        )
    suffix = "research" if content == "research" else f"evidence/{links[0].id}"
    response = client.get(_path(spec, suffix))
    assert response.status_code == (200 if content == "research" else 404)
    assert "PRIVATE" not in response.text and "SECRET" not in response.text
    if content == "research":
        body = response.json()
        assert (
            body["state"] == "withheld"
            and body["reason_code"] == "result_not_authorized"
        )
        assert body["result"] is None and body["draft_id"] is None
        assert body["evidence"] == [] and body["total_evidence"] == 0
        assert body["truncated"] is False


def test_locator_retains_real_pdf_parser_page_and_paragraph():
    import hashlib
    import io

    from reportlab.pdfgen.canvas import Canvas

    from app.datasources.docling import PypdfAdapter
    from app.services.research_gateway_content import _locator

    raw = io.BytesIO()
    canvas = Canvas(raw)
    canvas.drawString(72, 720, "Source report page one.")
    canvas.showPage()
    canvas.drawString(72, 720, "Revenue was 100 USD.")
    canvas.save()
    payload = raw.getvalue()
    spans = PypdfAdapter().extract_spans(
        payload, document_sha256=hashlib.sha256(payload).hexdigest()
    )
    span = spans[-1]
    assert _locator(
        SourceSpan(locator={}, locator_v1=span.locator.model_dump())
    ).model_dump() == {
        "page": 2,
        "paragraph": 1,
    }


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "//example.org/report",
        "https://user:secret@example.org/report",
        "https://example.org/report?token=secret",
        "https://example.org/report?api_key=secret",
        "https://example.org/report?X-Amz-Signature=secret",
        "https://example.org/report?%61ccess_token=secret",
        "https://example.org/report?code=secret",
        "http://localhost/report",
        "http://127.0.0.1/report",
        "http://169.254.169.254/report",
        "http://[::1]/report",
        "http://10.0.0.1/report",
        "https://example.org\\@localhost/report",
        "https://example.org/report#token=secret",
        "https://example.org/report\n?token=secret",
        "https://example..org/report",
        "https://example.-org.com/report",
        "https://example.org/%zz",
    ],
)
def test_dangerous_or_ambiguous_source_links_are_omitted(url):
    from app.services.research_gateway_content import _public_source_url

    assert _public_source_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sse.com.cn/report.txt",
        "http://example.org/report.pdf",
        "https://example.org/report?id=123&page=2",
        "https://example.org/report#page=2",
    ],
)
def test_public_source_links_preserve_safe_source_locations(url):
    from app.services.research_gateway_content import _public_source_url

    assert _public_source_url(url) == url


@pytest.mark.parametrize(
    "legacy,v1,expected",
    [
        (
            {"page": 2, "paragraph": 3, "secret": "token"},
            None,
            {"page": 2, "paragraph": 3},
        ),
        ({"page_no": 7}, None, {"page": 7, "paragraph": None}),
        (
            {"page": 2},
            {
                "schema": "source-locator/v1",
                "page": 4,
                "extra": {"paragraph": 5, "secret": "token"},
            },
            {"page": 4, "paragraph": 5},
        ),
        (
            {"page": True, "paragraph": -1},
            {"page": "2", "paragraph": 9},
            {"page": None, "paragraph": None},
        ),
    ],
)
def test_locator_projection_only_reads_known_parser_fields(legacy, v1, expected):
    from app.services.research_gateway_content import _locator

    assert _locator(SourceSpan(locator=legacy, locator_v1=v1)).model_dump() == expected
