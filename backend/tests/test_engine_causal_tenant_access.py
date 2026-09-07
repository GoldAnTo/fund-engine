"""HTTP thesis commands reject access before provider setup or ledger writes."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models.ledger import AIRun, AIAssessment, CausalEdge, CausalStep, EvidenceSnapshot, ResearchCase, Thesis
from app.models.operational import Job
from app.models.events import DomainEvent
from app.models.proposals import Proposal


@pytest.mark.parametrize("credential,expected", [("", 401), ("Bearer foreign", 404), ("Bearer invalid", 403)])
def test_denied_thesis_commands_have_no_side_effects(cmd_client, cmd_seeded, monkeypatch, credential, expected):
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign":"foreign-team"}')
    thesis = cmd_seeded.scalar(select(Thesis))
    def forbidden_client():
        pytest.fail("unauthorized command constructed an LLM client")
    monkeypatch.setattr("app.api.v1.commands.engine.LLMClient.from_env", forbidden_client)
    models = (AIRun, Job, AIAssessment, EvidenceSnapshot, CausalStep, CausalEdge, DomainEvent, Proposal)
    before = [cmd_seeded.scalar(select(func.count()).select_from(m)) for m in models]
    for suffix, payload in (
        ("rerun", None), ("propose", None),
        ("causal-steps", {"description": "denied", "sequence": 99}),
        ("causal-edges", {"source_step_id": str(uuid.uuid4()), "target_step_id": str(uuid.uuid4()), "rationale": "denied"}),
    ):
        response = cmd_client.post(f"/api/v1/theses/{thesis.id}/{suffix}", json=payload, headers={"Authorization": credential})
        assert response.status_code == expected, response.text
    assert before == [cmd_seeded.scalar(select(func.count()).select_from(m)) for m in models]


@pytest.mark.parametrize("missing", [False, True])
def test_unadmitted_and_missing_theses_never_start_work(cmd_client, cmd_session, monkeypatch, missing):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="unadmitted", industry_topic="test", created_by="test-team", created_at=now)
    cmd_session.add(case)
    cmd_session.flush()
    thesis = Thesis(research_case_id=case.id, statement="legacy orphan", created_by="test-team", created_at=now)
    cmd_session.add(thesis)
    cmd_session.commit()
    thesis_id = uuid.uuid4() if missing else thesis.id
    def forbidden_client():
        pytest.fail("unowned thesis constructed an LLM client")
    monkeypatch.setattr("app.api.v1.commands.engine.LLMClient.from_env", forbidden_client)
    for suffix, payload in (
        ("rerun", None), ("propose", None),
        ("causal-steps", {"description": "denied", "sequence": 1}),
        ("causal-edges", {"source_step_id": str(uuid.uuid4()), "target_step_id": str(uuid.uuid4()), "rationale": "denied"}),
    ):
        response = cmd_client.post(f"/api/v1/theses/{thesis_id}/{suffix}", json=payload)
        assert response.status_code == 404, response.text
    for model in (AIRun, Job, CausalStep, CausalEdge, DomainEvent, Proposal):
        assert cmd_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("nested_side", ["source", "target"])
@pytest.mark.parametrize("other_tenant", ["test-team", "foreign-team"])
def test_nested_step_must_belong_to_url_thesis(cmd_client, cmd_seeded, nested_side, other_tenant):
    from tests.tenant_admission import admit_case

    own = cmd_seeded.scalar(select(Thesis))
    now = datetime.now(timezone.utc)
    other_case = ResearchCase(title="other", industry_topic="test", created_by="test", created_at=now)
    cmd_seeded.add(other_case)
    cmd_seeded.flush()
    admit_case(cmd_seeded, other_case.id, tenant_id=other_tenant)
    other = Thesis(research_case_id=other_case.id, statement="other", created_by="test", created_at=now)
    cmd_seeded.add(other)
    cmd_seeded.flush()
    own_step = CausalStep(thesis_id=own.id, description="own step", sequence=90, created_at=now)
    other_step = CausalStep(thesis_id=other.id, description="other step", sequence=1, created_at=now)
    cmd_seeded.add_all([own_step, other_step])
    cmd_seeded.commit()
    counts = [cmd_seeded.scalar(select(func.count()).select_from(m)) for m in (CausalEdge, DomainEvent)]
    payload = {"source_step_id": str(own_step.id), "target_step_id": str(own_step.id), "rationale": "attempt"}
    payload[f"{nested_side}_step_id"] = str(other_step.id)
    response = cmd_client.post(f"/api/v1/theses/{own.id}/causal-edges", json=payload)
    assert response.status_code == 404, response.text
    assert counts == [cmd_seeded.scalar(select(func.count()).select_from(m)) for m in (CausalEdge, DomainEvent)]
