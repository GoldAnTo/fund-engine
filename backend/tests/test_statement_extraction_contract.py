"""Exercise the provider JSON boundary without contacting an LLM service."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.acquisition.policy import B_SCOPE_POLICY
from app.ai.extraction import StatementExtractor
from app.ai.prompts import EXTRACT_SYSTEM
from app.services.atomic_claims import AtomicClaimService
from app.services.automatic_admission import (
    B_SCOPE_GATE_VERSION,
    AdmissionContext,
    AutomaticAdmissionGate,
)


def _extract(session, document_service, *, period="2025", authority="primary_disclosure",
             fields=None, text=None):
    text = text or f"示例公司{period or ''}营业收入为100亿元。"
    document = document_service.freeze(
        raw=text.encode(),
        source_url=f"https://example.test/disclosure/{uuid.uuid4()}",
        published_at=datetime(2026, 1, 10, tzinfo=UTC),
        title="另一家公司2026年公告",
        source_authority=authority,
    )
    document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text=text,
    )

    class ProviderResponseClient:
        model_version = "provider-json-contract-test"

        def chat_json(self, messages, schema_hint=""):
            assert schema_hint == "extract"
            spans = json.loads(messages[-1]["content"])["spans"]
            # Only the network boundary is replaced: production extraction,
            # candidate persistence, and semantic validation remain in use.
            return json.loads(json.dumps({"statements": [{
                "span_id": spans[0]["span_id"],
                "kind": "disclosed_fact",
                "quote": text,
                "quote_start": 0,
                "quote_end": len(text),
                "normalized_text": text,
                "assertion_actor": "示例公司",
                "subject": "示例公司",
                "predicate": "营业收入",
                "object_text": "100亿元",
                "numeric_value": "100",
                "unit": "亿元",
                "observed_period": period,
                "scope": {},
                **(fields or {}),
            }]}))

    return StatementExtractor(ProviderResponseClient()).extract(document.id, session)


def _semantic(session, candidate):
    request = {
        "entity_names": ["示例公司"],
        "metric_terms": ["营业收入"],
        "period_start": "2024-01-01",
        "period_end": "2026-06-30",
        "objective": "support",
        "target_link_role": "supports",
    }
    context = AdmissionContext(
        job_id=uuid.uuid4(), retrieval_artifact_id=uuid.uuid4(), thesis_id=uuid.uuid4(),
        cutoff=datetime(2026, 6, 30, tzinfo=UTC), objective="support",
        target_link_role="supports", gate_version=B_SCOPE_GATE_VERSION,
        policy_version=B_SCOPE_POLICY.version,
        allowed_source_roles=frozenset({"company_disclosure"}),
        metric_terms=("营业收入",), lease_token="test-lease",
    )
    return AutomaticAdmissionGate(session)._semantic_gate(
        SimpleNamespace(job=SimpleNamespace(request_snapshot=request), candidate=candidate),
        context,
    )


def test_prompt_requests_complete_grounded_claim_fields_and_preserves_period_grain():
    for field in ("subject", "predicate", "numeric_value", "unit", "observed_period"):
        assert f'"{field}"' in EXTRACT_SYSTEM
    assert "observed_period 留空" not in EXTRACT_SYSTEM
    assert "YYYY-MM-DD" in EXTRACT_SYSTEM
    assert "YYYY-MM" in EXTRACT_SYSTEM
    assert "YYYY" in EXTRACT_SYSTEM
    assert "null" in EXTRACT_SYSTEM
    assert "predicate 仅保留指标名称" in EXTRACT_SYSTEM
    assert "不得包含主体、年份、期间" in EXTRACT_SYSTEM


@pytest.mark.parametrize("authority,expected_kind", [
    ("primary_disclosure", "disclosed_fact"),
    ("user_supplied", "reported_claim"),
    ("licensed_research", "reported_claim"),
])
@pytest.mark.parametrize("period", ["2025", "2025-06", "2025-06-30"])
def test_provider_claim_fields_reach_existing_semantic_gate(
    session, document_service, authority, expected_kind, period,
):
    candidates = _extract(session, document_service, period=period, authority=authority)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.claim_type == expected_kind
    assert candidate.structured_fields["subject"] == "示例公司"
    assert candidate.structured_fields["predicate"] == "营业收入"
    assert candidate.structured_fields["numeric_value"] == "100"
    assert candidate.structured_fields["unit"] == "亿元"
    assert candidate.structured_fields["observed_period"] == period
    result = _semantic(session, candidate)
    assert result.passed, result.facts


def test_missing_facts_stay_null_without_metadata_enrichment(session, document_service):
    fields = dict.fromkeys(("subject", "predicate", "numeric_value", "unit", "observed_period"))
    candidates = _extract(session, document_service, fields=fields, text="公告已发布。")
    assert len(candidates) == 1
    assert all(candidates[0].structured_fields[name] is None for name in fields)
    assert not _semantic(session, candidates[0]).passed


@pytest.mark.parametrize("period", ["2025-13", "2025-02-30", "2025-Q1", "2025-6", 2025])
def test_invalid_period_does_not_create_a_candidate(session, document_service, period):
    assert _extract(session, document_service, period=period) == []


@pytest.mark.parametrize("fields,reason", [
    ({"numeric_value": "NaN"}, "semantic_numeric_value_invalid"),
    ({"numeric_value": "Infinity"}, "semantic_numeric_value_invalid"),
    ({"numeric_value": "-Infinity"}, "semantic_numeric_value_invalid"),
    ({"numeric_value": "100", "unit": None}, "semantic_numeric_unit_missing"),
    ({"numeric_value": "101"}, "semantic_numeric_quote_mismatch"),
    ({"subject": "另一家公司"}, "semantic_subject_mismatch"),
    ({"predicate": "净利润"}, "semantic_metric_mismatch"),
    ({"normalized_text": "示例公司2025-06-30营业收入为101亿元。"},
     "semantic_normalized_text_numeric_mismatch"),
])
def test_extracted_fields_do_not_bypass_existing_grounding_gates(
    session, document_service, fields, reason,
):
    candidate = _extract(session, document_service, period="2025-06-30", fields=fields)[0]
    result = _semantic(session, candidate)
    assert not result.passed
    assert reason in result.facts["failures"]


@pytest.mark.parametrize("period,expected_date", [
    ("2025", date(2025, 12, 31)),
    ("2025-06", date(2025, 6, 30)),
    ("2025-06-20", date(2025, 6, 20)),
])
def test_human_review_preserves_candidate_grain_and_existing_period_end_projection(
    session, document_service, period, expected_date,
):
    from app.models.ledger import SourceStatement

    candidate = _extract(session, document_service, period=period)[0]
    review = AtomicClaimService(session).review(
        candidate.id, outcome="confirmed", reviewer="human:test",
        reason="已核对逐字原文", idempotency_key="confirmed",
    )
    statement = session.get(SourceStatement, review.published_source_statement_id)
    assert statement.observed_period == expected_date
    assert candidate.structured_fields["observed_period"] == period
