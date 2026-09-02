# Live Walkthrough Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish a live walkthrough without misclassifying historical absence or transient provider faults, while leaving true application defects visible.

**Architecture:** Provider clients retain finite per-attempt timeouts and expose only safe boundary errors. The walkthrough classifies a pre-creation historical 404 as an expected point-in-time observation. Extraction errors are first reproduced at the command boundary, then fixed only at the identified layer.

**Tech Stack:** FastAPI, SQLAlchemy, httpx, OpenAI-compatible SDK, pytest.

---

### Task 1: Reproduce and classify the live extraction 500

**Files:**
- Modify: `backend/tests/test_engine_commands_api.py`
- Inspect: `backend/app/ai/client.py`, `backend/app/ai/extraction.py`, `backend/app/api/v1/commands/engine.py`

- [ ] **Step 1: Write a failing test for a model candidate with a non-continuous quote.**

```python
def test_extract_discards_noncontinuous_llm_quote(...):
    monkeypatch.setattr(LLMClient, "chat_json", malformed_candidate)
    response = cmd_client.post(f"/api/v1/documents/{version_id}/extract")
    assert response.status_code == 201
```

- [ ] **Step 2: Run the targeted test and confirm the current 500.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_engine_commands_api.py -k malformed_provider`

- [ ] **Step 3: Catch the domain `ValidationError` beside other malformed candidate fields, so that one invalid model proposal is skipped.**

```python
except (KeyError, TypeError, ValueError, ValidationError):
    continue
```

- [ ] **Step 4: Re-run the targeted test and existing provider-error test.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_engine_commands_api.py -k 'noncontinuous or provider_failure'`

### Task 2: Add bounded transient transport retries

**Files:**
- Modify: `backend/app/ai/client.py`
- Modify: `backend/app/datasources/gildata/client.py`
- Modify: `backend/tests/test_ai_client_determinism.py`
- Modify: `backend/tests/test_gildata_client.py`

- [ ] **Step 1: Write failing tests that a first transient transport exception is retried once and a protocol failure is not retried.**

```python
assert fake.chat.completions.calls == 2
with pytest.raises(GildataMCPError):
    client.call_tool("FinQuery", {"query": "x"})
assert attempts == 1
```

- [ ] **Step 2: Run each new test and observe the un-retried failure.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_client_determinism.py backend/tests/test_gildata_client.py`

- [ ] **Step 3: Implement `LLM_MAX_ATTEMPTS` and `GILDATA_MAX_ATTEMPTS` as finite positive environment knobs (default 2), retrying only transport/provider exceptions.**

```python
for attempt in range(self._max_attempts):
    try:
        return call()
    except retryable_exception as exc:
        if attempt + 1 == self._max_attempts:
            raise safe_error from exc
```

- [ ] **Step 4: Re-run both focused suites.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_client_determinism.py backend/tests/test_gildata_client.py`

### Task 3: Make pre-creation historical absence an expected walkthrough result

**Files:**
- Modify: `backend/scripts/walkthrough_cambricon_case.py`
- Modify: `backend/tests/test_walkthrough_support.py`

- [ ] **Step 1: Write a failing pure helper test.**

```python
assert classify_historical_case_read(404, body) == "case_not_created_at_cutoff"
```

- [ ] **Step 2: Run it and confirm the helper is absent.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_support.py -k historical`

- [ ] **Step 3: Add the helper and have P11 store the expected observation without `issue(...)`; leave all other non-200 results as issues.**

- [ ] **Step 4: Re-run the walkthrough support test.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_walkthrough_support.py`

### Task 4: Verify the repaired flow

**Files:**
- Inspect: `docs/evaluation/walkthrough/cambricon_walkthrough_<run-id>_summary.json`

- [ ] **Step 1: Run unit/API regression coverage.**

Run: `backend/.venv/bin/python -m pytest -q backend/tests/test_ai_client_determinism.py backend/tests/test_gildata_client.py backend/tests/test_engine_commands_api.py backend/tests/test_walkthrough_support.py`

- [ ] **Step 2: Run the authenticated live walkthrough P0–P12 in its own run id.**

Run: `WALKTHROUGH_RUN_ID=<new-run> backend/.venv/bin/python backend/scripts/walkthrough_cambricon_case.py`

- [ ] **Step 3: Inspect the JSON summary: report successful stages, expected historical absence, and any remaining provider failures separately from code defects.**

- [ ] **Step 4: Run `git diff --check` and commit only the implementation files for this fix.**

### Task 5: Preserve the two human publication gates in the walkthrough

**Files:**
- Modify: `backend/app/scripts/walkthrough_support.py`
- Modify: `backend/scripts/walkthrough_cambricon_case.py`
- Modify: `backend/tests/test_walkthrough_support.py`

- [ ] **Step 1: Write failing tests for the atomic-claim and proposal-review payload contracts.**
- [ ] **Step 2: Run the focused support test and observe the missing helpers.**
- [ ] **Step 3: After P3, confirm all awaiting atomic-claim candidates; after P4, use the event review queue's immutable source-admission result to confirm publishable proposals and reject non-publishable ones. Record each gate in the audit summary.**
- [ ] **Step 4: Re-run the final live stages and verify P6 receives no proposal IDs; it may receive only legacy evidence links. Keep assessment review constrained to its frozen evidence snapshot.**
