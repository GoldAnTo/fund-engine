"""HTTP ownership boundaries for legacy review entry points."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models.ledger import AIAssessment, EvidenceLink, EvidenceReview, ReviewDecision, Thesis
from app.models.proposals import Proposal, ProposalReviewDecision
from app.models.operational import ReviewAssignment
from app.models.events import DomainEvent


@pytest.mark.parametrize("credential,expected", [("", 401), ("Bearer foreign", 404), ("Bearer invalid", 403)])
def test_denied_reviews_never_write(cmd_client, cmd_seeded, monkeypatch, credential, expected):
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign":"foreign-team"}')
    link = cmd_seeded.scalar(select(EvidenceLink))
    assessment = cmd_seeded.scalar(select(AIAssessment))
    thesis = cmd_seeded.get(Thesis, link.thesis_id)
    proposal = Proposal(
        kind="evidence_link", payload={}, target_context={"thesis_id": str(thesis.id)},
        proposed_by_type="ai", proposed_by_ref="test", proposed_at=datetime.now(timezone.utc),
        research_case_id=thesis.research_case_id,
    )
    cmd_seeded.add(proposal)
    cmd_seeded.commit()
    models = (EvidenceReview, ReviewDecision, ProposalReviewDecision, ReviewAssignment, DomainEvent)
    before = [cmd_seeded.scalar(select(func.count()).select_from(m)) for m in models]
    requests = [
        (f"/evidence-links/{link.id}/reviews", {"outcome": "rejected", "factor_role": "test", "scope_boundary": "test", "reason": "test", "reviewer": "test"}),
        (f"/assessments/{assessment.id}/reviews", {"outcome": "rejected", "reason": "test"}),
        (f"/review-proposals/{proposal.id}/claim", None),
        (f"/review-proposals/{proposal.id}/decisions", {"outcome": "rejected", "reason": "test", "reviewer_id": "test", "expected_version": 1}),
    ]
    for path, body in requests:
        response = cmd_client.post(f"/api/v1{path}", json=body, headers={"Authorization": credential})
        assert response.status_code == expected, response.text
    assert before == [cmd_seeded.scalar(select(func.count()).select_from(m)) for m in models]


def test_foreign_review_collections_are_empty(cmd_client, cmd_seeded, monkeypatch):
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team","foreign":"foreign-team"}')
    case_id = cmd_seeded.scalar(select(Thesis.research_case_id))
    for path in ("/review-queue", "/review-proposals"):
        response = cmd_client.get(f"/api/v1{path}", headers={"Authorization": "Bearer foreign"})
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert cmd_client.get(f"/api/v1{path}", params={"case_id": str(case_id)}, headers={"Authorization": "Bearer foreign"}).status_code == 404
        assert cmd_client.get(f"/api/v1{path}", headers={"Authorization": ""}).status_code == 401


def test_orphan_proposal_is_not_claimable(cmd_client, cmd_session):
    proposal = Proposal(kind="evidence_link", payload={}, target_context={"thesis_id": str(uuid.uuid4())}, proposed_by_type="ai", proposed_by_ref="test", proposed_at=datetime.now(timezone.utc))
    cmd_session.add(proposal)
    cmd_session.commit()
    assert cmd_client.post(f"/api/v1/review-proposals/{proposal.id}/claim").status_code == 404
    assert cmd_session.scalar(select(func.count()).select_from(ReviewAssignment)) == 0


def test_proposal_queue_filters_before_limit_and_rejects_mismatched_target(
    cmd_client, cmd_seeded
):
    from app.models.ledger import ResearchCase
    from tests.tenant_admission import admit_case

    owner_thesis = cmd_seeded.scalar(select(Thesis))
    now = datetime.now(timezone.utc)
    foreign_case = ResearchCase(title="foreign", industry_topic="test", created_by="test", created_at=now)
    cmd_seeded.add(foreign_case)
    cmd_seeded.flush()
    admit_case(cmd_seeded, foreign_case.id, tenant_id="foreign-team")
    foreign_thesis = Thesis(research_case_id=foreign_case.id, statement="secret", created_by="test", created_at=now)
    cmd_seeded.add(foreign_thesis)
    cmd_seeded.flush()
    proposals = []
    # Foreign and inconsistent rows sort before the authorized row.
    for case_id, thesis_id in (
        (foreign_case.id, foreign_thesis.id),
        (owner_thesis.research_case_id, foreign_thesis.id),
        (owner_thesis.research_case_id, owner_thesis.id),
    ):
        proposal = Proposal(kind="evidence_link", payload={}, target_context={"thesis_id": str(thesis_id)}, research_case_id=case_id, proposed_by_type="ai", proposed_by_ref="test", proposed_at=datetime(2026, 1, 1 + len(proposals), tzinfo=timezone.utc))
        cmd_seeded.add(proposal)
        proposals.append(proposal)
    cmd_seeded.commit()
    response = cmd_client.get("/api/v1/review-proposals", params={"limit": 1})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(proposals[-1].id)]
    assert cmd_client.post(f"/api/v1/review-proposals/{proposals[1].id}/claim").status_code == 404
    assert cmd_client.post(f"/api/v1/review-proposals/{proposals[-1].id}/claim").status_code == 201


def test_foreign_replacement_source_cannot_publish(cmd_client, cmd_seeded):
    from tests.test_proposal_review_api import _seed_event_evidence_proposal
    from app.models.versions import EvidenceLinkVersion

    own = _seed_event_evidence_proposal(cmd_seeded, source_url="https://www.cninfo.com.cn/own")
    other_case = _seed_event_evidence_proposal(cmd_seeded, source_url="https://www.cninfo.com.cn/other")
    cmd_seeded.add_all([own, other_case])
    cmd_seeded.commit()
    before = cmd_seeded.scalar(select(func.count()).select_from(ProposalReviewDecision))
    response = cmd_client.post(f"/api/v1/review-proposals/{own.id}/decisions", json={
        "outcome": "modified", "expected_version": 1, "reason": "replace", "reviewer_id": "test",
        "replacement_payload": other_case.payload,
    })
    assert response.status_code == 422, response.text
    assert cmd_seeded.scalar(select(func.count()).select_from(ProposalReviewDecision)) == before
    assert cmd_seeded.scalar(select(func.count()).select_from(EvidenceLinkVersion)) == 0


@pytest.mark.parametrize("resource", ["proposal", "ai_assessment"])
@pytest.mark.parametrize("orphan", [False, True])
def test_review_does_not_close_task_from_another_case(cmd_client, cmd_seeded, resource, orphan):
    from app.models.ledger import ResearchCase
    from app.repositories.operational import TaskRepository
    from tests.test_proposal_review_api import _seed_event_evidence_proposal

    foreign_case = ResearchCase(title="other task", industry_topic="test", created_by="test", created_at=datetime.now(timezone.utc))
    cmd_seeded.add(foreign_case)
    cmd_seeded.flush()
    if resource == "proposal":
        target = _seed_event_evidence_proposal(cmd_seeded, source_url="https://www.cninfo.com.cn/review")
        cmd_seeded.add(target)
        cmd_seeded.flush()
        path = f"/review-proposals/{target.id}/decisions"
        payload = {"outcome": "rejected", "reason": "test", "reviewer_id": "test", "expected_version": 1}
        task_type = "review_proposal"
    else:
        target = cmd_seeded.scalar(select(AIAssessment))
        path = f"/assessments/{target.id}/reviews"
        payload = {"outcome": "rejected", "reason": "test"}
        task_type = "review_assessment"
    task = TaskRepository(cmd_seeded).add_task(title="foreign task", task_type=task_type, ref_type=resource, ref_id=target.id, research_case_id=None if orphan else foreign_case.id)
    cmd_seeded.commit()
    response = cmd_client.post(f"/api/v1{path}", json=payload)
    assert response.status_code == 201, response.text
    cmd_seeded.refresh(task)
    assert task.status == "open"
