from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.ai.client import LLMClient, LLMProviderError


def _client(error, **kwargs):
    completion = MagicMock()
    completion.create.side_effect = error
    return LLMClient(model_version="test", client=SimpleNamespace(
        chat=SimpleNamespace(completions=completion)
    ), **kwargs), completion


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_http_failure_is_never_retried(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://invalid.test"))
    error = httpx.HTTPStatusError("private upstream body", request=response.request, response=response)
    client, completion = _client(error)
    with pytest.raises(LLMProviderError):
        client.chat_json([])
    assert completion.create.call_count == 1


def test_retry_after_exceeding_total_budget_does_not_retry():
    response = httpx.Response(429, headers={"Retry-After": "30"}, request=httpx.Request("POST", "https://invalid.test"))
    error = httpx.HTTPStatusError("limited", request=response.request, response=response)
    client, completion = _client(error, retry_budget_seconds=2)
    with pytest.raises(LLMProviderError):
        client.chat_json([])
    assert completion.create.call_count == 1


def test_transient_retry_backs_off_and_shares_remaining_budget(monkeypatch):
    from app.ai import client as module
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(module.time, "sleep", sleep)
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}", refusal=None))])
    client, completion = _client([httpx.ConnectError("offline"), response], retry_budget_seconds=2)
    assert client.chat_json([]) == {}
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1
    assert completion.create.call_args_list[0].kwargs["timeout"] == 2
    assert completion.create.call_args_list[1].kwargs["timeout"] == pytest.approx(2-sleeps[0])


@pytest.mark.parametrize("kwargs", [
    {"temperature": float("nan")}, {"temperature": 3},
    {"max_attempts": 100}, {"max_attempts": 1.5}, {"max_attempts": True},
    {"timeout_seconds": 10000}, {"retry_budget_seconds": float("inf")},
    {"model_version": " "},
])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        LLMClient(**{"model_version": "test", **kwargs})


def test_late_sync_result_is_rejected_without_claiming_hard_cancellation(monkeypatch):
    from app.ai import client as module
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def slow_response(**kwargs):
        clock[0] = 3.0
        return SimpleNamespace(choices=[])
    client, completion = _client(slow_response, retry_budget_seconds=2)
    with pytest.raises(LLMProviderError, match="LLM provider request failed"):
        client.chat_json([])
    assert completion.create.call_count == 1


@pytest.mark.parametrize("status,expected_calls", [(401, 1), (429, 2), (503, 2)])
def test_openai_status_errors_use_the_same_retry_classification(monkeypatch, status, expected_calls):
    from openai import APIStatusError
    from app.ai import client as module
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    response = httpx.Response(status, request=httpx.Request("POST", "https://invalid.test"))
    client, completion = _client(APIStatusError("private", response=response, body=None))
    with pytest.raises(LLMProviderError):
        client.chat_json([])
    assert completion.create.call_count == expected_calls


def test_logical_operation_shares_budget_and_resets_after_exit(monkeypatch):
    from app.ai import client as module
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}", refusal=None))])
    client, completion = _client([response, response, response], retry_budget_seconds=2)
    with client.operation_budget():
        assert client.chat_json([]) == {}
        clock[0] = 1.5
        assert client.chat_json([]) == {}
        assert completion.create.call_args.kwargs["timeout"] == 0.5
    clock[0] = 10
    assert client.chat_json([]) == {}
    assert completion.create.call_args.kwargs["timeout"] == 2
