"""Tests for the compliance gate and the production provider discipline.

Compliance: keyword rules (six violation categories, allow/rewrite/refuse),
sanitize/word-boundary engineering details, and the integration points that
keep refused AI text out of the ledger.

Provider discipline: only APP_ENV=test permits a missing LLM_API_KEY to select
deterministic mock mode. Every live runtime fails closed instead.
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


def _isolate_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TEMPERATURE",
        "LLM_SEED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
    monkeypatch.setenv("LLM_SEED", "")


@pytest.mark.parametrize("app_env", [None, "", "development", "production"])
def test_live_runtime_without_api_key_fails_loudly(monkeypatch, app_env):
    _isolate_llm_env(monkeypatch)
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)

    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        LLMClient.from_env()


@pytest.mark.parametrize("invalid_knob", ["LLM_TEMPERATURE", "LLM_SEED"])
def test_missing_live_api_key_error_precedes_invalid_knobs(
    monkeypatch, invalid_knob
):
    _isolate_llm_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(invalid_knob, "invalid")

    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        LLMClient.from_env()


def test_test_environment_without_api_key_uses_mock(monkeypatch):
    _isolate_llm_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    client = LLMClient.from_env()
    assert client._mock is True
    assert client.model_version.startswith("mock-")


def test_test_environment_with_api_key_uses_external_client(monkeypatch):
    from unittest.mock import patch

    _isolate_llm_env(monkeypatch)
    base_url = "https://external-llm.example.invalid/v1"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_API_KEY", "dummy")
    monkeypatch.setenv("LLM_BASE_URL", base_url)
    monkeypatch.setenv("LLM_MODEL", "external-test-model")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    monkeypatch.setenv("LLM_SEED", "42")

    sdk_client = object()
    with patch("openai.OpenAI", return_value=sdk_client) as openai_constructor:
        client = LLMClient.from_env()

    openai_constructor.assert_called_once_with(api_key="dummy", base_url=base_url)
    assert client._client is sdk_client
    assert client._mock is False
    assert client.model_version == "external-test-model"
    assert client._temperature == 0.25
    assert client._seed == 42


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
