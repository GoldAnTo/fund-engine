"""Finite resource policies shared by live and mock LLM calls.

Character limits count serialized Unicode characters, not billed tokens. No
tokenizer/model compatibility is inferred: the deployment selects the supported
Chat Completions output-limit parameter explicitly.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass


def bounded_number(name: str, value: object, low: float, high: float, *, positive: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not low <= value <= high or not math.isfinite(value)
            or (positive and value <= 0)):
        raise ValueError(f"{name} must be a finite number within its configured bounds")


def bounded_integer(name: str, value: object, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")


def env_number(name: str, default: float, *, integer: bool = False) -> int | float:
    try:
        return int(os.getenv(name, str(default))) if integer else float(os.getenv(name, str(default)))
    except (ValueError, OverflowError) as exc:
        # Environment values can contain secrets accidentally; never echo them.
        raise ValueError(f"{name} must be a valid number") from exc


@dataclass(frozen=True)
class LLMCallBudget:
    max_input_chars: int = 160_000
    max_output_tokens: int = 8192
    max_response_chars: int = 100_000

    def __post_init__(self) -> None:
        bounded_integer("max_input_chars", self.max_input_chars, 1, 1_000_000)
        bounded_integer("max_output_tokens", self.max_output_tokens, 1, 65_536)
        bounded_integer("max_response_chars", self.max_response_chars, 1, 1_000_000)

    def tighten(self, other: LLMCallBudget) -> LLMCallBudget:
        if not isinstance(other, LLMCallBudget):
            raise TypeError("budget must be an LLMCallBudget")
        return LLMCallBudget(
            max_input_chars=min(self.max_input_chars, other.max_input_chars),
            max_output_tokens=min(self.max_output_tokens, other.max_output_tokens),
            max_response_chars=min(self.max_response_chars, other.max_response_chars),
        )


@dataclass(frozen=True)
class LLMRetryPolicy:
    timeout_seconds: float = 90.0
    max_attempts: int = 2
    deadline_seconds: float = 180.0
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8.0
    retry_after_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        bounded_integer("max_attempts", self.max_attempts, 1, 5)
        bounded_number("timeout_seconds", self.timeout_seconds, 0, 300, positive=True)
        bounded_number("deadline_seconds", self.deadline_seconds, 0, 600, positive=True)
        bounded_number("backoff_base_seconds", self.backoff_base_seconds, 0, 30)
        bounded_number("backoff_max_seconds", self.backoff_max_seconds, 0, 60)
        bounded_number("retry_after_max_seconds", self.retry_after_max_seconds, 0, 60)


# These are operation ceilings, further intersected with deployment/call budgets.
DEFAULT_OPERATION_BUDGETS = {
    "extract": LLMCallBudget(max_input_chars=140_000),
    "propose": LLMCallBudget(max_input_chars=120_000, max_output_tokens=4096),
    "assess": LLMCallBudget(max_input_chars=120_000, max_output_tokens=4096),
    "rewrite": LLMCallBudget(max_input_chars=32_000, max_output_tokens=2048),
    "preparation_parse_claims": LLMCallBudget(),
    "preparation_draft_protocol": LLMCallBudget(),
    "preparation_draft_evidence_plan": LLMCallBudget(),
}
