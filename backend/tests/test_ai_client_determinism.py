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

import os
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ai.client import DEFAULT_TEMPERATURE, LLMClient


class _FakeCompletions:
    """Records the kwargs it was called with and returns a stub response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> MagicMock:
        self.calls.append(kwargs)
        # Mirror OpenAI's response shape enough for chat_json to extract content.
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = '{"conclusion": "supported"}'
        return response


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


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


class TestFromEnvReadsReproducibilityKnobs:
    """Pin that ``from_env`` wires env-driven reproducibility knobs through."""

    def test_from_env_with_no_key_runs_mock_with_knobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
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
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("LLM_SEED", "")
        client = LLMClient.from_env()
        assert client._seed is None

    def test_from_env_zero_seed_is_a_real_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The empty-string convention distinguishes "unset" from "0":
        ``LLM_SEED=0`` must pass 0 through, not be dropped to None."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("LLM_SEED", "0")
        client = LLMClient.from_env()
        assert client._seed == 0
