from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.ledger import AIAssessment, Thesis, ValidationError
from app.models.research_protocol import (
    MechanismEdgeVersion,
    OutcomeBindingVersion,
    VerificationRuleVersion,
)
from app.services.research_protocol import ResearchProtocolService
from tests.protocol_provenance import seed_protocol_footprint


def _protocol_kwargs(session, snapshot, *, status="single_metric_monitoring"):
    thesis = session.get(Thesis, snapshot.thesis_id)
    assert thesis is not None
    footprint = seed_protocol_footprint(session, thesis, status=status)
    return footprint, {
        "effective_binding_id": footprint.binding.id,
        "mechanism_template_version_id": footprint.template.id,
        "verification_rule_ids": [rule.id for rule in footprint.rules],
    }


def _strict_snapshot(
    assessment_service, research_service, research_case, *, statement
):
    thesis = research_service.add_thesis(
        research_case.id,
        statement=statement,
        created_by="tester",
        research_protocol_required=True,
    )
    return assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )


def test_create_ai_assessment_rejects_invalid_conclusion(assessment_service, snapshot):
    with pytest.raises(ValidationError):
        assessment_service.create_ai_assessment(
            snapshot.id, conclusion="invalid", rationale="x", gaps=[]
        )


def test_ai_assessment_is_displayed_as_provisional(assessment_service, snapshot):
    assessment = assessment_service.create_ai_assessment(
        snapshot.id, conclusion="supported", rationale="x", gaps=["gap1"]
    )
    assert assessment.displayed_as_provisional is True


def test_freeze_snapshot_revalidates_captured_link_thesis(
    assessment_service,
    research_service,
    research_case,
    thesis,
    statement,
):
    other_thesis = research_service.add_thesis(
        research_case.id,
        statement="Other thesis must not lend prompt evidence",
        created_by="tester",
    )
    other_link = research_service.link_evidence(
        other_thesis.id,
        statement.id,
        role="supports",
        reason="belongs to the other thesis",
        scope={"segment": "other"},
    )

    with pytest.raises(ValidationError, match="not visible for the snapshot thesis"):
        assessment_service.freeze_snapshot(
            thesis.id,
            cutoff=datetime(2027, 12, 31, tzinfo=timezone.utc),
            evidence_link_ids=[other_link.id],
        )


def test_freeze_snapshot_revalidates_captured_link_cutoff(
    assessment_service,
    research_service,
    thesis,
    statement,
):
    future_link = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="not yet visible at the original prompt cutoff",
        scope={"segment": "future"},
        available_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValidationError, match="not visible for the snapshot thesis"):
        assessment_service.freeze_snapshot(
            thesis.id,
            cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
            evidence_link_ids=[future_link.id],
        )


def test_ai_assessment_freezes_typed_research_protocol_provenance(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Ready provenance is frozen canonically",
    )
    footprint, _ = _protocol_kwargs(session, snapshot, status="ready")
    rule_ids = [footprint.rules[1].id, footprint.rules[0].id, footprint.rules[1].id]

    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale="Protocol-complete evidence supports the thesis.",
        gaps=[],
        research_protocol_status="ready",
        effective_binding_id=footprint.binding.id,
        mechanism_template_version_id=footprint.template.id,
        verification_rule_ids=rule_ids,
    )

    assert assessment.research_protocol_status == "ready"
    assert assessment.effective_binding_id == footprint.binding.id
    assert assessment.mechanism_template_version_id == footprint.template.id
    assert assessment.verification_rule_ids == sorted(
        {str(rule_id) for rule_id in rule_ids}
    )


def test_ai_assessment_rejects_an_incomplete_or_blocked_protocol_footprint(
    assessment_service, research_service, research_case
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Blocked strict assessment cannot persist",
    )
    with pytest.raises(ValidationError, match="blocked"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="insufficient_evidence",
            rationale="Incomplete provenance is not auditable.",
            gaps=[],
            research_protocol_status="single_metric_monitoring",
        )


def test_non_strict_assessment_rejects_caller_supplied_protocol_provenance(
    assessment_service, snapshot
):
    with pytest.raises(ValidationError, match="non-strict"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="supported",
            rationale="Legacy assessments cannot claim strict provenance.",
            gaps=[],
            research_protocol_status="ready",
            effective_binding_id="00000000-0000-0000-0000-000000000001",
            mechanism_template_version_id="00000000-0000-0000-0000-000000000002",
            verification_rule_ids=["00000000-0000-0000-0000-000000000003"],
        )
    with pytest.raises(ValidationError, match="non-strict"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="supported",
            rationale="An explicitly empty provenance list is still supplied.",
            gaps=[],
            verification_rule_ids=[],
        )


def test_strict_assessment_rejects_null_protocol_provenance(
    assessment_service, research_service, research_case
):
    thesis = research_service.add_thesis(
        research_case.id,
        statement="Strict assessment requires authoritative provenance",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )

    with pytest.raises(ValidationError, match="strict"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="insufficient_evidence",
            rationale="Missing provenance.",
            gaps=[],
        )


def test_strict_assessment_rejects_forged_ready_over_current_single_metric(
    assessment_service, research_service, research_case, session
):
    thesis = research_service.add_thesis(
        research_case.id,
        statement="Caller cannot upgrade monitoring status",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    assert (
        ResearchProtocolService(session).check_researchability(thesis.id).status
        == "single_metric_monitoring"
    )

    with pytest.raises(ValidationError, match="current research protocol"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="supported",
            rationale="Forged ready status.",
            gaps=[],
            research_protocol_status="ready",
            **protocol_kwargs,
        )


def test_strict_assessment_accepts_exact_current_protocol_footprint(
    assessment_service, research_service, research_case, session, monkeypatch
):
    thesis = research_service.add_thesis(
        research_case.id,
        statement="Exact monitoring footprint is auditable",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        thesis.id, cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc)
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    result = ResearchProtocolService(session).check_researchability(thesis.id)
    locked_case_ids = []
    monkeypatch.setattr(
        "app.services.assessment.lock_event_scope_case",
        lambda _session, case_id: locked_case_ids.append(case_id),
    )

    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Exact current protocol.",
        gaps=[],
        research_protocol_status=result.status,
        **protocol_kwargs,
    )

    assert assessment.research_protocol_status == "single_metric_monitoring"
    assert locked_case_ids == [thesis.research_case_id]


def test_single_metric_protocol_provenance_rejects_directional_ai_conclusion(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Monitoring protocol remains non-directional",
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.create_ai_assessment(
            snapshot.id,
            conclusion="supported",
            rationale="A directional result is unsafe for monitoring-only protocol.",
            gaps=[],
            research_protocol_status="single_metric_monitoring",
            **protocol_kwargs,
        )


def test_human_review_does_not_change_ai_assessment(assessment_service, ai_assessment):
    review = assessment_service.review(
        ai_assessment.id,
        outcome="modified",
        conclusion="insufficient_evidence",
        reason="scope mismatch",
    )
    assert assessment_service.get(ai_assessment.id).conclusion == "supported"
    assert review.ai_assessment_id == ai_assessment.id


@pytest.mark.parametrize("outcome", ["confirmed", "modified", "rejected"])
def test_historical_strict_null_provenance_fails_closed_for_directional_review(
    assessment_service, research_service, research_case, outcome
):
    strict_thesis = research_service.add_thesis(
        research_case.id,
        statement="A strict single-metric thesis",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        strict_thesis.id,
        cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    assessment = assessment_service._repo.insert_ai_assessment(
        snapshot_id=snapshot.id,
        conclusion="supported",
        rationale="Only one primary metric is available.",
        gaps=["insufficient_primary_metrics"],
    )

    with pytest.raises(ValidationError, match="missing protocol provenance"):
        assessment_service.review(
            assessment.id,
            outcome=outcome,
            conclusion="supported",
            reason="strict historical rows fail closed without typed provenance",
        )


def test_historical_non_strict_null_provenance_ignores_gap_prose(
    assessment_service, snapshot
):
    assessment = assessment_service._repo.insert_ai_assessment(
        snapshot_id=snapshot.id,
        conclusion="supported",
        rationale="Legacy model prose is not an authorization signal.",
        gaps=["insufficient_primary_metrics"],
    )

    review = assessment_service.review(
        assessment.id,
        outcome="confirmed",
        conclusion="supported",
        reason="non-strict legacy compatibility is thesis-derived",
    )

    assert review.conclusion == "supported"


def test_historical_strict_null_provenance_allows_insufficient_evidence(
    assessment_service, research_service, research_case
):
    strict_thesis = research_service.add_thesis(
        research_case.id,
        statement="A historical strict assessment without typed provenance",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        strict_thesis.id,
        cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    assessment = assessment_service._repo.insert_ai_assessment(
        snapshot_id=snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Historical strict assessment remains non-directional.",
        gaps=[],
    )

    review = assessment_service.review(
        assessment.id,
        outcome="confirmed",
        conclusion="insufficient_evidence",
        reason="non-directional historical review is safe",
    )

    assert review.conclusion == "insufficient_evidence"


def test_non_strict_review_preserves_legacy_directional_conclusion(
    assessment_service, ai_assessment
):
    review = assessment_service.review(
        ai_assessment.id,
        outcome="confirmed",
        conclusion="supported",
        reason="legacy workflow remains valid",
    )

    assert review.conclusion == "supported"


def test_strict_ready_assessment_review_preserves_directional_conclusion(
    assessment_service, research_service, research_case, session
):
    strict_thesis = research_service.add_thesis(
        research_case.id,
        statement="A strict thesis with complete protocol provenance",
        created_by="tester",
        research_protocol_required=True,
    )
    snapshot = assessment_service.freeze_snapshot(
        strict_thesis.id,
        cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot, status="ready")
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="supported",
        rationale="Multiple primary metrics satisfy the protocol.",
        gaps=["insufficient_primary_metrics"],
        research_protocol_status="ready",
        **protocol_kwargs,
    )

    review = assessment_service.review(
        assessment.id,
        outcome="confirmed",
        conclusion="supported",
        reason="protocol-ready evidence confirmed",
    )

    assert review.conclusion == "supported"


@pytest.mark.parametrize("outcome", ["confirmed", "modified", "rejected"])
def test_typed_single_metric_review_rejects_directional_conclusion_for_any_outcome(
    assessment_service, research_service, research_case, session, outcome
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Monitoring review remains non-directional",
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Monitoring only.",
        gaps=[],
        research_protocol_status="single_metric_monitoring",
        **protocol_kwargs,
    )

    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.review(
            assessment.id,
            outcome=outcome,
            conclusion="supported",
            reason="A rejected review conclusion is still consumed by read models.",
        )


def test_typed_single_metric_review_rejects_null_that_falls_back_to_directional_ai_result(
    assessment_service, monkeypatch
):
    # Simulate a malformed imported row so null review semantics cannot reopen
    # a directional conclusion. Current database invariants reject this row,
    # so the review authorization is tested at its repository boundary.
    assessment = SimpleNamespace(
        id=uuid.uuid4(),
        conclusion="contradicted",
        research_protocol_status="single_metric_monitoring",
    )
    monkeypatch.setattr(
        assessment_service._repo,
        "get_ai_assessment",
        lambda assessment_id: assessment if assessment_id == assessment.id else None,
    )

    with pytest.raises(ValidationError, match="insufficient_evidence"):
        assessment_service.review(
            assessment.id,
            outcome="rejected",
            conclusion=None,
            reason="Null would expose the directional AI fallback.",
        )


def test_typed_single_metric_review_permits_insufficient_evidence(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Monitoring review permits insufficient evidence",
    )
    _, protocol_kwargs = _protocol_kwargs(session, snapshot)
    assessment = assessment_service.create_ai_assessment(
        snapshot.id,
        conclusion="insufficient_evidence",
        rationale="Monitoring only.",
        gaps=[],
        research_protocol_status="single_metric_monitoring",
        **protocol_kwargs,
    )

    review = assessment_service.review(
        assessment.id,
        outcome="rejected",
        conclusion="insufficient_evidence",
        reason="The non-directional conclusion remains safe.",
    )

    assert review.conclusion == "insufficient_evidence"


def test_corrupt_typed_blocked_provenance_fails_closed_for_directional_review(
    assessment_service, monkeypatch
):
    assessment = SimpleNamespace(
        id=uuid.uuid4(),
        conclusion="supported",
        research_protocol_status="blocked",
    )
    monkeypatch.setattr(
        assessment_service._repo,
        "get_ai_assessment",
        lambda assessment_id: assessment if assessment_id == assessment.id else None,
    )

    with pytest.raises(ValidationError, match="non-directional"):
        assessment_service.review(
            assessment.id,
            outcome="rejected",
            conclusion="supported",
            reason="Fail closed for a corrupt typed blocked row.",
        )


def _bypass_assessment(snapshot, **overrides):
    values = {
        "snapshot_id": snapshot.id,
        "conclusion": "insufficient_evidence",
        "rationale": "database invariant probe",
        "gaps": [],
        "displayed_as_provisional": True,
        "creator_type": "ai",
        "created_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return AIAssessment(**values)


def test_create_all_installs_protocol_scope_trigger_once(session):
    if session.get_bind().dialect.name != "sqlite":
        pytest.skip("SQLite create_all trigger assertion")
    assert session.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'trigger' "
            "AND name = 'trg_ai_assessments_protocol_scope'"
        )
    ).scalar_one() == 1


def test_assessment_service_does_not_require_repository_session_escape_hatch(
    assessment_service
):
    assert not hasattr(assessment_service._repo, "session")


def test_database_rejects_partial_and_blocked_typed_protocol_provenance(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Database protocol check constraint",
    )
    _, protocol = _protocol_kwargs(session, snapshot)

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(snapshot, research_protocol_status="ready")
        )
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status=None,
                effective_binding_id=protocol["effective_binding_id"],
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id) for rule_id in protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status="blocked",
                effective_binding_id=protocol["effective_binding_id"],
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id) for rule_id in protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()


def test_database_rejects_directional_single_metric_assessment(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Database monitoring conclusion invariant",
    )
    _, protocol = _protocol_kwargs(session, snapshot)

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                conclusion="supported",
                research_protocol_status="single_metric_monitoring",
                effective_binding_id=protocol["effective_binding_id"],
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id) for rule_id in protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()


@pytest.mark.parametrize(
    ("actual_status", "claimed_status"),
    [
        ("single_metric_monitoring", "ready"),
        ("ready", "single_metric_monitoring"),
    ],
)
def test_database_rejects_protocol_status_that_does_not_match_current_footprint(
    assessment_service,
    research_service,
    research_case,
    session,
    actual_status,
    claimed_status,
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement=f"Database rejects {claimed_status} over {actual_status}",
    )
    _, protocol = _protocol_kwargs(session, snapshot, status=actual_status)

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status=claimed_status,
                effective_binding_id=protocol["effective_binding_id"],
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id) for rule_id in protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()


@pytest.mark.parametrize("mutation", ["omitted", "duplicate"])
def test_database_requires_exact_unique_current_verification_rule_set(
    assessment_service,
    research_service,
    research_case,
    session,
    mutation,
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement=f"Database rejects {mutation} rule footprint",
    )
    _, protocol = _protocol_kwargs(session, snapshot, status="ready")
    rule_ids = [str(rule_id) for rule_id in protocol["verification_rule_ids"]]
    invalid_rule_ids = (
        rule_ids[:-1] if mutation == "omitted" else [*rule_ids, rule_ids[0]]
    )

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status="ready",
                effective_binding_id=protocol["effective_binding_id"],
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=invalid_rule_ids,
            )
        )
        session.flush()


def test_database_rejects_stale_effective_binding_and_verification_rule(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Database rejects stale protocol versions",
    )
    footprint, protocol = _protocol_kwargs(session, snapshot, status="ready")
    later = datetime.now(timezone.utc) + timedelta(seconds=1)
    stale_binding = footprint.binding
    current_binding = OutcomeBindingVersion(
        thesis_id=stale_binding.thesis_id,
        metric_definition_id=stale_binding.metric_definition_id,
        entity_scope=dict(stale_binding.entity_scope),
        direction=stale_binding.direction,
        baseline=dict(stale_binding.baseline),
        horizon_start=stale_binding.horizon_start,
        horizon_end=stale_binding.horizon_end,
        state="approved",
        supersedes_id=stale_binding.id,
        reviewer="tester",
        reason="new effective binding",
        created_at=later,
    )
    stale_rule = footprint.rules[0]
    current_rule = VerificationRuleVersion(
        research_case_id=stale_rule.research_case_id,
        mechanism_edge_id=stale_rule.mechanism_edge_id,
        metric_definition_id=stale_rule.metric_definition_id,
        expected_direction=stale_rule.expected_direction,
        support_predicate=stale_rule.support_predicate,
        contradiction_predicate=stale_rule.contradiction_predicate,
        allowed_source_roles=list(stale_rule.allowed_source_roles),
        observed_period_start=stale_rule.observed_period_start,
        observed_period_end=stale_rule.observed_period_end,
        available_at_deadline=stale_rule.available_at_deadline,
        next_verification_event=stale_rule.next_verification_event,
        supersedes_id=stale_rule.id,
        reviewer="tester",
        reason="new effective rule",
        created_at=later,
    )
    session.add_all([current_binding, current_rule])
    session.flush()

    current = {
        **protocol,
        "effective_binding_id": current_binding.id,
        "verification_rule_ids": [
            current_rule.id if rule_id == stale_rule.id else rule_id
            for rule_id in protocol["verification_rule_ids"]
        ],
    }
    stale_variants = [
        {**current, "effective_binding_id": stale_binding.id},
        {**current, "verification_rule_ids": protocol["verification_rule_ids"]},
    ]
    for stale in stale_variants:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                _bypass_assessment(
                    snapshot,
                    research_protocol_status="ready",
                    effective_binding_id=stale["effective_binding_id"],
                    mechanism_template_version_id=stale[
                        "mechanism_template_version_id"
                    ],
                    verification_rule_ids=[
                        str(rule_id) for rule_id in stale["verification_rule_ids"]
                    ],
                )
            )
            session.flush()

    session.add(
        _bypass_assessment(
            snapshot,
            research_protocol_status="ready",
            effective_binding_id=current["effective_binding_id"],
            mechanism_template_version_id=current["mechanism_template_version_id"],
            verification_rule_ids=[
                str(rule_id) for rule_id in current["verification_rule_ids"]
            ],
        )
    )
    session.flush()


def test_database_rejects_current_unapproved_binding(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Database requires an approved effective binding",
    )
    footprint, protocol = _protocol_kwargs(session, snapshot, status="ready")
    approved = footprint.binding
    draft = OutcomeBindingVersion(
        thesis_id=approved.thesis_id,
        metric_definition_id=approved.metric_definition_id,
        entity_scope=dict(approved.entity_scope),
        direction=approved.direction,
        baseline=dict(approved.baseline),
        horizon_start=approved.horizon_start,
        horizon_end=approved.horizon_end,
        state="draft",
        supersedes_id=approved.id,
        reviewer="tester",
        reason="unapproved current binding",
        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    session.add(draft)
    session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status="ready",
                effective_binding_id=draft.id,
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id) for rule_id in protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()


def test_database_rejects_footprint_missing_required_rule(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Database rejects missing required rule",
    )
    footprint, protocol = _protocol_kwargs(session, snapshot, status="ready")
    first_edge = session.get(
        MechanismEdgeVersion, footprint.rules[0].mechanism_edge_id
    )
    assert first_edge is not None
    session.add(
        MechanismEdgeVersion(
            template_version_id=footprint.template.id,
            edge_key=f"unruled-required-{uuid.uuid4().hex}",
            source_node_id=first_edge.source_node_id,
            target_node_id=first_edge.target_node_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status="ready",
                effective_binding_id=protocol["effective_binding_id"],
                mechanism_template_version_id=protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id) for rule_id in protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()


def test_database_rejects_footprint_missing_counter_hypothesis(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Database rejects missing counter hypothesis",
    )
    thesis = session.get(Thesis, snapshot.thesis_id)
    footprint = seed_protocol_footprint(
        session,
        thesis,
        status="ready",
        counter_hypothesis=False,
    )

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                snapshot,
                research_protocol_status="ready",
                effective_binding_id=footprint.binding.id,
                mechanism_template_version_id=footprint.template.id,
                verification_rule_ids=[
                    str(rule.id) for rule in footprint.rules
                ],
            )
        )
        session.flush()


def test_database_rejects_malformed_protocol_rule_json(
    assessment_service, research_service, research_case, session
):
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Malformed database protocol rules",
    )
    _, protocol = _protocol_kwargs(session, snapshot, status="ready")
    base = {
        "research_protocol_status": "ready",
        "effective_binding_id": protocol["effective_binding_id"],
        "mechanism_template_version_id": protocol[
            "mechanism_template_version_id"
        ],
    }

    for malformed in ({"rule": "not-an-array"}, ["not-a-uuid"]):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                _bypass_assessment(
                    snapshot,
                    verification_rule_ids=malformed,
                    **base,
                )
            )
            session.flush()


def test_database_rejects_non_current_case_template(
    assessment_service, research_service, research_case, session
):
    first_snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="First selected mechanism template",
    )
    _, first_protocol = _protocol_kwargs(session, first_snapshot, status="ready")
    second_snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Later selected mechanism template",
    )
    _protocol_kwargs(session, second_snapshot, status="ready")

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            _bypass_assessment(
                first_snapshot,
                research_protocol_status="ready",
                effective_binding_id=first_protocol["effective_binding_id"],
                mechanism_template_version_id=first_protocol[
                    "mechanism_template_version_id"
                ],
                verification_rule_ids=[
                    str(rule_id)
                    for rule_id in first_protocol["verification_rule_ids"]
                ],
            )
        )
        session.flush()


def test_database_rejects_cross_scope_assessment_protocol_ids(
    assessment_service, research_service, research_case, session
):
    first_snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="First database protocol scope",
    )
    first, first_protocol = _protocol_kwargs(
        session, first_snapshot, status="ready"
    )
    other_case = research_service.add_case(
        title="Other protocol case", industry_topic="test", created_by="tester"
    )
    other_snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        other_case,
        statement="Other database protocol scope",
    )
    other, other_protocol = _protocol_kwargs(
        session, other_snapshot, status="ready"
    )

    invalid_footprints = [
        {
            **first_protocol,
            "effective_binding_id": other.binding.id,
        },
        {
            **first_protocol,
            "mechanism_template_version_id": other.template.id,
        },
        {
            **first_protocol,
            "verification_rule_ids": [rule.id for rule in other.rules],
        },
    ]
    for invalid in invalid_footprints:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                _bypass_assessment(
                    first_snapshot,
                    research_protocol_status="ready",
                    verification_rule_ids=[
                        str(rule_id) for rule_id in invalid["verification_rule_ids"]
                    ],
                    effective_binding_id=invalid["effective_binding_id"],
                    mechanism_template_version_id=invalid[
                        "mechanism_template_version_id"
                    ],
                )
            )
            session.flush()


def test_database_accepts_all_null_legacy_and_valid_typed_footprints(
    assessment_service, research_service, research_case, snapshot, session
):
    legacy = _bypass_assessment(snapshot)
    session.add(legacy)
    session.flush()

    strict_snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="Valid database protocol scope",
    )
    _, protocol = _protocol_kwargs(session, strict_snapshot, status="ready")
    valid = _bypass_assessment(
        strict_snapshot,
        research_protocol_status="ready",
        effective_binding_id=protocol["effective_binding_id"],
        mechanism_template_version_id=protocol["mechanism_template_version_id"],
        verification_rule_ids=[
            str(rule_id) for rule_id in protocol["verification_rule_ids"]
        ],
    )
    session.add(valid)
    session.flush()

    assert legacy.research_protocol_status is None
    assert valid.research_protocol_status == "ready"


@pytest.mark.pg_only
def test_postgres_trigger_rejects_inexact_protocol_footprint(
    assessment_service, research_service, research_case, session
):
    assert session.get_bind().dialect.name == "postgresql"
    snapshot = _strict_snapshot(
        assessment_service,
        research_service,
        research_case,
        statement="PostgreSQL exact protocol footprint",
    )
    _, protocol = _protocol_kwargs(session, snapshot, status="ready")
    rule_ids = [str(rule_id) for rule_id in protocol["verification_rule_ids"]]

    for invalid_rule_ids in (rule_ids[:-1], [*rule_ids, rule_ids[0]]):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                _bypass_assessment(
                    snapshot,
                    research_protocol_status="ready",
                    effective_binding_id=protocol["effective_binding_id"],
                    mechanism_template_version_id=protocol[
                        "mechanism_template_version_id"
                    ],
                    verification_rule_ids=invalid_rule_ids,
                )
            )
            session.flush()
