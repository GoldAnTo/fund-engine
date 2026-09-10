from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.models.ledger import ConflictError, ValidationError
from app.models.operational import Job
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.domain.company_research_critical_inputs import (
    CriticalDependencyEdge,
    CriticalDependencyGraph,
    CriticalInputCandidate,
    CriticalInputDecision,
    CriticalInputKind,
    CriticalInputSet,
    select_critical_inputs,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.services.company_research_ai_memo import _authenticated_input
from app.underwriting.services.company_research_critical_input_confirmation import (
    CompanyResearchCriticalInputConfirmationService,
)
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchModelBuilder,
)
from app.underwriting.services.company_research_preparation import (
    CompanyResearchPreparationWorker,
)
from app.underwriting.services.company_research_publication import (
    CompanyResearchPublicationService,
)
from tests.underwriting.test_company_research_model_builder import _build_input
from tests.underwriting.test_company_research_run_service import (
    _mainline_machine_draft,
)
from tests.underwriting.test_company_research_worker import NOW, _mainline_initialized


def _user_overlay(candidate, value, *, unit=None):
    strategy_version = (
        str(candidate.assumption_key).split(":", 1)[0]
        if candidate.assumption_key is not None
        else "company-research-mainline.v1"
    )
    return CriticalInputCandidate(
        key=candidate.key,
        kind=CriticalInputKind.USER_ASSUMPTION,
        value=value,
        period=candidate.period,
        unit=unit if unit is not None else candidate.unit,
        currency=candidate.currency,
        rationale=f"User overlay for {candidate.key}.",
        assumption_key=(
            f"{strategy_version}:user_assumption_focused_overlay"
        ),
    )


def test_typed_overlays_change_source_market_and_valuation_without_mutating_evidence(
) -> None:
    value = _build_input()
    builder = CompanyResearchModelBuilder()
    original = builder.build(value)
    candidates = {
        node.key: node.candidate
        for node in original.critical_input_dependency_graph.input_nodes
    }
    evidence_payload = dict(value.evidence_payload)

    cases = (
        ("fact:fy2025_search_other_revenue", Decimal(250000), "business"),
        ("market:capital:cash", Decimal(140000), "valuation"),
        ("market:price:nasdaq_goog", Decimal(225), "valuation"),
        ("market:fx:usd_cny", Decimal("7.5"), "input"),
        ("assumption:required_return", Decimal("0.14"), "valuation"),
        ("assumption:terminal_growth", Decimal("0.035"), "valuation"),
        ("scenario:bull:revenue", Decimal("1.2"), "scenario"),
    )
    decision_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    decision_hash = "d" * 64
    for key, replacement_value, surface in cases:
        candidate = candidates[key]
        replacement = _user_overlay(candidate, replacement_value)
        effective_input = replace(
            value,
            critical_input_replacements=(replacement,),
            critical_inputs_artifact_id=decision_id,
            critical_inputs_content_hash=decision_hash,
        )
        rebuilt = builder.build(effective_input)
        ai_input = _authenticated_input(effective_input, rebuilt)
        assert ai_input["critical_input_overlays"] == (
            {
                "key": key,
                "value": str(replacement_value),
                "unit": replacement.unit,
                "currency": replacement.currency,
                "period": replacement.period,
                "provenance_role": "user_assumption",
                "decision_artifact_id": str(decision_id),
                "decision_content_hash": decision_hash,
            },
        )
        if surface == "business":
            assert rebuilt.business_map != original.business_map
            classified = next(
                item
                for module in rebuilt.business_map.modules
                for item in module.classified_evidence
                if item.fact_ref.fact_key == candidate.key.removeprefix("fact:")
            )
            assert classified.fact_ref.source_role == "user_assumption"
            assert classified.fact_ref.raw_hash == decision_hash
            assert str(decision_id) in classified.fact_ref.source_url
            ai_fact = next(
                item for item in ai_input["facts"] if item["fact_key"] == classified.fact_ref.fact_key
            )
            assert ai_fact["value"] == str(replacement_value)
        elif surface == "scenario":
            assert rebuilt.scenario_set != original.scenario_set
            assert rebuilt.valuation_set != original.valuation_set, key
        elif surface == "input":
            effective = builder.apply_critical_input_overlays(
                effective_input
            )
            assert effective.market_context is not None
            assert effective.market_context.market_bridge.usd_cny_rate == (
                replacement_value
            )
            assert effective.market_context.market_bridge.fx_ref.source_role == (
                "user_assumption"
            )
            assert effective.market_context.market_bridge.fx_ref.raw_hash == (
                decision_hash
            )
        else:
            assert rebuilt.valuation_set != original.valuation_set, key
        selected = next(
            item
            for item in select_critical_inputs(
                rebuilt.critical_input_dependency_graph
            ).inputs
            if item.key == key
        )
        assert selected.kind is CriticalInputKind.USER_ASSUMPTION
        assert selected.value == replacement_value

    assert value.evidence_payload == evidence_payload
    assert canonical_hash(value.evidence_payload) == value.evidence_content_hash


def _small_mainline_machine_draft(session, *, key: str):
    initialized = _mainline_initialized(session, idempotency_key=key)
    source_worker = CompanyResearchPreparationWorker(session, now=lambda: NOW)
    source_claim = source_worker.claim_next()
    assert source_claim is not None
    assert source_worker.run_claim(source_claim) == "building_model"

    def small_graph(value):
        result = CompanyResearchModelBuilder().build(value)
        original = result.critical_input_dependency_graph
        input_nodes = original.input_nodes[:2]
        unknown_nodes = original.unknown_nodes[:1]
        surface_nodes = original.surface_nodes
        edges = [
            CriticalDependencyEdge(input_nodes[0].key, surface_nodes[0].key),
            CriticalDependencyEdge(input_nodes[1].key, surface_nodes[1].key),
        ]
        edges.extend(
            CriticalDependencyEdge(unknown_nodes[0].key, surface.key)
            for surface in surface_nodes[2:]
        )
        graph = CriticalDependencyGraph(
            input_nodes=input_nodes,
            calculation_nodes=(),
            unknown_nodes=unknown_nodes,
            surface_nodes=surface_nodes,
            edges=tuple(
                sorted(edges, key=lambda item: (item.parent_key, item.consumer_key))
            ),
        )
        return replace(result, critical_input_dependency_graph=graph)

    model_worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW, model_provider=small_graph
    )
    model_claim = model_worker.claim_next()
    assert model_claim is not None
    assert model_worker.run_claim(model_claim) == "awaiting_judgment_review"
    return initialized


def test_confirming_one_current_critical_input_appends_one_bound_successor(
    session,
) -> None:
    initialized = _mainline_machine_draft(
        session, key="critical-input-confirm-one"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    original = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(original) is CriticalInputSet
    selected = original.inputs[0]

    result = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=1)
    ).decide(
        project_id=initialized.project.id,
        critical_input_key=selected.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.CONFIRMED,
    )

    successor = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert successor is not None
    assert successor.supersedes_id == head.id
    assert successor.input_hash == repository.critical_input_successor_input_hash(
        parent_content_hash=head.content_hash,
        input_key=selected.key,
        input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.CONFIRMED,
        replacement=None,
    )
    decoded = CompanyResearchArtifactCodec.decode(
        "critical_inputs", successor.payload
    )
    assert type(decoded) is CriticalInputSet
    assert sum(
        before != after
        for before, after in zip(original.inputs, decoded.inputs, strict=True)
    ) == 1
    decided = next(item for item in decoded.inputs if item.key == selected.key)
    assert decided.decision is CriticalInputDecision.CONFIRMED
    assert result.critical_inputs is not None
    assert result.critical_inputs.artifact_id == successor.id
    assert repository.events(initialized.preparation.id)[-1].event_type == (
        "critical_input_decided"
    )

    with pytest.raises(ConflictError, match="artifact is stale"):
        CompanyResearchCriticalInputConfirmationService(
            session, now=lambda: NOW + timedelta(minutes=1)
        ).decide(
            project_id=initialized.project.id,
            critical_input_key=selected.key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.CONFIRMED,
        )
    with pytest.raises(ConflictError, match="contradictory"):
        CompanyResearchCriticalInputConfirmationService(
            session, now=lambda: NOW + timedelta(minutes=1)
        ).decide(
            project_id=initialized.project.id,
            critical_input_key=selected.key,
            expected_artifact_id=successor.id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.MARKED_UNKNOWN,
        )
    other = next(item for item in decoded.inputs if item.key != selected.key)
    with pytest.raises(ConflictError, match="fingerprint is stale"):
        CompanyResearchCriticalInputConfirmationService(
            session, now=lambda: NOW + timedelta(minutes=1)
        ).decide(
            project_id=initialized.project.id,
            critical_input_key=other.key,
            expected_artifact_id=successor.id,
            expected_input_fingerprint="f" * 64,
            decision=CriticalInputDecision.CONFIRMED,
        )


def test_replacement_requires_complete_user_assumption_and_queues_exact_rebuild(
    session,
) -> None:
    initialized = _mainline_machine_draft(
        session, key="critical-input-user-replacement"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    original = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(original) is CriticalInputSet
    selected = next(
        item for item in original.inputs if item.key == "assumption:revenue"
    )
    service = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=2)
    )

    with pytest.raises(ValidationError, match="derived critical input"):
        service.decide(
            project_id=initialized.project.id,
            critical_input_key="calculation:revenue",
            expected_artifact_id=head.id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
            replacement_value="1",
            replacement_unit="USD million",
            replacement_rationale="Direct calculations are not editable.",
        )

    source_fact = next(item for item in original.inputs if item.key.startswith("fact:"))
    assert isinstance(source_fact.value, Decimal)
    assert source_fact.unit is not None
    with pytest.raises(ValidationError, match="unit"):
        service.decide(
            project_id=initialized.project.id,
            critical_input_key=source_fact.key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=source_fact.input_fingerprint,
            decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
            replacement_value=str(source_fact.value + Decimal(1)),
            replacement_unit="ratio",
            replacement_rationale="A mismatched unit must fail closed.",
        )
    assert repository.current_artifact(
        initialized.project.id, "critical_inputs"
    ).id == head.id

    unknown = next(
        item for item in original.inputs if item.kind is CriticalInputKind.UNKNOWN
    )
    with pytest.raises(ValidationError, match="not safely mappable"):
        service.decide(
            project_id=initialized.project.id,
            critical_input_key=unknown.key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=unknown.input_fingerprint,
            decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
            replacement_value="1",
            replacement_unit="unknown",
            replacement_rationale="Unknown inputs must not become silent no-ops.",
        )
    assert repository.current_artifact(
        initialized.project.id, "critical_inputs"
    ).id == head.id

    for missing in ("value", "unit", "rationale"):
        values = {
            "replacement_value": "500000|580000|660000|740000|820000",
            "replacement_unit": "USD million",
            "replacement_rationale": "Use the explicit user planning case.",
        }
        values[f"replacement_{missing}"] = None
        with pytest.raises(ValidationError, match="value, unit, and rationale"):
            service.decide(
                project_id=initialized.project.id,
                critical_input_key=selected.key,
                expected_artifact_id=head.id,
                expected_input_fingerprint=selected.input_fingerprint,
                decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
                **values,
            )

    result = service.decide(
        project_id=initialized.project.id,
        critical_input_key=selected.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
        replacement_value="500000|580000|660000|740000|820000",
        replacement_unit="USD million",
        replacement_rationale="Use the explicit user planning case.",
    )

    successor = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert successor is not None and successor.supersedes_id == head.id
    decided = CompanyResearchArtifactCodec.decode(
        "critical_inputs", successor.payload
    )
    assert type(decided) is CriticalInputSet
    replacement = next(item for item in decided.inputs if item.key == selected.key)
    assert replacement.decision is (
        CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION
    )
    assert replacement.replacement is not None
    assert replacement.replacement.kind.value == "user_assumption"
    assert replacement.replacement.value == (
        "500000|580000|660000|740000|820000"
    )
    assert replacement.replacement.unit == "USD million"
    assert replacement.replacement.currency == selected.currency
    assert replacement.replacement.period == selected.period
    preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    assert job is not None
    assert (preparation.status, preparation.current_step, preparation.progress) == (
        "building_model",
        "model_bundle",
        25,
    )
    assert (job.status, job.step, job.progress) == ("queued", "model_bundle", 25)
    events = repository.events(initialized.preparation.id)
    assert [item.event_type for item in events[-2:]] == [
        "critical_input_decided",
        "model_rebuild_queued",
    ]
    assert events[-1].payload == {
        "critical_inputs_artifact_id": str(successor.id),
        "critical_inputs_content_hash": successor.content_hash,
    }
    assert result.critical_inputs is not None
    assert result.critical_inputs.artifact_id == successor.id

    with pytest.raises(ConflictError, match="stale"):
        service.decide(
            project_id=initialized.project.id,
            critical_input_key=selected.key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.CONFIRMED,
        )


@pytest.mark.parametrize(
    ("key", "replacement_value", "unit"),
    (
        ("assumption:required_return", "0", "ratio"),
        ("assumption:terminal_growth", "-0.01", "ratio"),
        ("scenario:bull:revenue", "0", "multiplier"),
        ("market:price:nasdaq_goog", "0", "per_share"),
        ("market:fx:usd_cny", "0", "quote_per_base"),
        ("market:capital:basic_shares", "0", "million"),
        ("market:capital:basic_shares", "1", "million"),
        ("market:capital:pension_liabilities", "1", "million"),
        (
            "assumption:cash_tax_rate",
            "0.2|0.3|1.1|0.3|0.2",
            "ratio",
        ),
    ),
)
def test_illegal_semantic_replacement_is_rejected_before_any_append(
    session,
    key: str,
    replacement_value: str,
    unit: str,
) -> None:
    initialized = _mainline_machine_draft(
        session, key=f"critical-input-invalid-{key.replace(':', '-')}"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    current = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(current) is CriticalInputSet
    selected = next(item for item in current.inputs if item.key == key)
    preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    assert job is not None
    state_before = (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        job.status,
        job.step,
        job.progress,
        len(repository.events(initialized.preparation.id)),
    )

    with pytest.raises(ValidationError, match="replacement"):
        CompanyResearchCriticalInputConfirmationService(
            session, now=lambda: NOW + timedelta(minutes=2)
        ).decide(
            project_id=initialized.project.id,
            critical_input_key=key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
            replacement_value=replacement_value,
            replacement_unit=unit,
            replacement_rationale="Invalid values must fail before append.",
        )

    assert repository.current_artifact(
        initialized.project.id, "critical_inputs"
    ).id == head.id
    preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    assert preparation is not None and preparation.job_id is not None
    job = session.get(Job, preparation.job_id)
    assert job is not None
    assert (
        preparation.status,
        preparation.current_step,
        preparation.progress,
        job.status,
        job.step,
        job.progress,
        len(repository.events(initialized.preparation.id)),
    ) == state_before


def test_marked_unknown_rebuilds_a_specific_gap_before_it_can_be_accepted(
    session,
) -> None:
    initialized = _mainline_machine_draft(
        session, key="critical-input-marked-unknown-rebuild"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    current = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(current) is CriticalInputSet
    selected = next(
        item for item in current.inputs if item.key.startswith("fact:")
    )
    service = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=3)
    )

    with pytest.raises(ValidationError, match="specific governed gap"):
        service.decide(
            project_id=initialized.project.id,
            critical_input_key=selected.key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.ACCEPTED_GAP,
        )
    assert repository.current_artifact(
        initialized.project.id, "critical_inputs"
    ).id == head.id

    marked = service.decide(
        project_id=initialized.project.id,
        critical_input_key=selected.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.MARKED_UNKNOWN,
    )
    assert marked.progress == 25
    assert marked.status.value == "analyzing_company"
    queued_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert queued_head is not None

    worker = CompanyResearchPreparationWorker(
        session, now=lambda: NOW + timedelta(minutes=4)
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert claim.critical_inputs_artifact_id == queued_head.id
    assert claim.critical_inputs_content_hash == queued_head.content_hash
    claimed = repository.events(initialized.preparation.id)[-1]
    assert claimed.event_type == "model_stage_claimed"
    assert claimed.payload == {
        "stage": "model_bundle",
        "attempt": 2,
        "critical_inputs_artifact_id": str(queued_head.id),
        "critical_inputs_content_hash": queued_head.content_hash,
    }
    outcome = worker.run_claim(claim)
    if outcome != "awaiting_judgment_review":
        preparation = repository.preparation_for_project(
            initialized.project.id, fresh=True
        )
        assert preparation is not None and preparation.job_id is not None
        job = session.get(Job, preparation.job_id)
        raise AssertionError(
            f"unexpected rebuild outcome={outcome}; error={job.error if job else None}"
        )

    rebuilt_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert rebuilt_head is not None and rebuilt_head.id != head.id
    rebuilt = CompanyResearchArtifactCodec.decode(
        "critical_inputs", rebuilt_head.payload
    )
    assert type(rebuilt) is CriticalInputSet
    unknown = next(item for item in rebuilt.inputs if item.key == selected.key)
    assert unknown.kind is CriticalInputKind.UNKNOWN
    assert unknown.decision is CriticalInputDecision.PENDING
    assert unknown.gap_key is not None
    memo = repository.current_artifact(initialized.project.id, "memo")
    gaps = repository.current_artifact(initialized.project.id, "research_gaps")
    assert memo is not None and gaps is not None
    decoded_memo = CompanyResearchArtifactCodec.decode("memo", memo.payload)
    decoded_gaps = CompanyResearchArtifactCodec.decode("research_gaps", gaps.payload)
    assert decoded_memo.assessment_status == "not_answerable"
    assert unknown.gap_key in decoded_memo.gap_keys
    assert unknown.gap_key in {gap.code for gap in decoded_gaps}

    accepted = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=5)
    ).decide(
        project_id=initialized.project.id,
        critical_input_key=unknown.key,
        expected_artifact_id=rebuilt_head.id,
        expected_input_fingerprint=unknown.input_fingerprint,
        decision=CriticalInputDecision.ACCEPTED_GAP,
    )
    assert accepted.critical_inputs is not None
    closed = next(
        item
        for item in accepted.critical_inputs.value.inputs
        if item.key == unknown.key
    )
    assert closed.decision is CriticalInputDecision.ACCEPTED_GAP


def test_rebuild_uses_a_copied_user_assumption_and_carries_only_unchanged_decisions(
    session,
) -> None:
    initialized = _mainline_machine_draft(
        session, key="critical-input-rebuild-carry-forward"
    )
    repository = CompanyResearchRepository(session)
    evidence = repository.current_artifact(initialized.project.id, "evidence_index")
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert evidence is not None and head is not None
    evidence_payload = dict(evidence.payload)
    selected = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(selected) is CriticalInputSet
    retained = next(
        item for item in selected.inputs if item.key == "assumption:operating_margin"
    )
    service = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=3)
    )
    first = service.decide(
        project_id=initialized.project.id,
        critical_input_key=retained.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=retained.input_fingerprint,
        decision=CriticalInputDecision.CONFIRMED,
    )
    assert first.critical_inputs is not None
    decided_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert decided_head is not None
    decided_set = CompanyResearchArtifactCodec.decode(
        "critical_inputs", decided_head.payload
    )
    assert type(decided_set) is CriticalInputSet
    replaced = next(
        item for item in decided_set.inputs if item.key == "assumption:revenue"
    )
    service.decide(
        project_id=initialized.project.id,
        critical_input_key=replaced.key,
        expected_artifact_id=decided_head.id,
        expected_input_fingerprint=replaced.input_fingerprint,
        decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
        replacement_value="500000|580000|660000|740000|820000",
        replacement_unit="USD million",
        replacement_rationale="Use the explicit user planning case.",
    )
    queued_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert queued_head is not None and queued_head.id != decided_head.id

    captured = []

    def compile_model(value):
        captured.append(value)
        return CompanyResearchModelBuilder().build(value)

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW + timedelta(minutes=4),
        model_provider=compile_model,
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert claim.critical_inputs_artifact_id == queued_head.id
    assert claim.critical_inputs_content_hash == queued_head.content_hash
    claimed = repository.events(initialized.preparation.id)[-1]
    assert claimed.event_type == "model_stage_claimed"
    assert claimed.payload == {
        "stage": "model_bundle",
        "attempt": 2,
        "critical_inputs_artifact_id": str(queued_head.id),
        "critical_inputs_content_hash": queued_head.content_hash,
    }
    outcome = worker.run_claim(claim)
    if outcome != "awaiting_judgment_review":
        preparation = repository.preparation_for_project(
            initialized.project.id, fresh=True
        )
        assert preparation is not None and preparation.job_id is not None
        job = session.get(Job, preparation.job_id)
        raise AssertionError(
            f"unexpected rebuild outcome={outcome}; error={job.error if job else None}"
        )

    assert len(captured) == 1
    publication_state = repository.lock_publication_state(initialized.project.id)
    machine_memo = publication_state.artifact_heads["memo"]
    model_claim = next(
        event
        for event in reversed(publication_state.events)
        if event.event_type == "model_stage_claimed"
    )
    CompanyResearchPublicationService(
        session, now=lambda: NOW + timedelta(minutes=5)
    )._validate_model_claim_audit(
        state=publication_state,
        machine_memo=machine_memo,
        claim=model_claim,
        allow_legacy_completion_delay=True,
    )
    revenue_path = next(
        item
        for item in captured[0].strategy_assumptions.driver_paths
        if item.driver_key == "revenue"
    )
    assert tuple(str(item) for item in revenue_path.values) == (
        "500000",
        "580000",
        "660000",
        "740000",
        "820000",
    )
    business_head = repository.current_artifact(
        initialized.project.id, "business_map"
    )
    assert business_head is not None
    model_refs = business_head.payload["_lineage"]["artifact_refs"]
    assert {
        "artifact_id": str(queued_head.id),
        "artifact_kind": "critical_inputs",
        "content_hash": queued_head.content_hash,
    } in model_refs
    durable_evidence = repository.current_artifact(
        initialized.project.id, "evidence_index"
    )
    assert durable_evidence is not None
    assert durable_evidence.id == evidence.id
    assert durable_evidence.payload == evidence_payload

    rebuilt_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert rebuilt_head is not None and rebuilt_head.id != decided_head.id
    rebuilt = CompanyResearchArtifactCodec.decode(
        "critical_inputs", rebuilt_head.payload
    )
    assert type(rebuilt) is CriticalInputSet
    frozen_rows = {
        kind: row
        for kind in (
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "memo",
        )
        if (row := repository.current_artifact(initialized.project.id, kind))
        is not None
    }
    CompanyResearchPublicationService(
        session, now=lambda: NOW + timedelta(minutes=5)
    )._authenticate_frozen_critical_model_boundary(
        project_id=initialized.project.id,
        artifact_rows=frozen_rows,
        frozen_critical=rebuilt_head,
    )
    by_key = {item.key: item for item in rebuilt.inputs}
    assert by_key[retained.key].decision is CriticalInputDecision.CONFIRMED
    assert by_key[replaced.key].kind.value == "user_assumption"
    assert by_key[replaced.key].decision is CriticalInputDecision.PENDING


def test_rebuild_discards_output_when_the_bound_critical_head_changes_mid_build(
    session,
) -> None:
    initialized = _mainline_machine_draft(
        session, key="critical-input-rebuild-stale-head"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    current = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(current) is CriticalInputSet
    selected = next(item for item in current.inputs if item.key == "assumption:revenue")
    CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=3)
    ).decide(
        project_id=initialized.project.id,
        critical_input_key=selected.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
        replacement_value="500000|580000|660000|740000|820000",
        replacement_unit="USD million",
        replacement_rationale="Bind this rebuild to one exact decision head.",
    )
    decision_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert decision_head is not None
    before_model_heads = {
        kind: row.id
        for kind in (
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "memo",
        )
        if (row := repository.current_artifact(initialized.project.id, kind))
        is not None
    }
    captured = []

    def compile_after_concurrent_decision(value):
        captured.append(value)
        result = CompanyResearchModelBuilder().build(value)
        bound = CompanyResearchArtifactCodec.decode(
            "critical_inputs", decision_head.payload
        )
        assert type(bound) is CriticalInputSet
        pending = next(
            item
            for item in bound.inputs
            if item.decision is CriticalInputDecision.PENDING
        )
        successor = CriticalInputSet(
            inputs=tuple(
                replace(item, decision=CriticalInputDecision.CONFIRMED)
                if item.key == pending.key
                else item
                for item in bound.inputs
            )
        )
        repository.append_critical_input_successor(
            project_id=initialized.project.id,
            payload=CompanyResearchArtifactCodec.encode(
                "critical_inputs", successor
            ),
            expected_parent_id=decision_head.id,
            created_at=NOW + timedelta(minutes=4),
        )
        session.commit()
        return result

    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW + timedelta(minutes=5),
        model_provider=compile_after_concurrent_decision,
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert claim.critical_inputs_artifact_id == decision_head.id
    assert claim.critical_inputs_content_hash == decision_head.content_hash
    assert worker.run_claim(claim) == "discarded"
    assert len(captured) == 1
    assert captured[0].critical_inputs_artifact_id == decision_head.id
    assert captured[0].critical_inputs_content_hash == decision_head.content_hash
    assert {
        kind: row.id
        for kind in before_model_heads
        if (row := repository.current_artifact(initialized.project.id, kind))
        is not None
    } == before_model_heads
    latest = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert latest is not None and latest.id != decision_head.id


def test_claimed_rebuild_never_consumes_a_later_critical_head(session) -> None:
    initialized = _mainline_machine_draft(
        session, key="critical-input-rebuild-claim-head"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    current = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(current) is CriticalInputSet
    selected = next(item for item in current.inputs if item.key == "assumption:revenue")
    CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=3)
    ).decide(
        project_id=initialized.project.id,
        critical_input_key=selected.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=selected.input_fingerprint,
        decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
        replacement_value="500000|580000|660000|740000|820000",
        replacement_unit="USD million",
        replacement_rationale="Bind the claim before any model input is read.",
    )
    decision_head = repository.current_artifact(
        initialized.project.id, "critical_inputs"
    )
    assert decision_head is not None
    captured = []
    worker = CompanyResearchPreparationWorker(
        session,
        now=lambda: NOW + timedelta(minutes=5),
        model_provider=lambda value: captured.append(value),
    )
    claim = worker.claim_next()
    assert claim is not None and claim.step == "model_bundle"
    assert claim.critical_inputs_artifact_id == decision_head.id
    assert claim.critical_inputs_content_hash == decision_head.content_hash

    bound = CompanyResearchArtifactCodec.decode(
        "critical_inputs", decision_head.payload
    )
    assert type(bound) is CriticalInputSet
    pending = next(
        item for item in bound.inputs if item.decision is CriticalInputDecision.PENDING
    )
    successor = CriticalInputSet(
        inputs=tuple(
            replace(item, decision=CriticalInputDecision.CONFIRMED)
            if item.key == pending.key
            else item
            for item in bound.inputs
        )
    )
    later = repository.append_critical_input_successor(
        project_id=initialized.project.id,
        payload=CompanyResearchArtifactCodec.encode("critical_inputs", successor),
        expected_parent_id=decision_head.id,
        created_at=NOW + timedelta(minutes=4),
    )
    session.commit()

    assert worker.run_claim(claim) == "discarded"
    assert captured == []
    assert repository.current_artifact(
        initialized.project.id, "critical_inputs"
    ).id == later.id
    assert repository.events(initialized.preparation.id)[-1].event_type == (
        "stale_output_discarded"
    )


def _ready_small_mainline_with_terminal_inputs(
    session,
):
    initialized = _small_mainline_machine_draft(
        session, key="critical-input-all-terminal-gate"
    )
    repository = CompanyResearchRepository(session)
    head = repository.current_artifact(initialized.project.id, "critical_inputs")
    assert head is not None
    current = CompanyResearchArtifactCodec.decode("critical_inputs", head.payload)
    assert type(current) is CriticalInputSet
    terminal = list(current.inputs)
    service = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=5)
    )
    decisions = []
    for item in terminal[:-1]:
        decision = (
            CriticalInputDecision.ACCEPTED_GAP
            if item.kind.value == "unknown"
            else CriticalInputDecision.CONFIRMED
        )
        service.decide(
            project_id=initialized.project.id,
            critical_input_key=item.key,
            expected_artifact_id=head.id,
            expected_input_fingerprint=item.input_fingerprint,
            decision=decision,
        )
        decisions.append(decision)
        head = repository.current_artifact(
            initialized.project.id, "critical_inputs"
        )
        assert head is not None
        current = CompanyResearchArtifactCodec.decode(
            "critical_inputs", head.payload
        )
        assert type(current) is CriticalInputSet

    last = current.inputs[-1]
    result = CompanyResearchCriticalInputConfirmationService(
        session, now=lambda: NOW + timedelta(minutes=6)
    ).decide(
        project_id=initialized.project.id,
        critical_input_key=last.key,
        expected_artifact_id=head.id,
        expected_input_fingerprint=last.input_fingerprint,
        decision=(
            CriticalInputDecision.ACCEPTED_GAP
            if last.kind.value == "unknown"
            else CriticalInputDecision.CONFIRMED
        ),
    )
    decisions.append(
        CriticalInputDecision.ACCEPTED_GAP
        if last.kind.value == "unknown"
        else CriticalInputDecision.CONFIRMED
    )

    preparation = repository.preparation_for_project(
        initialized.project.id, fresh=True
    )
    assert preparation is not None
    assert (preparation.status, preparation.current_step, preparation.progress) == (
        "ready_to_freeze",
        "memo",
        95,
    )
    assert result.progress == 95
    assert result.status.value == "completed"
    assert result.critical_inputs is not None
    assert all(
        item.decision is not CriticalInputDecision.PENDING
        for item in result.critical_inputs.value.inputs
    )
    assert CriticalInputDecision.ACCEPTED_GAP in decisions
    assert repository.events(initialized.preparation.id)[-1].event_type == (
        "judgment_confirmed"
    )
    return initialized, result


def test_all_terminal_decisions_confirm_the_current_not_answerable_memo_and_gate_ready(
    session,
) -> None:
    initialized, _run = _ready_small_mainline_with_terminal_inputs(session)
    repository = CompanyResearchRepository(session)
    memo = repository.current_artifact(initialized.project.id, "memo")
    assert memo is not None
    decoded = CompanyResearchArtifactCodec.decode("memo", memo.payload)
    assert decoded.narrative is not None
    assert decoded.markdown is not None
    assert decoded.narrative.summary.text in decoded.markdown
    assert decoded.narrative.business_explanation.text in decoded.markdown
    assert "## Key Drivers" in decoded.markdown
    assert decoded.markdown != (
        "AI research draft accepted after all critical inputs reached terminal decisions."
    )


def test_direct_judgment_confirmation_cannot_bypass_pending_critical_inputs(
    session,
) -> None:
    initialized = _small_mainline_machine_draft(
        session, key="critical-input-pending-confirmation-gate"
    )
    repository = CompanyResearchRepository(session)
    memo = repository.current_artifact(initialized.project.id, "memo")
    state = repository.lock_publication_state(initialized.project.id)
    assert memo is not None

    with pytest.raises(ConflictError, match="critical inputs are pending"):
        CompanyResearchPublicationService(
            session, now=lambda: NOW + timedelta(minutes=6)
        ).confirm_judgment(
            project_id=initialized.project.id,
            expected_lock_version=state.draft.lock_version,
            expected_memo_id=memo.id,
            expected_memo_content_hash=memo.content_hash,
            markdown="This direct confirmation must not bypass the input gate.",
        )


def test_ready_research_rejects_any_later_critical_input_edit(session) -> None:
    initialized, run = _ready_small_mainline_with_terminal_inputs(session)
    assert run.critical_inputs is not None
    selected = run.critical_inputs.value.inputs[0]

    with pytest.raises(ConflictError, match="frozen or stale"):
        CompanyResearchCriticalInputConfirmationService(
            session, now=lambda: NOW + timedelta(minutes=7)
        ).decide(
            project_id=initialized.project.id,
            critical_input_key=selected.key,
            expected_artifact_id=run.critical_inputs.artifact_id,
            expected_input_fingerprint=selected.input_fingerprint,
            decision=CriticalInputDecision.CONFIRMED,
        )
