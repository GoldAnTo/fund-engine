"""Tests for the compliance gate and the production provider discipline.

Compliance: keyword rules (six violation categories, allow/rewrite/refuse),
sanitize/word-boundary engineering details, and the integration points that
keep refused AI text out of the ledger.

Provider discipline: with APP_ENV=production a missing LLM_API_KEY is a hard
failure, never a silent fallback to mock mode.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.ai.proposal import EvidenceProposer
from app.models.ledger import AIRun
from app.services.compliance import (
    ComplianceAction,
    ComplianceRefusedError,
    ViolationCategory,
    assert_compliant,
    evaluate_compliance,
)


# ---------------------------------------------------------------------------
# Rule layer
# ---------------------------------------------------------------------------


def test_refuse_categories():
    assert evaluate_compliance("建议买入该股票").action is ComplianceAction.REFUSE
    assert evaluate_compliance("可以适当加仓").action is ComplianceAction.REFUSE
    assert evaluate_compliance("这是首选标的").action is ComplianceAction.REFUSE
    assert evaluate_compliance("帮我选股票").action is ComplianceAction.REFUSE


def test_rewrite_categories():
    assert evaluate_compliance("目标价 120 元").action is ComplianceAction.REWRITE
    assert evaluate_compliance("稳赚不赔的机会").action is ComplianceAction.REWRITE


def test_allow_cases():
    assert not evaluate_compliance("").is_hit
    assert not evaluate_compliance(None).is_hit
    assert not evaluate_compliance("营业收入同比增长 32%，研发投入持续加大").is_hit
    # 正常英文上下文不按子串误命中 buy/sell
    assert not evaluate_compliance("The rebuy of shares was disclosed").is_hit


def test_base64_data_uri_is_stripped_before_scan():
    text = "分析正常 ![chart](data:image/png;base64,buysellxyzbuysell)"
    assert not evaluate_compliance(text).is_hit


def test_assert_compliant_raises_with_decision():
    with pytest.raises(ComplianceRefusedError) as exc_info:
        assert_compliant("可以适当加仓")
    decision = exc_info.value.decision
    assert decision.is_hit
    assert decision.hits[0].category is ViolationCategory.POSITION_GUIDANCE


# ---------------------------------------------------------------------------
# Integration: refused AI text never reaches the ledger
# ---------------------------------------------------------------------------


def test_assessment_refusal_fails_run_without_writing(
    session, research_service, thesis, statement
):
    research_service.link_evidence(
        thesis.id, statement.id,
        role="supports", reason="orders rose", scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    from unittest.mock import patch

    payload = {
        "conclusion": "supported",
        "rationale": "证据支持，建议买入相关标的",
        "gaps": [],
    }
    with patch.object(client, "chat_json", return_value=payload):
        generator = AssessmentGenerator(client)
        with pytest.raises(ComplianceRefusedError):
            generator.generate(thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session)

    run = session.scalars(select(AIRun).where(AIRun.kind == "assess")).one()
    assert run.status == "failed"
    assert "compliance refused" in run.error


def test_proposal_skips_refused_links_but_keeps_clean_ones(
    session, document_service, research_service, thesis, document
):
    span_a = document_service.add_span(
        document_version_id=document.id, locator={"page": 1},
        verbatim_text="GPU demand 增长",
    )
    stmt_a = research_service.add_statement(
        span_a.id, "GPU demand 增长", kind="disclosed_fact"
    )
    span_b = document_service.add_span(
        document_version_id=document.id, locator={"page": 2},
        verbatim_text="GPU demand 订单 饱满",
    )
    stmt_b = research_service.add_statement(
        span_b.id, "GPU demand 订单 饱满", kind="disclosed_fact"
    )

    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "links": [
            {
                "source_statement_id": str(stmt_a.id),
                "role": "supports", "reason": "订单增长支撑命题",
                "scope": {"segment": "AI算力"},
            },
            {
                "source_statement_id": str(stmt_b.id),
                "role": "supports", "reason": "建议买入",
                "scope": {"segment": "AI算力"},
            },
        ]
    }
    from unittest.mock import patch

    with patch.object(client, "chat_json", return_value=payload):
        links = EvidenceProposer(client).propose(thesis.id, session)

    assert [link.source_statement_id for link in links] == [stmt_a.id]
    run = session.scalars(select(AIRun).where(AIRun.kind == "propose")).one()
    assert "1 refused by compliance" in run.output_summary


# ---------------------------------------------------------------------------
# Provider failure discipline
# ---------------------------------------------------------------------------


def test_production_without_api_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        LLMClient.from_env()


def test_development_without_api_key_uses_mock(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    client = LLMClient.from_env()
    assert client.model_version.startswith("mock-")
