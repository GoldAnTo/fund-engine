"""Production LLM boundary tests. Real SDK models, no paid/network requests."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, OpenAI
from openai.types.chat import ChatCompletion

from app.ai.client import LLMClient, LLMMalformedResponseError, LLMProviderError


def completion(content='{"ok": true}', *, finish_reason="stop", refusal=None, usage=None):
    return ChatCompletion.model_validate({
        "id": "chatcmpl-offline", "created": 0, "model": "actual-model-v1",
        "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content,
                                 "refusal": refusal}}],
        "usage": usage,
    })


class ScriptedProvider:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result()
        return result


@pytest.mark.parametrize("content", [
    '{"ok": true', '```json\n{"ok": true}\n```',
    '<think>private chain</think>{"ok": true}',
    'Here is the answer {"ok": true}', '{"ok": true}{"other": 1}',
    '{}', '[]', '{"ok": NaN}', '{"ok": Infinity}', '{"ok": 1, "ok": 2}',
    '{"ok": {"nested": 1, "nested": 2}}',
    '{"ok": 1e999}', '{"ok": -1e999}',
])
def test_rejects_incomplete_or_ambiguous_object(content):
    provider = ScriptedProvider([completion(content)])
    with pytest.raises(LLMMalformedResponseError, match="^LLM provider returned an invalid response$"):
        LLMClient(model_version="offline", client=provider).chat_json([])
    assert len(provider.calls) == 1


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", "function_call"])
def test_non_stop_completion_is_rejected_even_if_json_is_valid(finish_reason):
    provider = ScriptedProvider([completion(finish_reason=finish_reason)])
    with pytest.raises(LLMMalformedResponseError):
        LLMClient(model_version="offline", client=provider).chat_json([])
    assert len(provider.calls) == 1


def test_refusal_is_rejected_even_when_content_has_json():
    provider = ScriptedProvider([completion(refusal="sentinel-private-refusal")])
    with pytest.raises(LLMMalformedResponseError) as exc:
        LLMClient(model_version="offline", client=provider).chat_json([])
    assert "sentinel" not in str(exc.value)


@pytest.mark.parametrize("field", ["finish_reason", "message"])
def test_incomplete_sdk_choice_is_rejected(field):
    response = completion()
    delattr(response.choices[0], field)
    with pytest.raises(LLMMalformedResponseError):
        LLMClient(model_version="offline", client=ScriptedProvider([response])).chat_json([])


def test_valid_object_is_returned_unchanged():
    provider = ScriptedProvider([completion('  {"ok": false, "value": 0}  ')])
    assert LLMClient(model_version="offline", client=provider).chat_json([]) == {"ok": False, "value": 0}


def test_json_transport_contract_is_sent_without_mutating_caller_messages():
    messages = [
        {"role": "system", "content": "Return the requested schema."},
        {"role": "user", "content": '{"topic": "贵州茅台财务问题"}'},
    ]
    provider = ScriptedProvider([completion('{"ok": true}')])

    assert LLMClient(model_version="offline", client=provider).chat_json(messages) == {"ok": True}

    sent_messages = provider.calls[0]["messages"]
    assert sent_messages is not messages
    assert sent_messages[0]["role"] == "system"
    assert "Return the requested schema." in sent_messages[0]["content"]
    assert "Markdown" in sent_messages[0]["content"]
    assert "```json" in sent_messages[0]["content"]
    assert "首个非空白字符必须是 {" in sent_messages[0]["content"]
    assert messages[0]["content"] == "Return the requested schema."
    assert provider.calls[0]["response_format"] == {"type": "json_object"}


def test_json_transport_contract_is_added_when_caller_has_no_system_message():
    provider = ScriptedProvider([completion('{"ok": true}')])

    assert LLMClient(model_version="offline", client=provider).chat_json(
        [{"role": "user", "content": "Return JSON"}]
    ) == {"ok": True}

    sent_messages = provider.calls[0]["messages"]
    assert sent_messages[0]["role"] == "system"
    assert "只返回一个非空 JSON 对象" in sent_messages[0]["content"]
    assert sent_messages[1]["content"] == "Return JSON"


@pytest.mark.parametrize("field,value", [
    ("max_attempts", True), ("max_attempts", 1.5), ("max_attempts", 0), ("max_attempts", 6),
    ("timeout_seconds", True), ("timeout_seconds", float("nan")), ("timeout_seconds", float("inf")),
    ("timeout_seconds", 301), ("timeout_seconds", 0), ("temperature", True),
    pytest.param("timeout_seconds", 10**10000, id="huge-timeout"),
    ("temperature", float("nan")), ("temperature", -0.1), ("temperature", 2.1),
    ("seed", True), ("seed", 1.5), ("seed", 2**63),
    ("deadline_seconds", True), ("deadline_seconds", 0), ("deadline_seconds", float("inf")),
    ("deadline_seconds", 601), ("backoff_base_seconds", -1), ("backoff_base_seconds", 31),
    ("backoff_max_seconds", float("nan")), ("backoff_max_seconds", 61),
    ("retry_after_max_seconds", 61), ("output_token_parameter", "max_output_tokens"),
])
def test_configuration_is_finite_and_bounded(field, value):
    with pytest.raises(ValueError):
        LLMClient(model_version="offline", **{field: value})


def test_long_unicode_input_is_rejected_without_request_or_truncation():
    from app.ai.llm_config import LLMCallBudget

    provider = ScriptedProvider([])
    events = []
    client = LLMClient(model_version="offline", client=provider, on_attempt=events.append,
                       budget=LLMCallBudget(max_input_chars=80))
    messages = [{"role": "user", "content": "反证" * 100}]
    with pytest.raises(LLMProviderError, match="^LLM input exceeds the configured budget$"):
        client.chat_json(messages)
    assert provider.calls == []
    assert events == []
    assert messages[0]["content"] == "反证" * 100


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
def test_exactly_one_explicit_output_cap_is_forwarded(parameter):
    from app.ai.llm_config import LLMCallBudget

    provider = ScriptedProvider([completion()])
    LLMClient(model_version="offline", client=provider, output_token_parameter=parameter,
              budget=LLMCallBudget(max_output_tokens=300)).chat_json([])
    assert provider.calls[0][parameter] == 300
    other = "max_tokens" if parameter == "max_completion_tokens" else "max_completion_tokens"
    assert other not in provider.calls[0]


def test_call_and_operation_budgets_only_tighten_global_budget():
    from app.ai.llm_config import LLMCallBudget

    provider = ScriptedProvider([completion(), completion()])
    client = LLMClient(model_version="offline", client=provider,
                       budget=LLMCallBudget(max_output_tokens=300),
                       operation_budgets={"role": LLMCallBudget(max_output_tokens=200)})
    client.chat_json([], "role", budget=LLMCallBudget(max_output_tokens=1000))
    client.chat_json([], "role", budget=LLMCallBudget(max_output_tokens=100))
    assert [call["max_completion_tokens"] for call in provider.calls] == [200, 100]


def test_oversized_response_fails_before_schema_validation():
    from app.ai.llm_config import LLMCallBudget

    validated = []
    provider = ScriptedProvider([completion('{"text": "' + "反" * 100 + '"}')])
    client = LLMClient(model_version="offline", client=provider,
                       budget=LLMCallBudget(max_response_chars=50))
    with pytest.raises(LLMMalformedResponseError):
        client.chat_json([], validator=validated.append)
    assert validated == []


def test_optional_schema_validator_can_reject_and_success_preserves_dict():
    from pydantic import BaseModel, ConfigDict

    class ValidResult(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        ok: bool

    provider = ScriptedProvider([completion(), completion('{"ok": "sentinel-private"}')])
    client = LLMClient(model_version="offline", client=provider)
    assert client.chat_json([], validator=ValidResult.model_validate) == {"ok": True}
    with pytest.raises(LLMMalformedResponseError) as exc:
        client.chat_json([], validator=ValidResult.model_validate)
    assert "sentinel" not in str(exc.value)


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)


def status_error(code=429, headers=None):
    response = httpx.Response(code, headers=headers,
                              request=httpx.Request("POST", "https://provider.invalid/v1"))
    return APIStatusError("sentinel-private-provider-detail", response=response, body=None)


def clock_client(provider, clock, **kwargs):
    return LLMClient(model_version="offline", client=provider, clock=clock.now,
                     sleep=clock.sleep, random_value=lambda: 0.5,
                     wall_clock=lambda: 1_000_000, **kwargs)


def test_retry_after_is_capped_and_attempts_are_metered_individually():
    clock, events = FakeClock(), []
    success = completion(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    success._request_id = "req-ok"
    provider = ScriptedProvider([status_error(headers={"retry-after": "99999", "x-request-id": "req-failed"}), success])
    result = clock_client(provider, clock, on_attempt=events.append,
                           retry_after_max_seconds=2, deadline_seconds=5).chat_json([], "role")
    assert result == {"ok": True}
    assert clock.sleeps == [2]
    assert len(events) == 2
    assert events[0].call_id == events[1].call_id
    assert [event.attempt for event in events] == [1, 2]
    assert events[0].provider_request_id == "req-failed"
    assert events[0].usage_status == "unknown"
    assert events[0].prompt_tokens is None
    assert events[0].retryable is True
    assert events[0].retry_delay_seconds == 2
    assert events[1].requested_model == "offline"
    assert events[1].model == "actual-model-v1"
    assert events[1].provider_request_id == "req-ok"
    assert events[1].total_tokens == 15
    assert events[1].usage_status == "known"
    assert events[1].outcome == "succeeded"
    assert events[1].repaired is False
    assert "sentinel" not in repr(events)


@pytest.mark.parametrize("headers, expected", [
    ({"retry-after": "1.25"}, 1.25),
    ({"retry-after-ms": "1500"}, 1.5),
    ({"retry-after": "Mon, 12 Jan 1970 13:46:42 GMT"}, 2),
    ({"retry-after": "NaN"}, 0.75),
    ({"retry-after": "-3"}, 0.75),
    ({"retry-after": "broken"}, 0.75),
])
def test_retry_after_formats_and_fallback(headers, expected):
    clock = FakeClock()
    provider = ScriptedProvider([status_error(headers=headers), completion()])
    clock_client(provider, clock, backoff_base_seconds=1).chat_json([])
    assert clock.sleeps == [expected]


def test_retry_deadline_does_not_start_another_request_or_sleep_past_budget():
    clock, events = FakeClock(), []
    provider = ScriptedProvider([status_error(headers={"retry-after": "10"})])
    with pytest.raises(LLMProviderError, match="^LLM operation deadline exceeded$"):
        clock_client(provider, clock, deadline_seconds=2, on_attempt=events.append).chat_json([])
    assert len(provider.calls) == 1
    assert clock.sleeps == []
    assert len(events) == 1


def test_per_attempt_timeout_is_clamped_to_remaining_budget():
    clock = FakeClock()

    def slow_failure():
        clock.advance(2)
        raise status_error()

    provider = ScriptedProvider([slow_failure, completion()])
    clock_client(provider, clock, deadline_seconds=5, timeout_seconds=4,
                  backoff_base_seconds=1).chat_json([])
    assert [call["timeout"] for call in provider.calls] == [4, 2.25]


def test_late_response_is_rejected_but_usage_is_not_lost():
    clock, events = FakeClock(), []

    def late_response():
        clock.advance(3)
        return completion(usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})

    provider = ScriptedProvider([late_response])
    with pytest.raises(LLMProviderError, match="^LLM operation deadline exceeded$"):
        clock_client(provider, clock, deadline_seconds=2, on_attempt=events.append).chat_json([])
    assert len(events) == 1
    assert events[0].total_tokens == 3
    assert events[0].latency_ms == 3000
    assert events[0].outcome == "deadline_exceeded"


def test_validation_failure_is_metered_with_unknown_usage_and_is_not_retried():
    events = []
    provider = ScriptedProvider([completion(finish_reason="length")])
    with pytest.raises(LLMMalformedResponseError):
        LLMClient(model_version="offline", client=provider, on_attempt=events.append).chat_json([])
    assert len(events) == 1
    assert events[0].finish_reason == "length"
    assert events[0].usage_status == "unknown"
    assert events[0].total_tokens is None
    assert events[0].outcome == "malformed_response"


def test_callback_context_is_scoped_and_does_not_leak_to_next_call():
    outer, inner = [], []
    provider = ScriptedProvider([completion(), completion(), completion(), completion()])
    client = LLMClient(model_version="offline", client=provider)
    with client.capture_attempts(outer.append):
        client.chat_json([], "first")
        with client.capture_attempts(inner.append):
            client.chat_json([], "rewrite")
        client.chat_json([], "last")
    client.chat_json([], "uncaptured")
    assert [event.operation for event in outer] == ["first", "rewrite", "last"]
    assert [event.operation for event in inner] == ["rewrite"]
    assert len({event.call_id for event in outer}) == 3


def test_callback_failure_aborts_retry_with_safe_error():
    clock = FakeClock()
    provider = ScriptedProvider([status_error(), completion()])

    def broken_sink(event):
        raise RuntimeError("sentinel-private-ledger-error")

    with pytest.raises(LLMProviderError, match="^LLM attempt recording failed$"):
        clock_client(provider, clock, on_attempt=broken_sink).chat_json([])
    assert len(provider.calls) == 1
    assert clock.sleeps == []


def test_mock_attempts_have_unknown_usage_and_do_not_touch_provider():
    events = []
    client = LLMClient(model_version="mock-offline", mock=True, on_attempt=events.append)
    assert client.chat_json([{"role": "user", "content": '{"texts": ["risk"]}'}], "rewrite")
    assert len(events) == 1
    assert events[0].outcome == "mock"
    assert events[0].usage_status == "unknown"
    assert events[0].total_tokens is None


def test_mock_call_also_checks_deadline_after_callback():
    clock = FakeClock()
    client = LLMClient(model_version="mock-offline", mock=True, deadline_seconds=1,
                       clock=clock.now, on_attempt=lambda _: clock.advance(2))
    with pytest.raises(LLMProviderError, match="^LLM operation deadline exceeded$"):
        client.chat_json([{"role": "user", "content": '{"texts": ["risk"]}'}], "rewrite")


def test_injected_real_sdk_disables_implicit_retries_and_records_response_id():
    requests, events = [], []

    def respond(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "sentinel-private"}},
                              headers={"x-request-id": "req-429"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        sdk = OpenAI(api_key="offline-test", base_url="https://provider.invalid/v1", http_client=transport,
                     max_retries=4)
        client = LLMClient(model_version="offline", client=sdk, max_attempts=1, on_attempt=events.append)
        with pytest.raises(LLMProviderError):
            client.chat_json([])
    assert len(requests) == 1
    assert events[0].provider_request_id == "req-429"


def test_real_sdk_success_records_nullable_usage_and_actual_wire_cap():
    import json

    requests, events = [], []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion().model_dump(), headers={"x-request-id": "req-success"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        sdk = OpenAI(api_key="offline-test", base_url="https://provider.invalid/v1", http_client=transport)
        result = LLMClient(model_version="offline", client=sdk, on_attempt=events.append).chat_json([])
    assert result == {"ok": True}
    assert json.loads(requests[0].content)["max_completion_tokens"] == 8192
    assert events[0].provider_request_id == "req-success"
    assert events[0].usage_status == "unknown"
    assert events[0].total_tokens is None


@pytest.mark.parametrize("field,value", [
    ("max_input_chars", True), ("max_input_chars", 0), ("max_input_chars", 1_000_001),
    ("max_output_tokens", 1.5), ("max_output_tokens", True), ("max_output_tokens", 65_537),
    ("max_response_chars", 0), ("max_response_chars", float("inf")),
])
def test_call_budgets_reject_invalid_or_unbounded_values(field, value):
    from app.ai.llm_config import LLMCallBudget

    with pytest.raises(ValueError):
        LLMCallBudget(**{field: value})


@pytest.mark.parametrize("messages", [None, "not messages", [{}], [{"role": "user", "content": None}],
                                      [{"role": "administrator", "content": "text"}],
                                      [{"role": "user", "content": "text", "extra": float("nan")} ]])
def test_invalid_text_messages_fail_before_provider(messages):
    provider = ScriptedProvider([])
    with pytest.raises(LLMProviderError, match="^LLM input is invalid$"):
        LLMClient(model_version="offline", client=provider).chat_json(messages)
    assert provider.calls == []


@pytest.mark.parametrize("usage, expected, status", [
    ({"prompt_tokens": 4}, (4, None, None), "partial"),
    ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, (0, 0, 0), "known"),
    ({"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "14"}, (None, None, None), "unknown"),
])
def test_usage_never_coerces_missing_or_invalid_numbers_to_zero(usage, expected, status):
    from openai.types.completion_usage import CompletionUsage

    response, events = completion(), []
    response.usage = CompletionUsage.model_construct(**usage)
    LLMClient(model_version="offline", client=ScriptedProvider([response]), on_attempt=events.append).chat_json([])
    assert (events[0].prompt_tokens, events[0].completion_tokens, events[0].total_tokens) == expected
    assert events[0].usage_status == status


def test_schema_failure_callback_is_once_and_preserves_usage():
    response, events = completion(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}), []
    client = LLMClient(model_version="offline", client=ScriptedProvider([response]), on_attempt=events.append)
    with client.capture_attempts(events.append), pytest.raises(LLMMalformedResponseError):
        client.chat_json([], validator=lambda _: False, on_attempt=events.append)
    assert len(events) == 1
    assert events[0].outcome == "schema_error"
    assert events[0].total_tokens == 2


def test_attempt_context_is_isolated_between_concurrent_contexts():
    from contextvars import Context

    a, b = [], []
    client = LLMClient(model_version="offline", client=ScriptedProvider([completion(), completion()]))

    def run_b():
        with client.capture_attempts(b.append):
            client.chat_json([], "b")

    with client.capture_attempts(a.append):
        Context().run(run_b)
        client.chat_json([], "a")
    assert [event.operation for event in a] == ["a"]
    assert [event.operation for event in b] == ["b"]


@pytest.mark.parametrize("random_value, expected", [(0, [0.5, 1, 1.5]), (1, [1, 2, 3])])
def test_exponential_jitter_is_bounded(random_value, expected):
    clock = FakeClock()
    provider = ScriptedProvider([status_error(), status_error(), status_error(), completion()])
    client = LLMClient(model_version="offline", client=provider, max_attempts=4,
                       clock=clock.now, sleep=clock.sleep, random_value=lambda: random_value,
                       backoff_base_seconds=1, backoff_max_seconds=3)
    client.chat_json([])
    assert clock.sleeps == expected


def test_deadline_is_rechecked_after_attempt_callback_before_retry():
    clock = FakeClock()
    provider = ScriptedProvider([status_error()])
    with pytest.raises(LLMProviderError, match="^LLM operation deadline exceeded$"):
        clock_client(provider, clock, deadline_seconds=1,
                      on_attempt=lambda _: clock.advance(2)).chat_json([])
    assert len(provider.calls) == 1
    assert clock.sleeps == []


def test_from_env_wires_finite_budgets_and_provider_compatibility(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("LLM_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_DEADLINE_SECONDS", "12.5")
    monkeypatch.setenv("LLM_MAX_INPUT_CHARS", "100")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "256")
    monkeypatch.setenv("LLM_MAX_RESPONSE_CHARS", "1024")
    monkeypatch.setenv("LLM_OUTPUT_TOKEN_PARAMETER", "max_tokens")
    client = LLMClient.from_env()
    assert client._retry.deadline_seconds == 12.5
    assert client._budget.max_input_chars == 100
    assert client._budget.max_output_tokens == 256
    assert client._budget.max_response_chars == 1024
    assert client._output_token_parameter == "max_tokens"


def test_invalid_env_is_reported_safely_before_sdk_creation(monkeypatch):
    import openai

    created = []
    monkeypatch.setenv("LLM_API_KEY", "offline-key")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "sentinel-private-invalid-value")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: created.append(kwargs))
    with pytest.raises(ValueError, match="^LLM_MAX_ATTEMPTS must be a valid number$"):
        LLMClient.from_env()
    assert created == []
