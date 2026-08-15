from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.ledger import CaseTenantAdmission, DocumentVersion, ResearchCase, Thesis
from app.models.operational import Job, ResearchRun
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MechanismTemplateVersion,
)
from app.models.research_preparation import ResearchPreparationArtifact
from app.repositories.research_preparation import ResearchPreparationRepository
from app.scripts import run_research_preparation_worker as preparation_worker
from app.ai.research_preparation import ResearchPreparationGenerator, load_preparation_input


@dataclass
class _OpenAICompatibleFake:
    """A localhost-only OpenAI protocol probe used by the real LLM client."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    protocol: dict[str, Any] | None = None

    def response(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request["model"] == "preparation-integration-test-model"
        assert request["response_format"] == {"type": "json_object"}
        messages = request["messages"]
        assert isinstance(messages, list) and len(messages) == 2
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        payload = json.loads(messages[1]["content"])
        system = messages[0]["content"]
        self.calls.append({"system": system, "payload": payload})
        if "preparation-parse-claims-v1" in system:
            span = payload["spans"][0]
            text = span["verbatim_text"]
            return {"statements": [{
                "source_span_id": span["source_span_id"],
                "quote": text,
                "quote_start": 0,
                "quote_end": len(text),
                "normalized_text": text,
                "kind": "reported_claim",
            }]}
        if "preparation-protocol-v1" in system:
            assert self.protocol is not None, "test must configure the reviewed protocol fixture"
            return self.protocol
        if "preparation-evidence-plan-v1" in system:
            return {"items": [{
                "factor": factor,
                "evidence_target": "primary disclosure",
                "allowed_source_roles": ["primary_disclosure"],
                "priority": "normal",
                "stop_condition": "one reviewed source",
                "budget": 1,
            } for factor in payload["factors"]]}
        raise AssertionError("unexpected preparation prompt")


@pytest.fixture
def fake_openai_server(monkeypatch):
    fake = _OpenAICompatibleFake()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            assert self.path == "/v1/chat/completions"
            assert self.headers["Authorization"] == "Bearer preparation-integration-test-key"
            size = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(size))
            body = {"choices": [{"message": {"content": json.dumps(fake.response(request))}}]}
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LLM_API_KEY", "preparation-integration-test-key")
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("LLM_MODEL", "preparation-integration-test-model")
    try:
        yield fake
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _create_case(client) -> uuid.UUID:
    response = client.post("/api/v1/event-research", json={
        "raw_input": "The company disclosed that revenue increased in the quarter.",
        "source_type": "pasted_snapshot",
        "source_metadata": {"authority_level": "primary_disclosure"},
        "event_title": "Preparation integration case",
        "company_name": "Test company",
        "research_question": "Will demand convert into revenue?",
        "candidate_factors": ["demand", "delivery", "revenue"],
        "created_by": "integration-tester",
    })
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["case_id"])


def _worker_factory(session: Session):
    return sessionmaker(bind=session.bind, future=True)


def _run_worker_until_idle(session: Session) -> None:
    factory = _worker_factory(session)
    while preparation_worker.run_once(session_factory=factory):
        pass


def _configure_materializable_protocol(
    session: Session, *, case_id: uuid.UUID, fake: _OpenAICompatibleFake
) -> None:
    now = datetime.now(UTC)
    document_id = session.scalar(select(CaseTenantAdmission.initial_document_version_id).where(
        CaseTenantAdmission.research_case_id == case_id
    ))
    assert document_id is not None
    document = session.get(DocumentVersion, document_id)
    assert document is not None
    theses = list(session.scalars(select(Thesis).where(Thesis.research_case_id == case_id).order_by(Thesis.id)))
    assert len(theses) == 3
    template = MechanismTemplateVersion(
        template_key=f"integration-{case_id}", version=1, display_name="Integration template",
        industry_scope="test", supersedes_id=None, approved_by="integration-tester",
        reason="integration fixture", created_at=now,
    )
    session.add(template)
    session.flush()
    source = MechanismNodeVersion(template_version_id=template.id, node_key="demand", display_name="Demand", role="driver", created_at=now)
    target = MechanismNodeVersion(template_version_id=template.id, node_key="revenue", display_name="Revenue", role="outcome", created_at=now)
    session.add_all((source, target))
    session.flush()
    edge = MechanismEdgeVersion(template_version_id=template.id, edge_key="demand_to_revenue", source_node_id=source.id, target_node_id=target.id, created_at=now)
    session.add(edge)
    session.commit()
    available_at = document.available_at.replace(tzinfo=UTC).isoformat()
    baseline = {
        "source_ref": f"document:{document.id}", "value": "1", "unit": "yuan",
        "observed_period": "2025-12-31", "available_at": available_at,
    }
    fake.protocol = {
        "outcomes": [{
            "thesis_id": str(thesis.id),
            "metric": {
                "metric_id": f"integration-{thesis.id}", "display_name": "Revenue",
                "canonical_definition": "Quarterly revenue", "entity_scope": "company",
                "unit": "yuan", "frequency": "quarterly", "period_semantics": "period_end",
                "allowed_source_roles": ["primary_disclosure"], "role_eligibility": ["outcome"],
            },
            "binding": {
                "entity_scope": {"company_id": "test-company", "company": "Test company"},
                "direction": "increase", "baseline": baseline,
                "horizon_start": "2026-01-01", "horizon_end": "2026-12-31",
            },
            "template_version_id": str(template.id),
            "verification_rules": [{
                "mechanism_edge_id": str(edge.id), "expected_direction": "increase",
                "support_predicate": "reported increase", "contradiction_predicate": "reported decrease",
                "allowed_source_roles": ["primary_disclosure"],
                "observed_period_start": "2026-01-01", "observed_period_end": "2026-12-31",
                "available_at_deadline": "2027-01-01", "next_verification_event": "earnings",
            }],
        } for thesis in theses],
        "baseline": {"document_id": str(document.id)},
        "horizon": {"start": "2026-01-01", "end": "2026-12-31"},
        "mechanisms": [{"template_version_id": str(template.id)}],
        "verification_rules": [{"rule": "each outcome has a source-bound rule"}],
    }


def test_live_compatible_fake_provider_prepares_then_explicit_authorization_starts_one_run(
    cmd_client, cmd_session, fake_openai_server
) -> None:
    case_id = _create_case(cmd_client)
    _run_worker_until_idle(cmd_session)

    before = cmd_client.get(f"/api/v1/event-research/{case_id}/preparation")
    assert before.status_code == 200, before.text
    preparation = before.json()
    assert preparation["status"] == "awaiting_claim_review"
    assert preparation["research_run_id"] is None
    assert len(fake_openai_server.calls) == 1
    assert cmd_session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all() == []

    claims = preparation["artifacts"]["claims"]["payload"]["candidates"]
    confirmed = cmd_client.post(f"/api/v1/event-research/{case_id}/preparation/claims/confirm", json={
        "revision": preparation["revision"], "actor": "integration-reviewer",
        "decisions": [{"candidate_id": item["candidate_id"], "outcome": "confirmed", "reason": "verified"} for item in claims],
    })
    assert confirmed.status_code == 200, confirmed.text
    _configure_materializable_protocol(cmd_session, case_id=case_id, fake=fake_openai_server)
    _run_worker_until_idle(cmd_session)

    protocol_ready = cmd_client.get(f"/api/v1/event-research/{case_id}/preparation").json()
    assert protocol_ready["status"] == "awaiting_protocol_confirmation"
    protocol = protocol_ready["artifacts"]["protocol"]
    confirmed_protocol = cmd_client.post(f"/api/v1/event-research/{case_id}/preparation/protocol/confirm", json={
        "revision": protocol_ready["revision"], "actor": "integration-reviewer",
        "draft_sequence": protocol["sequence"], "edits": {},
    })
    assert confirmed_protocol.status_code == 200, confirmed_protocol.text
    _run_worker_until_idle(cmd_session)

    plan_ready = cmd_client.get(f"/api/v1/event-research/{case_id}/preparation").json()
    assert plan_ready["status"] == "awaiting_plan_authorization"
    plan = plan_ready["artifacts"]["plan"]
    assert len(fake_openai_server.calls) == 3
    assert cmd_session.scalars(
        select(ResearchRun).where(ResearchRun.research_case_id == case_id)
    ).all() == []
    authorized = cmd_client.post(f"/api/v1/event-research/{case_id}/preparation/authorize", json={
        "revision": plan_ready["revision"], "actor": "integration-reviewer",
        "plan_sequence": plan["sequence"], "idempotency_key": "integration-authorize-once",
    })
    assert authorized.status_code == 201, authorized.text
    after = authorized.json()
    assert after["status"] == "authorized"
    assert after["research_run_id"] is not None
    assert len(fake_openai_server.calls) == 3
    assert cmd_session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all()
    assert len(cmd_session.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)).all()) == 1


def test_crashed_preparation_job_is_reclaimed_without_duplicate_current_artifact(
    cmd_client, cmd_session, fake_openai_server
) -> None:
    """A reclaimed parse job makes one current artifact; stale output is discarded."""
    case_id = _create_case(cmd_client)
    factory = _worker_factory(cmd_session)
    with factory() as first:
        job = ResearchPreparationRepository(first).claim_next_preparation_job(now=datetime.now(UTC))
        assert job is not None
        stale_input = preparation_worker._begin(first, job)
        assert stale_input is not None and stale_input.claim_token is not None
        stale_job_id = job.id
        job.started_at = datetime.now(UTC) - timedelta(hours=1)
        first.commit()
    with factory() as stale_loader:
        loaded = load_preparation_input(stale_loader, case_id)
    assert preparation_worker.run_once(session_factory=factory)
    stale_drafts = ResearchPreparationGenerator().validate_claim_drafts(loaded)
    preparation_worker._complete(
        factory, job_id=stale_job_id, input=stale_input,
        generator=ResearchPreparationGenerator(), loaded_input=loaded, output=stale_drafts,
    )
    with factory() as check:
        current = list(check.scalars(select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        )))
        job = check.get(Job, stale_job_id)
        assert len(current) == 1
        assert job is not None and job.status == "succeeded"
        assert len(fake_openai_server.calls) == 2
