from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.errors import ConflictError
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    AtomicClaimReview,
    CaseDocumentVersion,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
)
from app.models.operational import Job, ResearchRun
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
    ResearchPreparationEvent,
)
from app.repositories.research_preparation import ResearchPreparationRepository
from app.services.atomic_claims import AtomicClaimService
from app.services.research_preparation import (
    ClaimDecision,
    PreparationFailureCode,
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


def _candidate(session, case: ResearchCase, *, suffix: str):
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
    session.add(
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
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


def _parse(service, preparation, candidates) -> None:
    service.complete_system_step(
        preparation.research_case_id,
        "parse_claims",
        {"candidates": [{"candidate_id": str(candidate.id)} for candidate in candidates]},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
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


def _snapshot(session, preparation: ResearchPreparation) -> dict[str, object]:
    return {
        "artifacts": len(session.scalars(select(ResearchPreparationArtifact)).all()),
        "reviews": len(session.scalars(select(AtomicClaimReview)).all()),
        "statements": len(session.scalars(select(SourceStatement)).all()),
        "events": len(session.scalars(select(ResearchPreparationEvent)).all()),
        "jobs": len(session.scalars(select(Job)).all()),
        "runs": len(session.scalars(select(ResearchRun)).all()),
        "state": (
            preparation.version,
            preparation.status,
            preparation.parse_claims_state,
            preparation.draft_protocol_state,
            preparation.draft_evidence_plan_state,
            preparation.claim_review_state,
            preparation.protocol_review_state,
            preparation.plan_review_state,
            preparation.research_run_id,
        ),
    }


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


def test_different_input_resets_preparation_stales_artifacts_and_discards_old_output(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="i" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="input-reset")
    _parse(service, preparation, [candidate])
    old_artifacts = list(
        session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        )
    )
    old_version = preparation.version
    old_fingerprint = preparation.input_fingerprint
    assert [artifact.state for artifact in old_artifacts] == ["current"]

    reset = service.create_for_case(case.id, input_fingerprint="j" * 64, actor="tester")

    assert reset.id == preparation.id
    assert reset.version == old_version + 1
    assert reset.parse_claims_state == "queued"
    assert reset.claim_review_state == "locked"
    assert reset.research_run_id is None
    assert [(artifact.id, artifact.state) for artifact in old_artifacts] == [
        (artifact.id, "stale") for artifact in old_artifacts
    ]
    assert len(
        session.scalars(
            select(Job).where(
                Job.correlation_id == f"{preparation.id}:{preparation.version}:parse_claims",
                Job.status.in_(("queued", "running")),
            )
        ).all()
    ) == 1
    artifact_snapshot = [
        (artifact.id, artifact.kind, artifact.state)
        for artifact in session.scalars(
            select(ResearchPreparationArtifact)
            .where(ResearchPreparationArtifact.research_preparation_id == preparation.id)
            .order_by(ResearchPreparationArtifact.sequence)
        )
    ]
    events_before = len(_events(session, preparation.id))

    result = service.complete_system_step(
        case.id,
        "parse_claims",
        {"candidates": []},
        expected_version=old_version,
        expected_fingerprint=old_fingerprint,
    )

    assert result.id == preparation.id
    assert [
        (artifact.id, artifact.kind, artifact.state)
        for artifact in session.scalars(
            select(ResearchPreparationArtifact)
            .where(ResearchPreparationArtifact.research_preparation_id == preparation.id)
            .order_by(ResearchPreparationArtifact.sequence)
        )
    ] == artifact_snapshot
    assert len(_events(session, preparation.id)) == events_before + 1
    assert _events(session, preparation.id)[-1].type == "preparation_output_discarded"
    assert session.scalars(select(ResearchRun)).all() == []


def test_system_completion_rejects_missing_guard_values_without_side_effects(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="o" * 64, actor="tester")
    baseline = _snapshot(session, preparation)

    with pytest.raises(ConflictError):
        service.complete_system_step(
            case.id,
            "parse_claims",
            {"candidates": []},
            expected_version=None,
            expected_fingerprint=preparation.input_fingerprint,
        )
    assert _snapshot(session, preparation) == baseline


def test_artifact_replacement_keeps_one_current_and_supersedes_predecessor(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="n" * 64, actor="tester")
    repository = ResearchPreparationRepository(session)

    first = repository.append_artifact(
        preparation,
        kind="atomic_claim_candidates",
        input_fingerprint=preparation.input_fingerprint,
        payload={"candidates": []},
    )
    second = repository.append_artifact(
        preparation,
        kind="atomic_claim_candidates",
        input_fingerprint=preparation.input_fingerprint,
        payload={"candidates": [{"candidate_id": str(uuid.uuid4())}]},
    )

    artifacts = list(
        session.scalars(
            select(ResearchPreparationArtifact)
            .where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            )
            .order_by(ResearchPreparationArtifact.sequence)
        )
    )
    assert [(artifact.id, artifact.state) for artifact in artifacts] == [
        (first.id, "superseded"),
        (second.id, "current"),
    ]
    assert len([artifact for artifact in artifacts if artifact.state == "current"]) == 1


def test_parse_completion_opens_only_claim_review_and_rejects_early_protocol(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="b" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="parse")

    _parse(service, preparation, [candidate])

    assert preparation.claim_review_state == "awaiting_review"
    assert preparation.protocol_review_state == "locked"
    assert preparation.status == "awaiting_claim_review"
    with pytest.raises(ConflictError):
        service.complete_system_step(
            case.id,
            "draft_protocol",
            {"draft": "too early"},
            expected_version=preparation.version,
            expected_fingerprint=preparation.input_fingerprint,
        )


def test_claim_modification_invalidates_downstream_and_queues_protocol_once(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="c" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="modified")
    _parse(service, preparation, [candidate])
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


def test_claim_decisions_require_exact_coverage_and_publish_exactly_once(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="k" * 64, actor="tester")
    candidates = [_candidate(session, case, suffix=f"coverage-{index}") for index in range(3)]
    extra = _candidate(session, case, suffix="coverage-extra")
    _parse(service, preparation, candidates)
    baseline = _snapshot(session, preparation)

    invalid_decision_sets = [
        [ClaimDecision(candidates[0].id, "confirmed", "reviewed")],
        [
            ClaimDecision(candidate.id, "confirmed", "reviewed")
            for candidate in candidates[:2]
        ] + [ClaimDecision(extra.id, "confirmed", "reviewed")],
        [
            ClaimDecision(candidates[0].id, "confirmed", "reviewed"),
            ClaimDecision(candidates[0].id, "confirmed", "reviewed"),
            ClaimDecision(candidates[1].id, "confirmed", "reviewed"),
        ],
    ]
    for decisions in invalid_decision_sets:
        with pytest.raises(ConflictError):
            service.confirm_claims(
                case.id,
                actor="reviewer",
                revision=preparation.version,
                decisions=decisions,
            )
        assert _snapshot(session, preparation) == baseline

    decisions = [
        ClaimDecision(candidates[0].id, "confirmed", "reviewed"),
        ClaimDecision(
            candidates[1].id,
            "modified",
            "reviewed",
            normalized_text="A reviewer-corrected statement",
        ),
        ClaimDecision(candidates[2].id, "rejected", "reviewed"),
    ]
    service.confirm_claims(
        case.id,
        actor="reviewer",
        revision=preparation.version,
        decisions=decisions,
    )

    reviews = list(session.scalars(select(AtomicClaimReview).order_by(AtomicClaimReview.id)))
    statements = list(session.scalars(select(SourceStatement).order_by(SourceStatement.id)))
    assert len(reviews) == 3
    assert len(statements) == 2
    assert {review.idempotency_key for review in reviews} == {
        f"preparation:{preparation.id}:1:claim:{candidate.id}" for candidate in candidates
    }
    assert {review.outcome for review in reviews if review.published_source_statement_id is not None} == {
        "confirmed",
        "modified",
    }
    assert [review for review in reviews if review.outcome == "rejected"][0].published_source_statement_id is None
    successful_snapshot = _snapshot(session, preparation)

    # The command is version-guarded: replaying an already successful request
    # with its old revision is a conflict with no duplicate ledger rows.
    with pytest.raises(ConflictError):
        service.confirm_claims(
            case.id,
            actor="reviewer",
            revision=1,
            decisions=decisions,
        )
    assert _snapshot(session, preparation) == successful_snapshot


def test_non_modifying_claim_confirmation_then_protocol_confirmation_queues_plan_once(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="d" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="confirmed")
    _parse(service, preparation, [candidate])

    _confirm_claims(service, case.id, preparation.version, [candidate])

    assert preparation.version == 1
    assert len(_active_jobs(session, preparation.id, "draft_protocol")) == 1
    service.complete_system_step(
        case.id,
        "draft_protocol",
        {"draft": "protocol"},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
    )
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
    candidate = _candidate(session, case, suffix="retry")
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    service.complete_system_step(
        case.id,
        "draft_protocol",
        {"draft": "protocol"},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
    )
    service.confirm_protocol(case.id, actor="reviewer", revision=1, payload=ProtocolConfirmation(2, {}))
    service.mark_step_failed(
        case.id,
        "draft_evidence_plan",
        error_code="provider_unavailable",
        retry_at=None,
    )

    before = {
        artifact.kind: (artifact.payload, artifact.state)
        for artifact in session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        )
    }
    protocol_jobs_before = len(_active_jobs(session, preparation.id, "draft_protocol"))
    service.retry_failed_step(case.id, actor="reviewer", revision=preparation.version)

    assert preparation.draft_evidence_plan_state == "queued"
    assert len(_active_jobs(session, preparation.id, "draft_evidence_plan")) == 1
    assert len(_active_jobs(session, preparation.id, "draft_protocol")) == protocol_jobs_before
    assert {
        artifact.kind: (artifact.payload, artifact.state)
        for artifact in session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        )
    } == before


def test_stale_output_records_discard_without_replacing_current_artifact(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="f" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="discard")
    _parse(service, preparation, [candidate])
    current = session.scalar(
        select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation.id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        )
    )
    assert current is not None
    artifact_count = len(
        session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        ).all()
    )

    result = service.complete_system_step(
        case.id,
        "parse_claims",
        {"candidates": []},
        expected_version=preparation.version + 1,
        expected_fingerprint="z" * 64,
    )

    assert result.id == preparation.id
    assert session.scalar(
        select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation.id,
            ResearchPreparationArtifact.kind == "atomic_claim_candidates",
            ResearchPreparationArtifact.state == "current",
        )
    ).id == current.id
    assert len(
        session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        ).all()
    ) == artifact_count
    assert _events(session, preparation.id)[-1].type == "preparation_output_discarded"


def test_stale_revision_writes_no_downstream_rows(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="g" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="revision")
    _parse(service, preparation, [candidate])

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


def test_stale_claim_and_protocol_revisions_have_no_side_effects(session) -> None:
    claim_case = _case(session)
    claim_service = _service(session)
    claim_preparation = claim_service.create_for_case(
        claim_case.id, input_fingerprint="l" * 64, actor="tester"
    )
    claim_candidate = _candidate(session, claim_case, suffix="stale-claim")
    _parse(claim_service, claim_preparation, [claim_candidate])
    claim_snapshot = _snapshot(session, claim_preparation)

    with pytest.raises(ConflictError):
        claim_service.confirm_claims(
            claim_case.id,
            actor="reviewer",
            revision=claim_preparation.version + 1,
            decisions=[ClaimDecision(claim_candidate.id, "confirmed", "reviewed")],
        )
    assert _snapshot(session, claim_preparation) == claim_snapshot

    protocol_case = _case(session)
    protocol_service = _service(session)
    protocol_preparation = protocol_service.create_for_case(
        protocol_case.id, input_fingerprint="m" * 64, actor="tester"
    )
    protocol_candidate = _candidate(session, protocol_case, suffix="stale-protocol")
    _parse(protocol_service, protocol_preparation, [protocol_candidate])
    _confirm_claims(
        protocol_service,
        protocol_case.id,
        protocol_preparation.version,
        [protocol_candidate],
    )
    protocol_service.complete_system_step(
        protocol_case.id,
        "draft_protocol",
        {"draft": "protocol"},
        expected_version=protocol_preparation.version,
        expected_fingerprint=protocol_preparation.input_fingerprint,
    )
    protocol_snapshot = _snapshot(session, protocol_preparation)

    with pytest.raises(ConflictError):
        protocol_service.confirm_protocol(
            protocol_case.id,
            actor="reviewer",
            revision=protocol_preparation.version + 1,
            payload=ProtocolConfirmation(draft_sequence=2, edits={}),
        )
    assert _snapshot(session, protocol_preparation) == protocol_snapshot
    assert protocol_preparation.status != "authorized"
    assert session.scalars(select(ResearchRun)).all() == []


def test_provider_failure_rejects_raw_input_and_persists_only_fixed_code(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="h" * 64, actor="tester")
    baseline = _snapshot(session, preparation)

    with pytest.raises(ConflictError):
        service.mark_step_failed(
            case.id,
            "parse_claims",
            error_code="sk-secret Bearer https://provider.example/raw-response",
            retry_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    assert _snapshot(session, preparation) == baseline
    provider_failure: PreparationFailureCode = "provider_unavailable"
    service.mark_step_failed(
        case.id,
        "parse_claims",
        error_code=provider_failure,
        retry_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    events = _events(session, preparation.id)
    serialized = " ".join(f"{event.message} {event.detail}" for event in events)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert "sk-" not in serialized
    assert "Bearer" not in serialized
    assert "provider.example" not in serialized
    assert preparation.last_error_code == "preparation_provider_unavailable"
    assert events[-1].detail == {
        "error_code": "preparation_provider_unavailable",
        "retry_scheduled": True,
    }
    assert session.scalars(select(ResearchRun)).all() == []
