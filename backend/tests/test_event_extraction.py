from __future__ import annotations

import httpx
import pytest
from unittest.mock import MagicMock

from app.ai.client import LLMMalformedResponseError
from app.services.event_extraction import (
    EventExtractionProviderError,
    EventExtractionService,
)


PROVIDER_ERROR_MESSAGE = (
    "event extraction LLM is unavailable or returned an invalid response"
)


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


def test_extraction_wraps_llm_setup_failure_without_leaking_details(monkeypatch) -> None:
    def unavailable_client():
        raise RuntimeError("local proxy exposed secret sk-private")

    monkeypatch.setattr(
        "app.services.event_extraction.LLMClient.from_env",
        unavailable_client,
    )

    with pytest.raises(EventExtractionProviderError) as exc_info:
        EventExtractionService()

    assert str(exc_info.value) == PROVIDER_ERROR_MESSAGE
    assert "sk-private" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_extraction_wraps_chat_json_failure_without_leaking_details() -> None:
    class UnavailableClient:
        def chat_json(self, messages, schema_hint):
            raise httpx.ConnectError("provider request exposed secret sk-private")

    with pytest.raises(EventExtractionProviderError) as exc_info:
        EventExtractionService(client=UnavailableClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )

    assert str(exc_info.value) == PROVIDER_ERROR_MESSAGE
    assert "sk-private" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_extraction_maps_llm_protocol_error_to_fixed_provider_failure() -> None:
    class MalformedProtocolClient:
        def chat_json(self, messages, schema_hint):
            raise LLMMalformedResponseError(
                "LLM provider returned an invalid response"
            )

    with pytest.raises(EventExtractionProviderError) as exc_info:
        EventExtractionService(client=MalformedProtocolClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )

    assert str(exc_info.value) == PROVIDER_ERROR_MESSAGE
    assert isinstance(exc_info.value.__cause__, LLMMalformedResponseError)


def test_extraction_does_not_map_llm_client_programming_error_to_provider_failure() -> None:
    class BrokenCompletions:
        def create(self, **kwargs):
            raise TypeError("programming defect")

    sdk_client = MagicMock()
    sdk_client.chat.completions = BrokenCompletions()
    client = __import__("app.ai.client", fromlist=["LLMClient"]).LLMClient(
        model_version="provider-test", client=sdk_client
    )

    with pytest.raises(TypeError, match="programming defect"):
        EventExtractionService(client=client).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )


@pytest.mark.parametrize(
    "programming_error",
    [TypeError, KeyError, NameError, AssertionError, AttributeError],
)
def test_extraction_does_not_wrap_setup_programming_errors(
    monkeypatch, programming_error
) -> None:
    def broken_client_factory():
        raise programming_error("programming defect")

    monkeypatch.setattr(
        "app.services.event_extraction.LLMClient.from_env",
        broken_client_factory,
    )

    with pytest.raises(programming_error, match="programming defect"):
        EventExtractionService()


@pytest.mark.parametrize(
    "programming_error",
    [TypeError, KeyError, NameError, AssertionError, AttributeError],
)
def test_extraction_does_not_wrap_call_programming_errors(programming_error) -> None:
    class BrokenClient:
        def chat_json(self, messages, schema_hint):
            raise programming_error("programming defect")

    with pytest.raises(programming_error, match="programming defect"):
        EventExtractionService(client=BrokenClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )


def test_extraction_does_not_wrap_client_value_error() -> None:
    class BrokenClient:
        def chat_json(self, messages, schema_hint):
            raise ValueError("programming defect")

    with pytest.raises(ValueError, match="programming defect"):
        EventExtractionService(client=BrokenClient()).extract(
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

    with pytest.raises(EventExtractionProviderError) as exc_info:
        EventExtractionService(client=MalformedClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )

    assert str(exc_info.value) == PROVIDER_ERROR_MESSAGE
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert error_message in str(exc_info.value.__cause__)


@pytest.mark.parametrize(
    "candidate_factors",
    [
        ["因素一", "因素二"],
        ["因素一", "因素二", "因素三", "因素四", "因素五", "因素六"],
        ["因素一", "因素二", "因素三", 4],
        ["因素一", "因素二", "因素三", "   "],
        ["因素一", "因素 二", "因素二"],
        ["因素一", "因素二", "因素三", "因素四", "因素五", "因素 五"],
    ],
)
def test_extraction_rejects_any_invalid_raw_candidate_factor_list(
    candidate_factors,
) -> None:
    class InvalidFactorsClient:
        def chat_json(self, messages, schema_hint):
            return {
                "research_question": "哪些因素需要验证？",
                "candidate_factors": candidate_factors,
            }

    with pytest.raises(EventExtractionProviderError) as exc_info:
        EventExtractionService(client=InvalidFactorsClient()).extract(
            raw_input="公司披露新的经营数据，等待人工核验。",
            source_url=None,
        )

    assert str(exc_info.value) == PROVIDER_ERROR_MESSAGE


def test_extraction_rejects_ticker_and_date_that_only_match_inside_larger_tokens() -> None:
    class BoundaryViolatingClient:
        def chat_json(self, messages, schema_hint):
            return {
                "ticker": "A",
                "event_at": "2024-01-01",
                "research_question": "哪些因素需要验证？",
                "candidate_factors": ["因素一", "因素二", "因素三"],
            }

    result = EventExtractionService(client=BoundaryViolatingClient()).extract(
        raw_input="Company announced on 2024-01-010",
        source_url=None,
    )

    assert result.ticker is None
    assert result.event_at is None


def test_extraction_keeps_boundary_delimited_ticker_and_date() -> None:
    class SupportedClient:
        def chat_json(self, messages, schema_hint):
            return {
                "ticker": "A",
                "event_at": "2024-01-01",
                "research_question": "哪些因素需要验证？",
                "candidate_factors": ["因素一", "因素二", "因素三"],
            }

    result = EventExtractionService(client=SupportedClient()).extract(
        raw_input="Company (A) announced on 2024-01-01.",
        source_url=None,
    )

    assert result.ticker == "A"
    assert result.event_at is not None
