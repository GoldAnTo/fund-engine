"""LLM client with OpenAI-compatible protocol and automatic mock mode.

When ``LLM_API_KEY`` is absent the client operates in *mock mode*: it returns
predefined structured JSON based on the prompt content, allowing tests and
offline runs to exercise the full AI-engine pipeline without a real API key.

Every call goes through ``chat_json`` which forces JSON output.  The
``model_version`` attribute records the model used (or ``mock-<model>`` in
mock mode) and is persisted on every ``AIRun`` audit record.
"""
from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_MODEL = "gpt-4o-mini"


class LLMClient:
    """Thin wrapper around the OpenAI SDK with a deterministic mock fallback."""

    def __init__(
        self,
        *,
        model_version: str,
        client: Any | None = None,
        mock: bool = False,
    ) -> None:
        self.model_version = model_version
        self._client = client
        self._mock = mock

    # ------------------------------------------------------------------ factory

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Build a client from ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``.

        Without ``LLM_API_KEY`` the client runs in mock mode so the engine
        never fails merely for lack of credentials.
        """
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL", DEFAULT_MODEL)

        if not api_key:
            return cls(model_version=f"mock-{model}", mock=True)

        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        return cls(model_version=model, client=client, mock=False)

    # ------------------------------------------------------------------ core

    def chat_json(self, messages: list[dict], schema_hint: str = "") -> dict:
        """Call the model and return parsed JSON.

        ``schema_hint`` is a short tag (e.g. ``"extract"``, ``"propose"``,
        ``"assess"``) that the mock uses to pick the right response shape.
        """
        if self._mock:
            return _mock_response(messages, schema_hint)

        assert self._client is not None  # noqa: S101
        response = self._client.chat.completions.create(
            model=self.model_version,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return json.loads(content)


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def _extract_user_data(messages: list[dict]) -> dict:
    """Parse the JSON payload embedded in the last user message."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return {}
    return {}


def _mock_response(messages: list[dict], schema_hint: str) -> dict:
    """Return a deterministic, structured response based on ``schema_hint``."""
    data = _extract_user_data(messages)

    if schema_hint == "extract":
        return _mock_extract(data)
    if schema_hint == "propose":
        return _mock_propose(data)
    if schema_hint == "assess":
        return _mock_assess(data)
    return {}


def _mock_extract(data: dict) -> dict:
    """Extract one atomic statement per span using keyword heuristics."""
    statements: list[dict] = []
    for span in data.get("spans", []):
        text = span.get("verbatim_text", "")
        if not text:
            continue
        kind = _guess_kind(text)
        statements.append(
            {
                "span_id": span.get("span_id", ""),
                "kind": kind,
                "normalized_text": text,
                "observed_period": None,
            }
        )
    return {"statements": statements}


def _mock_propose(data: dict) -> dict:
    """Propose one evidence link per statement using keyword heuristics."""
    links: list[dict] = []
    for stmt in data.get("statements", []):
        sid = stmt.get("id", "")
        text = stmt.get("text", "")
        role = _guess_role(text)
        links.append(
            {
                "source_statement_id": sid,
                "role": role,
                "reason": f"机器生成的证据关联判断：{text[:40]}",
                "scope": {"segment": "AI算力"},
            }
        )
    return {"links": links}


def _mock_assess(data: dict) -> dict:
    """Produce a three-valued conclusion from the visible evidence links."""
    links = data.get("links", [])
    supports = sum(1 for link in links if link.get("role") == "supports")
    contradicts = sum(1 for link in links if link.get("role") == "contradicts")

    if contradicts > supports:
        conclusion = "contradicted"
        rationale = (
            f"基于 {len(links)} 条证据的机器推理：反驳证据 ({contradicts}) "
            f"多于支持证据 ({supports})，命题与部分来源矛盾"
        )
        gaps = ["需要补充直接定量披露以确认反驳强度"]
    elif supports > 0 and contradicts == 0:
        conclusion = "supported"
        rationale = (
            f"基于 {len(links)} 条证据的机器推理：支持证据 ({supports}) "
            f"且无反驳证据"
        )
        gaps = []
    else:
        conclusion = "insufficient_evidence"
        rationale = (
            f"基于 {len(links)} 条证据的机器推理：支持证据 ({supports}) "
            f"与反驳证据 ({contradicts}) 并存或证据不足"
        )
        gaps = ["缺少更细颗粒度的分部数据", "需要补充直接传导证据"]

    return {
        "conclusion": conclusion,
        "rationale": rationale,
        "gaps": gaps,
    }


def _guess_kind(text: str) -> str:
    if any(k in text for k in ("预计", "预测", "有望", "将增长", "将超过")):
        return "forecast"
    if any(k in text for k in ("管理层", "表示", "认为", "指出")):
        return "management_attribution"
    if any(k in text for k in ("看好", "维持", "评级", "建议")):
        return "research_opinion"
    return "disclosed_fact"


def _guess_role(text: str) -> str:
    if any(k in text for k in ("风险", "谨慎", "透支", "审慎", "过于乐观", "回调")):
        return "contradicts"
    if any(k in text for k in ("分部", "口径", "背景", "占比较高", "尚需")):
        return "contextualizes"
    return "supports"
