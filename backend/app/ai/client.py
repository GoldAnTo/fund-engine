"""LLM client with an OpenAI-compatible protocol and test-only mock mode.

Live runtimes require ``LLM_API_KEY`` and fail closed when it is absent. Only
``APP_ENV=test`` may select deterministic mock responses, allowing tests to
exercise the full AI-engine pipeline without contacting an external service.

Every call goes through ``chat_json`` which forces JSON output.  The
``model_version`` attribute records the model used (or ``mock-<model>`` in
mock mode) and is persisted on every ``AIRun`` audit record.
"""
from __future__ import annotations

import json
import math
import os
import re
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, OpenAIError
from app.ai.usage import record_attempt

DEFAULT_MODEL = "gpt-4o-mini"

# Default temperature=0.0: every live call freezes sampling so the citation
# manifest + assessment conclusion are reproducible for the same input under
# the same model.  OpenAI's seed is a best-effort hint, not a guarantee
# (versions/regions may still drift), but combined with temperature=0 it
# closes the bulk of the variance.  See walkthrough defect 7.
DEFAULT_TEMPERATURE = 0.0
# Socket inactivity timeout, not cancellation of an entire synchronous call.
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_BUDGET_SECONDS = 180.0
DEFAULT_MAX_INPUT_BYTES = 1_048_576
DEFAULT_MAX_RESPONSE_BYTES = 2_097_152
DEFAULT_MAX_COMPLETION_TOKENS = 16_384
LLM_PROVIDER_ERROR_MESSAGE = "LLM provider request failed"
LLM_MALFORMED_RESPONSE_MESSAGE = "LLM provider returned an invalid response"
_operation_deadline: ContextVar[float | None] = ContextVar("llm_operation_deadline", default=None)


@contextmanager
def operation_budget(client):
    """Share a cooperative deadline across provider calls and compliance rewrite.

    Context-local state survives graph context propagation without placing a
    mutable deadline on a potentially shared client. Nested scopes cannot
    extend the caller's budget. This does not cancel synchronous I/O.
    """
    budget = getattr(client, "_retry_budget_seconds", DEFAULT_RETRY_BUDGET_SECONDS)
    deadline = time.monotonic() + budget
    parent = _operation_deadline.get()
    token = _operation_deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _operation_deadline.reset(token)


class LLMProviderError(RuntimeError):
    """Stable public boundary for live LLM request failures."""


class LLMMalformedResponseError(LLMProviderError):
    """Raised when the provider response does not match the JSON protocol."""


def _retry_delay(exc: Exception, attempt: int) -> float | None:
    response = getattr(exc, "response", None)
    if isinstance(exc, (APIStatusError, httpx.HTTPStatusError)):
        status = response.status_code
        if status not in {408, 409, 429} and not 500 <= status < 600:
            return None
    elif not isinstance(exc, (APIConnectionError, httpx.TransportError, TimeoutError, ConnectionError)):
        return None
    delay = random.uniform(0.5, 1.0) * min(2**attempt, 8)
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    seconds = 0
            if math.isfinite(seconds) and seconds > 0:
                # Never retry earlier than requested; decline an excessive wait.
                if seconds > 30:
                    return None
                delay = max(delay, seconds)
    return delay


class LLMClient:
    """Thin OpenAI SDK wrapper with deterministic mocks restricted to tests.

    Reproducibility contract (defect-7 fix, 2026-08-02):
    - ``temperature`` defaults to 0.0; pass a higher value only when you have
      a reason (eval harness sweep, exploratory research).
    - ``seed`` is forwarded to OpenAI's ``chat.completions.create`` as the
      ``seed`` field when set; unset (None) means "do not pin" — callers that
      need identical manifests across reruns should set this.
    - Test-only mock mode is deterministic by construction (keyword heuristics
      in ``_mock_response``); the same temperature/seed plumbing still
      exercises the configuration path used by live runtimes.
    """

    def __init__(
        self,
        *,
        model_version: str,
        client: Any | None = None,
        mock: bool = False,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_budget_seconds: float = DEFAULT_RETRY_BUDGET_SECONDS,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        for name, value, lower, upper in (
            ("timeout_seconds", timeout_seconds, 0.001, 300),
            ("retry_budget_seconds", retry_budget_seconds, 0.001, 600),
            ("temperature", temperature, 0, 2),
        ):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} must be finite and between {lower} and {upper}")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be an integer from 1 through 5")
        if type(max_completion_tokens) is not int or not 1 <= max_completion_tokens <= 131_072:
            raise ValueError("max_completion_tokens must be an integer from 1 through 131072")
        for name, value in (("max_input_bytes", max_input_bytes), ("max_response_bytes", max_response_bytes)):
            if type(value) is not int or not 1 <= value <= 16_777_216:
                raise ValueError(f"{name} must be an integer from 1 through 16777216")
        if not isinstance(model_version, str) or not model_version.strip():
            raise ValueError("model_version must not be blank")
        if seed is not None and (type(seed) is not int or not -(2**63) <= seed < 2**63):
            raise ValueError("seed must be a signed 64-bit integer")
        self.model_version = model_version
        self._client = client
        self._mock = mock
        self._temperature = temperature
        self._seed = seed
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_budget_seconds = retry_budget_seconds
        self._max_input_bytes = max_input_bytes
        self._max_response_bytes = max_response_bytes
        self._max_completion_tokens = max_completion_tokens

    # ------------------------------------------------------------------ factory

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Build a client from ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``.

        Reproducibility knobs read from env:
        - ``LLM_TEMPERATURE`` (default 0.0): passed straight to the OpenAI
          call.  Zero freezes sampling so reruns land on the same token.
        - ``LLM_SEED`` (default unset): forwarded to ``chat.completions.create``
          as ``seed``. Empty means "do not pin"; zero is a real seed.
        - ``LLM_TIMEOUT_SECONDS`` (default 90): socket inactivity timeout.
          Synchronous I/O cannot be forcibly cancelled by this wrapper.
        - ``LLM_RETRY_BUDGET_SECONDS`` (default 180): shared time budget for
          attempts and backoff within one chat_json call; late results fail.
        - ``LLM_MAX_ATTEMPTS`` (default 2): bounded application-level retries
          for transient provider transport errors.
        - ``LLM_MAX_COMPLETION_TOKENS`` (default 16384): generation cap sent
          on every provider attempt, including reasoning tokens where supported.
          This is not a total task budget or a monetary spending limit.

        Without ``LLM_API_KEY``, only ``APP_ENV=test`` may build a deterministic
        mock client. Every other environment is a live runtime and fails
        immediately rather than producing mock research output.
        """
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
        if not model.strip():
            raise ValueError("LLM_MODEL must not be blank")
        app_env = os.getenv("APP_ENV", "").strip().lower()

        if not api_key and app_env != "test":
            raise RuntimeError(
                "LLM_API_KEY is required outside APP_ENV=test; "
                "live runtimes never fall back to mock output"
            )

        temperature = float(os.getenv("LLM_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
        raw_seed = os.getenv("LLM_SEED", "").strip()
        seed: int | None = int(raw_seed) if raw_seed else None
        timeout_seconds = float(
            os.getenv("LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )
        max_attempts = int(os.getenv("LLM_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)))
        retry_budget_seconds = float(os.getenv("LLM_RETRY_BUDGET_SECONDS", str(DEFAULT_RETRY_BUDGET_SECONDS)))
        resource_limits = {
            "max_input_bytes": int(os.getenv("LLM_MAX_INPUT_BYTES", str(DEFAULT_MAX_INPUT_BYTES))),
            "max_response_bytes": int(os.getenv("LLM_MAX_RESPONSE_BYTES", str(DEFAULT_MAX_RESPONSE_BYTES))),
            "max_completion_tokens": int(os.getenv("LLM_MAX_COMPLETION_TOKENS", str(DEFAULT_MAX_COMPLETION_TOKENS))),
        }

        if not api_key:
            return cls(
                model_version=f"mock-{model}",
                mock=True,
                temperature=temperature,
                seed=seed,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_budget_seconds=retry_budget_seconds,
                **resource_limits,
            )

        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        return cls(
            model_version=model,
            client=client,
            mock=False,
            temperature=temperature,
            seed=seed,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_budget_seconds=retry_budget_seconds,
            **resource_limits,
        )

    # ------------------------------------------------------------------ core

    def operation_budget(self):
        return operation_budget(self)

    def chat_json(self, messages: list[dict], schema_hint: str = "") -> dict:
        """Call the model and return parsed JSON.

        ``schema_hint`` is a short tag (e.g. ``"extract"``, ``"propose"``,
        ``"assess"``) that the mock uses to pick the right response shape.
        """
        # Measure UTF-8 JSON messages, not characters or an estimated token
        # count. Stop encoding once the limit is reached; never log content.
        input_size = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(messages):
            try:
                input_size += len(chunk.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise LLMProviderError("LLM input contains invalid UTF-8") from exc
            if input_size > self._max_input_bytes:
                raise LLMProviderError("LLM input exceeds configured size limit")
        if self._mock:
            return _mock_response(messages, schema_hint)

        assert self._client is not None  # noqa: S101
        create_kwargs: dict[str, Any] = {
            "model": self.model_version,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": self._temperature,
            "max_completion_tokens": self._max_completion_tokens,
            "timeout": self._timeout_seconds,
        }
        if self._seed is not None:
            create_kwargs["seed"] = self._seed
        deadline = time.monotonic() + self._retry_budget_seconds
        parent_deadline = _operation_deadline.get()
        if parent_deadline is not None:
            deadline = min(deadline, parent_deadline)
        for attempt in range(self._max_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMProviderError(LLM_PROVIDER_ERROR_MESSAGE)
            create_kwargs["timeout"] = min(self._timeout_seconds, remaining)
            try:
                response = self._client.chat.completions.create(**create_kwargs)
                record_attempt(response, outcome="response_received")
                if time.monotonic() >= deadline:
                    # This rejects late results; it does not interrupt an
                    # in-flight synchronous request or undo provider billing.
                    raise LLMProviderError(LLM_PROVIDER_ERROR_MESSAGE)
                break
            except (OpenAIError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
                record_attempt(outcome="transport_error")
                delay = _retry_delay(exc, attempt)
                remaining = deadline - time.monotonic()
                if attempt + 1 == self._max_attempts or delay is None or delay >= remaining:
                    raise LLMProviderError(LLM_PROVIDER_ERROR_MESSAGE) from exc
                time.sleep(delay)

        try:
            choice = response.choices[0]
            content = choice.message.content
            finish_reason = choice.finish_reason
            refusal = getattr(choice.message, "refusal", None)
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMMalformedResponseError(
                LLM_MALFORMED_RESPONSE_MESSAGE
            ) from exc
        # Repair may restore JSON syntax, but cannot restore missing evidence
        # after truncation or make a provider refusal a valid research result.
        if finish_reason != "stop" or refusal:
            raise LLMMalformedResponseError(LLM_MALFORMED_RESPONSE_MESSAGE)
        if not isinstance(content, str):
            exc = TypeError("LLM response content must be a string")
            raise LLMMalformedResponseError(
                LLM_MALFORMED_RESPONSE_MESSAGE
            ) from exc
        # The SDK has already buffered the HTTP response. This bounds our
        # parsing/repair work, not network download size or provider billing.
        try:
            response_too_large = len(content) > self._max_response_bytes or len(content.encode("utf-8")) > self._max_response_bytes
        except UnicodeEncodeError as exc:
            raise LLMMalformedResponseError(LLM_MALFORMED_RESPONSE_MESSAGE) from exc
        if response_too_large:
            raise LLMMalformedResponseError("LLM response exceeds configured size limit")
        # 推理模型（如 MiniMax-M3）可能在 JSON 前加 <think>...</think> 块
        if "</think>" in content:
            content = content.split("</think>", 1)[1].strip()
        # 提取首个 JSON 对象（兼容模型偶尔加 markdown 包裹或多余文本）
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            content = content[start : end + 1]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            from json_repair import loads as repair_loads

            try:
                parsed = repair_loads(content)
            except (TypeError, ValueError) as exc:
                raise LLMMalformedResponseError(
                    LLM_MALFORMED_RESPONSE_MESSAGE
                ) from exc
        if not isinstance(parsed, dict):
            exc = TypeError("LLM JSON response must be an object")
            raise LLMMalformedResponseError(
                LLM_MALFORMED_RESPONSE_MESSAGE
            ) from exc
        return parsed


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
    if schema_hint == "preparation_parse_claims":
        return _mock_preparation_parse_claims(data)
    if schema_hint == "preparation_draft_protocol":
        return _mock_preparation_protocol()
    if schema_hint == "preparation_draft_evidence_plan":
        return _mock_preparation_evidence_plan(data)
    return {}


def _mock_preparation_parse_claims(data: dict) -> dict:
    """Deterministic test-only source-grounded preparation claims."""
    statements: list[dict] = []
    for span in data.get("spans", []):
        text = span.get("verbatim_text", "")
        source_span_id = span.get("source_span_id", "")
        if isinstance(text, str) and text and isinstance(source_span_id, str):
            statements.append({
                "source_span_id": source_span_id,
                "quote": text,
                "quote_start": 0,
                "quote_end": len(text),
                "normalized_text": text,
                "kind": "reported_claim",
            })
    return {"statements": statements}


def _mock_preparation_protocol() -> dict:
    """Static valid preparation protocol fixture for APP_ENV=test only."""
    return {
        "outcomes": [{"metric": "draft outcome"}],
        "baseline": {"metric": "draft baseline"},
        "horizon": {"start": "2026-01-01", "end": "2026-12-31"},
        "mechanisms": [{"driver": "draft mechanism"}],
        "verification_rules": [{"rule": "draft verification"}],
    }


def _mock_preparation_evidence_plan(data: dict) -> dict:
    """Deterministic valid plan fixture when a current factor is available."""
    factors = data.get("factors", [])
    factor = factors[0] if isinstance(factors, list) and factors else "draft factor"
    return {"items": [{
        "factor": factor,
        "evidence_target": "draft evidence target",
        "allowed_source_roles": ["primary_disclosure"],
        "priority": "normal",
        "stop_condition": "draft stop condition",
        "budget": 1,
    }]}


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
                "quote": text,
                "quote_start": 0,
                "quote_end": len(text),
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
