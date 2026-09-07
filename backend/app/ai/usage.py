"""Context-local provider usage, captured without prompts or provider errors."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy


_attempts: ContextVar[list[dict] | None] = ContextVar("llm_usage_attempts", default=None)


@contextmanager
def capture_usage():
    token = _attempts.set([])
    try:
        yield
    finally:
        _attempts.reset(token)


def current_usage() -> dict | None:
    attempts = _attempts.get()
    return None if attempts is None else {"schema_version": "llm_usage.v1", "attempts": deepcopy(attempts)}


def record_attempt(response=None, *, outcome: str):
    attempts = _attempts.get()
    if attempts is None:
        return
    usage = getattr(response, "usage", None)
    values = [getattr(usage, key, None) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
    valid = all(type(value) is int and 0 <= value <= 2**63 - 1 for value in values)
    valid = valid and values[0] + values[1] == values[2]
    attempts.append({
        "outcome": outcome,
        "usage_state": "reported" if valid else "unavailable",
        "prompt_tokens": values[0] if valid else None,
        "completion_tokens": values[1] if valid else None,
        "total_tokens": values[2] if valid else None,
    })
