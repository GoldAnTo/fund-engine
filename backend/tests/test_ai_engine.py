"""Tests for the AI research engine (extract / propose / assess).

All tests run with a mock LLMClient (no real API key required).  The mock
returns deterministic structured JSON based on the prompt content, so the
full pipeline can be exercised offline.
"""
from __future__ import annotations

from dataclasses import replace
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.ai.extraction import StatementExtractor
from app.ai.prompts import (
    ASSESS_PROMPT_VERSION,
    EXTRACT_PROMPT_VERSION,
    PROPOSE_PROMPT_VERSION,
)
from app.ai.proposal import EvidenceProposer
from app.models.ledger import (
    AIAssessment,
    AIRun,
    AtomicClaimCandidate,
    EvidenceSnapshot,
    SourceStatement,
    ValidationError,
)
from app.scripts.run_ai_engine import run_engine
from app.services.research_protocol import ResearchabilityResult
from app.repositories.research import ResearchRepository
from tests.protocol_provenance import seed_protocol_footprint


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extraction_creates_review_gated_candidates_and_airun(session, span):
    client = LLMClient(model_version="mock-test", mock=True)
    extractor = StatementExtractor(client)

    candidates = extractor.extract(span.document_version_id, session)

    assert len(candidates) >= 1
    for candidate in candidates:
        assert candidate.claim_type in {
            "disclosed_fact",
            "reported_claim",
            "management_attribution",
            "forecast",
            "research_opinion",
        }
        assert candidate.normalized_text
        assert candidate.source_span_id == span.id
        assert candidate.quote == span.verbatim_text
    assert list(session.scalars(select(SourceStatement))) == []

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "success"
    assert run.model_version == "mock-test"
    assert run.prompt_version == EXTRACT_PROMPT_VERSION
    assert "span_ids" in run.input_ref
    assert str(span.id) in run.input_ref["span_ids"]
    assert "atomic candidates" in run.output_summary
    assert {
        candidate.structured_fields["run_ref"] for candidate in candidates
    } == {f"extract:{run.id}"}
    assert {
        candidate.validation_result["normalizer_version"]
        for candidate in candidates
    } == {"atomic-claim-normalizer-v1"}


def test_extractor_releases_read_transaction_before_llm_provider(session, span):
    client = LLMClient(model_version="mock-test", mock=True)

    def provider(*_args, **_kwargs):
        assert not session.in_transaction()
        return {
            "statements": [
                {
                    "span_id": str(span.id),
                        "quote": span.verbatim_text,
                        "quote_start": 0,
                        "quote_end": len(span.verbatim_text),
                        "normalized_text": "Management disclosed a material operating update.",
                    "kind": "disclosed_fact",
                }
            ]
        }

    with patch.object(client, "chat_json", side_effect=provider):
        candidates = StatementExtractor(client).extract(span.document_version_id, session)
    assert len(candidates) == 1


def test_extraction_records_no_spans(session, document_service):
    version = document_service.freeze(
        raw=b"empty doc", source_url="https://example.test/empty"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    extractor = StatementExtractor(client)

    statements = extractor.extract(version.id, session)
    assert statements == []

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert "no source spans" in runs[0].output_summary


def test_no_span_extraction_honors_cancelled_output_slot(session, document_service):
    version = document_service.freeze(
        raw=b"empty cancelled doc",
        source_url="https://example.test/empty-cancelled",
    )
    client = LLMClient(model_version="provider-test", mock=True)

    result = StatementExtractor(client).extract(
        version.id,
        session,
        before_persist=lambda: False,
    )

    assert result is None
    assert list(session.scalars(select(AIRun).where(AIRun.kind == "extract"))) == []


def test_no_span_extraction_claims_output_slot_before_success_audit(
    session, document_service
):
    version = document_service.freeze(
        raw=b"empty accepted doc",
        source_url="https://example.test/empty-accepted",
    )
    slot_checks: list[bool] = []
    client = LLMClient(model_version="provider-test", mock=True)

    result = StatementExtractor(client).extract(
        version.id,
        session,
        before_persist=lambda: slot_checks.append(True) or True,
    )

    assert result == []
    assert slot_checks == [True]
    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1 and runs[0].status == "success"


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


def test_proposal_creates_links_and_airun(
    session, document_service, research_service, thesis, document
):
    # The proposer now recalls only cutoff-visible, thesis-relevant
    # statements; the generic fixture statement shares no tokens with the
    # thesis and is correctly filtered out, so seed a relevant one.
    span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="GPU demand 预计 增长",
    )
    research_service.add_statement(
        span.id, "GPU demand 预计 增长", kind="research_opinion"
    )
    client = LLMClient(model_version="mock-test", mock=True)
    proposer = EvidenceProposer(client)

    proposal_ids = proposer.propose(thesis.id, session)

    # The proposer emits Proposals awaiting human review, never reviewed
    # EvidenceLinks (design §9.2).
    from app.models.proposals import Proposal

    assert len(proposal_ids) >= 1
    for pid in proposal_ids:
        proposal = session.get(Proposal, pid)
        assert proposal.kind == "evidence_link"
        assert proposal.proposed_by_type == "ai"
        assert proposal.status == "pending"
        assert proposal.payload["role"] in {
            "supports",
            "contradicts",
            "contextualizes",
        }
        assert proposal.payload["reason"]
        assert proposal.payload["scope"]

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "propose")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "success"
    assert run.model_version == "mock-test"
    assert run.prompt_version == PROPOSE_PROMPT_VERSION
    assert "proposed" in run.output_summary


def test_proposer_releases_read_transaction_before_provider(
    session, document_service, research_service, thesis, document
):
    span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="GPU demand 预计 增长",
    )
    statement = research_service.add_statement(
        span.id, "GPU demand 预计 增长", kind="research_opinion"
    )
    client = LLMClient(model_version="mock-test", mock=True)

    def provider(*_args, **_kwargs):
        assert not session.in_transaction()
        return {
            "links": [
                {
                    "source_statement_id": str(statement.id),
                    "role": "supports",
                    "reason": "demand growth supports the factor",
                    "scope": {"segment": "DC"},
                }
            ]
        }

    with patch.object(client, "chat_json", side_effect=provider):
        assert EvidenceProposer(client).propose(thesis.id, session)


def test_no_recall_proposal_honors_cancelled_output_slot(session, thesis):
    client = LLMClient(model_version="provider-test", mock=True)

    result = EvidenceProposer(client).propose(
        thesis.id,
        session,
        before_persist=lambda: False,
    )

    assert result == []
    assert list(session.scalars(select(AIRun).where(AIRun.kind == "propose"))) == []


def test_no_recall_proposal_claims_output_slot_before_success_audit(session, thesis):
    slot_checks: list[bool] = []
    client = LLMClient(model_version="provider-test", mock=True)

    result = EvidenceProposer(client).propose(
        thesis.id,
        session,
        before_persist=lambda: slot_checks.append(True) or True,
    )

    assert result == []
    assert slot_checks == [True]
    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "propose")))
    assert len(runs) == 1 and runs[0].status == "success"


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def test_assessment_gen_creates_assessment_and_airun(
    session, research_service, thesis, statement
):
    research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )

    client = LLMClient(model_version="mock-test", mock=True)
    generator = AssessmentGenerator(client)

    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    assessment = generator.generate(thesis.id, cutoff, session)

    assert assessment.conclusion in {
        "supported",
        "contradicted",
        "insufficient_evidence",
    }
    assert assessment.displayed_as_provisional is True
    assert assessment.rationale
    assert isinstance(assessment.gaps, list)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "assess")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "success"
    assert run.model_version == "mock-test"
    assert run.prompt_version == ASSESS_PROMPT_VERSION
    assert "conclusion=" in run.output_summary
    assert run.input_ref["research_protocol_status"] is None
    assert run.input_ref["initial_protocol_status"] is None
    assert run.input_ref["final_protocol_status"] is None
    assert run.input_ref["effective_binding_id"] is None
    assert run.input_ref["mechanism_template_version_id"] is None
    assert run.input_ref["verification_rule_ids"] is None
    assert run.input_ref["evidence_link_ids"] == assessment_service_link_ids(
        session, assessment.snapshot_id
    )


def assessment_service_link_ids(session, snapshot_id):
    snapshot = session.get(EvidenceSnapshot, snapshot_id)
    assert snapshot is not None
    return snapshot.evidence_link_ids


def test_assessment_snapshot_contains_exact_prompt_evidence_when_link_arrives_during_provider(
    engine, session, research_service, thesis, statement
):
    original = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="original prompt evidence",
        scope={"segment": "DC"},
    )
    session.commit()
    client = LLMClient(model_version="mock-test", mock=True)
    inserted_ids: list[uuid.UUID] = []

    def provider(*_args, **_kwargs):
        from sqlalchemy.orm import Session

        with Session(engine) as concurrent:
            added = ResearchRepository(concurrent).link_evidence(
                thesis_id=thesis.id,
                source_statement_id=statement.id,
                role="supports",
                reason="published while provider runs",
                scope={"segment": "late"},
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            inserted_ids.append(added.id)
            concurrent.commit()
        return {
            "conclusion": "supported",
            "rationale": "Only the prompt evidence was assessed.",
            "gaps": [],
        }

    with patch.object(client, "chat_json", side_effect=provider):
        assessment = AssessmentGenerator(client).generate(
            thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    run = session.scalar(select(AIRun).where(AIRun.kind == "assess"))
    assert inserted_ids
    assert snapshot.evidence_link_ids == [str(original.id)]
    assert run.input_ref["evidence_link_ids"] == [str(original.id)]
    assert run.input_ref["link_count"] == 1


def test_assessment_explicit_evidence_ids_exclude_other_visible_links(
    session, research_service, thesis, statement
):
    excluded = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="visible but outside this automatic run",
        scope={"run": "older"},
    )
    included = research_service.link_evidence(
        thesis.id,
        statement.id,
        role="contradicts",
        reason="current automatic run evidence",
        scope={"run": "current"},
    )

    assessment = AssessmentGenerator(
        LLMClient(model_version="mock-test", mock=True)
    ).generate(
        thesis.id,
        datetime(2026, 12, 31, tzinfo=UTC),
        session,
        evidence_link_ids=[included.id],
    )

    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    assert snapshot is not None
    assert snapshot.evidence_link_ids == [str(included.id)]
    assert str(excluded.id) not in snapshot.evidence_link_ids


def test_assessment_explicit_evidence_ids_reject_cross_thesis_link(
    session, research_service, thesis, statement
):
    other = research_service.add_thesis(
        thesis.research_case_id,
        statement="另一命题",
        created_by="test",
    )
    wrong = research_service.link_evidence(
        other.id,
        statement.id,
        role="supports",
        reason="belongs to another thesis",
        scope={"run": "wrong-thesis"},
    )

    with pytest.raises(ValueError, match="visible|snapshot thesis"):
        AssessmentGenerator(LLMClient(model_version="mock-test", mock=True)).generate(
            thesis.id,
            datetime(2026, 12, 31, tzinfo=UTC),
            session,
            evidence_link_ids=[wrong.id],
        )


def test_assessment_releases_read_transaction_before_provider(
    session, research_service, thesis, statement
):
    research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)

    def provider(*_args, **_kwargs):
        assert not session.in_transaction()
        return {
            "conclusion": "insufficient_evidence",
            "rationale": "One source does not settle the factor.",
            "gaps": [],
        }

    with patch.object(client, "chat_json", side_effect=provider):
        assessment = AssessmentGenerator(client).generate(
            thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )
    assert assessment is not None


def test_single_metric_monitoring_coerces_assessment_to_insufficient_evidence(
    session, research_service, thesis, statement
):
    strict_thesis = research_service.add_thesis(
        thesis.research_case_id,
        statement="GPU demand will grow under the strict protocol",
        created_by="tester",
        research_protocol_required=True,
    )
    research_service.link_evidence(
        strict_thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    model_output = {
        "conclusion": "supported",
        "rationale": "The evidence supports the thesis.",
        "gaps": [
            "insufficient_primary_metrics",
            "missing raw data",
            "insufficient_primary_metrics",
        ],
    }
    gate = ResearchabilityResult(
        status="single_metric_monitoring",
        reason_codes=["insufficient_primary_metrics"],
        effective_binding_id=None,
        next_action="monitor only",
    )
    footprint = seed_protocol_footprint(session, strict_thesis)
    gate = replace(
        gate,
        effective_binding_id=footprint.binding.id,
        mechanism_template_version_id=footprint.template.id,
        verification_rule_ids=tuple(rule.id for rule in footprint.rules),
    )

    with (
        patch.object(client, "chat_json", return_value=model_output),
        patch(
            "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
            return_value=gate,
        ),
    ):
        assessment = AssessmentGenerator(client).generate(
            strict_thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert assessment.conclusion == "insufficient_evidence"
    assert assessment.gaps == ["missing raw data", "insufficient_primary_metrics"]
    run = session.scalar(select(AIRun).where(AIRun.kind == "assess"))
    assert run is not None
    assert "conclusion=insufficient_evidence" in run.output_summary


def test_single_metric_monitoring_preserves_protocol_gap_after_compliance_rewrite(
    session, research_service, thesis, statement
):
    strict_thesis = research_service.add_thesis(
        thesis.research_case_id,
        statement="GPU demand will grow under the strict protocol",
        created_by="tester",
        research_protocol_required=True,
    )
    research_service.link_evidence(
        strict_thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    model_output = {
        "conclusion": "insufficient_evidence",
        "rationale": "Evidence remains incomplete.",
        "gaps": ["目标价 85 元"],
    }
    gate = ResearchabilityResult(
        status="single_metric_monitoring",
        reason_codes=["insufficient_primary_metrics"],
        effective_binding_id=None,
        next_action="monitor only",
    )
    footprint = seed_protocol_footprint(session, strict_thesis)
    gate = replace(
        gate,
        effective_binding_id=footprint.binding.id,
        mechanism_template_version_id=footprint.template.id,
        verification_rule_ids=tuple(rule.id for rule in footprint.rules),
    )

    def model_then_rewrite(_messages, schema_hint=""):
        if schema_hint == "assess":
            return model_output
        assert schema_hint == "rewrite"
        return {
            "texts": [
                "Evidence remains incomplete.",
                "More operating data is needed.",
            ]
        }

    with (
        patch.object(client, "chat_json", side_effect=model_then_rewrite),
        patch(
            "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
            return_value=gate,
        ),
    ):
        assessment = AssessmentGenerator(client).generate(
            strict_thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert assessment.gaps == [
        "More operating data is needed.",
        "insufficient_primary_metrics",
    ]


def test_assessment_rechecks_protocol_after_provider_and_constrains_stale_result(
    session, research_service, thesis
):
    strict_thesis = research_service.add_thesis(
        thesis.research_case_id,
        statement="Protocol can change while the provider is running",
        created_by="tester",
        research_protocol_required=True,
    )
    ready = ResearchabilityResult(
        status="ready",
        reason_codes=[],
        effective_binding_id=None,
        next_action="assess",
    )
    footprint = seed_protocol_footprint(session, strict_thesis)
    binding_id = footprint.binding.id
    template_id = footprint.template.id
    rule_ids = [rule.id for rule in footprint.rules]
    single_metric = ResearchabilityResult(
        status="single_metric_monitoring",
        reason_codes=["insufficient_primary_metrics"],
        effective_binding_id=binding_id,
        next_action="monitor only",
        mechanism_template_version_id=template_id,
        verification_rule_ids=tuple(reversed(rule_ids)),
    )
    client = LLMClient(model_version="mock-test", mock=True)

    with (
        patch.object(
            client,
            "chat_json",
            return_value={
                "conclusion": "supported",
                "rationale": "The stale model result is directional.",
                "gaps": [],
            },
        ),
        patch(
            "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
            side_effect=[ready, single_metric, single_metric],
        ) as check,
    ):
        assessment = AssessmentGenerator(client).generate(
            strict_thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert check.call_count == 3
    assert assessment is not None
    assert assessment.conclusion == "insufficient_evidence"
    assert assessment.gaps == ["insufficient_primary_metrics"]
    assert assessment.research_protocol_status == "single_metric_monitoring"
    assert assessment.effective_binding_id == binding_id
    assert assessment.mechanism_template_version_id == template_id
    assert assessment.verification_rule_ids == sorted(
        [str(rule_id) for rule_id in rule_ids]
    )
    run = session.scalar(select(AIRun).where(AIRun.kind == "assess"))
    assert run.input_ref["initial_protocol_status"] == "ready"
    assert run.input_ref["final_protocol_status"] == "single_metric_monitoring"
    assert run.input_ref["effective_binding_id"] == str(binding_id)
    assert run.input_ref["mechanism_template_version_id"] == str(template_id)
    assert run.input_ref["verification_rule_ids"] == sorted(
        [str(rule_id) for rule_id in rule_ids]
    )


def test_assessment_takes_case_lock_before_final_protocol_recheck(
    session, research_service, thesis
):
    strict_thesis = research_service.add_thesis(
        thesis.research_case_id,
        statement="Final protocol check must be serialized with persistence",
        created_by="tester",
        research_protocol_required=True,
    )
    gate = ResearchabilityResult(
        status="ready",
        reason_codes=[],
        effective_binding_id=None,
        next_action="assess",
    )
    footprint = seed_protocol_footprint(session, strict_thesis, status="ready")
    gate = replace(
        gate,
        effective_binding_id=footprint.binding.id,
        mechanism_template_version_id=footprint.template.id,
        verification_rule_ids=tuple(rule.id for rule in footprint.rules),
    )
    events: list[str] = []
    client = LLMClient(model_version="mock-test", mock=True)

    def check(_service, _thesis_id):
        events.append("check")
        return gate

    with (
        patch.object(
            client,
            "chat_json",
            return_value={
                "conclusion": "supported",
                "rationale": "The final gate is ready.",
                "gaps": [],
            },
        ),
        patch(
            "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
            autospec=True,
            side_effect=check,
        ),
        patch(
            "app.ai.assessment_gen.lock_event_scope_case",
            side_effect=lambda _session, _case_id: events.append("lock"),
        ),
    ):
        assessment = AssessmentGenerator(client).generate(
            strict_thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert assessment is not None
    assert events == ["check", "lock", "check", "check"]


def test_assessment_rechecks_protocol_after_provider_and_blocks_persistence(
    session, research_service, thesis
):
    strict_thesis = research_service.add_thesis(
        thesis.research_case_id,
        statement="Protocol can become blocked while the provider is running",
        created_by="tester",
        research_protocol_required=True,
    )
    ready = ResearchabilityResult("ready", [], None, "assess")
    blocked = ResearchabilityResult(
        "blocked", ["missing_outcome_binding"], None, "complete protocol"
    )
    client = LLMClient(model_version="mock-test", mock=True)

    with (
        patch.object(
            client,
            "chat_json",
            return_value={
                "conclusion": "supported",
                "rationale": "The stale model result must not persist.",
                "gaps": [],
            },
        ),
        patch(
            "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
            side_effect=[ready, blocked],
        ),
        pytest.raises(ValidationError, match="researchability gate blocked"),
    ):
        AssessmentGenerator(client).generate(
            strict_thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert session.scalar(
        select(EvidenceSnapshot.id).where(EvidenceSnapshot.thesis_id == strict_thesis.id)
    ) is None
    assert session.scalar(select(AIAssessment.id)) is None
    run = session.scalar(select(AIRun).where(AIRun.kind == "assess"))
    assert run is not None and run.status == "failed"
    assert run.input_ref["final_protocol_status"] == "blocked"


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------


def test_ai_run_records_failure_on_extraction_error(session, span):
    client = LLMClient(model_version="mock-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        side_effect=RuntimeError("LLM error sentinel-secret"),
    ):
        extractor = StatementExtractor(client)
        with pytest.raises(RuntimeError, match="sentinel-secret"):
            extractor.extract(span.document_version_id, session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert run.error == "AI operation failed"
    assert "sentinel-secret" not in run.error
    assert run.model_version == "mock-test"
    assert run.prompt_version == EXTRACT_PROMPT_VERSION


def test_table_only_extraction_honors_cancelled_output_slot(
    session, document_service
):
    version = document_service.freeze(
        raw=b"table-only extraction",
        source_url="https://example.test/table-only-cancel",
    )
    document_service.add_span(
        document_version_id=version.id,
        locator={"page": 1},
        verbatim_text=(
            "主要会计数据 单位：千元\n"
            "指标 2025年 2024年\n"
            "营业收入 50,000,000 40,000,000\n"
        ),
    )
    client = LLMClient(model_version="provider-test", mock=True)

    result = StatementExtractor(client).extract(
        version.id,
        session,
        before_persist=lambda: False,
    )

    assert result is None
    assert list(session.scalars(select(AtomicClaimCandidate))) == []
    assert list(session.scalars(select(AIRun).where(AIRun.kind == "extract"))) == []


def test_cli_extract_failure_commits_airun_and_stops_before_propose(
    session, research_case, document_service
):
    version = document_service.freeze(
        raw=b"provider failure source",
        source_url="https://example.test/cli-extract-failure",
    )
    document_service.attach_to_case(
        research_case_id=research_case.id,
        document_version_id=version.id,
    )
    document_service.add_span(
        document_version_id=version.id,
        locator={"page": 1},
        verbatim_text=(
            "Management described sustained accelerator demand and a longer "
            "order backlog in the latest operating update."
        ),
    )
    version_id = version.id
    client = LLMClient(model_version="provider-test", mock=True)

    with (
        patch("app.scripts.run_ai_engine.LLMClient.from_env", return_value=client),
        patch.object(
            client,
            "chat_json",
            side_effect=RuntimeError("extract provider failed"),
        ),
        patch("app.scripts.run_ai_engine.EvidenceProposer.propose") as propose,
        patch("app.scripts.run_ai_engine.AssessmentGenerator.generate") as assess,
        pytest.raises(RuntimeError, match="extract provider failed"),
    ):
        run_engine(session, research_case)

    propose.assert_not_called()
    assess.assert_not_called()
    session.rollback()
    with Session(session.get_bind()) as check:
        run = check.scalar(
            select(AIRun)
            .where(AIRun.kind == "extract", AIRun.status == "failed")
            .where(
                AIRun.input_ref["document_version_id"].as_string()
                == str(version_id)
            )
        )
        assert run is not None


def test_ai_run_records_failure_on_proposal_error(
    session, document_service, research_service, thesis, document
):
    # A recalled (relevant) statement is required for the proposer to reach
    # the LLM call at all.
    span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="GPU demand 预计 增长",
    )
    research_service.add_statement(
        span.id, "GPU demand 预计 增长", kind="research_opinion"
    )
    client = LLMClient(model_version="mock-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        side_effect=RuntimeError("LLM error sentinel-secret"),
    ):
        proposer = EvidenceProposer(client)
        with pytest.raises(RuntimeError, match="sentinel-secret"):
            proposer.propose(thesis.id, session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "propose")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert run.error == "AI operation failed"
    assert "sentinel-secret" not in run.error
    assert run.model_version == "mock-test"
    assert run.prompt_version == PROPOSE_PROMPT_VERSION


def test_cli_propose_failure_commits_airun_and_stops_before_assess(
    session, document_service, research_service, research_case, thesis, document
):
    span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="GPU demand is expected to grow with accelerator orders.",
    )
    research_service.add_statement(
        span.id,
        "GPU demand is expected to grow with accelerator orders.",
        kind="research_opinion",
    )
    session.commit()
    thesis_id = thesis.id
    client = LLMClient(model_version="provider-test", mock=True)

    with (
        patch("app.scripts.run_ai_engine.LLMClient.from_env", return_value=client),
        patch.object(
            client,
            "chat_json",
            side_effect=RuntimeError("propose provider failed"),
        ),
        patch("app.scripts.run_ai_engine.AssessmentGenerator.generate") as assess,
        pytest.raises(RuntimeError, match="propose provider failed"),
    ):
        run_engine(session, research_case, skip_extract=True)

    assess.assert_not_called()
    session.rollback()
    with Session(session.get_bind()) as check:
        run = check.scalar(
            select(AIRun)
            .where(AIRun.kind == "propose", AIRun.status == "failed")
            .where(AIRun.input_ref["thesis_id"].as_string() == str(thesis_id))
        )
        assert run is not None


def test_cli_propose_partial_output_is_rolled_back_before_failed_audit(
    session, document_service, research_service, research_case, thesis, document
):
    import json

    from app.models.events import DomainEvent
    from app.models.proposals import Proposal

    first_span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="GPU demand is expected to grow with accelerator orders.",
    )
    second_span = document_service.add_span(
        document_version_id=document.id,
        locator={"page": 2},
        verbatim_text="GPU accelerator backlog supports continued demand growth.",
    )
    research_service.add_statement(
        first_span.id,
        "GPU demand is expected to grow with accelerator orders.",
        kind="research_opinion",
    )
    research_service.add_statement(
        second_span.id,
        "GPU accelerator backlog supports continued demand growth.",
        kind="research_opinion",
    )
    session.commit()
    thesis_id = thesis.id
    client = LLMClient(model_version="provider-test", mock=True)

    def partial_then_invalid(messages, schema_hint=""):
        statements = json.loads(messages[-1]["content"])["statements"]
        assert len(statements) >= 2
        return {
            "links": [
                {
                    "source_statement_id": statements[0]["id"],
                    "role": "supports",
                    "reason": "valid first proposal",
                    "scope": {"segment": "DC"},
                },
                {
                    "source_statement_id": statements[1]["id"],
                    "reason": "missing role after partial output",
                    "scope": {"segment": "DC"},
                },
            ]
        }

    with (
        patch("app.scripts.run_ai_engine.LLMClient.from_env", return_value=client),
        patch.object(client, "chat_json", side_effect=partial_then_invalid),
        patch("app.scripts.run_ai_engine.AssessmentGenerator.generate") as assess,
        pytest.raises(KeyError, match="role"),
    ):
        run_engine(session, research_case, skip_extract=True)

    assess.assert_not_called()
    session.rollback()
    with Session(session.get_bind()) as check:
        assert check.scalar(select(func.count()).select_from(Proposal)) == 0
        assert check.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.type == "evidence_link_proposed")
        ) == 0
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(
                AIRun.kind == "propose",
                AIRun.status == "failed",
                AIRun.input_ref["thesis_id"].as_string() == str(thesis_id),
            )
        ) == 1


def test_ai_run_records_failure_on_assessment_error(
    session, research_service, thesis, statement
):
    research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )

    client = LLMClient(model_version="mock-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        side_effect=RuntimeError("LLM error sentinel-secret"),
    ):
        generator = AssessmentGenerator(client)
        with pytest.raises(RuntimeError, match="sentinel-secret"):
            generator.generate(thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "assess")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert run.error == "AI operation failed"
    assert "sentinel-secret" not in run.error
    assert run.model_version == "mock-test"
    assert run.prompt_version == ASSESS_PROMPT_VERSION


def test_assessment_failure_rolls_back_partial_snapshot_before_failed_audit(
    session, research_service, thesis, statement
):
    research_service.link_evidence(
        thesis.id,
        statement.id,
        role="supports",
        reason="orders rose",
        scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)

    with (
        patch(
            "app.services.assessment.AssessmentService.create_ai_assessment",
            side_effect=ValidationError("assessment persistence rejected"),
        ),
        pytest.raises(ValidationError, match="assessment persistence rejected"),
    ):
        AssessmentGenerator(client).generate(
            thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert session.scalar(
        select(EvidenceSnapshot.id).where(EvidenceSnapshot.thesis_id == thesis.id)
    ) is None
    assert session.scalar(select(AIAssessment.id)) is None
    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "assess")))
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error == "AI operation failed"
    assert "researchability gate blocked" not in runs[0].error


# ---------------------------------------------------------------------------
# Mock mode auto-activation
# ---------------------------------------------------------------------------


def test_llm_client_auto_mocks_without_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    client = LLMClient.from_env()
    assert client._mock is True
    assert client.model_version.startswith("mock-")


def test_llm_client_chat_json_returns_dict_in_mock_mode():
    client = LLMClient(model_version="mock-test", mock=True)
    messages = [
        {"role": "system", "content": "test"},
        {"role": "user", "content": '{"spans": [{"span_id": "x", "verbatim_text": "收入增长30%"}]}'},
    ]
    result = client.chat_json(messages, schema_hint="extract")
    assert isinstance(result, dict)
    assert "statements" in result
    assert len(result["statements"]) == 1
    assert result["statements"][0]["kind"] == "disclosed_fact"
