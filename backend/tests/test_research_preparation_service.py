from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.errors import ConflictError
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import DocumentVersion, ResearchCase, SourceSpan
from app.models.operational import Job, ResearchRun
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
    ResearchPreparationEvent,
)
from app.services.atomic_claims import AtomicClaimService
from app.services.research_preparation import (
    ClaimDecision,
    ProtocolConfirmation,
    ResearchPreparationService,
)


def _case(session) -> ResearchCase:
    case = ResearchCase(
        title="Preparation state machine",
        industry_topic="test",
        created_by="tester",
        created_at=datetime.now(UTC),
    )
    session.add(case)
    session.flush()
    return case


def _candidate(session, *, suffix: str):
    now = datetime.now(UTC)
    quote = f"Candidate {suffix} is grounded in the source."
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex * 2,
        source_url=f"https://example.test/{suffix}",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text=quote)
    session.add(span)
    session.flush()
    return AtomicClaimService(session).admit(
        AtomicClaimDraft(
            source_span_id=span.id,
            quote=quote,
            quote_start=0,
            quote_end=len(quote),
            normalized_text=f"Candidate {suffix} normalized",
            claim_type="forecast",
            assertion_actor="company_management",
            subject="company",
            predicate="forecast",
            object_text=None,
            numeric_value=None,
            unit=None,
            observed_period=None,
            scope={},
        ),
        authority_level="primary_disclosure",
        run_ref=f"preparation-test:{suffix}",
    )


def _service(session) -> ResearchPreparationService:
    return ResearchPreparationService(session)


def _parse(service, case_id, candidates) -> None:
    service.complete_system_step(
        case_id,
        "parse_claims",
        {"candidates": [{"candidate_id": str(candidate.id)} for candidate in candidates]},
    )


def _confirm_claims(service, case_id, revision, candidates, *, modified: bool = False) -> None:
    decisions = [
        ClaimDecision(
            candidate_id=candidate.id,
            outcome="modified" if modified and index == 0 else "confirmed",
            reason="reviewed",
            normalized_text="Updated normalized claim" if modified and index == 0 else None,
        )
        for index, candidate in enumerate(candidates)
    ]
    service.confirm_claims(case_id, actor="reviewer", revision=revision, decisions=decisions)


def _events(session, preparation_id):
    return list(
        session.scalars(
            select(ResearchPreparationEvent)
            .where(ResearchPreparationEvent.research_preparation_id == preparation_id)
            .order_by(ResearchPreparationEvent.seq)
        )
    )


def _active_jobs(session, preparation_id, step: str):
    return list(
        session.scalars(
            select(Job).where(
                Job.target_id == preparation_id,
                Job.correlation_id.like(f"%:{step}"),
                Job.status.in_(("queued", "running")),
            )
        )
    )


def test_create_is_idempotent_and_queues_only_parse(session) -> None:
    case = _case(session)
    service = _service(session)

    first = service.create_for_case(case.id, input_fingerprint="a" * 64, actor="tester")
    second = service.create_for_case(case.id, input_fingerprint="a" * 64, actor="tester")

    assert second.id == first.id
    assert first.status == "preparing"
    assert first.parse_claims_state == "queued"
    assert first.protocol_review_state == "locked"
    assert len(session.scalars(select(ResearchPreparation)).all()) == 1
    assert len(_active_jobs(session, first.id, "parse_claims")) == 1
    assert [event.seq for event in _events(session, first.id)] == [1]
    assert session.scalars(select(ResearchRun)).all() == []


def test_parse_completion_opens_only_claim_review_and_rejects_early_protocol(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="b" * 64, actor="tester")
    candidate = _candidate(session, suffix="parse")

    _parse(service, case.id, [candidate])

    assert preparation.claim_review_state == "awaiting_review"
    assert preparation.protocol_review_state == "locked"
    assert preparation.status == "awaiting_claim_review"
    with pytest.raises(ConflictError):
        service.complete_system_step(case.id, "draft_protocol", {"draft": "too early"})


def test_claim_modification_invalidates_downstream_and_queues_protocol_once(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="c" * 64, actor="tester")
    candidate = _candidate(session, suffix="modified")
    _parse(service, case.id, [candidate])
    old_version = preparation.version

    _confirm_claims(service, case.id, old_version, [candidate], modified=True)

    assert preparation.version == old_version + 1
    assert preparation.draft_protocol_state == "queued"
    assert preparation.draft_evidence_plan_state == "stale"
    assert preparation.protocol_review_state == "locked"
    assert len(_active_jobs(session, preparation.id, "draft_protocol")) == 1
    assert all(
        artifact.state != "current"
        for artifact in session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind.in_(("research_protocol_draft", "evidence_acquisition_plan")),
            )
        )
    )


def test_non_modifying_claim_confirmation_then_protocol_confirmation_queues_plan_once(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="d" * 64, actor="tester")
    candidate = _candidate(session, suffix="confirmed")
    _parse(service, case.id, [candidate])

    _confirm_claims(service, case.id, preparation.version, [candidate])

    assert preparation.version == 1
    assert len(_active_jobs(session, preparation.id, "draft_protocol")) == 1
    service.complete_system_step(case.id, "draft_protocol", {"draft": "protocol"})
    protocol = service.confirm_protocol(
        case.id,
        actor="reviewer",
        revision=preparation.version,
        payload=ProtocolConfirmation(draft_sequence=2, edits={"title": "confirmed"}),
    )

    assert protocol.protocol_review_state == "confirmed"
    assert len(_active_jobs(session, preparation.id, "draft_evidence_plan")) == 1
    assert session.scalars(select(ResearchRun)).all() == []


def test_failed_plan_preserves_prior_artifacts_and_manual_retry_only_queues_plan(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="e" * 64, actor="tester")
    candidate = _candidate(session, suffix="retry")
    _parse(service, case.id, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    service.complete_system_step(case.id, "draft_protocol", {"draft": "protocol"})
    service.confirm_protocol(case.id, actor="reviewer", revision=1, payload=ProtocolConfirmation(2, {}))
    service.mark_step_failed(case.id, "draft_evidence_plan", error_code="provider_timeout", retry_at=None)

    before = list(session.scalars(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id == preparation.id)))
    protocol_jobs_before = len(_active_jobs(session, preparation.id, "draft_protocol"))
    service.retry_failed_step(case.id, actor="reviewer", revision=preparation.version)

    assert preparation.draft_evidence_plan_state == "queued"
    assert len(_active_jobs(session, preparation.id, "draft_evidence_plan")) == 1
    assert len(_active_jobs(session, preparation.id, "draft_protocol")) == protocol_jobs_before
    assert {artifact.kind for artifact in before} == {"atomic_claim_candidates", "research_protocol_draft"}


def test_stale_output_records_discard_without_replacing_current_artifact(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="f" * 64, actor="tester")
    candidate = _candidate(session, suffix="discard")
    _parse(service, case.id, [candidate])
    current = session.scalar(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id == preparation.id))

    result = service.complete_system_step(
        case.id,
        "parse_claims",
        {"candidates": []},
        expected_version=preparation.version + 1,
        expected_fingerprint="z" * 64,
    )

    assert result.id == preparation.id
    assert session.scalar(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id == preparation.id)).id == current.id
    assert _events(session, preparation.id)[-1].type == "preparation_output_discarded"


def test_stale_revision_writes_no_downstream_rows(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="g" * 64, actor="tester")
    candidate = _candidate(session, suffix="revision")
    _parse(service, case.id, [candidate])

    with pytest.raises(ConflictError):
        service.confirm_claims(case.id, actor="reviewer", revision=preparation.version + 1, decisions=[])

    assert list(session.scalars(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id == preparation.id)))
    assert list(
        session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind.in_(
                    ("research_protocol_draft", "evidence_acquisition_plan")
                ),
            )
        )
    ) == []
    assert _active_jobs(session, preparation.id, "draft_protocol") == []
    assert _active_jobs(session, preparation.id, "draft_evidence_plan") == []


def test_activity_events_are_contiguous_and_sanitize_provider_error_text(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="h" * 64, actor="tester")
    service.mark_step_failed(
        case.id,
        "parse_claims",
        error_code="sk-secret Bearer raw provider response",
        retry_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    events = _events(session, preparation.id)
    serialized = " ".join(f"{event.message} {event.detail}" for event in events)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert "sk-" not in serialized
    assert "Bearer" not in serialized
    assert "raw provider response" not in serialized
    assert session.scalars(select(ResearchRun)).all() == []
