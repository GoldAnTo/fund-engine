"""Tests for the AI research engine (extract / propose / assess).

All tests run with a mock LLMClient (no real API key required).  The mock
returns deterministic structured JSON based on the prompt content, so the
full pipeline can be exercised offline.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import (
    LLMClient,
    LLMJSONResponse,
    LLMMalformedResponseError,
    LLMProviderError,
)
from app.ai.extraction import StatementExtractor, _resolve_verbatim_quote_offsets
from app.ai.prompts import (
    ASSESS_PROMPT_VERSION,
    EXTRACT_JSON_RETRY_SYSTEM,
    EXTRACT_JSON_RETRY_VERSION,
    EXTRACT_PROMPT_VERSION,
    EXTRACT_SYSTEM,
    MAX_EXTRACT_NORMALIZED_TEXT_CHARACTERS,
    MAX_EXTRACT_QUOTE_CHARACTERS,
    MAX_EXTRACT_RETRY_STATEMENTS,
    MAX_EXTRACT_STATEMENTS_PER_RESPONSE,
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
from app.repositories.research import ResearchRepository
from app.scripts.run_ai_engine import run_engine
from app.services.research_protocol import ResearchabilityResult
from tests.protocol_provenance import seed_protocol_footprint


class _ProviderCompletions:
    def __init__(self, content: str, *failures: Exception) -> None:
        self._content = content
        self._failures = list(failures)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._content),
                    finish_reason="stop",
                )
            ]
        )


def _provider_client(completions: _ProviderCompletions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))

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
    assert "candidate extraction completed" in run.output_summary
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


def test_extract_keeps_valid_candidate_and_audits_rejected_sibling(session, span):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid audit-secret normalized text",
        "kind": "reported_claim",
    }
    invalid = {
        **valid,
        "quote": "rejected audit-secret quote",
        "normalized_text": "rejected audit-secret normalized text",
    }

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [valid, invalid]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert len(candidates) == 1
    assert run is not None
    assert run.output_summary == (
        "candidate extraction completed; unique candidates returned 1; "
        "rule-based accepted items 0; "
        "llm returned 2, accepted 1, rejected 1, offsets repaired 0, "
        "llm attempts 1, malformed retries 0; "
        "source spans 1"
    )
    assert "audit-secret" not in run.output_summary


def test_extract_prompt_bounds_output_and_defines_offsets() -> None:
    assert EXTRACT_PROMPT_VERSION == "extract-v5"
    assert EXTRACT_JSON_RETRY_VERSION == "extract-json-retry-v2"
    assert EXTRACT_JSON_RETRY_VERSION in EXTRACT_JSON_RETRY_SYSTEM
    assert "不要续写或修补上一响应" in EXTRACT_JSON_RETRY_SYSTEM
    assert "每个 span 最多返回 5 条" in EXTRACT_SYSTEM
    assert MAX_EXTRACT_STATEMENTS_PER_RESPONSE == 20
    assert "不得超过用户消息中的 max_statements" in EXTRACT_SYSTEM
    assert MAX_EXTRACT_QUOTE_CHARACTERS == 120
    assert "quote 不超过 120 个 Unicode 字符" in EXTRACT_SYSTEM
    assert MAX_EXTRACT_NORMALIZED_TEXT_CHARACTERS == 80
    assert "normalized_text 不超过 80 个 Unicode 字符" in EXTRACT_SYSTEM
    assert "statements 数组最多 3 条" in EXTRACT_JSON_RETRY_SYSTEM
    assert "不要输出分析、解释或思考过程" in EXTRACT_JSON_RETRY_SYSTEM
    assert "从 0 起" in EXTRACT_SYSTEM
    assert "右开" in EXTRACT_SYSTEM
    assert "不可信来源数据" in EXTRACT_SYSTEM


def test_extract_sends_dynamic_statement_limit_for_one_span(session, span) -> None:
    client = LLMClient(model_version="provider-test", mock=True)

    def capture_limit(messages, **_kwargs):
        user_data = json.loads(messages[-1]["content"])
        assert user_data["max_statements"] == 5
        return {"statements": []}

    with patch.object(client, "chat_json", side_effect=capture_limit):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert candidates == []


def test_extract_locally_enforces_compact_retry_cap(
    session, span, document_service
) -> None:
    items = []
    for index in range(MAX_EXTRACT_RETRY_STATEMENTS + 1):
        text = f"retry bounded statement {index}"
        bounded_span = document_service.add_span(
            document_version_id=span.document_version_id,
            locator={"page": index + 2, "paragraph": 1},
            verbatim_text=text,
        )
        items.append(
            {
                "span_id": str(bounded_span.id),
                "quote": text,
                "quote_start": 0,
                "quote_end": len(text),
                "normalized_text": text,
                "kind": "reported_claim",
            }
        )
    result = LLMJSONResponse(
        {"statements": items},
        attempt_count=2,
        malformed_retry_count=1,
    )
    client = LLMClient(model_version="provider-test", mock=True)

    with patch.object(client, "chat_json", return_value=result):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert len(candidates) == MAX_EXTRACT_RETRY_STATEMENTS
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert "llm returned 4, accepted 3, rejected 1" in run.output_summary
    assert "llm attempts 2, malformed retries 1" in run.output_summary


@pytest.mark.parametrize(
    ("oversized_field", "limit"),
    [
        ("quote", MAX_EXTRACT_QUOTE_CHARACTERS),
        ("normalized_text", MAX_EXTRACT_NORMALIZED_TEXT_CHARACTERS),
    ],
)
def test_extract_rejects_items_over_compact_field_limits(
    session, span, document_service, oversized_field, limit
):
    text = "x" * (limit + 1)
    oversized_span = document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 2, "paragraph": 1},
        verbatim_text=text,
    )
    model_item = {
        "span_id": str(oversized_span.id),
        "quote": text,
        "quote_start": 0,
        "quote_end": len(text),
        "normalized_text": "bounded",
        "kind": "reported_claim",
    }
    model_item[oversized_field] = text
    client = LLMClient(model_version="provider-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [model_item]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert candidates == []
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert "llm returned 1, accepted 0, rejected 1" in run.output_summary


def test_extract_repairs_wrong_offsets_for_a_unique_verbatim_quote(
    session,
    span,
    document_service,
):
    unicode_span = document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="前缀寒武纪收入增长",
    )
    client = LLMClient(model_version="provider-test", mock=True)
    unique_quote = "寒武纪"
    model_item = {
        "span_id": str(unicode_span.id),
        "quote": unique_quote,
        "quote_start": 0,
        "quote_end": len(unique_quote),
        "normalized_text": "unique quote with repaired offsets",
        "kind": "reported_claim",
    }

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [model_item]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert len(candidates) == 1
    expected_start = unicode_span.verbatim_text.index(unique_quote)
    assert candidates[0].quote == unique_quote
    assert candidates[0].quote_start == expected_start
    assert candidates[0].quote_end == expected_start + len(unique_quote)
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert (
        "llm returned 1, accepted 1, rejected 0, offsets repaired 1"
        in run.output_summary
    )


def test_extract_rejects_wrong_offsets_for_an_ambiguous_quote(
    session,
    span,
    document_service,
):
    repeated_span = document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="repeat and repeat",
    )
    client = LLMClient(model_version="provider-test", mock=True)
    model_item = {
        "span_id": str(repeated_span.id),
        "quote": "repeat",
        "quote_start": 1,
        "quote_end": 7,
        "normalized_text": "ambiguous quote must stay rejected",
        "kind": "reported_claim",
    }

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [model_item]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert candidates == []
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert "llm returned 1, accepted 0, rejected 1" in run.output_summary


@pytest.mark.parametrize("quote", ["", " ", "\n", "\t"])
def test_quote_offset_resolution_rejects_blank_only_quote(quote):
    source_text = f"x{quote}y"
    assert _resolve_verbatim_quote_offsets(
        source_text=source_text,
        quote=quote,
        quote_start=1,
        quote_end=1 + len(quote),
    ) is None


def test_quote_offset_resolution_keeps_exact_overlapping_match_but_will_not_guess():
    assert _resolve_verbatim_quote_offsets(
        source_text="aaaa",
        quote="aa",
        quote_start=1,
        quote_end=3,
    ) == (1, 3)
    assert _resolve_verbatim_quote_offsets(
        source_text="aaaa",
        quote="aa",
        quote_start=0,
        quote_end=1,
    ) is None


def test_extract_enforces_per_span_cap_without_spending_another_spans_budget(
    session,
    span,
    document_service,
):
    bounded_span = document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 1, "paragraph": 1},
        verbatim_text="alpha beta gamma delta epsilon zeta",
    )
    items = []
    for word in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
        start = bounded_span.verbatim_text.index(word)
        items.append(
            {
                "span_id": str(bounded_span.id),
                "quote": word,
                "quote_start": start,
                "quote_end": start + len(word),
                "normalized_text": f"claim {word}",
                "kind": "reported_claim",
            }
        )
    items.append(
        {
            "span_id": str(span.id),
            "quote": span.verbatim_text,
            "quote_start": 0,
            "quote_end": len(span.verbatim_text),
            "normalized_text": "independent span budget",
            "kind": "reported_claim",
        }
    )
    client = LLMClient(model_version="provider-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": items},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert len(candidates) == 6
    assert {candidate.normalized_text for candidate in candidates} == {
        "claim alpha",
        "claim beta",
        "claim gamma",
        "claim delta",
        "claim epsilon",
        "independent span budget",
    }
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert (
        "llm returned 7, accepted 6, rejected 1, offsets repaired 0"
        in run.output_summary
    )


def test_extract_enforces_total_response_cap(session, span, document_service):
    items = []
    for index in range(21):
        text = f"bounded statement {index}"
        bounded_span = document_service.add_span(
            document_version_id=span.document_version_id,
            locator={"page": index + 2, "paragraph": 1},
            verbatim_text=text,
        )
        items.append(
            {
                "span_id": str(bounded_span.id),
                "quote": text,
                "quote_start": 0,
                "quote_end": len(text),
                "normalized_text": text,
                "kind": "reported_claim",
            }
        )
    client = LLMClient(model_version="provider-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": items},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert len(candidates) == 20
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert (
        "llm returned 21, accepted 20, rejected 1, offsets repaired 0"
        in run.output_summary
    )


def test_extract_enforces_dynamic_response_cap_before_per_span_validation(
    session, span, document_service
):
    second_span = document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 2, "paragraph": 1},
        verbatim_text="alpha beta gamma delta epsilon zeta",
    )

    first_items = [
        {
            "span_id": str(span.id),
            "quote": span.verbatim_text,
            "quote_start": 0,
            "quote_end": len(span.verbatim_text),
            "normalized_text": f"first span claim {index}",
            "kind": "reported_claim",
        }
        for index in range(6)
    ]
    second_items = []
    for word in ("alpha", "beta", "gamma", "delta", "epsilon"):
        start = second_span.verbatim_text.index(word)
        second_items.append(
            {
                "span_id": str(second_span.id),
                "quote": word,
                "quote_start": start,
                "quote_end": start + len(word),
                "normalized_text": f"second span claim {word}",
                "kind": "reported_claim",
            }
        )
    client = LLMClient(model_version="provider-test", mock=True)

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [*first_items, *second_items]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    assert len(candidates) == 9
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert "llm returned 11, accepted 9, rejected 2" in run.output_summary


def test_extract_counts_non_object_item_as_rejected_and_keeps_valid_sibling(
    session, span
):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid sibling",
        "kind": "reported_claim",
    }

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": ["rejected audit-secret item", valid]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert len(candidates) == 1
    assert run is not None
    assert "llm returned 2, accepted 1, rejected 1" in run.output_summary
    assert "audit-secret" not in run.output_summary


def test_extract_reconciles_every_invalid_model_item_without_losing_valid_sibling(
    session, span
):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid sibling",
        "kind": "reported_claim",
    }
    missing_kind = dict(valid)
    missing_kind.pop("kind")
    invalid_items = [
        {**valid, "span_id": ["unhashable audit-secret span"]},
        {**valid, "span_id": str(uuid.uuid4())},
        {**valid, "normalized_text": 123},
        {**valid, "normalized_text": "   "},
        missing_kind,
        {**valid, "kind": ["reported_claim"]},
        {**valid, "kind": "unsupported_claim_type"},
        {
            **valid,
            "quote": span.verbatim_text[1:],
            "quote_start": True,
        },
        {**valid, "observed_period": 2025},
    ]
    # Keep the valid sibling inside the one-span response budget. Items beyond
    # that budget are still reconciled as rejected overflow.
    returned_items = [valid, *invalid_items]

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": returned_items},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert len(candidates) == 1
    assert run is not None
    assert (
        f"llm returned {len(returned_items)}, accepted 1, "
        f"rejected {len(invalid_items)}"
    ) in run.output_summary
    assert "audit-secret" not in run.output_summary


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("assertion_actor", ["audit-secret"]),
        ("subject", {"audit-secret": "subject"}),
        ("predicate", 123),
        ("object_text", ["audit-secret"]),
        ("numeric_value", 123),
        ("unit", ["audit-secret"]),
        ("scope", ["audit-secret"]),
        ("scope", []),
        ("scope", ""),
        ("scope", 0),
        ("scope", False),
        ("scope", {"segment": ["audit-secret"]}),
    ],
)
def test_extract_rejects_invalid_optional_field_shape_and_keeps_valid_sibling(
    session, span, field, invalid_value
):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid sibling",
        "kind": "reported_claim",
    }
    invalid = {**valid, field: invalid_value}

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [invalid, valid]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert len(candidates) == 1
    assert run is not None
    assert "llm returned 2, accepted 1, rejected 1" in run.output_summary
    assert "audit-secret" not in run.output_summary


@pytest.mark.parametrize(
    "provider_result",
    [
        None,
        [],
        {},
        {"statements": None},
        {"statements": "malformed audit-secret statements"},
        {"statements": {"audit-secret": "item"}},
    ],
)
def test_extract_requires_top_level_statements_list(session, span, provider_result):
    client = LLMClient(model_version="provider-test", mock=True)

    with (
        patch.object(client, "chat_json", return_value=provider_result),
        pytest.raises(LLMMalformedResponseError),
    ):
        StatementExtractor(client).extract(span.document_version_id, session)

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert run.status == "failed"
    assert run.output_summary == (
        "llm attempts 1; failure category malformed_response"
    )
    assert run.error == "AI operation failed"
    assert "audit-secret" not in run.error
    assert list(session.scalars(select(AtomicClaimCandidate))) == []


def test_extract_does_not_misclassify_pre_commit_guard_failure_as_rejection(
    session, span
):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid sibling",
        "kind": "reported_claim",
    }
    guard_calls = 0

    def fail_candidate_write_guard(_session):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise ValidationError("stale output slot audit-secret")

    with (
        patch.object(client, "chat_json", return_value={"statements": [valid]}),
        pytest.raises(ValidationError, match="stale output slot"),
    ):
        StatementExtractor(client).extract(
            span.document_version_id,
            session,
            pre_commit_guard=fail_candidate_write_guard,
        )

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert run.status == "failed"
    assert run.error == "AI operation failed"
    assert "audit-secret" not in run.error
    assert list(session.scalars(select(AtomicClaimCandidate))) == []


def test_extract_does_not_misclassify_database_failure_as_model_rejection(
    session, span
):
    from sqlalchemy.exc import OperationalError

    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid sibling",
        "kind": "reported_claim",
    }
    database_error = OperationalError(
        "INSERT audit-secret",
        {},
        RuntimeError("database audit-secret"),
    )

    with (
        patch.object(client, "chat_json", return_value={"statements": [valid]}),
        patch(
            "app.ai.extraction.AtomicClaimService.admit",
            side_effect=database_error,
        ),
        pytest.raises(OperationalError),
    ):
        StatementExtractor(client).extract(span.document_version_id, session)

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert run.status == "failed"
    assert run.output_summary == "llm attempts 0; failure category unknown"
    assert run.error == "AI operation failed"
    assert "audit-secret" not in run.error
    assert list(session.scalars(select(AtomicClaimCandidate))) == []


def test_extract_counts_deduplicated_admission_as_accepted_model_item(session, span):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "stable candidate",
        "kind": "reported_claim",
    }

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [valid]},
    ):
        first = StatementExtractor(client).extract(span.document_version_id, session)
        second = StatementExtractor(client).extract(span.document_version_id, session)

    runs = list(
        session.scalars(
            select(AIRun)
            .where(AIRun.kind == "extract")
            .order_by(AIRun.started_at)
        )
    )
    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert session.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == 1
    assert len(runs) == 2
    assert runs[-1].output_summary == (
        "candidate extraction completed; unique candidates returned 1; "
        "rule-based accepted items 0; "
        "llm returned 1, accepted 1, rejected 0, offsets repaired 0, "
        "llm attempts 1, malformed retries 0; "
        "source spans 1"
    )
    assert "extracted" not in runs[-1].output_summary


def test_extract_counts_duplicate_model_items_as_accepted_but_returns_unique(
    session, span
):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "stable duplicate candidate",
        "kind": "reported_claim",
        "scope": None,
    }

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [valid, dict(valid)]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert len(candidates) == 1
    assert len({candidate.id for candidate in candidates}) == 1
    assert session.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == 1
    assert run is not None
    assert run.output_summary == (
        "candidate extraction completed; unique candidates returned 1; "
        "rule-based accepted items 0; "
        "llm returned 2, accepted 2, rejected 0, offsets repaired 0, "
        "llm attempts 1, malformed retries 0; "
        "source spans 1"
    )
    assert "extracted" not in run.output_summary


def test_extract_reports_rule_based_candidates_separately_from_llm_admission(
    session, span, document_service
):
    table_span = document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 2},
        verbatim_text=(
            "主要会计数据 单位：千元\n"
            "指标 2025年 2024年\n"
            "营业收入 50,000,000 40,000,000\n"
        ),
    )
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid narrative candidate",
        "kind": "reported_claim",
    }
    invalid = {**valid, "quote": "not in the narrative span"}

    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [valid, invalid]},
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    rule_count = sum(
        candidate.source_span_id == table_span.id for candidate in candidates
    )
    llm_count = sum(candidate.source_span_id == span.id for candidate in candidates)
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert rule_count > 0
    assert llm_count == 1
    assert run is not None
    assert run.output_summary == (
        f"candidate extraction completed; unique candidates returned "
        f"{len(candidates)}; rule-based accepted items {rule_count}; "
        "llm returned 2, accepted 1, rejected 1, offsets repaired 0, "
        "llm attempts 1, malformed retries 0; "
        "source spans 2"
    )
    assert len(candidates) == rule_count + llm_count


def test_extract_returns_candidate_once_when_rule_and_llm_admissions_overlap(
    session, span, document_service
):
    client = LLMClient(model_version="provider-test", mock=True)
    valid = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "overlapping candidate",
        "kind": "reported_claim",
    }
    with patch.object(
        client,
        "chat_json",
        return_value={"statements": [valid]},
    ):
        existing = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )[0]
    document_service.add_span(
        document_version_id=span.document_version_id,
        locator={"page": 2},
        verbatim_text=(
            "主要会计数据 单位：千元\n"
            "指标 2025年 2024年\n"
            "营业收入 50,000,000 40,000,000\n"
        ),
    )

    with (
        patch.object(
            client,
            "chat_json",
            return_value={"statements": [valid]},
        ),
        patch(
            "app.ai.extraction.AtomicClaimService.admit",
            return_value=existing,
        ) as admit,
    ):
        candidates = StatementExtractor(client).extract(
            span.document_version_id,
            session,
        )

    latest_run = session.scalar(
        select(AIRun)
        .where(AIRun.kind == "extract")
        .order_by(AIRun.started_at.desc())
        .limit(1)
    )
    assert admit.call_count >= 2
    assert [candidate.id for candidate in candidates] == [existing.id]
    assert latest_run is not None
    rule_accepted_count = admit.call_count - 1
    assert latest_run.output_summary == (
        "candidate extraction completed; unique candidates returned 1; "
        f"rule-based accepted items {rule_accepted_count}; "
        "llm returned 1, accepted 1, rejected 0, offsets repaired 0, "
        "llm attempts 1, malformed retries 0; "
        "source spans 2"
    )
    assert "extracted" not in latest_run.output_summary


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
    assert runs[0].output_summary == (
        "skipped: no source spans attached to this version; "
        "llm returned 0, accepted 0, rejected 0"
    )


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


def test_extraction_persists_safe_timeout_attempt_diagnostics(session, span):
    completions = _ProviderCompletions(
        "unused sentinel-secret payload",
        httpx.ReadTimeout("sentinel-secret timeout one"),
        httpx.ReadTimeout("sentinel-secret timeout two"),
    )
    client = LLMClient(
        model_version="provider-test",
        client=_provider_client(completions),
        max_attempts=2,
        sleep=lambda _: None,
    )

    with pytest.raises(LLMProviderError) as exc_info:
        StatementExtractor(client).extract(span.document_version_id, session)

    assert str(exc_info.value) == "LLM provider request failed"
    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].output_summary == (
        "llm attempts 2; failure category timeout"
    )
    assert runs[0].error == "AI operation failed"
    assert "sentinel-secret" not in runs[0].output_summary
    assert completions.calls == 2


def test_extraction_exhausts_one_shared_budget_for_transport_and_malformed(
    session, span
):
    completions = _ProviderCompletions(
        json.dumps({"statements": {"sentinel-secret": "not-a-list"}}),
        httpx.ReadTimeout("sentinel-secret transient timeout"),
    )
    sleep_calls: list[float] = []
    client = LLMClient(
        model_version="provider-test",
        client=_provider_client(completions),
        max_attempts=3,
        sleep=sleep_calls.append,
    )

    with pytest.raises(LLMMalformedResponseError) as exc_info:
        StatementExtractor(client).extract(span.document_version_id, session)

    assert exc_info.value.attempt_count == 3
    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].output_summary == (
        "llm attempts 3; failure category malformed_response"
    )
    assert runs[0].error == "AI operation failed"
    assert "sentinel-secret" not in runs[0].output_summary
    assert completions.calls == 3
    assert len(sleep_calls) == 2


def test_extraction_persists_safe_output_limit_category(session, span):
    class AlwaysTruncatedCompletions:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"statements":["sentinel-secret"'
                        ),
                        finish_reason="length",
                    )
                ]
            )

    completions = AlwaysTruncatedCompletions()
    client = LLMClient(
        model_version="provider-test",
        client=_provider_client(completions),
        max_attempts=2,
        sleep=lambda _: None,
    )

    with pytest.raises(LLMMalformedResponseError) as exc_info:
        StatementExtractor(client).extract(span.document_version_id, session)

    assert exc_info.value.failure_category == "output_limit"
    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert run is not None
    assert run.status == "failed"
    assert run.output_summary == "llm attempts 2; failure category output_limit"
    assert run.error == "AI operation failed"
    assert "sentinel-secret" not in run.output_summary
    assert completions.calls == 2


def test_extraction_successful_retry_records_only_one_success_airun(session, span):
    statement = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid retried provider statement",
        "kind": "reported_claim",
    }
    completions = _ProviderCompletions(
        json.dumps({"statements": [statement]}),
        httpx.ReadTimeout("sentinel-secret transient timeout"),
    )
    client = LLMClient(
        model_version="provider-test",
        client=_provider_client(completions),
        max_attempts=2,
        sleep=lambda _: None,
    )

    candidates = StatementExtractor(client).extract(span.document_version_id, session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(candidates) == 1
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert completions.calls == 2
    assert "llm attempts 2, malformed retries 0" in runs[0].output_summary


def test_extraction_malformed_retry_records_one_success_airun(session, span):
    statement = {
        "span_id": str(span.id),
        "quote": span.verbatim_text,
        "quote_start": 0,
        "quote_end": len(span.verbatim_text),
        "normalized_text": "valid corrected provider statement",
        "kind": "reported_claim",
    }

    class MalformedThenValidCompletions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            content = (
                '{"sentinel-secret":'
                if len(self.calls) == 1
                else json.dumps({"statements": [statement]})
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason="stop",
                    )
                ]
            )

    completions = MalformedThenValidCompletions()
    client = LLMClient(
        model_version="provider-test",
        client=_provider_client(completions),
        max_attempts=2,
        sleep=lambda _: None,
    )

    candidates = StatementExtractor(client).extract(span.document_version_id, session)

    runs = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert len(candidates) == 1
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert "llm attempts 2, malformed retries 1" in runs[0].output_summary
    assert "sentinel-secret" not in runs[0].output_summary
    assert len(completions.calls) == 2
    assert completions.calls[1]["messages"][0] == {
        "role": "system",
        "content": EXTRACT_SYSTEM + "\n\n" + EXTRACT_JSON_RETRY_SYSTEM,
    }
    assert len(completions.calls[1]["messages"]) == 2


def test_extraction_rejects_invalid_item_without_provider_retry(session, span):
    invalid_statement = {
        "span_id": str(span.id),
        "quote": "invented quote",
        "quote_start": 0,
        "quote_end": len("invented quote"),
        "normalized_text": "invalid provider statement",
        "kind": "reported_claim",
    }
    completions = _ProviderCompletions(
        json.dumps({"statements": [invalid_statement]})
    )
    client = LLMClient(
        model_version="provider-test",
        client=_provider_client(completions),
        max_attempts=3,
        sleep=lambda _: pytest.fail("item rejection must not retry"),
    )

    candidates = StatementExtractor(client).extract(span.document_version_id, session)

    run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    assert candidates == []
    assert completions.calls == 1
    assert run is not None and run.status == "success"
    assert "llm returned 1, accepted 0, rejected 1" in run.output_summary
    assert "llm attempts 1, malformed retries 0" in run.output_summary


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
    assert run.output_summary == "llm attempts 0; failure category unknown"
    assert run.error == "AI operation failed"
    assert "sentinel-secret" not in run.output_summary
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
