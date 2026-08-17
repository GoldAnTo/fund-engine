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
    input_kind: str = "topic"
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
        company_name = _supported_text(result.get("company_name"), raw_input)
        ticker = _supported_ticker(result.get("ticker"), raw_input)
        market_reaction = _supported_text(result.get("market_reaction"), raw_input)
        summary = _supported_text(result.get("summary"), raw_input)
        event_at = _supported_datetime(result.get("event_at"), raw_input)
        question = _question(result.get("research_question"))
        factors = _factors(result.get("candidate_factors"))
        input_kind = _input_kind(result.get("input_kind"), raw_input)
        return EventExtraction(
            event_title=title,
            company_name=company_name,
            ticker=ticker,
            event_at=event_at,
            market_reaction=market_reaction,
            summary=summary,
            research_question=question,
            candidate_factors=factors,
            input_kind=input_kind,
        )

    def _ask_model(self, raw_input: str, source_url: str | None) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是事件研究助理。仅从用户给出的原文提取事件标题、公司、代码、"
                    "时间、市场反应和摘要；无法确认必须返回 null。研究问题和候选因素"
                    "是待验证假设，返回一个问题和 3 到 5 个因素。判断输入是仅提出研究"
                    "主题（topic）还是包含可抽取事实的用户材料（material），并返回"
                    "input_kind。仅返回 JSON。"
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


_MATERIAL_MARKER_RE = re.compile(
    r"(?:公告|财报|研报|报告显示|披露|数据显示|宣布|通知|会议纪要|"
    r"announcement|filing|research report|earnings report|reported|report shows|"
    r"disclosed|data shows)",
    re.IGNORECASE,
)
_MATERIAL_FACT_RE = re.compile(
    r"(?:20\d{2}[-年/]\d{1,2}|\d+(?:\.\d+)?\s*(?:%|元|万元|亿元|usd|rmb))",
    re.IGNORECASE,
)
_TOPIC_PROMPT_RE = re.compile(
    r"^(?:请|帮我|研究|分析|调查|评估|看看|what\b|how\b|research\b|"
    r"analy[sz]e\b|please\s+(?:research|analy[sz]e)\b)",
    re.IGNORECASE,
)
_TOPIC_INTENT_RE = re.compile(
    r"(?:研究|分析|影响|如何|意味着什么|research|analy[sz]e|impact|affect|"
    r"how\b|what\s+does\b[^?？.。]{0,40}\bmean\b)",
    re.IGNORECASE,
)
_MATERIAL_DOCUMENT_ASSERTION_RE = re.compile(
    r"(?:(?:公告|财报|研报|报告)\s*(?:显示|指出|称|披露|报道|：|:)|"
    r"根据\s*(?:公司)?\s*(?:公告|财报|研报|报告)|"
    r"(?:announcement|filing|research report|earnings report)\s*"
    r"(?:reports?|states?|shows?|discloses?|says?|:)|"
    r"according\s+to\s+(?:the\s+)?"
    r"(?:announcement|filing|research report|earnings report))",
    re.IGNORECASE,
)


def _has_strong_material_signal(raw_input: str) -> bool:
    facts = _MATERIAL_FACT_RE.findall(raw_input)
    if facts and _MATERIAL_DOCUMENT_ASSERTION_RE.search(raw_input):
        return True
    nonempty_lines = [line for line in raw_input.splitlines() if line.strip()]
    return len(nonempty_lines) >= 2 and len(facts) >= 2


def _input_kind(value: object, raw_input: str) -> str:
    """Require deterministic material signals before granting evidence intent."""
    if value != "material":
        return "topic"
    normalized = raw_input.strip()
    if not normalized:
        return "topic"
    # Document assertions and pasted facts remain material even when the user
    # wraps them in an imperative or asks a short question about them.
    if _has_strong_material_signal(normalized):
        return "material"
    if (
        _TOPIC_PROMPT_RE.search(normalized)
        or _TOPIC_INTENT_RE.search(normalized)
        or normalized.endswith(("?", "？"))
    ):
        return "topic"
    if _MATERIAL_MARKER_RE.search(normalized) or _MATERIAL_FACT_RE.search(normalized):
        return "material"
    return "topic"
