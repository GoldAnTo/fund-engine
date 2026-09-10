# MiniMax startup JSON protocol fix

Date: 2026-09-09

## Incident

The formal Docker startup flow for a Guizhou Moutai financial research request reached the live MiniMax-compatible LLM and failed at the strict LLM boundary with `malformed_response`, surfaced to the API as a 503 provider failure. The captured provider behavior was a normal `stop` completion whose assistant content wrapped an otherwise valid business JSON object in a Markdown `json` code fence.

The parser should keep rejecting that response shape. Accepting fenced JSON, stripping wrappers, repairing JSON, or retrying arbitrary malformed output would weaken the strict provider boundary and make malformed completions indistinguishable from valid protocol responses.

## Provider documentation checked

Primary MiniMax documentation checked on 2026-09-09:

- [OpenAI SDK](https://platform.minimaxi.com/docs/api-reference/text-openai-api)
- [Chat Completions API](https://platform.minimaxi.com/docs/api-reference/text-chat-openai.md)
- [M series prompting best practices](https://platform.minimaxi.com/docs/token-plan/prompting-best-practices)

Relevant findings:

- MiniMax documents `reasoning_split=True` for separating thinking from assistant `content`.
- MiniMax documents `max_completion_tokens`, `max_tokens`, `temperature`, `tools`, `thinking`, and `service_tier` for the OpenAI-compatible Chat Completions API.
- The checked MiniMax OpenAI-compatible Chat Completions docs do not list `response_format` / `json_object` as a documented request parameter, though the existing client already sends the OpenAI-compatible `response_format={"type":"json_object"}` and tests preserve it.
- MiniMax prompting best practices emphasize explicit task, constraints, and output contract wording for stable structured outputs.

## Fix

The fix is request-side only:

- `backend/app/ai/client.py` now copies outbound messages and adds a system-level transport contract requiring exactly one raw, nonempty JSON object, with no Markdown, no code block, no `json` fence, no `<think>` tag, and no text before or after the object.
- The existing strict parser remains unchanged: fenced JSON, `<think>` content in `content`, prose prefixes, duplicate keys, nonfinite numbers, empty objects, refusals, and non-`stop` finish reasons still fail closed.
- The existing OpenAI-compatible `response_format={"type":"json_object"}`, SDK `max_retries=0`, 8192 default output cap, bounded deadlines, and transient retry policy are retained.
- `backend/app/services/event_extraction.py` now states the same no-Markdown/no-code-fence JSON contract directly in the startup event extraction prompt.
- `backend/app/ai/prompts.py` now states the same raw JSON contract for atomic statement extraction, preventing the next LLM step in the research flow from hitting the same provider habit.

## Regression coverage

Added tests:

- `test_json_transport_contract_is_sent_without_mutating_caller_messages`
- `test_json_transport_contract_is_added_when_caller_has_no_system_message`
- Extended `test_live_prompt_defines_exact_json_keys_and_factor_shape` to require explicit no-Markdown/no-`json`-fence event extraction instructions.

Existing strict malformed-response coverage continues to assert that a fenced response such as:

````text
```json
{"ok": true}
```
````

raises `LLMMalformedResponseError`.

## Verification

Red checks:

- `backend/.venv/bin/python -m pytest backend/tests/test_llm_production.py -k 'json_transport_contract'` initially failed because the request messages were passed through without the transport contract.
- `backend/.venv/bin/python -m pytest backend/tests/test_event_extraction.py -k 'live_prompt_defines_exact_json_keys_and_factor_shape'` initially failed because the event extraction prompt did not mention Markdown/code fences.

Green checks:

```bash
env -u LLM_API_KEY -u TEST_DATABASE_URL -u NEO4J_URL DATABASE_URL=sqlite:// APP_ENV=test backend/.venv/bin/python -m pytest backend/tests/test_llm_production.py backend/tests/test_llm_minimax_compatibility.py backend/tests/test_gateway_llm_retry_policy.py backend/tests/test_event_extraction.py backend/tests/test_ai_client_determinism.py
```

Result: 199 passed, 1 existing Starlette deprecation warning.

The command requested with `.venv/bin/python` could not run in this worktree because the root-level `.venv` path does not exist. The worktree-local backend environment is `backend/.venv/bin/python`.

## Live provider probe

One bounded, non-mutating event-extraction probe used `.env.gateway.local` without printing secrets or raw provider content.

Sanitized result:

- Provider: `minimax`
- Requested model: `MiniMax-M2.7-highspeed`
- Actual model: `MiniMax-M2.7-highspeed`
- Finish reason: `stop`
- Usage status: known
- Tokens: prompt 390, completion 319, total 709
- Outcome: `succeeded`
- Extracted structure: company `贵州茅台`, input kind `topic`, event title present, 5 candidate factors
