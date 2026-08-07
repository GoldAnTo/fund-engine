from __future__ import annotations

from app.services.event_extraction import EventExtractionService


def test_extraction_keeps_unknown_event_facts_empty_and_marks_confirmation() -> None:
    result = EventExtractionService().extract(
        raw_input="公司宣布新指引，盘后下跌。",
        source_url="https://example.com/brief",
    )

    assert result.company_name is None
    assert result.ticker is None
    assert result.event_at is None
    assert result.confirmation_required is True
    assert result.research_question
    assert len(result.candidate_factors) in {3, 4, 5}


def test_extraction_drops_model_values_that_are_not_supported_by_the_raw_input() -> None:
    class InventingClient:
        def chat_json(self, messages, schema_hint):
            assert schema_hint == "event_research_extract"
            return {
                "event_title": "某公司股价下跌",
                "company_name": "不存在的公司",
                "ticker": "FAKE",
                "market_reaction": "下跌 10%",
                "research_question": "为什么下跌？",
                "candidate_factors": ["因素一", "因素二", "因素三"],
            }

    result = EventExtractionService(client=InventingClient()).extract(
        raw_input="公司宣布新指引，盘后下跌。",
        source_url=None,
    )

    assert result.company_name is None
    assert result.ticker is None
    assert result.market_reaction is None
    assert result.event_title is not None
    assert result.research_question == "为什么下跌？"
