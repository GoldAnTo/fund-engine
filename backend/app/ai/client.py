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
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    OpenAIError,
)

DEFAULT_MODEL = "gpt-4o-mini"

# Default temperature=0.0: every live call freezes sampling so the citation
# manifest + assessment conclusion are reproducible for the same input under
# the same model.  OpenAI's seed is a best-effort hint, not a guarantee
# (versions/regions may still drift), but combined with temperature=0 it
# closes the bulk of the variance.  See walkthrough defect 7.
DEFAULT_TEMPERATURE = 0.0
# A provider call is part of a synchronous worker/API request.  Keep a finite
# ceiling so one unavailable upstream cannot indefinitely occupy that worker.
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_RETRY_BASE_SECONDS = 0.25
DEFAULT_RETRY_MAX_SECONDS = 2.0
LLM_PROVIDER_ERROR_MESSAGE = "LLM provider request failed"
LLM_MALFORMED_RESPONSE_MESSAGE = "LLM provider returned an invalid response"


class LLMFailureCategory(StrEnum):
    """Stable, secret-free classifications safe for persisted diagnostics."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    CLIENT_ERROR = "client_error"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    RESPONSE_VALIDATION = "response_validation"
    MALFORMED_RESPONSE = "malformed_response"
    OUTPUT_LIMIT = "output_limit"
    REFUSAL = "refusal"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"


_TRANSIENT_ENVELOPE_FAILURES = {
    1000: LLMFailureCategory.SERVER_ERROR,
    1001: LLMFailureCategory.TIMEOUT,
    1002: LLMFailureCategory.RATE_LIMIT,
    # 1013 has appeared as an internal error in older compatible responses;
    # current MiniMax documentation uses 1024 and 1033.
    1013: LLMFailureCategory.SERVER_ERROR,
    1024: LLMFailureCategory.SERVER_ERROR,
    1033: LLMFailureCategory.SERVER_ERROR,
}
_REFUSAL_ENVELOPE_STATUS_CODES = frozenset({1026, 1027})
_RETRYABLE_LLM_FAILURE_CATEGORIES = frozenset(
    {
        LLMFailureCategory.TIMEOUT,
        LLMFailureCategory.RATE_LIMIT,
        LLMFailureCategory.SERVER_ERROR,
    }
)


class LLMProviderError(RuntimeError):
    """Stable public boundary for live LLM request failures."""

    def __init__(
        self,
        message: str,
        *,
        attempt_count: int = 1,
        failure_category: LLMFailureCategory | str = LLMFailureCategory.PROVIDER_ERROR,
    ) -> None:
        if (
            isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or attempt_count < 0
        ):
            raise ValueError("attempt_count must be a non-negative integer")
        try:
            normalized_category = LLMFailureCategory(failure_category)
        except (TypeError, ValueError) as exc:
            raise ValueError("failure_category must be a known LLM category") from exc
        super().__init__(message)
        self.attempt_count = attempt_count
        self.failure_category = normalized_category


class LLMMalformedResponseError(LLMProviderError):
    """Raised when the provider response does not match the JSON protocol."""

    def __init__(
        self,
        message: str,
        *,
        attempt_count: int = 1,
        failure_category: LLMFailureCategory | str = (
            LLMFailureCategory.MALFORMED_RESPONSE
        ),
    ) -> None:
        super().__init__(
            message,
            attempt_count=attempt_count,
            failure_category=failure_category,
        )


class LLMJSONResponse(dict[str, Any]):
    """Parsed JSON plus call-local, secret-free provider diagnostics."""

    def __init__(
        self,
        values: dict[str, Any],
        *,
        attempt_count: int,
        malformed_retry_count: int,
    ) -> None:
        super().__init__(values)
        self.attempt_count = attempt_count
        self.malformed_retry_count = malformed_retry_count


def _positive_finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite positive number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return normalized


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a positive integer")
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _provider_status_code(exc: BaseException) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code
    return None


def _is_retryable_status(status_code: int | None) -> bool:
    return status_code in (408, 429) or (
        status_code is not None and 500 <= status_code <= 599
    )


def _is_transient_provider_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            APIConnectionError,
            httpx.TimeoutException,
            httpx.NetworkError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return True
    if isinstance(exc, (APIStatusError, httpx.HTTPStatusError)):
        return _is_retryable_status(_provider_status_code(exc))
    return False


def _provider_failure_category(exc: BaseException) -> LLMFailureCategory:
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return LLMFailureCategory.TIMEOUT
    status_code = _provider_status_code(exc)
    if status_code == 408:
        return LLMFailureCategory.TIMEOUT
    if status_code == 429:
        return LLMFailureCategory.RATE_LIMIT
    if status_code is not None and 500 <= status_code <= 599:
        return LLMFailureCategory.SERVER_ERROR
    if status_code is not None and 400 <= status_code <= 499:
        return LLMFailureCategory.CLIENT_ERROR
    if isinstance(exc, APIResponseValidationError):
        return LLMFailureCategory.RESPONSE_VALIDATION
    if isinstance(
        exc,
        (APIConnectionError, httpx.NetworkError, ConnectionError),
    ):
        return LLMFailureCategory.CONNECTION
    return LLMFailureCategory.PROVIDER_ERROR


def _retry_after_seconds(exc: BaseException) -> float | None:
    if not _is_retryable_status(_provider_status_code(exc)):
        return None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw_value = headers.get("Retry-After")
    except (AttributeError, TypeError):
        return None
    if raw_value is None:
        return None
    try:
        seconds = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _retry_delay_seconds(
    exc: BaseException,
    *,
    attempt: int,
    base_seconds: float,
    max_seconds: float,
) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, max_seconds)
    try:
        exponential_delay = math.ldexp(base_seconds, attempt)
    except OverflowError:
        return max_seconds
    return min(exponential_delay, max_seconds)


def _has_balanced_json_object_delimiters(content: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in content:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string


def _parse_chat_json_response(
    response: Any,
    *,
    schema_hint: str,
    attempt_count: int,
) -> dict:
    sensitive_flags = (
        getattr(response, "input_sensitive", None),
        getattr(response, "output_sensitive", None),
    )
    if any(flag is True for flag in sensitive_flags):
        exc = ValueError("LLM provider refused the response")
        raise LLMProviderError(
            LLM_PROVIDER_ERROR_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.REFUSAL,
        ) from exc
    if any(flag is not None and flag is not False for flag in sensitive_flags):
        exc = TypeError("LLM provider returned malformed safety flags")
        raise LLMProviderError(
            LLM_PROVIDER_ERROR_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.RESPONSE_VALIDATION,
        ) from exc
    base_response = getattr(response, "base_resp", None)
    if base_response is not None:
        missing_status = object()
        base_status_code = (
            base_response.get("status_code", missing_status)
            if isinstance(base_response, Mapping)
            else getattr(base_response, "status_code", missing_status)
        )
        if isinstance(base_status_code, bool) or not isinstance(
            base_status_code, int
        ):
            exc = TypeError("LLM provider returned a malformed response envelope")
            raise LLMProviderError(
                LLM_PROVIDER_ERROR_MESSAGE,
                attempt_count=attempt_count,
                failure_category=LLMFailureCategory.PROVIDER_ERROR,
            ) from exc
        if base_status_code != 0:
            failure_category = _TRANSIENT_ENVELOPE_FAILURES.get(
                base_status_code,
                (
                    LLMFailureCategory.REFUSAL
                    if base_status_code in _REFUSAL_ENVELOPE_STATUS_CODES
                    else LLMFailureCategory.PROVIDER_ERROR
                ),
            )
            exc = ValueError("LLM provider returned a failed response envelope")
            raise LLMProviderError(
                LLM_PROVIDER_ERROR_MESSAGE,
                attempt_count=attempt_count,
                failure_category=failure_category,
            ) from exc
    try:
        choice = response.choices[0]
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    finish_reason = getattr(choice, "finish_reason", None)
    message = getattr(choice, "message", None)
    refusal = getattr(message, "refusal", None)
    if finish_reason == "content_filter" or (
        isinstance(refusal, str) and refusal.strip()
    ):
        exc = ValueError("LLM provider refused the response")
        raise LLMProviderError(
            LLM_PROVIDER_ERROR_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.REFUSAL,
        ) from exc
    if refusal is not None and not isinstance(refusal, str):
        exc = TypeError("LLM provider returned a malformed refusal field")
        raise LLMProviderError(
            LLM_PROVIDER_ERROR_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.RESPONSE_VALIDATION,
        ) from exc
    if finish_reason == "length":
        exc = ValueError("LLM response reached its output-token limit")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.OUTPUT_LIMIT,
        ) from exc
    if finish_reason != "stop":
        exc = ValueError("LLM response ended with an unsupported finish reason")
        raise LLMProviderError(
            LLM_PROVIDER_ERROR_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.RESPONSE_VALIDATION,
        ) from exc
    tool_calls = getattr(message, "tool_calls", None)
    function_call = getattr(message, "function_call", None)
    has_tool_calls = not (
        tool_calls is None
        or (isinstance(tool_calls, (list, tuple)) and not tool_calls)
    )
    has_function_call = function_call is not None
    if has_tool_calls or has_function_call:
        exc = ValueError("LLM response unexpectedly requested a tool call")
        raise LLMProviderError(
            LLM_PROVIDER_ERROR_MESSAGE,
            attempt_count=attempt_count,
            failure_category=LLMFailureCategory.RESPONSE_VALIDATION,
        ) from exc
    try:
        content = message.content
    except AttributeError as exc:
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    if not isinstance(content, str):
        exc = TypeError("LLM response content must be a string")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    # 推理模型（如 MiniMax M2.x/M3）可能在 JSON 前加完整的
    # ``<think>...</think>`` 块。只识别锚定前缀，避免把 JSON 字符串内的
    # 字面 ``</think>`` 误当成协议包装。
    stripped_content = content.lstrip()
    if stripped_content.startswith("<think>"):
        closing_tag = stripped_content.find("</think>")
        if closing_tag < 0:
            exc = ValueError("LLM reasoning wrapper is incomplete")
            raise LLMMalformedResponseError(
                LLM_MALFORMED_RESPONSE_MESSAGE,
                attempt_count=attempt_count,
            ) from exc
        content = stripped_content[closing_tag + len("</think>") :].strip()
    # 提取首个 JSON 对象（兼容模型偶尔加 markdown 包裹或多余文本）
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        exc = ValueError("LLM response does not contain a complete JSON object")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    content = content[start : end + 1]
    if not _has_balanced_json_object_delimiters(content):
        exc = ValueError("LLM response contains a truncated JSON object")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    json_decode_failed = False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        json_decode_failed = True
        parsed = None
    if json_decode_failed:
        exc = ValueError("LLM response is not valid JSON")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    if not isinstance(parsed, dict):
        exc = TypeError("LLM JSON response must be an object")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    if schema_hint == "extract" and (
        "statements" not in parsed or not isinstance(parsed["statements"], list)
    ):
        exc = TypeError("LLM extract response must contain a statements list")
        raise LLMMalformedResponseError(
            LLM_MALFORMED_RESPONSE_MESSAGE,
            attempt_count=attempt_count,
        ) from exc
    return parsed


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
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        retry_max_seconds: float = DEFAULT_RETRY_MAX_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        timeout_seconds = _positive_finite_number(timeout_seconds, "timeout_seconds")
        max_attempts = _positive_integer(max_attempts, "max_attempts")
        max_output_tokens = _positive_integer(
            max_output_tokens, "max_output_tokens"
        )
        retry_base_seconds = _positive_finite_number(
            retry_base_seconds, "retry_base_seconds"
        )
        retry_max_seconds = _positive_finite_number(
            retry_max_seconds, "retry_max_seconds"
        )
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        self.model_version = model_version
        self._client = client
        self._mock = mock
        self._temperature = temperature
        self._seed = seed
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_output_tokens = max_output_tokens
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._sleep = sleep

    # ------------------------------------------------------------------ factory

    @classmethod
    def from_env(cls) -> LLMClient:
        """Build a client from ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``.

        Reproducibility knobs read from env:
        - ``LLM_TEMPERATURE`` (default 0.0): passed straight to the OpenAI
          call.  Zero freezes sampling so reruns land on the same token.
        - ``LLM_SEED`` (default unset): forwarded to ``chat.completions.create``
          as ``seed``. Empty means "do not pin"; zero is a real seed.
        - ``LLM_TIMEOUT_SECONDS`` (default 90): hard per-request limit for
          live provider calls. The SDK's implicit retries are disabled so this
          remains an actual bound rather than several stacked timeout windows.
        - ``LLM_MAX_ATTEMPTS`` (default 2): one total application-level attempt
          budget shared by transient transport retries and any caller-opted-in
          malformed-JSON retry.
        - ``LLM_MAX_OUTPUT_TOKENS`` (default 4096): hard completion-token
          budget forwarded to the provider as ``max_completion_tokens``. The
          completion budget includes reasoning tokens on reasoning models.
        - ``LLM_RETRY_BASE_SECONDS`` / ``LLM_RETRY_MAX_SECONDS`` (defaults
          0.25 / 2.0): bounded exponential retry delay.

        Without ``LLM_API_KEY``, only ``APP_ENV=test`` may build a deterministic
        mock client. Every other environment is a live runtime and fails
        immediately rather than producing mock research output.
        """
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
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
        max_output_tokens = int(
            os.getenv("LLM_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
        )
        retry_base_seconds = float(
            os.getenv("LLM_RETRY_BASE_SECONDS", str(DEFAULT_RETRY_BASE_SECONDS))
        )
        retry_max_seconds = float(
            os.getenv("LLM_RETRY_MAX_SECONDS", str(DEFAULT_RETRY_MAX_SECONDS))
        )

        if not api_key:
            return cls(
                model_version=f"mock-{model}",
                mock=True,
                temperature=temperature,
                seed=seed,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                max_output_tokens=max_output_tokens,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
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
            max_output_tokens=max_output_tokens,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )

    # ------------------------------------------------------------------ core

    def chat_json(
        self,
        messages: list[dict],
        schema_hint: str = "",
        *,
        malformed_retry_system: str | None = None,
    ) -> LLMJSONResponse:
        """Call the model and return parsed JSON.

        ``schema_hint`` is a short tag (e.g. ``"extract"``, ``"propose"``,
        ``"assess"``) that the mock uses to pick the right response shape.
        Malformed JSON is terminal unless the caller explicitly supplies a
        fixed ``malformed_retry_system`` message. That opt-in retry shares the
        same total attempt budget as transient transport retries.
        """
        if malformed_retry_system is not None:
            if not isinstance(malformed_retry_system, str):
                raise TypeError("malformed_retry_system must be a string or None")
            if not malformed_retry_system.strip():
                raise ValueError("malformed_retry_system must not be blank")
        if self._mock:
            return LLMJSONResponse(
                _mock_response(messages, schema_hint),
                attempt_count=1,
                malformed_retry_count=0,
            )

        assert self._client is not None
        create_kwargs: dict[str, Any] = {
            "model": self.model_version,
            "response_format": {"type": "json_object"},
            "temperature": self._temperature,
            "timeout": self._timeout_seconds,
            "max_completion_tokens": self._max_output_tokens,
        }
        if self._seed is not None:
            create_kwargs["seed"] = self._seed
        request_messages = messages
        malformed_retry_count = 0
        for attempt in range(self._max_attempts):
            try:
                response = self._client.chat.completions.create(
                    **create_kwargs,
                    messages=request_messages,
                )
            except (OpenAIError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
                if (
                    not _is_transient_provider_error(exc)
                    or attempt + 1 == self._max_attempts
                ):
                    raise LLMProviderError(
                        LLM_PROVIDER_ERROR_MESSAGE,
                        attempt_count=attempt + 1,
                        failure_category=_provider_failure_category(exc),
                    ) from exc
                retry_error: BaseException = exc
            else:
                try:
                    parsed = _parse_chat_json_response(
                        response,
                        schema_hint=schema_hint,
                        attempt_count=attempt + 1,
                    )
                except LLMMalformedResponseError as exc:
                    if (
                        malformed_retry_system is None
                        or attempt + 1 == self._max_attempts
                    ):
                        raise
                    retry_error = exc
                    malformed_retry_count += 1
                    request_messages = _messages_with_malformed_correction(
                        messages,
                        malformed_retry_system,
                    )
                except LLMProviderError as exc:
                    if (
                        exc.failure_category
                        not in _RETRYABLE_LLM_FAILURE_CATEGORIES
                        or attempt + 1 == self._max_attempts
                    ):
                        raise
                    retry_error = exc
                else:
                    return LLMJSONResponse(
                        parsed,
                        attempt_count=attempt + 1,
                        malformed_retry_count=malformed_retry_count,
                    )
            self._sleep(
                _retry_delay_seconds(
                    retry_error,
                    attempt=attempt,
                    base_seconds=self._retry_base_seconds,
                    max_seconds=self._retry_max_seconds,
                )
            )
        raise AssertionError("bounded LLM attempt loop exited unexpectedly")


def _messages_with_malformed_correction(
    messages: list[dict],
    correction: str,
) -> list[dict]:
    """Append a fixed correction to the trusted system contract, immutably."""
    corrected = [dict(message) for message in messages]
    for index, message in enumerate(corrected):
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        corrected[index] = {
            **message,
            "content": f"{content}\n\n{correction}",
        }
        return corrected
    return [{"role": "system", "content": correction}, *corrected]


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
