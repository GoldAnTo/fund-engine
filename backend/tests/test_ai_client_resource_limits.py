import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ai.client import LLMClient, LLMProviderError


def make_client(content='{}', **kwargs):
    create = MagicMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        finish_reason='stop', message=SimpleNamespace(content=content, refusal=None))]))
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return LLMClient(model_version='test', client=sdk, **kwargs), create


def test_oversized_utf8_input_rejected_before_provider():
    messages = [{'role': 'user', 'content': '汉' * 20}]
    size = len(json.dumps(messages, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    client, create = make_client(max_input_bytes=size-1)
    with pytest.raises(LLMProviderError, match='input exceeds configured size limit'):
        client.chat_json(messages)
    create.assert_not_called()
    client, create = make_client(max_input_bytes=size)
    assert client.chat_json(messages) == {}
    create.assert_called_once()


def test_oversized_response_rejected_before_json_repair(monkeypatch):
    repair = MagicMock(side_effect=AssertionError('repair must not run'))
    monkeypatch.setattr('json_repair.loads', repair)
    client, create = make_client('汉' * 20, max_response_bytes=59)
    with pytest.raises(LLMProviderError, match='response exceeds configured size limit'):
        client.chat_json([])
    create.assert_called_once()
    repair.assert_not_called()


def test_response_exact_limit_parses():
    client, _ = make_client('{"text":"汉"}', max_response_bytes=len('{"text":"汉"}'.encode('utf-8')))
    assert client.chat_json([]) == {'text':'汉'}


def test_invalid_unicode_response_uses_stable_provider_error():
    client, create = make_client('\ud800')
    with pytest.raises(LLMProviderError, match='invalid response'):
        client.chat_json([])
    create.assert_called_once()


def test_invalid_unicode_input_never_calls_provider():
    client, create = make_client()
    with pytest.raises(LLMProviderError, match='invalid UTF-8'):
        client.chat_json([{'role': 'user', 'content': '\ud800'}])
    create.assert_not_called()


@pytest.mark.parametrize('name', ['max_input_bytes', 'max_response_bytes'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5, 100_000_000])
def test_invalid_resource_limits(name, value):
    with pytest.raises(ValueError):
        make_client(**{name:value})


def test_environment_config_applies_to_test_mock(monkeypatch):
    monkeypatch.setenv('LLM_MAX_INPUT_BYTES', '8')
    monkeypatch.setenv('LLM_MAX_RESPONSE_BYTES', '12')
    client = LLMClient.from_env()
    assert client._max_response_bytes == 12
    with pytest.raises(LLMProviderError):
        client.chat_json([{'role':'user', 'content':'oversized'}])


def test_output_token_budget_is_sent_on_every_attempt(monkeypatch):
    client, create = make_client(max_completion_tokens=4096)
    valid = create.return_value
    create.side_effect = [TimeoutError('private provider diagnostic'), valid]
    monkeypatch.setattr('app.ai.client.time.sleep', lambda _: None)
    assert client.chat_json([]) == {}
    assert create.call_count == 2
    assert all(call.kwargs['max_completion_tokens'] == 4096 for call in create.call_args_list)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, 131073])
def test_invalid_output_token_budget_is_rejected(value):
    with pytest.raises(ValueError, match='max_completion_tokens'):
        make_client(max_completion_tokens=value)


def test_output_token_budget_environment(monkeypatch):
    monkeypatch.setenv('LLM_MAX_COMPLETION_TOKENS', '3072')
    assert LLMClient.from_env()._max_completion_tokens == 3072


def test_default_output_token_budget_is_bounded():
    client, create = make_client()
    client.chat_json([])
    assert create.call_args.kwargs['max_completion_tokens'] == 16384
