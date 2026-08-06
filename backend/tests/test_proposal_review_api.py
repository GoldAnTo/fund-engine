"""Tests for the unified Proposal + ReviewDecision flow (design §5.3 / §9.2).

Key guarantees:
  * AI propose writes Proposals, not reviewed EvidenceLinks.
  * An evidence_link proposal, when confirmed/modified, publishes a formal
    EvidenceLinkVersion (+ legacy EvidenceLink row for the transition window).
  * Concurrent decisions collide via expected_version (409 review_conflict).
  * rejected proposals emit no formal entity.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.ledger import EvidenceLink
from app.models.proposals import Proposal, ProposalReviewDecision
from app.models.versions import EvidenceLinkVersion
from app.repositories.operational import TaskRepository


def _seed_proposal(cmd_session, *, research_case_id=None) -> Proposal:
    from app.repositories.proposals import ProposalRepository

    repo = ProposalRepository(cmd_session)
    return repo.add_proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(uuid.uuid4()),
            "role": "supports",
            "reason": "orders rose",
            "scope": {"segment": "DC"},
        },
        target_context={"thesis_id": str(uuid.uuid4()), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="mock",
        research_case_id=research_case_id,
    )


def test_confirmed_proposal_publishes_evidence_link_version(
    cmd_client, cmd_session
):
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()

    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "looks correct",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["outcome"] == "confirmed"
    assert body["published_entity_id"]

    # A formal EvidenceLinkVersion was published.
    version = cmd_session.scalars(select(EvidenceLinkVersion)).first()
    assert version is not None
    assert version.proposal_id == proposal.id
    assert version.review_decision_id == uuid.UUID(body["id"])
    # Transition-window legacy row also written.
    legacy = cmd_session.scalars(select(EvidenceLink)).first()
    assert legacy is not None
    assert legacy.review_state == "reviewed"


def test_modified_proposal_publishes_replacement(cmd_client, cmd_session):
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()

    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "modified",
            "reason": "scope too broad",
            "expected_version": 1,
            "reviewer_id": "human:alice",
            "replacement_payload": {
                "source_statement_id": proposal.payload["source_statement_id"],
                "role": "supports",
                "reason": "orders rose in DC only",
                "scope": {"segment": "DC", "region": "CN"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    version = cmd_session.scalars(select(EvidenceLinkVersion)).first()
    assert version is not None
    assert version.scope["region"] == "CN"


def test_rejected_proposal_publishes_no_entity(cmd_client, cmd_session):
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()

    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "rejected",
            "reason": "not supported by statement",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["published_entity_id"] is None
    assert cmd_session.scalars(select(EvidenceLinkVersion)).first() is None


def test_concurrent_decision_conflicts(cmd_client, cmd_session):
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()

    first = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "ok",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )
    assert first.status_code == 201

    # Second reviewer, still holding version=1, must be rejected as a conflict.
    second = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "also ok",
            "expected_version": 1,
            "reviewer_id": "human:bob",
        },
    )
    assert second.status_code == 409, second.text


def test_decision_rejects_stale_expected_version(cmd_client, cmd_session):
    """A reviewer holding a stale version must lose (optimistic concurrency)."""
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()
    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "ok",
            "expected_version": 99,  # never matches the real version (1)
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 409, resp.text


def test_decision_requires_expected_version_field(cmd_client, cmd_session):
    """``expected_version`` is mandatory — blind decisions are refused."""
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()
    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "ok",
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 422, resp.text


def test_modified_requires_replacement_payload(cmd_client, cmd_session):
    proposal = _seed_proposal(cmd_session)
    cmd_session.commit()
    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "modified",
            "reason": "needs change",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 422, resp.text


def test_queue_lists_pending_proposals(cmd_client, cmd_session):
    _seed_proposal(cmd_session)
    _seed_proposal(cmd_session)
    cmd_session.commit()
    resp = cmd_client.get("/api/v1/review-proposals")
    assert resp.status_code == 200
    assert resp.json()["items"]  # at least one pending
    assert all(item["status"] == "pending" for item in resp.json()["items"])


def test_decision_closes_review_proposal_task(cmd_client, cmd_session):
    """Deciding a proposal marks the matching review task done (any outcome)."""
    proposal = _seed_proposal(cmd_session)
    other = _seed_proposal(cmd_session)
    task_repo = TaskRepository(cmd_session)
    open_task = task_repo.add_task(
        title="Review proposal",
        task_type="review_proposal",
        status="open",
        ref_type="proposal",
        ref_id=proposal.id,
    )
    in_progress_other = task_repo.add_task(
        title="Review other proposal",
        task_type="review_proposal",
        status="in_progress",
        ref_type="proposal",
        ref_id=other.id,
    )
    cmd_session.commit()

    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "rejected",
            "reason": "not supported",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 201, resp.text
    cmd_session.refresh(open_task)
    cmd_session.refresh(in_progress_other)
    assert open_task.status == "done"
    # Only the decided proposal's task is closed.
    assert in_progress_other.status == "in_progress"

    # Idempotent: already-done task stays done and does not raise.
    already = task_repo.close_review_task(
        "review_proposal", "proposal", proposal.id
    )
    assert already is not None
    assert already.status == "done"
    # Missing task is a no-op (no error).
    assert (
        task_repo.close_review_task(
            "review_proposal", "proposal", uuid.uuid4()
        )
        is None
    )


def test_confirmed_decision_closes_in_progress_review_task(
    cmd_client, cmd_session
):
    proposal = _seed_proposal(cmd_session)
    task = TaskRepository(cmd_session).add_task(
        title="Review proposal",
        task_type="review_proposal",
        status="in_progress",
        ref_type="proposal",
        ref_id=proposal.id,
    )
    cmd_session.commit()

    resp = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "looks correct",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )
    assert resp.status_code == 201, resp.text
    cmd_session.refresh(task)
    assert task.status == "done"
