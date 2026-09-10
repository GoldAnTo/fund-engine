"""AI-assisted event framing with a strict non-fabrication boundary."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn

import httpx
from openai import OpenAIError

from app.ai.client import LLMClient, LLMProviderError


EVENT_EXTRACTION_PROVIDER_ERROR_MESSAGE = (
    "event extraction LLM is unavailable or returned an invalid response"
)


class EventExtractionProviderError(Exception):
    """Raised for unavailable providers and invalid provider responses."""


@dataclass(frozen=True)
class EventExtraction:
    event_title: str | None
    company_name: str | None
    ticker: str | None
    event_at: datetime | None
    market_reaction: str | None
    summary: str | None
    research_question: str
    candidate_factors: tuple[str, ...]
    confirmation_required: bool = True


class EventExtractionService:
    """Extract a draft while refusing unsupported event facts.

    A language model may suggest a structured framing, but any populated fact
    is retained only when its text literally occurs in the user's supplied
    material. The question and factors must be valid provider output and must
    be confirmed or edited by a human.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client if client is not None else self._provider_client()

    @staticmethod
    def _provider_client() -> Any:
        try:
            return LLMClient.from_env()
        except (RuntimeError, ValueError, OpenAIError, httpx.HTTPError) as exc:
            raise EventExtractionProviderError(
                EVENT_EXTRACTION_PROVIDER_ERROR_MESSAGE
            ) from exc

    def extract(self, *, raw_input: str, source_url: str | None) -> EventExtraction:
        raw_input = raw_input.strip()
        result = self._ask_model(raw_input, source_url)
        title = _supported_text(result.get("event_title"), raw_input) or _fallback_title(raw_input)
        company_name = _supported_company_name(result.get("company_name"), raw_input)
        ticker = _supported_ticker(result.get("ticker"), raw_input)
        market_reaction = _supported_text(result.get("market_reaction"), raw_input)
        summary = _supported_text(result.get("summary"), raw_input)
        event_at = _supported_datetime(result.get("event_at"), raw_input)
        question = _question(result.get("research_question"))
        factors = _factors(result.get("candidate_factors"))
        return EventExtraction(
            event_title=title,
            company_name=company_name,
            ticker=ticker,
            event_at=event_at,
            market_reaction=market_reaction,
            summary=summary,
            research_question=question,
            candidate_factors=factors,
        )

    def _ask_model(self, raw_input: str, source_url: str | None) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是事件研究助理。仅从用户给出的原文提取事件标题、公司、代码、"
                    "时间、市场反应和摘要；无法确认必须返回 null。研究问题和候选因素"
                    "是待验证假设。仅返回一个 JSON 对象，字段必须为："
                    '{"event_title": null, "company_name": null, "ticker": null, '
                    '"event_at": null, "market_reaction": null, "summary": null, '
                    '"research_question": "string", '
                    '"candidate_factors": ["string", "string", "string"]}。'
                    "company_name 只能是原文中的简短主体名称（公司、机构或行业名），"
                    "不能返回事件句子、摘要或整段原文；"
                    "candidate_factors 必须包含 3 到 5 个互不重复的非空 JSON 字符串；"
                    "禁止返回对象、数字或 null。不要添加 JSON 之外的解释。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"raw_input": raw_input, "source_url": source_url}, ensure_ascii=False
                ),
            },
        ]
        return self._provider_result(messages)

    def _provider_result(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            result = self._client.chat_json(
                messages, schema_hint="event_research_extract"
            )
        except (
            LLMProviderError,
            OpenAIError,
            httpx.HTTPError,
            json.JSONDecodeError,
        ) as exc:
            raise EventExtractionProviderError(
                EVENT_EXTRACTION_PROVIDER_ERROR_MESSAGE
            ) from exc
        if not isinstance(result, dict):
            _invalid_provider_response(
                "event extraction response must be a JSON object"
            )
        return result


def _normalise(value: str) -> str:
    return "".join(value.lower().split())


def _supported_text(value: object, raw_input: str) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if _normalise(cleaned) in _normalise(raw_input) else None


def _supported_company_name(value: object, raw_input: str) -> str | None:
    supported = _supported_text(value, raw_input)
    if supported is None:
        return None
    if (
        len(supported) > 80
        or "\n" in supported
        or "\r" in supported
        or len(supported.split()) > 8
        or any(mark in supported for mark in "。！？；")
    ):
        return None
    return supported


def _supported_ticker(value: object, raw_input: str) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    candidate = _normalise(cleaned)
    source = _normalise(raw_input)
    pattern = rf"(?<![\w.-]){re.escape(candidate)}(?![\w.-])"
    return cleaned if re.search(pattern, source) else None


def _supported_datetime(value: object, raw_input: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    candidate = _normalise(value)
    source = _normalise(raw_input)
    if not re.search(rf"(?<!\d){re.escape(candidate)}(?!\d)", source):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fallback_title(raw_input: str) -> str:
    first_sentence = next(
        (part.strip() for part in raw_input.replace("！", "。").replace("!", ".").split("。") if part.strip()),
        raw_input,
    )
    return first_sentence[:120]


def _invalid_provider_response(detail: str) -> NoReturn:
    exc = ValueError(detail)
    raise EventExtractionProviderError(
        EVENT_EXTRACTION_PROVIDER_ERROR_MESSAGE
    ) from exc


def _question(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    _invalid_provider_response(
        "event extraction response requires a non-empty research_question"
    )


def _factors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 3 <= len(value) <= 5:
        _invalid_provider_response(
            "event extraction response requires 3 to 5 unique candidate_factors"
        )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _invalid_provider_response(
            "event extraction candidate_factors must be non-empty strings"
        )
    clean = [item.strip() for item in value]
    if len({_normalise(item) for item in clean}) != len(clean):
        _invalid_provider_response(
            "event extraction candidate_factors must be unique after normalization"
        )
    return tuple(clean)
