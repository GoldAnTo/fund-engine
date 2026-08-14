from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from threading import Event, Thread, get_ident

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import app.repositories.research_preparation as preparation_repository_module
from app.errors import ConflictError
from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    AtomicClaimReview,
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
    ValidationError,
)
from app.models.event_research import EventResearchScopeFactor, EventResearchScopeVersion
from app.models.operational import Job, ResearchRun
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MechanismTemplateVersion,
)
from app.models.source_governance import SourceContract
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


def _admit_candidate_document(session, case: ResearchCase, candidate) -> None:
    span = session.get(SourceSpan, candidate.source_span_id)
    assert span is not None
    session.add(
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id="test-team",
            initial_document_version_id=span.document_version_id,
            admitted_by="test-fixture",
            admitted_at=datetime.now(UTC),
        )
    )
    session.flush()


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


def _candidate_context(service, case_id) -> str:
    return service.current_candidate_context_fingerprint(case_id)


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


def _materializable_protocol(session, case: ResearchCase, candidate) -> dict[str, object]:
    """Build the narrowest full protocol accepted by the formal review gate."""
    now = datetime.now(UTC)
    span = session.get(SourceSpan, candidate.source_span_id)
    assert span is not None
    document = session.get(DocumentVersion, span.document_version_id)
    assert document is not None
    if session.scalar(select(CaseTenantAdmission).where(CaseTenantAdmission.research_case_id == case.id)) is None:
        session.add(CaseTenantAdmission(research_case_id=case.id, tenant_id="test-team", initial_document_version_id=document.id, admitted_by="test", admitted_at=now))
    if session.scalar(select(SourceContract).where(SourceContract.document_version_id == document.id)) is None:
        session.add(SourceContract(document_version_id=document.id, source_type="uploaded_file", provider_or_tenant="test", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="CN", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="test", intake_metadata={}, declared_by="test", created_at=now))
    thesis = Thesis(research_case_id=case.id, statement=f"Outcome {candidate.id}", research_protocol_required=True, created_by="test", created_at=now)
    session.add(thesis)
    session.flush()
    scope = EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by="test", change_summary="protocol fixture", created_at=now)
    session.add(scope)
    session.flush()
    session.add(EventResearchScopeFactor(scope_version_id=scope.id, statement=thesis.statement, description=None, position=1))
    template = MechanismTemplateVersion(template_key=f"protocol-{case.id}-{candidate.id}", version=1, display_name="Protocol fixture", industry_scope="test", supersedes_id=None, approved_by="test", reason="fixture", created_at=now)
    session.add(template)
    session.flush()
    source = MechanismNodeVersion(template_version_id=template.id, node_key="source", display_name="Source", role="driver", created_at=now)
    target = MechanismNodeVersion(template_version_id=template.id, node_key="target", display_name="Target", role="outcome", created_at=now)
    session.add_all((source, target))
    session.flush()
    edge = MechanismEdgeVersion(template_version_id=template.id, edge_key="source_to_target", source_node_id=source.id, target_node_id=target.id, created_at=now)
    session.add(edge)
    session.flush()
    baseline = {"source_ref": f"document:{document.id}", "value": "1", "unit": "yuan", "observed_period": "2025-12-31", "available_at": document.available_at.replace(tzinfo=UTC).isoformat()}
    return {
        "outcomes": [{"thesis_id": str(thesis.id), "metric": {"metric_id": f"metric-{candidate.id}", "display_name": "Revenue", "canonical_definition": "Quarterly revenue", "entity_scope": "company", "unit": "yuan", "frequency": "quarterly", "period_semantics": "period_end", "allowed_source_roles": ["primary_disclosure"], "role_eligibility": ["outcome"]}, "binding": {"entity_scope": {"company_id": "company", "company": "Company"}, "direction": "increase", "baseline": baseline, "horizon_start": "2026-01-01", "horizon_end": "2026-12-31"}, "template_version_id": str(template.id), "verification_rules": [{"mechanism_edge_id": str(edge.id), "expected_direction": "increase", "support_predicate": "supports", "contradiction_predicate": "contradicts", "allowed_source_roles": ["primary_disclosure"], "observed_period_start": "2026-01-01", "observed_period_end": "2026-12-31", "available_at_deadline": "2027-01-01", "next_verification_event": "earnings"}]}],
        "baseline": {"document_id": str(document.id), "review_note": "original"}, "horizon": {"start": "2026-01-01", "end": "2026-12-31"}, "mechanisms": [{"template_version_id": str(template.id)}], "verification_rules": [{"rule": "outcome rule"}],
    }


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
        research_case_id=case.id,
        kind="atomic_claim_candidates",
        input_fingerprint=preparation.input_fingerprint,
        payload={"candidates": []},
    )
    second = repository.append_artifact(
        preparation,
        research_case_id=case.id,
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


def test_direct_repository_mutators_lock_case_before_preparation_write(session, monkeypatch) -> None:
    case = _case(session)
    preparation = _service(session).create_for_case(
        case.id, input_fingerprint="p" * 64, actor="tester"
    )
    repository = ResearchPreparationRepository(session)
    lock_calls: list[uuid.UUID] = []
    original_lock = preparation_repository_module.lock_event_scope_case

    def observe_case_lock(db_session, case_id):
        lock_calls.append(case_id)
        return original_lock(db_session, case_id)

    monkeypatch.setattr(preparation_repository_module, "lock_event_scope_case", observe_case_lock)

    artifact = repository.append_artifact(
        preparation,
        research_case_id=case.id,
        kind="atomic_claim_candidates",
        input_fingerprint=preparation.input_fingerprint,
        payload={"candidates": []},
    )
    event = repository.append_event(
        preparation,
        research_case_id=case.id,
        type="repository_direct_write",
        step="parse_claims",
        message="direct repository write",
        detail={},
    )
    job = repository.queue_step_job(
        preparation,
        research_case_id=case.id,
        step="parse_claims",
    )

    assert lock_calls == [case.id, case.id, case.id]
    assert artifact.research_preparation_id == preparation.id
    assert event.research_preparation_id == preparation.id
    assert job.target_id == preparation.id


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
        ResearchPreparationService._claim_review_idempotency_key(decision)
        for decision in decisions
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
        _materializable_protocol(session, case, candidate),
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=_candidate_context(service, case.id),
    )
    protocol = service.confirm_protocol(
        case.id,
        actor="reviewer",
        revision=preparation.version,
        payload=ProtocolConfirmation(draft_sequence=2, edits={}),
    )

    assert protocol.protocol_review_state == "confirmed"
    assert len(_active_jobs(session, preparation.id, "draft_evidence_plan")) == 1
    assert session.scalars(select(ResearchRun)).all() == []


def test_protocol_completion_freezes_current_candidate_context_and_discards_stale_context(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="v" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="context")
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    current = _candidate_context(service, case.id)
    artifacts_before = len(session.scalars(select(ResearchPreparationArtifact)).all())

    discarded = service.complete_system_step(
        case.id,
        "draft_protocol",
        {"draft": "stale"},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint="0" * 64,
    )

    assert discarded is preparation
    assert len(session.scalars(select(ResearchPreparationArtifact)).all()) == artifacts_before
    assert _events(session, preparation.id)[-1].type == "preparation_output_discarded"
    service.complete_system_step(
        case.id,
        "draft_protocol",
        {"draft": "current"},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=current,
    )
    artifact = service._repo.current_artifact(preparation.id, "research_protocol_draft")
    assert artifact is not None and artifact.context_fingerprint == current


def test_modified_review_changes_current_candidate_context_fingerprint(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="w" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="context-modified")
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    confirmed = _candidate_context(service, case.id)
    service._claims.review(
        candidate.id,
        outcome="modified",
        reviewer="reviewer",
        reason="human correction",
        normalized_text="Human-corrected candidate context",
        idempotency_key="context-modified",
    )

    assert service.current_candidate_context_fingerprint(case.id) != confirmed


def test_protocol_confirmation_preserves_edits_in_successor_and_rejects_stale_sequence(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="q" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="protocol-edits")
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    service.complete_system_step(
        case.id,
        "draft_protocol",
        _materializable_protocol(session, case, candidate),
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=_candidate_context(service, case.id),
    )
    source = session.scalar(
        select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation.id,
            ResearchPreparationArtifact.kind == "research_protocol_draft",
            ResearchPreparationArtifact.state == "current",
        )
    )
    assert source is not None
    source_sequence = source.sequence
    stale_snapshot = _snapshot(session, preparation)

    with pytest.raises(ConflictError):
        service.confirm_protocol(
            case.id,
            actor="reviewer",
            revision=preparation.version,
            payload=ProtocolConfirmation(source_sequence + 1, {"title": "stale"}),
        )
    assert _snapshot(session, preparation) == stale_snapshot

    with pytest.raises(ValidationError):
        service.confirm_protocol(
            case.id,
            actor="reviewer",
            revision=preparation.version,
            payload=ProtocolConfirmation(
                source_sequence,
                {"Bearer sk-secret-123": "sentinel-secret-value"},
            ),
        )
    assert _snapshot(session, preparation) == stale_snapshot

    service.confirm_protocol(
        case.id,
        actor="reviewer",
        revision=preparation.version,
        payload=ProtocolConfirmation(
            source_sequence,
            {
                "baseline": {"review_note": "sentinel-secret-value"},
            },
        ),
    )

    artifacts = list(
        session.scalars(
            select(ResearchPreparationArtifact)
            .where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind == "research_protocol_draft",
            )
            .order_by(ResearchPreparationArtifact.sequence)
        )
    )
    assert [(artifact.sequence, artifact.state) for artifact in artifacts] == [
        (source_sequence, "superseded"),
        (source_sequence + 1, "current"),
    ]
    assert artifacts[-1].payload["baseline"]["review_note"] == "sentinel-secret-value"
    event = _events(session, preparation.id)[-1]
    assert event.detail == {
        "source_draft_sequence": source_sequence,
        "confirmed_draft_sequence": source_sequence + 1,
        "edit_count": 1,
    }
    assert "sentinel-secret-value" not in str(event.detail)
    assert "verification_rules" not in str(event.detail)
    assert len(_active_jobs(session, preparation.id, "draft_evidence_plan")) == 1


def test_confirm_protocol_rejects_candidate_context_changed_after_draft(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="x" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="protocol-context-stale")
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    original_context = _candidate_context(service, case.id)
    strict_payload = _materializable_protocol(session, case, candidate)
    service.complete_system_step(
        case.id,
        "draft_protocol",
        strict_payload,
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=original_context,
    )
    original = service._repo.current_artifact(preparation.id, "research_protocol_draft")
    assert original is not None
    AtomicClaimService(session).review(
        candidate.id,
        outcome="modified",
        reviewer="other-reviewer",
        reason="correction",
        normalized_text="human corrected protocol claim",
        idempotency_key="late-correction",
    )

    with pytest.raises(ConflictError, match="candidate context changed"):
        service.confirm_protocol(
            case.id,
            actor="reviewer",
            revision=preparation.version,
            payload=ProtocolConfirmation(original.sequence, {}),
        )

    assert original.state == "stale"
    assert original.invalidated_reason == "candidate_context_changed"
    assert preparation.draft_protocol_state == "stale"
    assert preparation.protocol_review_state == "locked"
    assert preparation.draft_evidence_plan_state == "stale"
    assert preparation.plan_review_state == "locked"
    assert _active_jobs(session, preparation.id, "draft_evidence_plan") == []
    assert len(_active_jobs(session, preparation.id, "draft_protocol")) == 1
    event = _events(session, preparation.id)[-1]
    assert event.type == "preparation_protocol_context_stale"
    assert event.detail == {"source_draft_sequence": original.sequence}
    assert "human corrected protocol claim" not in str(event.detail)
    assert session.scalars(select(ResearchRun)).all() == []

    refreshed_context = _candidate_context(service, case.id)
    service.complete_system_step(
        case.id,
        "draft_protocol",
        strict_payload,
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=refreshed_context,
    )
    refreshed = service._repo.current_artifact(preparation.id, "research_protocol_draft")
    assert refreshed is not None and refreshed.id != original.id
    service.confirm_protocol(
        case.id,
        actor="reviewer",
        revision=preparation.version,
        payload=ProtocolConfirmation(refreshed.sequence, {}),
    )
    assert preparation.protocol_review_state == "confirmed"
    assert len(_active_jobs(session, preparation.id, "draft_evidence_plan")) == 1


def test_atomic_review_locks_current_preparation_case_then_preparation(session, monkeypatch) -> None:
    """A current preparation candidate review obtains the shared Case → prep lock."""
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="z" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="review-lock-order")
    _admit_candidate_document(session, case, candidate)
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])

    lock_order: list[str] = []
    original_case_lock = preparation_repository_module.lock_event_scope_case
    original_preparation_lock = (
        ResearchPreparationRepository._lock_preparation_after_case_lock
    )

    def record_case_lock(locking_session, case_id):
        lock_order.append("case")
        return original_case_lock(locking_session, case_id)

    def record_preparation_lock(self, research_case_id, preparation_id):
        preparation = original_preparation_lock(self, research_case_id, preparation_id)
        lock_order.append("preparation")
        return preparation

    monkeypatch.setattr(
        preparation_repository_module, "lock_event_scope_case", record_case_lock
    )
    monkeypatch.setattr(
        ResearchPreparationRepository,
        "_lock_preparation_after_case_lock",
        record_preparation_lock,
    )
    review = AtomicClaimService(session).review(
        candidate.id,
        outcome="rejected",
        reviewer="independent-reviewer",
        reason="withdrawn",
        idempotency_key="review-lock-order",
    )

    assert review.atomic_claim_candidate_id == candidate.id
    assert lock_order == ["case", "preparation"]


def test_atomic_review_locks_all_same_tenant_preparations_for_shared_candidate(
    session, monkeypatch
) -> None:
    """A shared, tenant-local candidate locks each current preparation once."""
    first_case = _case(session)
    second_case = _case(session)
    candidate = _candidate(session, first_case, suffix="shared-review-lock")
    span = session.get(SourceSpan, candidate.source_span_id)
    assert span is not None
    session.add(
        CaseDocumentVersion(
            research_case_id=second_case.id,
            document_version_id=span.document_version_id,
            linked_at=datetime.now(UTC),
        )
    )
    _admit_candidate_document(session, first_case, candidate)
    _admit_candidate_document(session, second_case, candidate)
    first_preparation = _service(session).create_for_case(
        first_case.id, input_fingerprint="a" * 64, actor="tester"
    )
    second_preparation = _service(session).create_for_case(
        second_case.id, input_fingerprint="b" * 64, actor="tester"
    )
    _parse(_service(session), first_preparation, [candidate])
    _parse(_service(session), second_preparation, [candidate])

    lock_trace: list[tuple[str, uuid.UUID]] = []
    original_case_lock = preparation_repository_module.lock_event_scope_case
    original_preparation_lock = (
        ResearchPreparationRepository._lock_preparation_after_case_lock
    )

    def record_case_lock(locking_session, case_id):
        lock_trace.append(("case", case_id))
        return original_case_lock(locking_session, case_id)

    def record_preparation_lock(self, case_id, preparation_id):
        lock_trace.append(("preparation", preparation_id))
        return original_preparation_lock(self, case_id, preparation_id)

    monkeypatch.setattr(
        preparation_repository_module, "lock_event_scope_case", record_case_lock
    )
    monkeypatch.setattr(
        ResearchPreparationRepository,
        "_lock_preparation_after_case_lock",
        record_preparation_lock,
    )
    review = AtomicClaimService(session).review(
        candidate.id,
        outcome="confirmed",
        reviewer="shared-reviewer",
        reason="shared source reviewed",
        idempotency_key="shared-review-lock",
    )
    session.commit()

    ordered = sorted(
        (
            (first_case.id, first_preparation.id),
            (second_case.id, second_preparation.id),
        ),
        key=lambda pair: (str(pair[0]), str(pair[1])),
    )
    assert review.atomic_claim_candidate_id == candidate.id
    assert len(session.scalars(select(AtomicClaimReview)).all()) == 1
    assert len(session.scalars(select(SourceStatement)).all()) == 1
    assert lock_trace == [
        entry
        for case_id, preparation_id in ordered
        for entry in (("case", case_id), ("preparation", preparation_id))
    ]


def test_confirm_claims_locks_candidates_before_case_and_skips_shared_mapping(
    session, monkeypatch
) -> None:
    """Preparation confirmation never holds one Case while traversing peers."""
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="c" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="confirm-claim-lock-order")
    _parse(service, preparation, [candidate])

    lock_trace: list[str] = []
    review_modes: list[str | None] = []
    original_lock_for_case = ResearchPreparationRepository.lock_for_case
    original_review = AtomicClaimService.review

    def record_candidate_lock(self, candidate_ids):
        lock_trace.append("candidate")
        return [candidate]

    def record_case_lock(self, case_id):
        lock_trace.append("case")
        return original_lock_for_case(self, case_id)

    def record_review(self, *args, **kwargs):
        review_modes.append(kwargs.get("preparation_locking"))
        return original_review(self, *args, **kwargs)

    def fail_cross_preparation_lock(self, candidate_id):
        raise AssertionError("confirm_claims must not traverse shared preparation mappings")

    monkeypatch.setattr(
        ResearchPreparationRepository,
        "lock_candidate_rows",
        record_candidate_lock,
        raising=False,
    )
    monkeypatch.setattr(ResearchPreparationRepository, "lock_for_case", record_case_lock)
    monkeypatch.setattr(AtomicClaimService, "review", record_review)
    monkeypatch.setattr(
        ResearchPreparationRepository,
        "lock_preparation_for_candidate_review",
        fail_cross_preparation_lock,
    )

    service.confirm_claims(
        case.id,
        actor="reviewer",
        revision=preparation.version,
        decisions=[ClaimDecision(candidate.id, "confirmed", "reviewed")],
    )

    assert lock_trace[:2] == ["candidate", "case"]
    assert review_modes == ["already_locked"]


@pytest.mark.pg_only
def test_postgres_shared_candidate_confirmations_serialize_on_candidate_lock(
    engine, session, monkeypatch
) -> None:
    """Two shared-case confirmations cannot deadlock while traversing cases."""
    session_factory = sessionmaker(bind=engine, future=True)
    first_case = _case(session)
    second_case = _case(session)
    candidate = _candidate(session, first_case, suffix="shared-confirm-race")
    span = session.get(SourceSpan, candidate.source_span_id)
    assert span is not None
    session.add(CaseDocumentVersion(
        research_case_id=second_case.id,
        document_version_id=span.document_version_id,
        linked_at=datetime.now(UTC),
    ))
    _admit_candidate_document(session, first_case, candidate)
    _admit_candidate_document(session, second_case, candidate)
    first_preparation = _service(session).create_for_case(
        first_case.id, input_fingerprint="d" * 64, actor="tester"
    )
    second_preparation = _service(session).create_for_case(
        second_case.id, input_fingerprint="e" * 64, actor="tester"
    )
    _parse(_service(session), first_preparation, [candidate])
    _parse(_service(session), second_preparation, [candidate])
    first_case_id, second_case_id, candidate_id = (
        first_case.id,
        second_case.id,
        candidate.id,
    )
    session.commit()

    candidate_locked, release_first = Event(), Event()
    second_candidate_lock_attempted, second_finished = Event(), Event()
    errors: list[BaseException] = []
    first_thread_id: list[int] = []
    second_thread_id: list[int] = []
    original_lock_candidates = ResearchPreparationRepository.lock_candidate_rows

    def pause_first_candidate_lock(repository, candidate_ids):
        locked = original_lock_candidates(repository, candidate_ids)
        if first_thread_id and get_ident() == first_thread_id[0]:
            candidate_locked.set()
            assert release_first.wait(timeout=5)
        return locked

    def observe_second_candidate_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            second_thread_id
            and get_ident() == second_thread_id[0]
            and "atomic_claim_candidates" in statement.lower()
            and "for update" in statement.lower()
        ):
            second_candidate_lock_attempted.set()

    monkeypatch.setattr(
        ResearchPreparationRepository,
        "lock_candidate_rows",
        pause_first_candidate_lock,
    )

    def confirm(case_id: uuid.UUID, *, first: bool) -> None:
        confirming = session_factory()
        try:
            if first:
                first_thread_id.append(get_ident())
            else:
                second_thread_id.append(get_ident())
            ResearchPreparationService(confirming).confirm_claims(
                case_id,
                actor="reviewer",
                revision=1,
                decisions=[ClaimDecision(candidate_id, "confirmed", "reviewed")],
            )
            confirming.commit()
            if not first:
                second_finished.set()
        except BaseException as exc:
            errors.append(exc)
            confirming.rollback()
        finally:
            confirming.close()

    first_thread = Thread(target=lambda: confirm(first_case_id, first=True))
    second_thread = Thread(target=lambda: confirm(second_case_id, first=False))
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe_second_candidate_lock)
    try:
        first_thread.start()
        assert candidate_locked.wait(timeout=5)
        second_thread.start()
        assert second_candidate_lock_attempted.wait(timeout=5)
        assert not second_finished.is_set()
        release_first.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert errors == []
    finally:
        release_first.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe_second_candidate_lock)

    verifier = session_factory()
    try:
        assert verifier.get(ResearchPreparation, first_preparation.id).claim_review_state == "confirmed"
        assert verifier.get(ResearchPreparation, second_preparation.id).claim_review_state == "confirmed"
        assert len(verifier.scalars(select(AtomicClaimReview)).all()) == 1
    finally:
        verifier.close()


@pytest.mark.pg_only
def test_postgres_candidate_review_waits_for_protocol_confirmation_context_lock(
    engine, session, monkeypatch
) -> None:
    """A review cannot land between protocol context read and confirmation."""
    session_factory = sessionmaker(bind=engine, future=True)
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="p" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="protocol-review-race")
    _admit_candidate_document(session, case, candidate)
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    original_context = _candidate_context(service, case.id)
    service.complete_system_step(
        case.id,
        "draft_protocol",
        {"rationale": "serialized protocol draft"},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=original_context,
    )
    artifact = service._repo.current_artifact(preparation.id, "research_protocol_draft")
    assert artifact is not None
    case_id, preparation_id, candidate_id, draft_sequence = (
        case.id,
        preparation.id,
        candidate.id,
        artifact.sequence,
    )
    session.commit()

    context_read, release_confirmation = Event(), Event()
    review_lock_attempted, review_finished = Event(), Event()
    confirmation_errors: list[BaseException] = []
    review_errors: list[BaseException] = []
    review_thread_id: list[int] = []

    original_context_fingerprint = (
        ResearchPreparationService.current_candidate_context_fingerprint
    )

    def pause_after_context_read(service, locked_case_id):
        fingerprint = original_context_fingerprint(service, locked_case_id)
        if locked_case_id == case_id:
            context_read.set()
            assert release_confirmation.wait(timeout=5)
        return fingerprint

    def observe_review_case_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if (
            review_thread_id
            and get_ident() == review_thread_id[0]
            and "research_cases" in statement.lower()
            and "for update" in statement.lower()
        ):
            review_lock_attempted.set()

    monkeypatch.setattr(
        ResearchPreparationService,
        "current_candidate_context_fingerprint",
        pause_after_context_read,
    )

    def confirm() -> None:
        confirmation_session = session_factory()
        try:
            ResearchPreparationService(confirmation_session).confirm_protocol(
                case_id,
                actor="reviewer",
                revision=1,
                payload=ProtocolConfirmation(draft_sequence, {}),
            )
            confirmation_session.commit()
        except BaseException as exc:
            confirmation_errors.append(exc)
            confirmation_session.rollback()
        finally:
            confirmation_session.close()

    def review() -> None:
        review_session = session_factory()
        try:
            review_thread_id.append(get_ident())
            AtomicClaimService(review_session).review(
                candidate_id,
                outcome="rejected",
                reviewer="other-reviewer",
                reason="late correction",
                idempotency_key="serialized-review",
            )
            review_session.commit()
            review_finished.set()
        except BaseException as exc:
            review_errors.append(exc)
            review_session.rollback()
        finally:
            review_session.close()

    confirmation_thread = Thread(target=confirm)
    review_thread = Thread(target=review)
    sqlalchemy_event.listen(engine, "before_cursor_execute", observe_review_case_lock)
    try:
        confirmation_thread.start()
        assert context_read.wait(timeout=5)
        review_thread.start()
        assert review_lock_attempted.wait(timeout=5)
        assert not review_finished.is_set()
        release_confirmation.set()
        confirmation_thread.join(timeout=5)
        review_thread.join(timeout=5)
        assert not confirmation_thread.is_alive()
        assert not review_thread.is_alive()
        assert confirmation_errors == []
        assert review_errors == []
    finally:
        release_confirmation.set()
        confirmation_thread.join(timeout=5)
        review_thread.join(timeout=5)
        sqlalchemy_event.remove(engine, "before_cursor_execute", observe_review_case_lock)

    verifier = session_factory()
    try:
        persisted_preparation = verifier.get(ResearchPreparation, preparation_id)
        assert persisted_preparation is not None
        assert persisted_preparation.protocol_review_state == "confirmed"
        assert (
            ResearchPreparationService(verifier).current_candidate_context_fingerprint(case_id)
            != original_context
        )
    finally:
        verifier.close()


def test_confirm_protocol_persists_stale_context_before_conflict(session, monkeypatch) -> None:
    """A 409 must not let request cleanup roll back the invalidation transition."""
    session_factory = sessionmaker(bind=session.get_bind(), future=True)
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="y" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="durable-context-stale")
    _parse(service, preparation, [candidate])
    _confirm_claims(service, case.id, preparation.version, [candidate])
    service.complete_system_step(
        case.id,
        "draft_protocol",
        {"rationale": "draft before independent review"},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=_candidate_context(service, case.id),
    )
    artifact = service._repo.current_artifact(preparation.id, "research_protocol_draft")
    assert artifact is not None
    case_id, preparation_id, artifact_id, draft_sequence = (
        case.id,
        preparation.id,
        artifact.id,
        artifact.sequence,
    )
    session.commit()

    modifier = session_factory()
    try:
        persisted_candidate = modifier.get(type(candidate), candidate.id)
        assert persisted_candidate is not None
        AtomicClaimService(modifier).review(
            persisted_candidate.id,
            outcome="rejected",
            reviewer="other-reviewer",
            reason="withdrawn",
            normalized_text=None,
            idempotency_key="independent-review",
        )
        modifier.commit()
    finally:
        modifier.close()

    request_session = session_factory()
    try:
        commit_calls = 0
        original_commit = request_session.commit

        def count_commit() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()

        monkeypatch.setattr(request_session, "commit", count_commit)
        with pytest.raises(ConflictError, match="candidate context changed"):
            ResearchPreparationService(request_session).confirm_protocol(
                case_id,
                actor="reviewer",
                revision=1,
                payload=ProtocolConfirmation(draft_sequence, {}),
            )
        assert commit_calls == 1
        request_session.rollback()  # FastAPI-style cleanup after a 409 response.
    finally:
        request_session.close()

    verifier = session_factory()
    try:
        persisted_preparation = verifier.get(ResearchPreparation, preparation_id)
        persisted_artifact = verifier.get(ResearchPreparationArtifact, artifact_id)
        assert persisted_preparation is not None
        assert persisted_artifact is not None
        assert persisted_artifact.state == "stale"
        assert persisted_artifact.invalidated_reason == "candidate_context_changed"
        assert persisted_preparation.status == "preparing"
        assert persisted_preparation.protocol_review_state == "locked"
        assert _active_jobs(verifier, preparation_id, "draft_evidence_plan") == []
        assert len(_active_jobs(verifier, preparation_id, "draft_protocol")) == 1
        protocol_artifacts = list(
            verifier.scalars(
                select(ResearchPreparationArtifact).where(
                    ResearchPreparationArtifact.research_preparation_id == preparation_id,
                    ResearchPreparationArtifact.kind == "research_protocol_draft",
                )
            )
        )
        assert [item.id for item in protocol_artifacts] == [artifact_id]
        assert verifier.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation_id,
                ResearchPreparationArtifact.kind == "evidence_acquisition_plan",
            )
        ).all() == []
        events = _events(verifier, preparation_id)
        assert events[-1].type == "preparation_protocol_context_stale"
        assert events[-1].detail == {"source_draft_sequence": draft_sequence}
        assert verifier.scalars(select(ResearchRun)).all() == []
    finally:
        verifier.close()


def test_claim_validation_is_atomic_before_any_review_write(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="r" * 64, actor="tester")
    candidates = [_candidate(session, case, suffix=f"atomic-{index}") for index in range(2)]
    _parse(service, preparation, candidates)
    baseline = _snapshot(session, preparation)

    with pytest.raises(ConflictError):
        service.confirm_claims(
            case.id,
            actor="reviewer",
            revision=preparation.version,
            decisions=[
                ClaimDecision(candidates[0].id, "confirmed", "reviewed"),
                ClaimDecision(candidates[1].id, "modified", "reviewed"),
            ],
        )
    session.commit()

    assert _snapshot(session, preparation) == baseline


def test_new_input_clears_authorized_run_before_resetting_preparation(session) -> None:
    case = _case(session)
    service = _service(session)
    preparation = service.create_for_case(case.id, input_fingerprint="s" * 64, actor="tester")
    candidate = _candidate(session, case, suffix="authorized-reset")
    _parse(service, preparation, [candidate])
    now = datetime.now(UTC)
    run = ResearchRun(
        research_case_id=case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=100,
        budget_used=0,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    preparation.research_run_id = run.id
    preparation.authorized_evidence_plan = {"items": [{"factor": "frozen", "evidence_target": "primary", "allowed_source_roles": ["primary_disclosure"], "priority": "normal", "stop_condition": "one", "budget": 1}]}
    preparation.status = "authorized"
    session.flush()

    reset = service.create_for_case(case.id, input_fingerprint="t" * 64, actor="tester")
    session.commit()

    assert reset.status == "preparing"
    assert reset.research_run_id is None
    assert session.get(ResearchRun, run.id) is not None
    assert reset.parse_claims_state == "queued"
    assert all(
        artifact.state == "stale"
        for artifact in session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        )
    )


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
        _materializable_protocol(session, case, candidate),
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=_candidate_context(service, case.id),
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
