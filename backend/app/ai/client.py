"""Strict, bounded OpenAI-compatible JSON calls with per-attempt telemetry.

The public return type remains dict. Provider text is never included in public
errors or attempt metadata; exceptions retain causes for controlled diagnosis.
Do not expose their tracebacks in public or shared logs.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI, OpenAIError

from app.ai.llm_config import (
    DEFAULT_OPERATION_BUDGETS,
    LLMCallBudget,
    LLMRetryPolicy,
    bounded_integer,
    bounded_number,
    env_number,
)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_DEADLINE_SECONDS = 180.0
LLM_PROVIDER_ERROR_MESSAGE = "LLM provider request failed"
LLM_MALFORMED_RESPONSE_MESSAGE = "LLM provider returned an invalid response"
LLM_DEADLINE_MESSAGE = "LLM operation deadline exceeded"
JSON_TRANSPORT_CONTRACT = (
    "输出传输契约：只返回一个非空 JSON 对象的原始文本。不要使用 Markdown、代码块、"
    "```json、标签、说明文字、<think> 标签，也不要在 JSON 前后添加任何字符；"
    "首个非空白字符必须是 {，最后一个非空白字符必须是 }。"
    "如果任务格式与本契约冲突，以本契约为准。"
)


class LLMProviderError(RuntimeError):
    """Stable public boundary for live LLM request failures."""


class LLMMalformedResponseError(LLMProviderError):
    """The provider response failed the strict JSON/result protocol."""


class LLMInputBudgetError(LLMProviderError):
    """Input exceeded a configured budget; no request was started."""


class LLMDeadlineError(LLMProviderError):
    """The operation's monotonic time budget was exhausted."""


@dataclass(frozen=True)
class LLMAttempt:
    """Safe request metadata. None usage is unknown, never a zero-cost claim."""

    call_id: str
    attempt: int
    operation: str
    provider: str
    requested_model: str
    model: str | None
    provider_request_id: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    usage_status: str
    latency_ms: float
    outcome: str
    retryable: bool = False
    retry_delay_seconds: float | None = None
    repaired: bool = False


AttemptCallback = Callable[[LLMAttempt], None]
JSONValidator = Callable[[dict], object]


class LLMClient:
    """Synchronous transport with strict JSON, finite budgets and attempt hooks.

    ``deadline_seconds`` bounds retries, waits and response acceptance. Each
    SDK timeout is clamped to remaining time, but synchronous HTTP phase
    timeouts cannot forcibly cancel every in-flight request at that instant.
    No retry is admitted after the deadline and late output is never accepted.
    """

    def __init__(
        self, *, model_version: str, client: Any | None = None, mock: bool = False,
        temperature: float = DEFAULT_TEMPERATURE, seed: int | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
        backoff_base_seconds: float = 0.5, backoff_max_seconds: float = 8.0,
        retry_after_max_seconds: float = 30.0,
        budget: LLMCallBudget | None = None,
        operation_budgets: Mapping[str, LLMCallBudget] | None = None,
        output_token_parameter: str = "max_completion_tokens",
        reasoning_split: bool = False,
        provider: str = "openai-compatible", on_attempt: AttemptCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._retry = LLMRetryPolicy(
            timeout_seconds=timeout_seconds, max_attempts=max_attempts,
            deadline_seconds=deadline_seconds, backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            retry_after_max_seconds=retry_after_max_seconds,
        )
        bounded_number("temperature", temperature, 0, 2)
        if seed is not None:
            bounded_integer("seed", seed, -(2**63), 2**63 - 1)
        if _safe_identifier(model_version) is None:
            raise ValueError("model_version must be a nonempty model identifier")
        if _safe_identifier(provider) is None:
            raise ValueError("provider must be a nonempty provider identifier")
        if output_token_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ValueError("output_token_parameter must be max_tokens or max_completion_tokens")
        if type(reasoning_split) is not bool:
            raise ValueError("reasoning_split must be boolean")
        self._reasoning_split = reasoning_split
        self._budget = budget if budget is not None else LLMCallBudget()
        if not isinstance(self._budget, LLMCallBudget):
            raise TypeError("budget must be an LLMCallBudget")
        self._operation_budgets = dict(DEFAULT_OPERATION_BUDGETS)
        if operation_budgets is not None:
            for operation, operation_budget in operation_budgets.items():
                if _safe_identifier(operation) is None or not isinstance(operation_budget, LLMCallBudget):
                    raise ValueError("operation_budgets must map operation identifiers to LLMCallBudget")
                self._operation_budgets[operation] = operation_budget
        if on_attempt is not None and not callable(on_attempt):
            raise ValueError("on_attempt must be callable")
        self.model_version = model_version
        # An injected genuine SDK client can otherwise silently multiply retries.
        # Keep simple test/provider adapters unchanged; they own no SDK retries.
        self._client = client.with_options(max_retries=0) if isinstance(client, OpenAI) else client
        self._mock = mock
        self._temperature, self._seed = temperature, seed
        self._timeout_seconds, self._max_attempts = timeout_seconds, max_attempts
        self._output_token_parameter, self._provider = output_token_parameter, provider
        self._on_attempt = on_attempt
        self._clock, self._sleep = clock, sleep
        self._random_value, self._wall_clock = random_value, wall_clock
        self._attempt_callbacks: ContextVar[tuple[AttemptCallback, ...]] = ContextVar(
            f"llm_attempt_callbacks_{id(self)}", default=(),
        )

    @classmethod
    def from_env(cls) -> LLMClient:
        """Load explicit finite configuration; only APP_ENV=test may mock.

        LLM_MAX_INPUT_CHARS / LLM_MAX_OUTPUT_TOKENS / LLM_MAX_RESPONSE_CHARS
        control resource budgets. LLM_OUTPUT_TOKEN_PARAMETER selects exactly
        one provider-supported cap field. LLM_DEADLINE_SECONDS and LLM_BACKOFF_*
        control application retries; SDK retries are always disabled.
        """
        api_key = os.getenv("LLM_API_KEY")
        model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
        if not api_key and os.getenv("APP_ENV", "").strip().lower() != "test":
            raise RuntimeError(
                "LLM_API_KEY is required outside APP_ENV=test; "
                "live runtimes never fall back to mock output"
            )
        seed = None
        if os.getenv("LLM_SEED", "").strip():
            seed = env_number("LLM_SEED", 0, integer=True)
        split_setting = os.getenv("LLM_REASONING_SPLIT", "").strip().lower()
        if split_setting not in {"", "true", "false", "1", "0"}:
            raise ValueError("LLM_REASONING_SPLIT must be true or false")
        minimax = urlsplit(os.getenv("LLM_BASE_URL", "")).hostname in {
            "api.minimaxi.com", "api.minimax.io", "api.minimax.cn",
        }
        split_reasoning = split_setting in {"true", "1"} if split_setting else minimax
        # Validate before constructing a network client, including mock runtimes.
        instance = cls(
            model_version=model if api_key else f"mock-{model}", mock=not api_key,
            temperature=env_number("LLM_TEMPERATURE", DEFAULT_TEMPERATURE), seed=seed,
            timeout_seconds=env_number("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            max_attempts=env_number("LLM_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, integer=True),
            deadline_seconds=env_number("LLM_DEADLINE_SECONDS", DEFAULT_DEADLINE_SECONDS),
            backoff_base_seconds=env_number("LLM_BACKOFF_BASE_SECONDS", 0.5),
            backoff_max_seconds=env_number("LLM_BACKOFF_MAX_SECONDS", 8.0),
            retry_after_max_seconds=env_number("LLM_RETRY_AFTER_MAX_SECONDS", 30.0),
            output_token_parameter=os.getenv("LLM_OUTPUT_TOKEN_PARAMETER", "max_completion_tokens"),
            provider=os.getenv("LLM_PROVIDER", "minimax" if minimax else "openai-compatible"),
            reasoning_split=split_reasoning,
            budget=LLMCallBudget(
                max_input_chars=env_number("LLM_MAX_INPUT_CHARS", 160_000, integer=True),
                max_output_tokens=env_number("LLM_MAX_OUTPUT_TOKENS", 8192, integer=True),
                max_response_chars=env_number("LLM_MAX_RESPONSE_CHARS", 100_000, integer=True),
            ),
        )
        if api_key:
            from openai import OpenAI as SDKClient

            instance._client = SDKClient(
                api_key=api_key, base_url=os.getenv("LLM_BASE_URL"),
                timeout=instance._timeout_seconds, max_retries=0,
            )
        return instance

    @contextmanager
    def capture_attempts(self, callback: AttemptCallback) -> Iterator[None]:
        """Collect this context's calls, including nested rewrite calls.

        ContextVar isolates concurrent requests. Callbacks should append to an
        operation-local collection; persist it after the network phase so no
        database transaction remains open while waiting on the provider.
        """
        if not callable(callback):
            raise TypeError("attempt callback must be callable")
        token = self._attempt_callbacks.set((*self._attempt_callbacks.get(), callback))
        try:
            yield
        finally:
            self._attempt_callbacks.reset(token)

    def chat_json(
        self, messages: list[dict], schema_hint: str = "", *,
        validator: JSONValidator | None = None, on_attempt: AttemptCallback | None = None,
        budget: LLMCallBudget | None = None,
    ) -> dict:
        """Return one complete object, optionally checked by a schema validator.

        Non-JSON wrappers, repairable JSON, refusals and non-stop completion
        reasons fail closed. The validator may raise ValueError/TypeError or
        return False to reject. Successful validator return values are ignored.
        """
        started = self._clock()
        deadline = started + self._retry.deadline_seconds
        if schema_hint and _safe_identifier(schema_hint) is None:
            raise ValueError("schema_hint must be an operation identifier")
        if validator is not None and not callable(validator):
            raise ValueError("validator must be callable")
        if on_attempt is not None and not callable(on_attempt):
            raise ValueError("on_attempt must be callable")
        effective_budget = self._budget
        if schema_hint in self._operation_budgets:
            effective_budget = effective_budget.tighten(self._operation_budgets[schema_hint])
        if budget is not None:
            effective_budget = effective_budget.tighten(budget)
        messages = _with_json_transport_contract(messages)
        _check_input(messages, effective_budget.max_input_chars)
        call_id = str(uuid4())
        callbacks = tuple(cb for cb in (self._on_attempt, *self._attempt_callbacks.get(), on_attempt)
                          if cb is not None)
        if self._mock:
            return self._mock_call(messages, schema_hint, validator, effective_budget,
                                   call_id, callbacks, deadline)
        if self._client is None:
            raise LLMProviderError(LLM_PROVIDER_ERROR_MESSAGE)
        create_kwargs: dict[str, Any] = {
            "model": self.model_version, "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": self._temperature,
            self._output_token_parameter: effective_budget.max_output_tokens,
        }
        if self._reasoning_split:
            create_kwargs["extra_body"] = {"reasoning_split": True}
        if self._seed is not None:
            create_kwargs["seed"] = self._seed
        for attempt in range(1, self._max_attempts + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise LLMDeadlineError(LLM_DEADLINE_MESSAGE)
            create_kwargs["timeout"] = min(self._timeout_seconds, remaining)
            attempt_started = self._clock()
            response = None
            failure: Exception | None = None
            retryable = False
            delay = None
            outcome = "internal_error"
            try:
                response = self._client.chat.completions.create(**create_kwargs)
                self._check_deadline(deadline)
                outcome = "malformed_response"
                parsed = _parse_response(response, effective_budget.max_response_chars)
                outcome = "schema_error"
                _validate_object(parsed, validator)
                self._check_deadline(deadline)
                outcome = "succeeded"
            except (OpenAIError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
                failure = exc
                retryable = _retryable(exc)
                if self._clock() >= deadline:
                    outcome = "deadline_exceeded"
                else:
                    outcome = "provider_error"
                    if retryable and attempt < self._max_attempts:
                        delay = self._retry_delay(exc, attempt)
                        if delay >= deadline - self._clock():
                            outcome = "deadline_exceeded"
                            delay = None
            except LLMDeadlineError:
                outcome = "deadline_exceeded"
                raise
            finally:
                event = self._make_attempt(
                    call_id, attempt, schema_hint, attempt_started, response, failure,
                    outcome, retryable, delay,
                )
                self._emit_attempt(event, callbacks)
            if outcome == "succeeded":
                self._check_deadline(deadline)
                return parsed
            if outcome == "deadline_exceeded":
                raise LLMDeadlineError(LLM_DEADLINE_MESSAGE) from failure
            if delay is None:
                raise LLMProviderError(LLM_PROVIDER_ERROR_MESSAGE) from failure
            self._check_deadline(deadline)
            # A callback may have consumed time since the delay was computed.
            if delay >= deadline - self._clock():
                raise LLMDeadlineError(LLM_DEADLINE_MESSAGE) from failure
            self._sleep(delay)
        raise LLMProviderError(LLM_PROVIDER_ERROR_MESSAGE)  # guarded by validated attempts

    def _check_deadline(self, deadline: float) -> None:
        if self._clock() >= deadline:
            raise LLMDeadlineError(LLM_DEADLINE_MESSAGE)

    def _retry_delay(self, error: Exception, attempt: int) -> float:
        header_delay = _retry_after(error, self._wall_clock())
        if header_delay is not None:
            return min(header_delay, self._retry.retry_after_max_seconds)
        base = min(self._retry.backoff_max_seconds,
                   self._retry.backoff_base_seconds * (2 ** (attempt - 1)))
        jitter = self._random_value()
        bounded_number("random_value", jitter, 0, 1)
        # Equal jitter avoids a synchronized zero-delay retry storm.
        return base * (0.5 + jitter / 2)

    def _make_attempt(
        self, call_id: str, attempt: int, operation: str, started: float,
        response: Any, error: Exception | None, outcome: str,
        retryable: bool = False, delay: float | None = None,
    ) -> LLMAttempt:
        usage = getattr(response, "usage", None)
        prompt = _usage_int(getattr(usage, "prompt_tokens", None))
        completion_tokens = _usage_int(getattr(usage, "completion_tokens", None))
        total = _usage_int(getattr(usage, "total_tokens", None))
        known = sum(value is not None for value in (prompt, completion_tokens, total))
        usage_status = "known" if known == 3 else "partial" if known else "unknown"
        request_id = _safe_identifier(getattr(response, "_request_id", None))
        if request_id is None and isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            request_id = _safe_identifier(error.response.headers.get("x-request-id"))
        choices = getattr(response, "choices", None)
        finish_reason = None
        if isinstance(choices, list) and choices:
            reason = getattr(choices[0], "finish_reason", None)
            if reason in ("stop", "length", "content_filter", "tool_calls", "function_call"):
                finish_reason = reason
        return LLMAttempt(
            call_id=call_id, attempt=attempt, operation=operation,
            provider="mock" if self._mock else self._provider,
            requested_model=self.model_version,
            model=_safe_identifier(getattr(response, "model", None)),
            provider_request_id=request_id, finish_reason=finish_reason,
            prompt_tokens=prompt, completion_tokens=completion_tokens, total_tokens=total,
            usage_status=usage_status, latency_ms=max(0.0, (self._clock() - started) * 1000),
            outcome=outcome, retryable=retryable, retry_delay_seconds=delay,
        )

    @staticmethod
    def _emit_attempt(event: LLMAttempt, callbacks: tuple[AttemptCallback, ...]) -> None:
        delivered: list[AttemptCallback] = []
        for callback in callbacks:
            if callback in delivered:
                continue
            try:
                callback(event)
            except Exception as exc:
                # Never silently lose accounting and continue spending.
                raise LLMProviderError("LLM attempt recording failed") from exc
            delivered.append(callback)

    def _mock_call(
        self, messages: list[dict], operation: str, validator: JSONValidator | None,
        budget: LLMCallBudget, call_id: str, callbacks: tuple[AttemptCallback, ...],
        deadline: float,
    ) -> dict:
        self._check_deadline(deadline)
        started = self._clock()
        outcome = "malformed_response"
        try:
            parsed = _mock_response(messages, operation)
            if len(json.dumps(parsed, ensure_ascii=False, allow_nan=False)) > budget.max_response_chars:
                raise LLMMalformedResponseError(LLM_MALFORMED_RESPONSE_MESSAGE)
            outcome = "schema_error"
            _validate_object(parsed, validator)
            self._check_deadline(deadline)
            outcome = "mock"
        except LLMDeadlineError:
            outcome = "deadline_exceeded"
            raise
        finally:
            self._emit_attempt(self._make_attempt(call_id, 1, operation, started, None, None, outcome), callbacks)
        self._check_deadline(deadline)
        return parsed


def _check_input(messages: list[dict], max_chars: int) -> None:
    try:
        if not isinstance(messages, list):
            raise TypeError("messages must be a list")
        for message in messages:
            if (not isinstance(message, dict) or not isinstance(message.get("content"), str)
                    or message.get("role") not in ("system", "developer", "user", "assistant", "tool")):
                raise ValueError("messages must contain text content and supported roles")
        # iterencode bounds serialized allocation; no silent truncation of sources.
        size = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(messages):
            size += len(chunk)
            if size > max_chars:
                raise LLMInputBudgetError("LLM input exceeds the configured budget")
    except (ValueError, TypeError, RecursionError) as exc:
        raise LLMProviderError("LLM input is invalid") from exc


def _with_json_transport_contract(messages: Any) -> Any:
    if not isinstance(messages, list):
        return messages
    enhanced: list[dict] = []
    system_index = None
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            return messages
        copy = dict(message)
        enhanced.append(copy)
        if (
            system_index is None
            and copy.get("role") == "system"
            and isinstance(copy.get("content"), str)
        ):
            system_index = index
    if system_index is None:
        return [{"role": "system", "content": JSON_TRANSPORT_CONTRACT}, *enhanced]
    content = enhanced[system_index]["content"].rstrip()
    enhanced[system_index]["content"] = f"{content}\n\n{JSON_TRANSPORT_CONTRACT}"
    return enhanced


def _parse_response(response: Any, max_chars: int) -> dict:
    try:
        choices = response.choices
        choice = choices[0]  # retain precise cause for empty choices
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Expected exactly one completion")
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise ValueError("LLM completion did not finish normally")
        content = choice.message.content
        if not isinstance(content, str) or len(content) > max_chars:
            raise ValueError("LLM response content is invalid or too large")
        parsed = json.loads(content, object_pairs_hook=_unique_object,
                            parse_constant=_reject_nonfinite, parse_float=_finite_float)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("LLM JSON response must be a nonempty object")
        return parsed
    except (AttributeError, IndexError, TypeError, ValueError, RecursionError) as exc:
        raise LLMMalformedResponseError(LLM_MALFORMED_RESPONSE_MESSAGE) from exc


def _validate_object(parsed: dict, validator: JSONValidator | None) -> None:
    try:
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("LLM JSON response must be a nonempty object")
        if validator is not None and validator(parsed) is False:
            raise ValueError("LLM JSON response failed schema validation")
    except (ValueError, TypeError) as exc:
        raise LLMMalformedResponseError(LLM_MALFORMED_RESPONSE_MESSAGE) from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def _safe_identifier(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", value) else None


def _usage_int(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _retryable(error: Exception) -> bool:
    if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
        status = error.status_code if isinstance(error, APIStatusError) else error.response.status_code
        return status in (408, 409, 429) or 500 <= status < 600
    return isinstance(error, (APIConnectionError, httpx.TransportError, TimeoutError, ConnectionError))


def _retry_after(error: Exception, now: float) -> float | None:
    if not isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
        return None
    headers = error.response.headers
    for name, divisor in (("retry-after-ms", 1000), ("retry-after", 1)):
        raw = headers.get(name)
        if raw is None or len(raw) > 128:
            continue
        try:
            seconds = float(raw) / divisor
        except ValueError:
            if name != "retry-after":
                continue
            try:
                seconds = parsedate_to_datetime(raw).timestamp() - now
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


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
