from __future__ import annotations

import pytest

from app.services.event_extraction import EventExtractionService


class ValidExtractionClient:
    def chat_json(self, messages, schema_hint):
        assert schema_hint == "event_research_extract"
        return {
            "research_question": "新指引是否改变了市场对公司前景的判断？",
            "candidate_factors": ["新指引", "盘后交易", "市场预期"],
        }


def test_extraction_keeps_unknown_event_facts_empty_and_marks_confirmation() -> None:
    result = EventExtractionService(client=ValidExtractionClient()).extract(
        raw_input="公司宣布新指引，盘后下跌。",
        source_url="https://example.com/brief",
    )

    assert result.company_name is None
    assert result.ticker is None
    assert result.event_at is None
    assert result.event_title == "公司宣布新指引，盘后下跌"
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


def test_extraction_propagates_llm_setup_failure(monkeypatch) -> None:
    def unavailable_client():
        raise RuntimeError("local proxy is unavailable")

    monkeypatch.setattr(
        "app.services.event_extraction.LLMClient.from_env",
        unavailable_client,
    )

    with pytest.raises(RuntimeError, match="local proxy is unavailable"):
        EventExtractionService()


def test_extraction_propagates_chat_json_failure() -> None:
    class UnavailableClient:
        def chat_json(self, messages, schema_hint):
            raise RuntimeError("provider request failed")

    with pytest.raises(RuntimeError, match="provider request failed"):
        EventExtractionService(client=UnavailableClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )


@pytest.mark.parametrize(
    ("provider_result", "error_message"),
    [
        ([], "JSON object"),
        (
            {
                "candidate_factors": ["因素一", "因素二", "因素三"],
            },
            "research_question",
        ),
        (
            {
                "research_question": "哪些因素需要验证？",
                "candidate_factors": ["因素一", "因素一", " ", 3, "因素二"],
            },
            "candidate_factors",
        ),
        (
            {
                "research_question": "哪些因素需要验证？",
                "candidate_factors": [
                    "因素一",
                    "因素二",
                    "因素三",
                    "因素四",
                    "因素五",
                    "因素六",
                ],
            },
            "candidate_factors",
        ),
    ],
)
def test_extraction_rejects_malformed_provider_results(
    provider_result, error_message
) -> None:
    class MalformedClient:
        def chat_json(self, messages, schema_hint):
            return provider_result

    with pytest.raises(ValueError, match=error_message):
        EventExtractionService(client=MalformedClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )
