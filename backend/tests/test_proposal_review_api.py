"""Tests for the unified Proposal + ReviewDecision flow (design §5.3 / §9.2).

Key guarantees:
  * AI propose writes Proposals, not reviewed EvidenceLinks.
  * An evidence_link proposal, when confirmed/modified, publishes a formal
    EvidenceLinkVersion (+ legacy EvidenceLink row for the transition window).
  * Concurrent decisions collide via expected_version (409 review_conflict).
  * rejected proposals emit no formal entity.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.ledger import (
    AIAssessment,
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.proposals import Proposal, ProposalReviewDecision
from app.models.operational import ResearchRun, ResearchTask
from app.models.source_governance import SourceContract
from app.models.research_monitor import ResearchRunEvent
from app.models.versions import EvidenceLinkVersion
from app.repositories.operational import TaskRepository
from app.services.auto_research import AutoResearchService


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


def _seed_waiting_run_with_proposals(cmd_session, *, proposal_count: int):
    """Create one run-local proposal gate per requested proposal."""
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="run review reconciliation",
        industry_topic="testing",
        created_by="tester",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="review all outputs before completing the run",
        created_by="tester",
        created_at=now,
    )
    cmd_session.add(thesis)
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(run)
    cmd_session.flush()

    proposals = []
    for index in range(proposal_count):
        proposal = Proposal(
            kind="evidence_link",
            payload={"statement": f"candidate {index}"},
            target_context={
                "thesis_id": str(thesis.id),
                "entity_type": "evidence_link",
            },
            proposed_by_type="ai",
            proposed_by_ref="test-run",
            proposed_at=now,
            research_case_id=case.id,
        )
        cmd_session.add(proposal)
        cmd_session.flush()
        proposals.append(proposal)
        TaskRepository(cmd_session).add_task(
            title="Review automatic-research proposal",
            task_type="review_proposal",
            ref_type="proposal",
            ref_id=proposal.id,
            research_case_id=case.id,
        )

    cmd_session.add(
        ResearchTask(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="reviewable outputs",
            result={
                "proposed_proposal_ids": [str(proposal.id) for proposal in proposals]
            },
            created_at=now,
            updated_at=now,
        )
    )
    return run, proposals


def _seed_event_evidence_proposal(
    cmd_session, *, source_url: str, authorised_gildata: bool = False
) -> Proposal:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="event evidence admission",
        industry_topic="event",
        created_by="tester",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="event factor",
        created_by="tester",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=hashlib.sha256(source_url.encode()).hexdigest(),
        source_url=source_url,
        title="event source",
        available_at=now,
        acquired_at=now,
        parser_version="gildata-mcp-1" if authorised_gildata else "html-v1",
        parse_state="success",
    )
    cmd_session.add_all([thesis, document])
    cmd_session.flush()
    if authorised_gildata:
        cmd_session.add(
            SourceContract(
                document_version_id=document.id,
                source_type="licensed_provider",
                research_source_type="licensed_provider",
                provider_or_tenant="gildata",
                allow_ai_processing=True,
                allow_display=True,
                allow_export=False,
                allow_api=False,
                region="CN",
                effective_from=None,
                effective_until=None,
                retention_policy="case_retained",
                deletion_policy="contract_controlled",
                downstream_restrictions=[],
                contract_version=None,
                intake_metadata={},
                declared_by="tester",
                created_at=now,
            )
        )
    cmd_session.add(
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="event evidence",
    )
    cmd_session.add(span)
    cmd_session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="event evidence statement",
        created_at=now,
    )
    cmd_session.add(statement)
    cmd_session.flush()
    return Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": "event evidence supports the factor",
            "scope": {"period": "event"},
        },
        target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="mock",
        proposed_at=now,
        research_case_id=case.id,
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
    # The version must point to the actual formal edge.  SQLite does not
    # enforce foreign keys by default, so this assertion protects the same
    # integrity guarantee that PostgreSQL rejected in the live review flow.
    assert version.evidence_link_id == legacy.id


@pytest.mark.parametrize("outcome", ["confirmed", "modified"])
def test_invalid_event_source_decision_is_rejected_without_publication(
    cmd_client, cmd_session, outcome
):
    proposal = _seed_event_evidence_proposal(
        cmd_session, source_url="https://example.com/invalid"
    )
    cmd_session.add(proposal)
    cmd_session.flush()
    task = TaskRepository(cmd_session).add_task(
        title="Review invalid event evidence",
        task_type="review_proposal",
        status="open",
        ref_type="proposal",
        ref_id=proposal.id,
    )
    cmd_session.commit()

    decision = {
        "outcome": outcome,
        "reason": "looks correct",
        "expected_version": 1,
        "reviewer_id": "human:alice",
    }
    if outcome == "modified":
        decision["replacement_payload"] = {
            **proposal.payload,
            "reason": "narrowed event evidence",
        }
    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json=decision,
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    cmd_session.refresh(proposal)
    cmd_session.refresh(task)
    assert proposal.status == "pending"
    assert proposal.version == 1
    assert task.status == "open"
    assert cmd_session.scalars(select(ProposalReviewDecision)).all() == []
    assert cmd_session.scalars(select(EvidenceLink)).all() == []
    assert cmd_session.scalars(select(EvidenceLinkVersion)).all() == []


def test_valid_event_source_can_be_confirmed_and_published(cmd_client, cmd_session):
    proposal = _seed_event_evidence_proposal(
        cmd_session, source_url="https://investor.tsmc.com/english/quarterly-results/valid"
    )
    cmd_session.add(proposal)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "looks correct",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )

    assert response.status_code == 201, response.text
    assert cmd_session.scalars(select(EvidenceLink)).one().thesis_id == uuid.UUID(
        proposal.target_context["thesis_id"]
    )
    assert cmd_session.scalars(select(EvidenceLinkVersion)).one().proposal_id == proposal.id


def test_confirmed_authorised_gildata_proposal_publishes_formal_evidence(
    cmd_client, cmd_session
):
    proposal = _seed_event_evidence_proposal(
        cmd_session,
        source_url="gildata://research_report/content-sha256",
        authorised_gildata=True,
    )
    cmd_session.add(proposal)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "licence permits use",
            "reviewer_id": "human",
            "expected_version": proposal.version,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["published_entity_id"]


def test_event_evidence_publish_takes_the_case_lifecycle_lock(
    cmd_client, cmd_session, monkeypatch
):
    proposal = _seed_event_evidence_proposal(
        cmd_session, source_url="https://investor.tsmc.com/english/quarterly-results/lock"
    )
    cmd_session.add(proposal)
    cmd_session.commit()
    locked_case_ids: list[uuid.UUID] = []

    def record_lifecycle_lock(session, case_id):
        locked_case_ids.append(case_id)
        return None

    monkeypatch.setattr(
        "app.services.event_research_scope_evidence.lock_event_research_lifecycle",
        record_lifecycle_lock,
    )

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "looks correct",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )

    assert response.status_code == 201, response.text
    assert locked_case_ids == [proposal.research_case_id]


def test_modified_event_proposal_with_empty_replacement_uses_original_source(
    cmd_client, cmd_session
):
    proposal = _seed_event_evidence_proposal(
        cmd_session, source_url="https://investor.tsmc.com/english/quarterly-results/valid"
    )
    cmd_session.add(proposal)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "modified",
            "reason": "no payload changes",
            "expected_version": 1,
            "reviewer_id": "human:alice",
            "replacement_payload": {},
        },
    )

    assert response.status_code == 201, response.text
    version = cmd_session.scalars(select(EvidenceLinkVersion)).one()
    assert version.source_statement_id == uuid.UUID(
        proposal.payload["source_statement_id"]
    )


def test_modified_event_proposal_rejects_invalid_replacement_source(
    cmd_client, cmd_session
):
    proposal = _seed_event_evidence_proposal(
        cmd_session, source_url="https://investor.tsmc.com/english/quarterly-results/valid"
    )
    now = datetime.now(timezone.utc)
    invalid_document = DocumentVersion(
        content_sha256=hashlib.sha256(b"invalid replacement").hexdigest(),
        source_url="https://example.com/invalid-replacement",
        title="invalid replacement source",
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    cmd_session.add_all([proposal, invalid_document])
    cmd_session.flush()
    invalid_span = SourceSpan(
        document_version_id=invalid_document.id,
        locator={"page": 1},
        verbatim_text="invalid replacement evidence",
    )
    cmd_session.add(invalid_span)
    cmd_session.flush()
    invalid_statement = SourceStatement(
        source_span_id=invalid_span.id,
        kind="fact",
        normalized_text="invalid replacement statement",
        created_at=now,
    )
    cmd_session.add(invalid_statement)
    cmd_session.flush()
    task = TaskRepository(cmd_session).add_task(
        title="Review replacement source",
        task_type="review_proposal",
        status="open",
        ref_type="proposal",
        ref_id=proposal.id,
    )
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "modified",
            "reason": "change source",
            "expected_version": 1,
            "reviewer_id": "human:alice",
            "replacement_payload": {
                **proposal.payload,
                "source_statement_id": str(invalid_statement.id),
            },
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"
    cmd_session.refresh(proposal)
    cmd_session.refresh(task)
    assert proposal.status == "pending"
    assert proposal.version == 1
    assert task.status == "open"
    assert cmd_session.scalars(select(ProposalReviewDecision)).all() == []
    assert cmd_session.scalars(select(EvidenceLink)).all() == []
    assert cmd_session.scalars(select(EvidenceLinkVersion)).all() == []


def test_invalid_event_source_can_be_rejected(cmd_client, cmd_session):
    proposal = _seed_event_evidence_proposal(
        cmd_session, source_url="https://example.com/invalid"
    )
    cmd_session.add(proposal)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "rejected",
            "reason": "source is invalid",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.refresh(proposal)
    assert proposal.status == "decided"
    assert cmd_session.scalars(select(EvidenceLink)).all() == []
    assert cmd_session.scalars(select(EvidenceLinkVersion)).all() == []


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


def test_deciding_final_run_proposal_reconciles_run_once(cmd_client, cmd_session):
    run, proposals = _seed_waiting_run_with_proposals(cmd_session, proposal_count=2)
    proposal, already_decided = proposals
    already_decided.status = "decided"
    TaskRepository(cmd_session).close_review_task(
        "review_proposal", "proposal", already_decided.id
    )
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "rejected",
            "reason": "not supported",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.refresh(run)
    assert run.status == "succeeded"
    assert run.stage == "complete"
    events = list(
        cmd_session.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "review_complete")
        )
    )
    assert len(events) == 1
    assert events[0].payload_json["trigger_ref"] == f"proposal:{proposal.id}"

    assert not AutoResearchService(cmd_session).reconcile_run(
        run.id, trigger_ref=f"proposal:{proposal.id}"
    )
    assert (
        len(
            cmd_session.scalars(
                select(ResearchRunEvent)
                .where(ResearchRunEvent.run_id == run.id)
                .where(ResearchRunEvent.stage == "review_complete")
            ).all()
        )
        == 1
    )


def test_deciding_proposal_keeps_run_waiting_for_other_run_local_proposal(
    cmd_client, cmd_session
):
    run, proposals = _seed_waiting_run_with_proposals(cmd_session, proposal_count=2)
    proposal, _other_open_proposal = proposals
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "rejected",
            "reason": "not supported",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.refresh(run)
    assert run.status == "waiting_for_review"
    assert (
        cmd_session.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "review_complete")
        ).all()
        == []
    )


def test_deciding_proposal_keeps_run_waiting_for_run_local_assessment(
    cmd_client, cmd_session
):
    run, (proposal,) = _seed_waiting_run_with_proposals(
        cmd_session, proposal_count=1
    )
    now = datetime.now(timezone.utc)
    thesis = cmd_session.scalar(
        select(Thesis).where(Thesis.research_case_id == run.research_case_id)
    )
    assert thesis is not None
    snapshot = EvidenceSnapshot(
        thesis_id=thesis.id,
        cutoff=now,
        evidence_link_ids=[],
        created_at=now,
    )
    cmd_session.add(snapshot)
    cmd_session.flush()
    assessment = AIAssessment(
        snapshot_id=snapshot.id,
        conclusion="insufficient_evidence",
        rationale="needs another source",
        gaps=["another source"],
        created_at=now,
    )
    cmd_session.add(assessment)
    cmd_session.flush()
    cmd_session.add(
        ResearchTask(
            run_id=run.id,
            research_case_id=run.research_case_id,
            thesis_id=thesis.id,
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="provisional assessment",
            result={"assessment_id": str(assessment.id)},
            created_at=now,
            updated_at=now,
        )
    )
    TaskRepository(cmd_session).add_task(
        title="Review provisional assessment",
        task_type="review_assessment",
        ref_type="ai_assessment",
        ref_id=assessment.id,
        research_case_id=run.research_case_id,
    )
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/review-proposals/{proposal.id}/decisions",
        json={
            "outcome": "rejected",
            "reason": "not supported",
            "expected_version": 1,
            "reviewer_id": "human:alice",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.refresh(run)
    assert run.status == "waiting_for_review"
    assert (
        cmd_session.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "review_complete")
        ).all()
        == []
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
