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
        proposal_ids = EvidenceProposer(client).propose(thesis.id, session)

    # The proposer now returns Proposal ids (design §9.2); the refused link is
    # never proposed at all.
    from app.models.proposals import Proposal

    proposals = [session.get(Proposal, pid) for pid in proposal_ids]
    assert [p.payload["source_statement_id"] for p in proposals] == [str(stmt_a.id)]
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


# ---------------------------------------------------------------------------
# Bounded rewrite loop (assessment path)
# ---------------------------------------------------------------------------


def _assess_then_rewrite(client, assess_payload, rewrite_payload=None):
    """Patch chat_json: assess calls get ``assess_payload``; rewrite calls get
    ``rewrite_payload`` (or the real mock rewrite when None)."""
    from unittest.mock import patch

    real_mock = client._mock

    def fake(messages, schema_hint=""):
        if schema_hint == "assess":
            return assess_payload
        if rewrite_payload is not None and schema_hint == "rewrite":
            return rewrite_payload
        # delegate to the real mock implementation
        from app.ai.client import _mock_response

        return _mock_response(messages, schema_hint)

    return patch.object(client, "chat_json", new=fake)


def test_rewriteable_violation_repaired_and_admitted(
    session, research_service, thesis, statement
):
    """A target-price expression (REWRITE category) gets one rewrite attempt;
    the cleaned rationale reaches the ledger and the AIRun records the repair."""
    research_service.link_evidence(
        thesis.id, statement.id,
        role="supports", reason="orders rose", scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "conclusion": "supported",
        "rationale": "证据一致支持命题。对应目标价85元。",
        "gaps": [],
    }
    with _assess_then_rewrite(client, payload):
        assessment = AssessmentGenerator(client).generate(
            thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )

    assert "目标价" not in assessment.rationale
    assert "证据一致支持命题" in assessment.rationale
    assert assessment.displayed_as_provisional is True

    run = session.scalars(select(AIRun).where(AIRun.kind == "assess")).one()
    assert run.status == "success"
    assert "rewritten_for_compliance" in run.output_summary


def test_rewrite_still_violating_refuses_without_writing(
    session, research_service, thesis, statement
):
    """A rewrite that comes back still violating refuses the whole run;
    nothing is persisted except the failed AIRun."""
    from app.models.ledger import AIAssessment

    research_service.link_evidence(
        thesis.id, statement.id,
        role="supports", reason="orders rose", scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "conclusion": "supported",
        "rationale": "证据支持命题。对应目标价85元。",
        "gaps": [],
    }
    bad_rewrite = {"texts": ["修复后仍建议买入"]}
    with _assess_then_rewrite(client, payload, rewrite_payload=bad_rewrite):
        with pytest.raises(ComplianceRefusedError):
            AssessmentGenerator(client).generate(
                thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
            )

    assert session.scalars(select(AIAssessment)).all() == []
    run = session.scalars(select(AIRun).where(AIRun.kind == "assess")).one()
    assert run.status == "failed"
    assert "compliance refused" in run.error


def test_refuse_category_never_reaches_rewrite(
    session, research_service, thesis, statement
):
    """REFUSE-category hits are refused immediately — the rewrite stage is
    never invoked (chat_json sees exactly one assess call)."""
    research_service.link_evidence(
        thesis.id, statement.id,
        role="supports", reason="orders rose", scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "conclusion": "supported",
        "rationale": "证据支持，建议买入相关标的",
        "gaps": [],
    }
    calls: list[str] = []

    def fake(messages, schema_hint=""):
        calls.append(schema_hint)
        return payload

    from unittest.mock import patch

    with patch.object(client, "chat_json", new=fake):
        with pytest.raises(ComplianceRefusedError):
            AssessmentGenerator(client).generate(
                thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
            )
    assert calls == ["assess"]


def test_malformed_rewrite_response_refuses_with_original_decision(
    session, research_service, thesis, statement
):
    """A rewrite response of the wrong shape/length refuses with the original
    violation rather than admitting garbage."""
    research_service.link_evidence(
        thesis.id, statement.id,
        role="supports", reason="orders rose", scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "conclusion": "supported",
        "rationale": "证据支持命题。对应目标价85元。",
        "gaps": ["缺少分部数据"],
    }
    with _assess_then_rewrite(client, payload, rewrite_payload={"texts": ["only one"]}):
        with pytest.raises(ComplianceRefusedError) as exc_info:
            AssessmentGenerator(client).generate(
                thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
            )
    assert any(
        h.category is ViolationCategory.TARGET_PRICE
        for h in exc_info.value.decision.hits
    )


def test_rewrite_applies_to_gaps_too(session, research_service, thesis, statement):
    """Violations in gaps are repaired the same way as the rationale."""
    research_service.link_evidence(
        thesis.id, statement.id,
        role="supports", reason="orders rose", scope={"segment": "DC"},
    )
    client = LLMClient(model_version="mock-test", mock=True)
    payload = {
        "conclusion": "insufficient_evidence",
        "rationale": "证据方向不一，仍需验证。",
        "gaps": ["缺少分部数据", "需关注预期收益口径"],
    }
    with _assess_then_rewrite(client, payload):
        assessment = AssessmentGenerator(client).generate(
            thesis.id, datetime(2026, 12, 31, tzinfo=UTC), session
        )
    assert all("预期收益" not in str(g) for g in assessment.gaps)
