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


def test_extraction_preserves_provider_material_classification() -> None:
    class MaterialClient:
        def chat_json(self, messages, schema_hint=""):
            assert schema_hint == "event_research_extract"
            return {
                "input_kind": "material",
                "research_question": "公告说明了什么？",
                "candidate_factors": ["收入", "利润", "订单"],
            }

    result = EventExtractionService(client=MaterialClient()).extract(
        raw_input="公司公告：收入和利润变化，订单增加。", source_url=None
    )

    assert result.input_kind == "material"


def test_extraction_defaults_unknown_input_kind_to_topic() -> None:
    class UnknownKindClient:
        def chat_json(self, messages, schema_hint=""):
            return {
                "input_kind": "guess",
                "research_question": "行业会如何变化？",
                "candidate_factors": ["需求", "供给", "替代"],
            }

    result = EventExtractionService(client=UnknownKindClient()).extract(
        raw_input="行业会如何变化？", source_url=None
    )

    assert result.input_kind == "topic"


def test_extraction_does_not_turn_a_topic_prompt_into_material_on_model_label() -> None:
    class MisclassifyingClient:
        def chat_json(self, messages, schema_hint=""):
            return {
                "input_kind": "material",
                "research_question": "英伟达供应链会如何变化？",
                "candidate_factors": ["需求", "供给", "替代"],
            }

    result = EventExtractionService(client=MisclassifyingClient()).extract(
        raw_input="研究英伟达供应链会如何变化？", source_url=None
    )

    assert result.input_kind == "topic"


@pytest.mark.parametrize(
    "raw_input",
    [
        "请分析公告：公司披露收入增长20%",
        "公司公告显示收入增长20%，意味着什么？",
        "Please analyze this announcement: Example Corp disclosed revenue increased 20%.",
        "The filing reports revenue increased 20%. What does it mean?",
        "According to the research report, orders increased 15% and revenue reached 300 USD.",
        "研报指出：公司订单增长15%，收入达到20亿元。",
        "Research report:\nRevenue increased 20%.\nOrders reached 300 USD.",
        "公司一季度收入增长20%。\n订单同比增加15%。\n毛利率达到30%。",
    ],
)
def test_extraction_prioritizes_strong_material_facts_over_prompt_shape(
    raw_input: str,
) -> None:
    class MaterialClient:
        def chat_json(self, messages, schema_hint=""):
            return {
                "input_kind": "material",
                "research_question": "材料中的变化意味着什么？",
                "candidate_factors": ["收入", "订单", "利润率"],
            }

    result = EventExtractionService(client=MaterialClient()).extract(
        raw_input=raw_input,
        source_url=None,
    )

    assert result.input_kind == "material"


@pytest.mark.parametrize(
    "raw_input",
    [
        "请研究AI服务器电力需求",
        "AI服务器电力需求会如何变化？",
        "研究公告行业",
        "Please research the announcement industry",
        "研究公司公告中收入增长20%对股价的影响",
        "2026年8月公司公告会如何影响股价？",
        "Research the impact of 20% revenue growth in the company announcement",
        "How will the 2026/08 company announcement affect the share price?",
        (
            "围绕AI服务器电力需求建立研究框架。先讨论需求增长，再分析供给约束；"
            "同时比较不同地区的电网建设节奏。还需要研究设备效率、能源成本和替代方案，"
            "并评估这些变量对行业竞争格局的长期影响。最后整理可验证的问题和候选因素，"
            "供后续自动检索公开资料使用。研究范围还包括需求弹性、供给周期、竞争壁垒、"
            "政策环境和技术路线；这些都只是待验证的问题，不是用户提供的事实材料。"
        ),
    ],
)
def test_extraction_keeps_short_research_prompts_as_topic(raw_input: str) -> None:
    class MisclassifyingClient:
        def chat_json(self, messages, schema_hint=""):
            return {
                "input_kind": "material",
                "research_question": "这个主题会如何变化？",
                "candidate_factors": ["需求", "供给", "替代"],
            }

    result = EventExtractionService(client=MisclassifyingClient()).extract(
        raw_input=raw_input,
        source_url=None,
    )

    assert result.input_kind == "topic"


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
