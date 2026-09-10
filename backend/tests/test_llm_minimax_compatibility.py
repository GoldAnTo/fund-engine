"""Use MiniMax's documented separated reasoning without weakening JSON parsing."""
import pytest

from app.ai.client import LLMClient
from tests.test_llm_production import ScriptedProvider, completion


@pytest.mark.parametrize('base', ['https://api.minimaxi.com/v1', 'https://api.minimax.io/v1', 'https://api.minimax.cn/v1'])
def test_minimax_environment_requests_separated_reasoning(monkeypatch, base):
    provider = ScriptedProvider([completion()])
    monkeypatch.setenv('LLM_API_KEY', 'offline-key')
    monkeypatch.setenv('LLM_BASE_URL', base)
    monkeypatch.setenv('LLM_MODEL', 'MiniMax-M2.7-highspeed')
    monkeypatch.delenv('LLM_REASONING_SPLIT', raising=False)
    monkeypatch.setattr('openai.OpenAI', lambda **kwargs: provider)
    client = LLMClient.from_env()
    assert client.chat_json([{'role': 'user', 'content': 'Return JSON'}]) == {'ok': True}
    assert provider.calls[0]['extra_body'] == {'reasoning_split': True}


def test_other_providers_do_not_receive_minimax_extension(monkeypatch):
    provider = ScriptedProvider([completion()])
    monkeypatch.setenv('LLM_API_KEY', 'offline-key')
    monkeypatch.setenv('LLM_BASE_URL', 'https://provider.invalid/v1')
    monkeypatch.delenv('LLM_REASONING_SPLIT', raising=False)
    monkeypatch.setattr('openai.OpenAI', lambda **kwargs: provider)
    LLMClient.from_env().chat_json([{'role': 'user', 'content': 'Return JSON'}])
    assert 'extra_body' not in provider.calls[0]


def test_reasoning_split_setting_rejects_ambiguous_configuration(monkeypatch):
    monkeypatch.setenv('LLM_REASONING_SPLIT', 'sometimes')
    with pytest.raises(ValueError, match='LLM_REASONING_SPLIT'):
        LLMClient.from_env()
