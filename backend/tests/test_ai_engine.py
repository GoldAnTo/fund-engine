"""Tests for the AI research engine (extract / propose / assess).

All tests run with a mock LLMClient (no real API key required).  The mock
returns deterministic structured JSON based on the prompt content, so the
full pipeline can be exercised offline.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select

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
    EvidenceLink,
    SourceStatement,
)
from app.services.research_protocol import ResearchabilityResult


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


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------


def test_ai_run_records_failure_on_extraction_error(session, span):
    client = LLMClient(model_version="mock-test", mock=True)

    with patch.object(client, "chat_json", side_effect=RuntimeError("LLM error")):
        extractor = StatementExtractor(client)
        with pytest.raises(RuntimeError, match="LLM error"):
            extractor.extract(span.document_version_id, session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert "LLM error" in run.error
    assert run.model_version == "mock-test"
    assert run.prompt_version == EXTRACT_PROMPT_VERSION


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

    with patch.object(client, "chat_json", side_effect=RuntimeError("LLM error")):
        proposer = EvidenceProposer(client)
        with pytest.raises(RuntimeError, match="LLM error"):
            proposer.propose(thesis.id, session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "propose")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert "LLM error" in run.error
    assert run.model_version == "mock-test"
    assert run.prompt_version == PROPOSE_PROMPT_VERSION


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

    with patch.object(client, "chat_json", side_effect=RuntimeError("LLM error")):
        generator = AssessmentGenerator(client)
        with pytest.raises(RuntimeError, match="LLM error"):
            generator.generate(thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "assess")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "failed"
    assert "LLM error" in run.error
    assert run.model_version == "mock-test"
    assert run.prompt_version == ASSESS_PROMPT_VERSION


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
