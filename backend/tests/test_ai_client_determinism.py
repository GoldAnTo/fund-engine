"""Defect-7 fix verification: LLMClient now pins ``temperature`` (default 0.0)
and forwards an optional ``seed`` so the citation manifest + assessment
conclusion land on the same tokens across reruns.

The walkthrough evidence (2026-08-02) showed T2 寒武纪 (盈利拐点) producing
``insufficient_evidence`` on the first rerun and ``supported`` on the second,
with the same frozen evidence snapshot.  Two root causes: (1) LLM temperature
not pinned, (2) input ordering not normalized.  This module pins (1) and
keeps the regression lock visible at the unit-test layer.

We do NOT make any claim about OpenAI's "seed" field being a strict
determinism guarantee (it is best-effort across versions/regions), but
temperature=0 plus a fixed seed closes the bulk of the variance.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from app.ai.client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
    LLMClient,
    LLMMalformedResponseError,
    LLMProviderError,
)
from app.ai.prompts import EXTRACT_JSON_RETRY_SYSTEM


def _isolate_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TEMPERATURE",
        "LLM_SEED",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_ATTEMPTS",
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_RETRY_BASE_SECONDS",
        "LLM_RETRY_MAX_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
    monkeypatch.setenv("LLM_SEED", "")


class _FakeCompletions:
    """Records the kwargs it was called with and returns a stub response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> MagicMock:
        self.calls.append(kwargs)
        # Mirror OpenAI's response shape enough for chat_json to extract content.
        response = MagicMock()
        response.input_sensitive = None
        response.output_sensitive = None
        response.base_resp = None
        response.choices = [MagicMock()]
        response.choices[0].message.content = '{"conclusion": "supported"}'
        response.choices[0].message.refusal = None
        response.choices[0].message.tool_calls = None
        response.choices[0].message.function_call = None
        response.choices[0].finish_reason = "stop"
        return response


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


class _FailingCompletions:
    def create(self, **kwargs: Any) -> MagicMock:
        raise httpx.ConnectError(
            "upstream leaked https://llm.example/v1?token=sentinel-secret"
        )


class _MalformedCompletions:
    def create(self, **kwargs: Any) -> MagicMock:
        response = MagicMock()
        response.input_sensitive = None
        response.output_sensitive = None
        response.base_resp = None
        response.choices = []
        return response


class _ClientWithCompletions:
    def __init__(self, completions: Any) -> None:
        self.chat = MagicMock()
        self.chat.completions = completions


def _json_response(
    content: str = '{"ok": true}', *, finish_reason: str = "stop"
) -> MagicMock:
    response = MagicMock()
    response.input_sensitive = None
    response.output_sensitive = None
    response.base_resp = None
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.refusal = None
    response.choices[0].message.tool_calls = None
    response.choices[0].message.function_call = None
    response.choices[0].finish_reason = finish_reason
    return response


class _SequenceCompletions:
    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls = 0

    def create(self, **_kwargs: Any) -> MagicMock:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return _json_response()


class _HTTPStatusCompletions:
    def __init__(
        self,
        status_code: int,
        *,
        retry_after: str | None = None,
        succeeds_after: bool = False,
        sdk_error: bool = False,
    ) -> None:
        request = httpx.Request(
            "POST", "https://llm.example.invalid/v1/chat/completions"
        )
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        response = httpx.Response(
            status_code,
            request=request,
            headers=headers,
            json={"detail": "sentinel-secret-provider-body"},
        )
        if sdk_error:
            self.error: Exception = openai.APIStatusError(
                "sentinel-secret-provider-status",
                response=response,
                body={"detail": "sentinel-secret-provider-body"},
            )
        else:
            self.error = httpx.HTTPStatusError(
                "sentinel-secret-provider-status", request=request, response=response
            )
        self.calls = 0
        self.succeeds_after = succeeds_after

    def create(self, **_kwargs: Any) -> MagicMock:
        self.calls += 1
        if self.succeeds_after and self.calls > 1:
            return _json_response()
        raise self.error


class TestLLMClientDeterminism:
    """Pin the temperature/seed contract introduced in defect-7 fix."""

    def test_default_temperature_is_zero(self) -> None:
        """The walkthrough acceptance criterion is that re-running the same
        assessment on the same evidence must produce the same conclusion in
        the bulk of cases.  Default temperature=0 is the floor of that
        contract; tests below pin the default so a future refactor cannot
        silently raise it."""
        client = LLMClient(model_version="gpt-4o-mini", client=_FakeOpenAIClient())
        assert client._temperature == DEFAULT_TEMPERATURE == 0.0

    def test_live_call_forwards_default_temperature(self) -> None:
        fake = _FakeOpenAIClient()
        client = LLMClient(model_version="gpt-4o-mini", client=fake)
        client.chat_json([{"role": "user", "content": "{}"}], schema_hint="assess")
        assert len(fake.chat.completions.calls) == 1
        assert fake.chat.completions.calls[0]["temperature"] == 0.0

    def test_live_call_forwards_explicit_temperature(self) -> None:
        fake = _FakeOpenAIClient()
        client = LLMClient(
            model_version="gpt-4o-mini", client=fake, temperature=0.7
        )
        client.chat_json([{"role": "user", "content": "{}"}], schema_hint="assess")
        assert fake.chat.completions.calls[0]["temperature"] == 0.7

    def test_live_call_has_a_finite_timeout(self) -> None:
        """A stalled provider must not leave a worker blocked indefinitely."""
        fake = _FakeOpenAIClient()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=fake,
            timeout_seconds=12.5,
        )

        client.chat_json([{"role": "user", "content": "{}"}], schema_hint="assess")

        assert fake.chat.completions.calls[0]["timeout"] == 12.5

    def test_live_call_forwards_configured_max_completion_tokens(self) -> None:
        fake = _FakeOpenAIClient()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=fake,
            max_output_tokens=4096,
        )

        client.chat_json([{"role": "user", "content": "{}"}])

        assert DEFAULT_MAX_OUTPUT_TOKENS == 4096
        assert fake.chat.completions.calls[0]["max_completion_tokens"] == 4096
        assert "max_tokens" not in fake.chat.completions.calls[0]

    def test_transient_failure_retries_with_bounded_backoff(self) -> None:
        sleep_calls: list[float] = []
        completions = _SequenceCompletions(
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("disconnected"),
            httpx.WriteError("write failed"),
        )
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=4,
            retry_base_seconds=0.25,
            retry_max_seconds=0.6,
            sleep=sleep_calls.append,
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }
        assert completions.calls == 4
        assert sleep_calls == [0.25, 0.5, 0.6]

    def test_exhausted_timeouts_expose_safe_structured_diagnostics(self) -> None:
        sleep_calls: list[float] = []
        completions = _SequenceCompletions(
            httpx.ReadTimeout("sentinel-secret first timeout"),
            httpx.ReadTimeout("sentinel-secret second timeout"),
        )
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=sleep_calls.append,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider request failed"
        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == "timeout"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 2
        assert len(sleep_calls) == 1

    @pytest.mark.parametrize("status_code", [408, 429, 500, 503, 599])
    def test_retryable_http_status_retries(self, status_code: int) -> None:
        sleep_calls: list[float] = []
        completions = _HTTPStatusCompletions(status_code, succeeds_after=True)
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=sleep_calls.append,
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }
        assert completions.calls == 2
        assert len(sleep_calls) == 1

    def test_openai_connection_error_retries(self) -> None:
        request = httpx.Request(
            "POST", "https://llm.example.invalid/v1/chat/completions"
        )
        completions = _SequenceCompletions(
            openai.APIConnectionError(message="disconnected", request=request)
        )
        sleep_calls: list[float] = []
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=sleep_calls.append,
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }
        assert completions.calls == 2
        assert len(sleep_calls) == 1

    def test_openai_retryable_status_uses_public_response_fields(self) -> None:
        sleep_calls: list[float] = []
        completions = _HTTPStatusCompletions(
            429,
            retry_after="0.75",
            succeeds_after=True,
            sdk_error=True,
        )
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            retry_base_seconds=0.25,
            retry_max_seconds=2.0,
            sleep=sleep_calls.append,
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }
        assert completions.calls == 2
        assert sleep_calls == [0.75]

    @pytest.mark.parametrize(
        ("status_code", "expected_category"),
        [(408, "timeout"), (429, "rate_limit"), (503, "server_error")],
    )
    def test_exhausted_retryable_status_exposes_attempt_count_and_category(
        self, status_code: int, expected_category: str
    ) -> None:
        completions = _HTTPStatusCompletions(status_code)
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=lambda _: None,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider request failed"
        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == expected_category
        assert completions.calls == 2

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
    def test_nonretryable_http_status_is_attempted_once(
        self, status_code: int
    ) -> None:
        completions = _HTTPStatusCompletions(status_code)
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal failures must not sleep"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider request failed"
        assert "sentinel-secret" not in str(exc_info.value)
        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "client_error"
        assert completions.calls == 1

    def test_response_validation_failure_is_attempted_once(self) -> None:
        request = httpx.Request(
            "POST", "https://llm.example.invalid/v1/chat/completions"
        )
        response = httpx.Response(200, request=request)
        error = openai.APIResponseValidationError(
            response=response,
            body={"detail": "sentinel-secret-provider-body"},
            message="sentinel-secret-invalid-response",
        )
        completions = _SequenceCompletions(error)
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal failures must not sleep"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider request failed"
        assert "sentinel-secret" not in str(exc_info.value)
        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "response_validation"
        assert completions.calls == 1

    @pytest.mark.parametrize(
        ("retry_after", "expected_delay"),
        [
            ("30", 2.0),
            ("0", 0.0),
            ("nan", 0.25),
            ("inf", 0.25),
            ("-1", 0.25),
            ("tomorrow", 0.25),
        ],
    )
    def test_retry_after_is_finite_nonnegative_and_clamped(
        self, retry_after: str, expected_delay: float
    ) -> None:
        sleep_calls: list[float] = []
        completions = _HTTPStatusCompletions(
            429, retry_after=retry_after, succeeds_after=True
        )
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            retry_base_seconds=0.25,
            retry_max_seconds=2.0,
            sleep=sleep_calls.append,
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }
        assert sleep_calls == [expected_delay]

    def test_malformed_response_retries_with_safe_json_correction(self) -> None:
        class MalformedThenValidCompletions:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def create(self, **kwargs: Any) -> MagicMock:
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return _json_response('{"sentinel-secret":')
                return _json_response('{"statements": []}')

        completions = MalformedThenValidCompletions()
        sleep_calls: list[float] = []
        original_messages = [{"role": "user", "content": "{}"}]
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=sleep_calls.append,
        )

        result = client.chat_json(
            original_messages,
            schema_hint="extract",
            malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
        )

        assert result == {"statements": []}
        assert result.attempt_count == 2
        assert result.malformed_retry_count == 1
        assert len(completions.calls) == 2
        assert completions.calls[0]["messages"] == original_messages
        retry_messages = completions.calls[1]["messages"]
        assert retry_messages[1:] == original_messages
        assert retry_messages[0] == {
            "role": "system",
            "content": EXTRACT_JSON_RETRY_SYSTEM,
        }
        assert "sentinel-secret" not in retry_messages[0]["content"]
        assert original_messages == [{"role": "user", "content": "{}"}]
        assert sleep_calls == [0.25]

    def test_extract_retry_merges_correction_into_existing_system_message(
        self,
    ) -> None:
        class MalformedThenValidCompletions:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def create(self, **kwargs: Any) -> MagicMock:
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return _json_response('{"sentinel-secret":')
                return _json_response('{"statements": []}')

        completions = MalformedThenValidCompletions()
        original_messages = [
            {"role": "system", "content": "trusted extraction contract"},
            {"role": "user", "content": "sentinel-source-data"},
        ]
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=lambda _: None,
        )

        client.chat_json(
            original_messages,
            schema_hint="extract",
            malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
        )

        retry_messages = completions.calls[1]["messages"]
        assert retry_messages == [
            {
                "role": "system",
                "content": (
                    "trusted extraction contract\n\n"
                    + EXTRACT_JSON_RETRY_SYSTEM
                ),
            },
            {"role": "user", "content": "sentinel-source-data"},
        ]
        assert original_messages == [
            {"role": "system", "content": "trusted extraction contract"},
            {"role": "user", "content": "sentinel-source-data"},
        ]

    @pytest.mark.parametrize("malformed_kind", ["length", "extract_schema"])
    def test_extract_retry_accepts_length_or_schema_correction(
        self, malformed_kind: str
    ) -> None:
        class MalformedThenValidCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                if self.calls == 1:
                    if malformed_kind == "length":
                        return _json_response(
                            '{"statements": [', finish_reason="length"
                        )
                    return _json_response('{"items": []}')
                return _json_response('{"statements": []}')

        completions = MalformedThenValidCompletions()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=lambda _: None,
        )

        result = client.chat_json(
            [{"role": "user", "content": "{}"}],
            schema_hint="extract",
            malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
        )

        assert result == {"statements": []}
        assert result.attempt_count == 2
        assert result.malformed_retry_count == 1
        assert completions.calls == 2

    @pytest.mark.parametrize("max_attempts", [1, 3])
    def test_extract_malformed_retry_exhaustion_uses_one_total_budget(
        self, max_attempts: int
    ) -> None:
        class AlwaysMalformedCompletions:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def create(self, **kwargs: Any) -> MagicMock:
                self.calls.append(kwargs)
                return _json_response('{"sentinel-secret":')

        completions = AlwaysMalformedCompletions()
        sleep_calls: list[float] = []
        original_messages = [{"role": "user", "content": "{}"}]
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=max_attempts,
            sleep=sleep_calls.append,
        )

        with pytest.raises(LLMMalformedResponseError) as exc_info:
            client.chat_json(
                original_messages,
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == max_attempts
        assert completions.calls[0]["messages"] == original_messages
        assert len(completions.calls) == max_attempts
        assert len(sleep_calls) == max_attempts - 1
        for call in completions.calls[1:]:
            assert call["messages"] == [
                {"role": "system", "content": EXTRACT_JSON_RETRY_SYSTEM},
                *original_messages,
            ]
            assert "sentinel-secret" not in call["messages"][0]["content"]

    @pytest.mark.parametrize(
        "failure_order", ["malformed_then_timeout", "timeout_then_malformed"]
    )
    def test_extract_malformed_and_transport_retries_share_budget_and_correction(
        self, failure_order: str
    ) -> None:
        class MixedFailuresThenValidCompletions:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def create(self, **kwargs: Any) -> MagicMock:
                self.calls.append(kwargs)
                failures = failure_order.split("_then_")
                if len(self.calls) > len(failures):
                    return _json_response('{"statements": []}')
                current_failure = failures[len(self.calls) - 1]
                if current_failure == "malformed":
                    return _json_response('{"sentinel-secret":')
                if current_failure == "timeout":
                    raise httpx.ReadTimeout("sentinel-secret timeout")
                raise AssertionError("unexpected test failure kind")

        completions = MixedFailuresThenValidCompletions()
        sleep_calls: list[float] = []
        original_messages = [{"role": "user", "content": "{}"}]
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=sleep_calls.append,
        )

        result = client.chat_json(
            original_messages,
            schema_hint="extract",
            malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
        )

        assert result == {"statements": []}
        assert result.attempt_count == 3
        assert result.malformed_retry_count == 1
        correction_messages = [
            call["messages"]
            for call in completions.calls
            if len(call["messages"]) == len(original_messages) + 1
        ]
        assert correction_messages
        assert all(
            messages[0]
            == {"role": "system", "content": EXTRACT_JSON_RETRY_SYSTEM}
            for messages in correction_messages
        )
        assert sleep_calls == [0.25, 0.5]

    @pytest.mark.parametrize(
        ("terminal_kind", "expected_category"),
        [
            ("http_400", "client_error"),
            ("http_503", "server_error"),
            ("refusal", "refusal"),
        ],
    )
    def test_extract_malformed_then_terminal_failure_stops_shared_budget(
        self, terminal_kind: str, expected_category: str
    ) -> None:
        terminal = (
            _HTTPStatusCompletions(int(terminal_kind.removeprefix("http_"))).error
            if terminal_kind.startswith("http_")
            else None
        )

        class MalformedThenTerminalCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                if self.calls == 1:
                    return _json_response('{"sentinel-secret":')
                if terminal is not None:
                    raise terminal
                response = _json_response('{"statements": []}')
                response.choices[0].message.refusal = "sentinel-secret refusal"
                return response

        completions = MalformedThenTerminalCompletions()
        sleep_calls: list[float] = []
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=sleep_calls.append,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == expected_category
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 2
        assert sleep_calls == [0.25]

    def test_extract_correction_messages_do_not_leak_into_the_next_call(self) -> None:
        class MalformedThenValidCompletions:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def create(self, **kwargs: Any) -> MagicMock:
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return _json_response('{"sentinel-secret":')
                return _json_response('{"statements": []}')

        completions = MalformedThenValidCompletions()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=lambda _: None,
        )
        first_messages = [{"role": "user", "content": "first"}]
        second_messages = [{"role": "user", "content": "second"}]

        client.chat_json(
            first_messages,
            schema_hint="extract",
            malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
        )
        client.chat_json(
            second_messages,
            schema_hint="extract",
            malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
        )

        assert completions.calls[1]["messages"][0] == {
            "role": "system",
            "content": EXTRACT_JSON_RETRY_SYSTEM,
        }
        assert completions.calls[2]["messages"] == second_messages

    @pytest.mark.parametrize("terminal_kind", ["content_filter", "refusal"])
    def test_extract_retry_does_not_retry_provider_refusal(
        self, terminal_kind: str
    ) -> None:
        class RefusedCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                response = _json_response(
                    '{"statements": []}',
                    finish_reason=(
                        "content_filter"
                        if terminal_kind == "content_filter"
                        else "stop"
                    ),
                )
                if terminal_kind == "refusal":
                    response.choices[0].message.refusal = "sentinel-secret refusal"
                return response

        completions = RefusedCompletions()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("refusal must not sleep"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert str(exc_info.value) == "LLM provider request failed"
        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "refusal"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 1

    def test_truncated_finish_reason_is_malformed_without_retry(self) -> None:
        class TruncatedCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("sentinel-secret transient timeout")
                return _json_response('{"ok": tru', finish_reason="length")

        completions = TruncatedCompletions()
        sleep_calls: list[float] = []
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=sleep_calls.append,
        )

        with pytest.raises(LLMMalformedResponseError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider returned an invalid response"
        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == "output_limit"
        assert completions.calls == 2
        assert len(sleep_calls) == 1

    def test_content_filter_without_message_is_terminal_refusal(self) -> None:
        class FilteredCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> SimpleNamespace:
                self.calls += 1
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="content_filter",
                            message=None,
                        )
                    ]
                )

        completions = FilteredCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("refusal must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "refusal"
        assert completions.calls == 1

    @pytest.mark.parametrize("sensitive_field", ["input_sensitive", "output_sensitive"])
    def test_provider_sensitive_flag_is_terminal_refusal(
        self, sensitive_field: str
    ) -> None:
        response = _json_response('{"statements": []}')
        setattr(response, sensitive_field, True)

        class SensitiveCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                return response

        completions = SensitiveCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("refusal must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "refusal"
        assert completions.calls == 1

    def test_provider_sensitive_flag_precedes_missing_choices_validation(self) -> None:
        response = SimpleNamespace(output_sensitive=True, choices=[])

        class SensitiveWithoutChoicesCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> SimpleNamespace:
                self.calls += 1
                return response

        completions = SensitiveWithoutChoicesCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("refusal must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "refusal"
        assert completions.calls == 1

    @pytest.mark.parametrize("finish_reason", ["tool_calls", "function_call", "other"])
    def test_non_stop_finish_reason_is_terminal_response_validation(
        self, finish_reason: str
    ) -> None:
        response = _json_response('{"statements": []}', finish_reason=finish_reason)

        class UnexpectedFinishCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                return response

        completions = UnexpectedFinishCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "response_validation"
        assert completions.calls == 1

    @pytest.mark.parametrize(
        "message",
        [None, SimpleNamespace(content=None)],
    )
    def test_explicit_tool_finish_precedes_missing_content_validation(
        self, message: object
    ) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=message,
                )
            ]
        )

        class ToolFinishCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> SimpleNamespace:
                self.calls += 1
                return response

        completions = ToolFinishCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "response_validation"
        assert completions.calls == 1

    def test_provider_base_status_error_precedes_missing_choices_validation(
        self,
    ) -> None:
        response = SimpleNamespace(
            base_resp=SimpleNamespace(
                status_code=1234,
                status_msg="sentinel-secret provider detail",
            ),
            choices=[],
        )

        class FailedEnvelopeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> SimpleNamespace:
                self.calls += 1
                return response

        completions = FailedEnvelopeCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "provider_error"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 1

    def test_provider_base_status_dict_from_sdk_is_terminal(self) -> None:
        response = openai.types.chat.ChatCompletion.model_validate(
            {
                "id": "chatcmpl-test",
                "choices": [],
                "created": 0,
                "model": "provider-test",
                "object": "chat.completion",
                "base_resp": {
                    "status_code": 1234,
                    "status_msg": "sentinel-secret provider detail",
                },
            }
        )
        assert isinstance(response.base_resp, dict)

        class FailedSDKEnvelopeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> object:
                self.calls += 1
                return response

        completions = FailedSDKEnvelopeCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "provider_error"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 1

    @pytest.mark.parametrize(
        ("status_code", "failure_category"),
        [
            (1000, "server_error"),
            (1001, "timeout"),
            (1002, "rate_limit"),
            (1013, "server_error"),
            (1024, "server_error"),
            (1033, "server_error"),
        ],
    )
    def test_transient_provider_base_status_retries_with_shared_budget(
        self, status_code: int, failure_category: str
    ) -> None:
        failed_response = openai.types.chat.ChatCompletion.model_validate(
            {
                "id": "chatcmpl-test",
                "choices": [],
                "created": 0,
                "model": "provider-test",
                "object": "chat.completion",
                "base_resp": {"status_code": status_code},
            }
        )

        class TransientEnvelopeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> object:
                self.calls += 1
                if self.calls == 1:
                    return failed_response
                return _json_response()

        sleep_calls: list[float] = []
        completions = TransientEnvelopeCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=sleep_calls.append,
        )

        result = client.chat_json([{"role": "user", "content": "{}"}])

        assert result == {"ok": True}
        assert result.attempt_count == 2
        assert completions.calls == 2
        assert sleep_calls == [0.25]

        exhausted = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(
                SimpleNamespace(create=lambda **_kwargs: failed_response)
            ),
            max_attempts=1,
        )
        with pytest.raises(LLMProviderError) as exc_info:
            exhausted.chat_json([{"role": "user", "content": "{}"}])
        assert exc_info.value.failure_category == failure_category

    @pytest.mark.parametrize(
        ("response_field", "invalid_value"),
        [
            ("input_sensitive", 1),
            ("output_sensitive", "false"),
        ],
    )
    def test_malformed_sensitive_flag_shape_fails_closed(
        self, response_field: str, invalid_value: object
    ) -> None:
        response = _json_response()
        setattr(response, response_field, invalid_value)
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(
                SimpleNamespace(create=lambda **_kwargs: response)
            ),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert exc_info.value.failure_category == "response_validation"

    def test_malformed_refusal_shape_fails_closed(self) -> None:
        response = _json_response()
        response.choices[0].message.refusal = {
            "detail": "sentinel-secret refusal"
        }
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(
                SimpleNamespace(create=lambda **_kwargs: response)
            ),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert exc_info.value.failure_category == "response_validation"
        assert "sentinel-secret" not in str(exc_info.value)

    @pytest.mark.parametrize(
        "invalid_status_code",
        ["0", False, None],
    )
    def test_malformed_base_status_shape_fails_closed(
        self, invalid_status_code: object
    ) -> None:
        response = _json_response()
        response.base_resp = {"status_code": invalid_status_code}
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(
                SimpleNamespace(create=lambda **_kwargs: response)
            ),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert exc_info.value.failure_category == "provider_error"

    @pytest.mark.parametrize("status_code", [1026, 1027])
    def test_sensitive_base_status_is_terminal_refusal(
        self, status_code: int
    ) -> None:
        response = openai.types.chat.ChatCompletion.model_validate(
            {
                "id": "chatcmpl-test",
                "choices": [],
                "created": 0,
                "model": "provider-test",
                "object": "chat.completion",
                "base_resp": {"status_code": status_code},
            }
        )

        class SensitiveEnvelopeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> object:
                self.calls += 1
                return response

        completions = SensitiveEnvelopeCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("refusal must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "refusal"
        assert completions.calls == 1

    @pytest.mark.parametrize("message", [None, SimpleNamespace(content=None)])
    def test_missing_finish_reason_precedes_missing_content_validation(
        self, message: object
    ) -> None:
        response = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=None, message=message)]
        )

        class MissingFinishCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> SimpleNamespace:
                self.calls += 1
                return response

        completions = MissingFinishCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "response_validation"
        assert completions.calls == 1

    @pytest.mark.parametrize("message_field", ["tool_calls", "function_call"])
    def test_stop_response_with_call_payload_is_terminal_response_validation(
        self, message_field: str
    ) -> None:
        response = _json_response('{"statements": []}')
        value: object
        if message_field == "tool_calls":
            value = [{"id": "sentinel-call"}]
        else:
            value = {"name": "sentinel-function"}
        setattr(response.choices[0].message, message_field, value)

        class UnexpectedCallCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                return response

        completions = UnexpectedCallCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "response_validation"
        assert "sentinel" not in str(exc_info.value)
        assert completions.calls == 1

    @pytest.mark.parametrize(
        ("message_field", "invalid_payload"),
        [
            ("tool_calls", {"id": "sentinel-secret-call"}),
            ("function_call", "sentinel-secret-function"),
        ],
    )
    def test_sdk_loose_call_payload_shape_fails_closed(
        self, message_field: str, invalid_payload: object
    ) -> None:
        message = openai.types.chat.ChatCompletionMessage.model_construct(
            content='{"statements": []}',
            role="assistant",
            tool_calls=None,
            function_call=None,
        )
        setattr(message, message_field, invalid_payload)
        choice = openai.types.chat.chat_completion.Choice.model_construct(
            finish_reason="stop",
            index=0,
            message=message,
        )
        response = openai.types.chat.ChatCompletion.model_construct(
            id="chatcmpl-test",
            choices=[choice],
            created=0,
            model="provider-test",
            object="chat.completion",
        )

        class LoosePayloadCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> object:
                self.calls += 1
                return response

        completions = LoosePayloadCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("terminal envelope must not retry"),
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "response_validation"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 1

    def test_literal_think_closing_tag_inside_json_is_not_stripped(self) -> None:
        class LiteralTagCompletions:
            def create(self, **_kwargs: Any) -> MagicMock:
                return _json_response('{"ok": "literal </think> marker"}')

        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(LiteralTagCompletions()),
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": "literal </think> marker"
        }

    def test_anchored_complete_think_wrapper_is_stripped(self) -> None:
        class WrappedCompletions:
            def create(self, **_kwargs: Any) -> MagicMock:
                return _json_response(
                    '  \n<think>provider reasoning</think>{"ok": true}'
                )

        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(WrappedCompletions()),
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }

    def test_unclosed_think_wrapper_uses_bounded_extract_retry(self) -> None:
        class UnclosedThinkCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                return _json_response("<think>sentinel-secret reasoning")

        completions = UnclosedThinkCompletions()
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=lambda _: None,
        )

        with pytest.raises(LLMMalformedResponseError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
                malformed_retry_system=EXTRACT_JSON_RETRY_SYSTEM,
            )

        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == "malformed_response"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 2

    def test_unclosed_json_object_is_not_repaired_as_truncated_output(self) -> None:
        class UnclosedJSONCompletions:
            def create(self, **_kwargs: Any) -> MagicMock:
                return _json_response('{"ok": true')

        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(UnclosedJSONCompletions()),
        )

        with pytest.raises(LLMMalformedResponseError):
            client.chat_json([{"role": "user", "content": "{}"}])

    @pytest.mark.parametrize(
        "malformed_content",
        [
            '{"statements":[}',
            '{"items":[1,2}',
            '{"ok": tru}',
        ],
    )
    def test_balanced_but_invalid_json_is_never_repaired(
        self, malformed_content: str
    ) -> None:
        class InvalidJSONCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                return _json_response(malformed_content)

        completions = InvalidJSONCompletions()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=lambda _: pytest.fail("malformed responses must not sleep"),
        )

        with pytest.raises(LLMMalformedResponseError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider returned an invalid response"
        assert exc_info.value.attempt_count == 1
        assert exc_info.value.failure_category == "malformed_response"
        assert not hasattr(exc_info.value.__cause__, "doc")
        assert completions.calls == 1

    def test_markdown_wrapped_complete_json_remains_supported(self) -> None:
        class WrappedJSONCompletions:
            def create(self, **_kwargs: Any) -> MagicMock:
                return _json_response('```json\n{"ok": true}\n```')

        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(WrappedJSONCompletions()),
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "ok": True
        }

    @pytest.mark.parametrize(
        "provider_content",
        [
            "{}",
            '{"statements":{"sentinel-secret":"not-a-list"}}',
        ],
    )
    def test_extract_schema_failure_preserves_attempt_count_after_retry(
        self, provider_content: str
    ) -> None:
        class InvalidExtractCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ReadTimeout("sentinel-secret transient timeout")
                return _json_response(provider_content)

        completions = InvalidExtractCompletions()
        sleep_calls: list[float] = []
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=3,
            sleep=sleep_calls.append,
        )

        with pytest.raises(LLMMalformedResponseError) as exc_info:
            client.chat_json(
                [{"role": "user", "content": "{}"}],
                schema_hint="extract",
            )

        assert str(exc_info.value) == "LLM provider returned an invalid response"
        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == "malformed_response"
        assert "sentinel-secret" not in str(exc_info.value)
        assert completions.calls == 2
        assert len(sleep_calls) == 1

    def test_live_call_retries_one_transient_transport_failure(self) -> None:
        class FailsOnceCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs: Any) -> MagicMock:
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ConnectError("transient provider disconnect")
                response = MagicMock()
                response.input_sensitive = None
                response.output_sensitive = None
                response.base_resp = None
                response.choices = [MagicMock()]
                response.choices[0].message.content = '{"conclusion": "supported"}'
                response.choices[0].message.refusal = None
                response.choices[0].message.tool_calls = None
                response.choices[0].message.function_call = None
                response.choices[0].finish_reason = "stop"
                return response

        completions = FailsOnceCompletions()
        client = LLMClient(
            model_version="gpt-4o-mini",
            client=_ClientWithCompletions(completions),
            max_attempts=2,
            sleep=lambda _: None,
        )

        assert client.chat_json([{"role": "user", "content": "{}"}]) == {
            "conclusion": "supported"
        }
        assert completions.calls == 2

    def test_seed_set_is_forwarded_to_openai(self) -> None:
        """When ``seed`` is set, the call kwargs must include it so OpenAI's
        backend can attempt to reproduce the run."""
        fake = _FakeOpenAIClient()
        client = LLMClient(
            model_version="gpt-4o-mini", client=fake, seed=42
        )
        client.chat_json([{"role": "user", "content": "{}"}], schema_hint="assess")
        assert fake.chat.completions.calls[0]["seed"] == 42

    def test_seed_none_omits_field_entirely(self) -> None:
        """When ``seed`` is None, we must NOT pass a ``seed=0`` (which OpenAI
        would treat as a real seed) and we must NOT pass ``seed=None`` either
        (which on some SDK versions serialises to 0).  Omit the key entirely."""
        fake = _FakeOpenAIClient()
        client = LLMClient(model_version="gpt-4o-mini", client=fake)
        client.chat_json([{"role": "user", "content": "{}"}], schema_hint="assess")
        assert "seed" not in fake.chat.completions.calls[0]

    def test_mock_mode_does_not_touch_underlying_client(self) -> None:
        """Mock mode is deterministic by construction; the live ``_client``
        must never be called even when one is provided.  This guards against
        accidental mock→live fallthrough that would burn API budget on test
        runs (defect boundary: ``mavis`` agent memory)."""
        fake = _FakeOpenAIClient()
        client = LLMClient(model_version="mock-gpt-4o-mini", client=fake, mock=True)
        client.chat_json(
            [{"role": "user", "content": '{"texts": ["管理层认为风险可控"]}'}],
            schema_hint="rewrite",
        )
        assert fake.chat.completions.calls == []

    def test_live_provider_failure_uses_fixed_safe_message(self) -> None:
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(_FailingCompletions()),
            sleep=lambda _: None,
        )

        with pytest.raises(LLMProviderError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider request failed"
        assert "sentinel-secret" not in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
        assert exc_info.value.attempt_count == 2
        assert exc_info.value.failure_category == "connection"

    @pytest.mark.parametrize(
        "programming_error", [TypeError, KeyError, AssertionError, AttributeError]
    )
    def test_live_call_does_not_wrap_programming_errors(
        self, programming_error
    ) -> None:
        class BrokenCompletions:
            def create(self, **kwargs: Any) -> MagicMock:
                raise programming_error("programming defect")

        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(BrokenCompletions()),
        )

        with pytest.raises(programming_error, match="programming defect"):
            client.chat_json([{"role": "user", "content": "{}"}])

    def test_empty_choices_uses_dedicated_malformed_response_error(self) -> None:
        client = LLMClient(
            model_version="provider-test",
            client=_ClientWithCompletions(_MalformedCompletions()),
        )

        with pytest.raises(LLMMalformedResponseError) as exc_info:
            client.chat_json([{"role": "user", "content": "{}"}])

        assert str(exc_info.value) == "LLM provider returned an invalid response"
        assert isinstance(exc_info.value.__cause__, IndexError)


class TestFromEnvReadsReproducibilityKnobs:
    """Pin that ``from_env`` wires env-driven reproducibility knobs through."""

    def test_from_env_with_no_key_runs_mock_with_knobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_llm_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
        monkeypatch.setenv("LLM_SEED", "1234")
        client = LLMClient.from_env()
        assert client._mock is True
        assert client._temperature == 0.0
        assert client._seed == 1234

    def test_from_env_with_empty_seed_means_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty ``LLM_SEED`` env must map to ``None`` (no seed passed to
        OpenAI) — a footgun here would silently seed every run with 0,
        locking users to a deterministic but unconfigurable run."""
        _isolate_llm_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("LLM_SEED", "")
        client = LLMClient.from_env()
        assert client._seed is None

    def test_from_env_zero_seed_is_a_real_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The empty-string convention distinguishes "unset" from "0":
        ``LLM_SEED=0`` must pass 0 through, not be dropped to None."""
        _isolate_llm_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("LLM_SEED", "0")
        client = LLMClient.from_env()
        assert client._seed == 0

    def test_from_env_configures_finite_timeout_without_sdk_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK defaults to retries, which can multiply a request timeout.

        A live workflow needs its configured limit to be a real upper bound,
        rather than three sequential timeout windows hidden inside the SDK.
        """
        import openai

        _isolate_llm_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
        captured: dict[str, Any] = {}

        class FakeSDK:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(openai, "OpenAI", FakeSDK)

        client = LLMClient.from_env()

        assert client._timeout_seconds == 12.5
        assert captured["timeout"] == 12.5
        assert captured["max_retries"] == 0

    def test_from_env_reads_output_and_retry_bounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_llm_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("LLM_MAX_ATTEMPTS", "4")
        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "3072")
        monkeypatch.setenv("LLM_RETRY_BASE_SECONDS", "0.125")
        monkeypatch.setenv("LLM_RETRY_MAX_SECONDS", "1.5")

        client = LLMClient.from_env()

        assert client._max_attempts == 4
        assert client._max_output_tokens == 3072
        assert client._retry_base_seconds == 0.125
        assert client._retry_max_seconds == 1.5

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("LLM_MAX_ATTEMPTS", "0"),
            ("LLM_MAX_ATTEMPTS", "1.5"),
            ("LLM_TIMEOUT_SECONDS", "0"),
            ("LLM_TIMEOUT_SECONDS", "nan"),
            ("LLM_TIMEOUT_SECONDS", "inf"),
            ("LLM_MAX_OUTPUT_TOKENS", "0"),
            ("LLM_MAX_OUTPUT_TOKENS", "1.5"),
            ("LLM_RETRY_BASE_SECONDS", "0"),
            ("LLM_RETRY_BASE_SECONDS", "nan"),
            ("LLM_RETRY_MAX_SECONDS", "-1"),
            ("LLM_RETRY_MAX_SECONDS", "inf"),
        ],
    )
    def test_from_env_rejects_invalid_llm_bounds(
        self, monkeypatch: pytest.MonkeyPatch, name: str, value: str
    ) -> None:
        _isolate_llm_env(monkeypatch)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError):
            LLMClient.from_env()

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"max_attempts": 0}, "max_attempts"),
            ({"max_attempts": 1.5}, "max_attempts"),
            ({"max_attempts": True}, "max_attempts"),
            ({"max_output_tokens": 0}, "max_output_tokens"),
            ({"max_output_tokens": 1.5}, "max_output_tokens"),
            ({"max_output_tokens": True}, "max_output_tokens"),
            ({"retry_base_seconds": float("nan")}, "retry_base_seconds"),
            ({"retry_max_seconds": 0}, "retry_max_seconds"),
        ],
    )
    def test_constructor_rejects_invalid_llm_bounds(
        self, kwargs: dict[str, Any], message: str
    ) -> None:
        with pytest.raises((TypeError, ValueError), match=message):
            LLMClient(model_version="gpt-4o-mini", **kwargs)

    def test_default_timeout_is_positive(self) -> None:
        assert DEFAULT_TIMEOUT_SECONDS > 0
