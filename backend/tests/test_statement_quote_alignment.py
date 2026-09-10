"""LLM character counts are hints; only exact, unambiguous source slices anchor claims."""
import json
import uuid

import pytest
from sqlalchemy import select

from app.ai.extraction import StatementExtractor
from app.ai.prompts import EXTRACT_SYSTEM
from app.models.ledger import AIRun

FACT = "示例公司2024年营业收入为100亿元。"


def _extract(session, document_service, text, quote, start, end):
    document = document_service.freeze(
        raw=text.encode(), source_url=f"https://example.test/{uuid.uuid4()}",
        source_authority="user_supplied",
    )
    document_service.add_span(document_version_id=document.id,
                              locator={"page": 1}, verbatim_text=text)

    class Client:
        model_version = "provider-json-boundary"

        def chat_json(self, messages, schema_hint=""):
            span_id = json.loads(messages[-1]["content"])["spans"][0]["span_id"]
            return {"statements": [{
                "span_id": span_id, "kind": "reported_claim", "quote": quote,
                "quote_start": start, "quote_end": end, "normalized_text": FACT,
                "subject": "示例公司", "predicate": "营业收入",
                "numeric_value": "100", "unit": "亿元", "observed_period": "2024",
            }]}

    return StatementExtractor(Client()).extract(document.id, session)


@pytest.mark.parametrize("start,end", [(144, 187), (-1, 5), (0, 999), (True, False), (None, None)])
def test_exact_unique_quote_is_anchored_to_real_character_positions(
    session, document_service, start, end,
):
    prefix = "研究材料：\n"
    text = prefix + FACT
    candidates = _extract(session, document_service, text, FACT, start, end)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.quote_start, candidate.quote_end) == (len(prefix), len(text))
    assert text[candidate.quote_start:candidate.quote_end] == candidate.quote == FACT
    assert "1 exact quotes reanchored" in session.scalar(select(AIRun)).output_summary


def test_valid_offset_disambiguates_repeated_literal_quote(session, document_service):
    text = FACT + "\n" + FACT
    start = len(FACT) + 1
    candidate = _extract(session, document_service, text, FACT, start, len(text))[0]
    assert candidate.quote_start == start


@pytest.mark.parametrize("text,quote", [
    (FACT + "\n" + FACT, FACT),
    (FACT, FACT.replace("100", "101")),
    (FACT, ""),
    ("甲\n乙", "甲乙"),
    ("甲，乙", "甲,乙"),
])
def test_no_fuzzy_or_ambiguous_quote_repair(session, document_service, text, quote):
    assert _extract(session, document_service, text, quote, 900, 999) == []


def test_prompt_does_not_discard_literal_facts_for_uncertain_character_counts():
    assert "字符位置无法确定时返回 null" in EXTRACT_SYSTEM
    assert "系统会在同一 span 内做逐字精确定位" in EXTRACT_SYSTEM
