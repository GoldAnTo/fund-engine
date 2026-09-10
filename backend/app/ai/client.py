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
import re
from typing import Any

DEFAULT_MODEL = "gpt-4o-mini"

# Default temperature=0.0: every live call freezes sampling so the citation
# manifest + assessment conclusion are reproducible for the same input under
# the same model.  OpenAI's seed is a best-effort hint, not a guarantee
# (versions/regions may still drift), but combined with temperature=0 it
# closes the bulk of the variance.  See walkthrough defect 7.
DEFAULT_TEMPERATURE = 0.0


class LLMClient:
    """Thin wrapper around the OpenAI SDK with a deterministic mock fallback.

    Reproducibility contract (defect-7 fix, 2026-08-02):
    - ``temperature`` defaults to 0.0; pass a higher value only when you have
      a reason (eval harness sweep, exploratory research).
    - ``seed`` is forwarded to OpenAI's ``chat.completions.create`` as the
      ``seed`` field when set; unset (None) means "do not pin" — callers that
      need identical manifests across reruns should set this.
    - Mock mode is already deterministic by construction (keyword heuristics
      in ``_mock_response``); the same temperature/seed plumbing still
      exercises the code path so production config matches test config.
    """

    def __init__(
        self,
        *,
        model_version: str,
        client: Any | None = None,
        mock: bool = False,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = None,
    ) -> None:
        self.model_version = model_version
        self._client = client
        self._mock = mock
        self._temperature = temperature
        self._seed = seed

    # ------------------------------------------------------------------ factory

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Build a client from ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``.

        Reproducibility knobs read from env:
        - ``LLM_TEMPERATURE`` (default 0.0): passed straight to the OpenAI
          call.  Zero freezes sampling so reruns land on the same token.
        - ``LLM_SEED`` (default unset): forwarded to ``chat.completions.create``
          as ``seed``.  Empty/0 means "do not pin".

        Without ``LLM_API_KEY`` the client runs in mock mode for development
        and tests.  In production (``APP_ENV=production``) a missing key is a
        hard failure: silently falling back to mock would produce fabricated
        research output while appearing live (provider discipline borrowed
        from VCRA's ProviderFactory — real providers fail, never degrade).
        """
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
        temperature = float(os.getenv("LLM_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
        raw_seed = os.getenv("LLM_SEED", "").strip()
        seed: int | None = int(raw_seed) if raw_seed else None

        if not api_key:
            app_env = os.getenv("APP_ENV", "development").strip().lower()
            if app_env in {"production", "prod"}:
                raise RuntimeError(
                    "LLM_API_KEY is required when APP_ENV=production; "
                    "mock mode is restricted to development and tests"
                )
            return cls(
                model_version=f"mock-{model}",
                mock=True,
                temperature=temperature,
                seed=seed,
            )

        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        return cls(
            model_version=model,
            client=client,
            mock=False,
            temperature=temperature,
            seed=seed,
        )

    # ------------------------------------------------------------------ core

    def chat_json(self, messages: list[dict], schema_hint: str = "") -> dict:
        """Call the model and return parsed JSON.

        ``schema_hint`` is a short tag (e.g. ``"extract"``, ``"propose"``,
        ``"assess"``) that the mock uses to pick the right response shape.
        """
        if self._mock:
            return _mock_response(messages, schema_hint)

        assert self._client is not None  # noqa: S101
        create_kwargs: dict[str, Any] = {
            "model": self.model_version,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": self._temperature,
        }
        if self._seed is not None:
            create_kwargs["seed"] = self._seed
        response = self._client.chat.completions.create(**create_kwargs)
        content = response.choices[0].message.content
        # 推理模型（如 MiniMax-M3）可能在 JSON 前加 <think>...</think> 块
        if "</think>" in content:
            content = content.split("</think>", 1)[1].strip()
        # 提取首个 JSON 对象（兼容模型偶尔加 markdown 包裹或多余文本）
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            content = content[start : end + 1]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            from json_repair import loads as repair_loads

            return repair_loads(content)


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
    if schema_hint == "rewrite":
        return _mock_rewrite(data)
    return {}


def _mock_rewrite(data: dict) -> dict:
    """Deterministic offline compliance rewrite.

    Drops clauses that the compliance gate flags (in mock mode every hit
    reaching this stage is a REWRITE-category expression, since REFUSE
    categories never enter the rewrite loop).  A text reduced to nothing is
    replaced by a compliant placeholder.
    """
    from app.services.compliance import evaluate_compliance

    cleaned: list[str] = []
    for text in data.get("texts", []):
        clauses = [c for c in re.split(r"(?<=[。；;!?！？])", str(text)) if c.strip()]
        kept = [c for c in clauses if not evaluate_compliance(c).is_hit]
        cleaned.append("".join(kept) if kept else "该表述已按合规要求省略")
    return {"texts": cleaned}


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
