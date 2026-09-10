"""AI-assisted event framing with a strict non-fabrication boundary."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ai.client import LLMClient


_DEFAULT_FACTORS = (
    "经营或业绩变化是否足以解释市场反应",
    "现金流、资本开支或融资压力是否改变预期",
    "估值或市场环境是否放大了价格波动",
)


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
    material.  The question and factors are explicitly hypotheses, so they
    are allowed to be generated and must be confirmed or edited by a human.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        if self._client is None:
            try:
                self._client = LLMClient.from_env()
            except Exception:
                # A client can fail before its first request (for example, a
                # missing proxy transport).  The intake remains safe because
                # extraction below will produce only the source-bound title
                # plus editable hypothesis fields, never a factual assertion.
                self._client = None

    def extract(self, *, raw_input: str, source_url: str | None) -> EventExtraction:
        raw_input = raw_input.strip()
        result = self._ask_model(raw_input, source_url)
        title = _supported_text(result.get("event_title"), raw_input) or _fallback_title(raw_input)
        company_name = _supported_text(result.get("company_name"), raw_input)
        ticker = _supported_text(result.get("ticker"), raw_input)
        market_reaction = _supported_text(result.get("market_reaction"), raw_input)
        summary = _supported_text(result.get("summary"), raw_input)
        event_at = _supported_datetime(result.get("event_at"), raw_input)
        question = _question(result.get("research_question"), title)
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
        if self._client is None:
            return {}
        messages = [
            {
                "role": "system",
                "content": (
                    "你是事件研究助理。仅从用户给出的原文提取事件标题、公司、代码、"
                    "时间、市场反应和摘要；无法确认必须返回 null。研究问题和候选因素"
                    "是待验证假设，返回一个问题和 3 到 5 个因素。仅返回 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"raw_input": raw_input, "source_url": source_url}, ensure_ascii=False
                ),
            },
        ]
        try:
            result = self._client.chat_json(messages, schema_hint="event_research_extract")
        except Exception:
            # The creator can still proceed with a blank-safe draft when an
            # external provider is unavailable; the outage never becomes a
            # reason to invent facts.
            return {}
        return result if isinstance(result, dict) else {}


def _normalise(value: str) -> str:
    return "".join(value.lower().split())


def _supported_text(value: object, raw_input: str) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if _normalise(cleaned) in _normalise(raw_input) else None


def _supported_datetime(value: object, raw_input: str) -> datetime | None:
    if not isinstance(value, str) or _normalise(value) not in _normalise(raw_input):
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


def _question(value: object, title: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"“{title}”所反映的市场反应，主要由哪些可验证因素驱动？"


def _factors(value: object) -> tuple[str, ...]:
    candidates = value if isinstance(value, list) else []
    clean: list[str] = []
    for item in candidates:
        if not isinstance(item, str) or not item.strip():
            continue
        text = item.strip()
        if text not in clean:
            clean.append(text)
        if len(clean) == 5:
            break
    for fallback in _DEFAULT_FACTORS:
        if len(clean) >= 3:
            break
        if fallback not in clean:
            clean.append(fallback)
    return tuple(clean[:5])
