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


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extraction_creates_statements_and_airun(session, span):
    client = LLMClient(model_version="mock-test", mock=True)
    extractor = StatementExtractor(client)

    statements = extractor.extract(span.document_version_id, session)

    assert len(statements) >= 1
    for stmt in statements:
        assert stmt.kind in {
            "disclosed_fact",
            "management_attribution",
            "forecast",
            "research_opinion",
        }
        assert stmt.normalized_text
        assert stmt.source_span_id == span.id

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "success"
    assert run.model_version == "mock-test"
    assert run.prompt_version == EXTRACT_PROMPT_VERSION
    assert "span_ids" in run.input_ref
    assert str(span.id) in run.input_ref["span_ids"]
    assert "extracted" in run.output_summary


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
    assert "no spans" in runs[0].output_summary


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

    links = proposer.propose(thesis.id, session)

    assert len(links) >= 1
    for link in links:
        assert link.creator_type == "ai"
        assert link.review_state == "machine_generated"
        assert link.role in {"supports", "contradicts", "contextualizes"}
        assert link.reason
        assert link.scope

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "propose")))
    assert len(runs) == 1
    run = runs[0]
    assert run.status == "success"
    assert run.model_version == "mock-test"
    assert run.prompt_version == PROPOSE_PROMPT_VERSION
    assert "proposed" in run.output_summary


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
