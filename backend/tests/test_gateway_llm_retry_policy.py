"""Offline retry policy checks; never instantiate a network provider."""
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, OpenAIError
from openai.types.chat import ChatCompletion

from app.ai.client import LLMClient, LLMProviderError


class FailingProvider:
    def __init__(self, error, *, recover=False):
        self.error = error
        self.recover = recover
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        if self.recover and self.calls > 1:
            return ChatCompletion.model_validate({
                "id": "chatcmpl-offline", "created": 0, "model": "offline",
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": '{"ok": true}', "refusal": None,
                }}],
            })
        raise self.error


def status_error(code, kind):
    response = httpx.Response(code, request=httpx.Request("POST", "https://provider.invalid/v1"))
    if kind == "sdk":
        return APIStatusError("sentinel-private-provider-detail", response=response, body=None)
    return httpx.HTTPStatusError("sentinel-private-provider-detail", request=response.request, response=response)


@pytest.mark.parametrize("kind", ["sdk", "httpx"])
@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_permanent_status_failure_is_not_retried(code, kind):
    provider = FailingProvider(status_error(code, kind))
    client = LLMClient(model_version="offline", client=provider, max_attempts=3)
    with pytest.raises(LLMProviderError, match="^LLM provider request failed$"):
        client.chat_json([])
    assert provider.calls == 1


@pytest.mark.parametrize("kind", ["sdk", "httpx"])
@pytest.mark.parametrize("code", [408, 409, 429, 500, 503])
def test_transient_status_can_recover_within_budget(code, kind):
    provider = FailingProvider(status_error(code, kind), recover=True)
    client = LLMClient(model_version="offline", client=provider, max_attempts=3)
    assert client.chat_json([]) == {"ok": True}
    assert provider.calls == 2


@pytest.mark.parametrize("error", [
    APIConnectionError(request=httpx.Request("POST", "https://provider.invalid/v1")),
    httpx.ConnectError("offline"), TimeoutError("offline"), ConnectionError("offline"),
])
def test_transport_failure_stops_at_attempt_budget(error):
    provider = FailingProvider(error)
    client = LLMClient(model_version="offline", client=provider, max_attempts=2)
    with pytest.raises(LLMProviderError, match="^LLM provider request failed$"):
        client.chat_json([])
    assert provider.calls == 2


def test_unknown_sdk_error_is_not_assumed_transient():
    provider = FailingProvider(OpenAIError("sentinel-private-provider-detail"))
    client = LLMClient(model_version="offline", client=provider, max_attempts=3)
    with pytest.raises(LLMProviderError, match="^LLM provider request failed$"):
        client.chat_json([])
    assert provider.calls == 1
