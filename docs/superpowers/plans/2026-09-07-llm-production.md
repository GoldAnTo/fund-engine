# LLM Production Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make existing synchronous JSON model calls bounded, strict, measurable per attempt, and safe to expose through the research workflow.

**Architecture:** Keep `LLMClient.chat_json(messages, schema_hint="") -> dict`. A small validated configuration module owns resource limits; the client owns transport retry and strict response validation. An immutable attempt record and context-local callback let the role ledger capture every request, including retries and rewrites, without storing raw prompts, responses, or upstream errors.

**Tech Stack:** Python 3.13 local runtime, OpenAI Python SDK 3.8.0, httpx, dataclasses, ContextVar, pytest; all verification is offline.

---

User authorization: the parent task confirms the user approved implementing the audit recommendations as production work, so this plan is executed immediately. Existing dirty work is preserved. No whole-tree commit, database migration, or paid request is part of this subtask.

## Public contract and design decisions

```python
client.chat_json(
    messages,
    schema_hint="assess",
    validator=AssessmentSchema.model_validate,
    on_attempt=attempts.append,
    budget=LLMCallBudget(max_input_chars=20000, max_output_tokens=2048),
)
with client.capture_attempts(attempts.append):
    existing_engine.generate(...)
```

The validator is optional, receives the decoded object, and raises `ValueError`/`TypeError` on schema rejection; an explicit `False` also rejects. Its successful result is ignored, preserving dict callers. Input and response limits count serialized Unicode characters, not estimated billed tokens. The output cap is sent as exactly one explicitly configured compatible parameter (`max_completion_tokens` by default, or `max_tokens`). Operation limits and per-call limits can only tighten the configured global budget. Oversize evidence is rejected, never silently truncated.

`LLMAttempt` fields: `call_id`, `attempt` (one-based), `operation`, `provider`, `requested_model`, `model`, `provider_request_id`, `finish_reason`, nullable `prompt_tokens`/`completion_tokens`/`total_tokens`, `usage_status` (`known`, `partial`, `unknown`), `latency_ms`, `outcome`, `retryable`, `retry_delay_seconds`, `repaired=False`. All fields are metadata; no provider error text or body is included. Every started SDK attempt emits exactly once, even if parsing/schema validation fails. Callbacks run synchronously; failed persistence aborts further work with a safe boundary error. Mock responses emit a mock outcome with unknown usage. Input rejection emits no attempt because no request was started.

Retry eligibility remains connection/transport/timeout plus HTTP 408/409/429/5xx. Retry count is bounded to 1–5; finite timeout/deadline and bounded exponential backoff with jitter are configured separately. Retry-After seconds, HTTP date, and retry-after-ms are bounded. The deadline covers admission, retry delay, response validation, and acceptance; each SDK timeout is clamped to remaining time. A synchronous httpx timeout cannot forcibly interrupt every in-flight phase, so this is not advertised as a hard wall-clock cancellation guarantee. A returned response after deadline is rejected and still metered. No detached threads conceal ongoing paid work.

JSON protocol is strict: one nonempty object, no repair or extraction from prose/fences/thinking text, no duplicate keys, nonfinite numbers, arrays, malformed/truncated JSON, refusal, or non-stop completion. Existing downstream business validators remain necessary for evidence/authorization checks.

## Files

- Modify `backend/app/ai/client.py`: preserve mock helpers; implement strict caller boundary and immutable attempt metadata.
- Create `backend/app/ai/llm_config.py`: immutable finite configuration and resource budgets.
- Create `backend/tests/test_llm_production.py`: real SDK models plus injected clock and provider fixtures.
- Update existing client test fixtures only if required to reflect genuine SDK fields (`finish_reason`, `refusal`, `model`, `usage`). No business caller/schema/migration changes.

### Task 1: Strict JSON completion acceptance

- [x] Write tests using `openai.types.chat.ChatCompletion`:

```python
@pytest.mark.parametrize("content", ['{"ok":true', '```json\n{"ok":true}\n```',
                                   '{"ok":true}{"other":1}', '{}', '[]',
                                   '{"ok": NaN}', '{"ok":1,"ok":2}'])
def test_rejects_incomplete_or_ambiguous_object(content):
    provider = ScriptedProvider([completion(content)])
    with pytest.raises(LLMMalformedResponseError):
        LLMClient(model_version="offline", client=provider).chat_json([])
    assert len(provider.calls) == 1
```

- [x] Run `cd backend && env -u LLM_API_KEY -u TEST_DATABASE_URL -u NEO4J_URL APP_ENV=test DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests/test_llm_production.py -q`; confirm failures show the existing permissive parser accepts invalid/incomplete output.
- [x] Require `finish_reason == "stop"`, absent refusal, string content, bounded content, strict `json.loads` with duplicate-key/nonfinite rejection, and a nonempty dict. Wrap only validation errors in `LLMMalformedResponseError` with the existing fixed public message.
- [x] Rerun strict tests; confirm all pass and no provider calls are retried for malformed responses.

### Task 2: Finite config and input/output budgets

- [x] Write table-driven constructor failures for bool/float/unbounded attempts, NaN/Inf/negative timeout/deadline/temperature, bad seed, unsupported token parameter, and invalid budget values. Write oversized Unicode input tests asserting zero calls, and cap forwarding tests for both supported parameter names.

```python
@pytest.mark.parametrize("value", [True, 1.5, 0, 6])
def test_attempt_count_is_bounded_integer(value):
    with pytest.raises(ValueError):
        LLMClient(model_version="offline", max_attempts=value)
```

- [x] Run the tests and record expected missing validation failures.
- [x] Implement frozen `LLMCallBudget`, frozen `LLMRetryPolicy`, constructor/env validation, per-operation caps, and per-call cap intersection. Serialize with `ensure_ascii=False, allow_nan=False`; count the full request message JSON; reject before SDK invocation.
- [x] Rerun budget/config and strict tests, then existing determinism/retry tests.

### Task 3: Retry budget and attempt metadata

- [x] Write fake-clock tests for retry-after numeric/date/ms, jitter bounds, deadline exhaustion before next attempt, remaining timeout, late successful response rejection, permanent errors, unknown usage, partial usage, and callback isolation/failure.

```python
clock = FakeClock()
events = []
client = LLMClient(model_version="offline", client=provider,
                   deadline_seconds=3, clock=clock.now, sleep=clock.sleep,
                   random_value=lambda: 0.5, on_attempt=events.append)
```

- [x] Run and record failures for missing kwargs / callback / deadline behavior.
- [x] Emit frozen attempt records exactly once after each request outcome; safe numeric metadata only; missing usage stays None. Capture callbacks using a per-client ContextVar, restore on context exit, and suppress duplicate callback identity. Apply backoff with an injected random source, bound headers, recheck deadline after callbacks and sleeps, and disable SDK implicit retries even for an injected real SDK client.
- [x] Rerun tests, then real `OpenAI(http_client=httpx.Client(transport=MockTransport(...)))` tests to verify wire cap, SDK response IDs, and max_retries=0 without network access.

### Task 4: Integration verification and handoff

- [x] Run `env -u TEST_DATABASE_URL -u NEO4J_URL -u LLM_API_KEY DATABASE_URL=sqlite:// APP_ENV=test .venv/bin/python -m pytest tests/test_llm_production.py tests/test_gateway_llm_retry_policy.py tests/test_ai_client_determinism.py tests/test_ai_engine.py tests/test_research_preparation_generator.py tests/test_compliance.py tests/test_compliance_graph.py -q` in `backend`.
- [x] Inspect the owned-file diff and run `git diff --check` for the owned paths. Report exact red/green counts, interface fields, command, and remaining synchronous deadline/model compatibility boundaries to the parent task.
- [x] Mark this plan with actual results; do not commit unrelated shared changes.

## SDK evidence

Local SDK inspection: `openai/types/chat/chat_completion.py` defines completion choices and finish reasons; `chat_completion_message.py` defines refusal; `_models.py` exposes `_request_id`; `_client.py` provides `with_options(max_retries=0)`. The [official Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create) documents output caps, response completion states, and usage. This implementation retains the existing configured model and does not infer provider compatibility from model-name prefixes.


## Implemented configuration

All values validate before SDK construction. Global budgets intersect with operation and per-call budgets. Default operation ceilings are extract 140,000 characters / 8192 output tokens; propose and assess 120,000 / 4096; rewrite 32,000 / 2048; preparation 160,000 / 8192. These count the serialized messages, including system prompts and JSON escaping overhead. This is an input character budget; it is not a tokenizer or provider context-window guarantee.

| Environment variable | Default | Allowed range / values |
| --- | --- | --- |
| `LLM_TIMEOUT_SECONDS` | 90 | Finite number greater than 0, at most 300 |
| `LLM_MAX_ATTEMPTS` | 2 | Integer 1–5 |
| `LLM_DEADLINE_SECONDS` | 180 | Finite number greater than 0, at most 600 |
| `LLM_BACKOFF_BASE_SECONDS` | 0.5 | Finite number 0–30 |
| `LLM_BACKOFF_MAX_SECONDS` | 8 | Finite number 0–60 |
| `LLM_RETRY_AFTER_MAX_SECONDS` | 30 | Finite number 0–60 |
| `LLM_MAX_INPUT_CHARS` | 160000 | Integer 1–1000000 |
| `LLM_MAX_OUTPUT_TOKENS` | 8192 | Integer 1–65536 |
| `LLM_MAX_RESPONSE_CHARS` | 100000 | Integer 1–1000000 |
| `LLM_OUTPUT_TOKEN_PARAMETER` | `max_completion_tokens` | `max_completion_tokens` or `max_tokens` |
| `LLM_TEMPERATURE` | 0 | Finite number 0–2 |
| `LLM_SEED` | unset | Signed 64-bit integer |
| `LLM_PROVIDER` | `openai-compatible` | Safe provider identifier, no URL query or credentials |

The output field is explicit because compatible providers differ. No automatic retry with another cap parameter occurs on HTTP 400. Global strict parsing intentionally replaces permissive legacy repair; old fake response fixtures now use real SDK ChatCompletion models. Business caller return types and accepted complete JSON remain unchanged.

## Verification record (2026-09-07)

- Strict parser red: **15 failed, 4 passed**. Green: **19 passed**.
- Config, budgets, retry and telemetry red: **43 failed, 23 passed**. Green: **66 passed**.
- Adversarial numeric/config red: **3 failed, 91 passed** (JSON exponent overflow and enormous timeout integer); fixed with finite float parsing and range-first config checks.
- Mock deadline callback red: **1 failed**; fixed by rechecking deadline after callbacks for mock calls as for live calls.
- Final targeted + existing engine/preparation/compliance regression: **254 passed, 2 warnings**. The two warnings are existing Starlette anyio deprecation and SourceLocatorV1 schema field shadowing.
- Ruff on the two production files and three test files: **All checks passed**. Owned-path `git diff --check`: no whitespace errors.
- Offline fixtures include two real OpenAI SDK calls over httpx.MockTransport; they cannot contact a network provider. No paid requests, actual model-quality evaluation, PostgreSQL migration, production credential read, or persistence-layer changes were performed by this LLM boundary subtask.

Remaining boundaries: synchronous transport is not a hard wall-clock cancellation mechanism; output cap/provider compatibility and actual model research quality still require deployment-specific verification. Per-attempt hooks expose safe usage metadata but persistence and versioned pricing belong to the caller's ledger. Missing usage stays unknown, and no price is guessed.
