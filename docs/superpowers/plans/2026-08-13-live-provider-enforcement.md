# Live Provider Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require real LLM and Gildata providers for every non-test runtime while preserving explicitly isolated automated tests.

**Architecture:** Make `LLMClient.from_env()` a closed two-mode factory: `APP_ENV=test` may construct the deterministic mock, while every other environment requires an API key and constructs the OpenAI-compatible client. Keep Gildata's existing fail-closed factory, activate production policy in the gitignored local env, and verify both providers plus one real isolated AI-engine run without touching the application database.

**Tech Stack:** Python 3, pytest, OpenAI-compatible Python SDK, httpx JSON-RPC, SQLAlchemy/SQLite

---

### Task 1: Make non-test LLM construction fail closed

**Files:**
- Modify: `backend/tests/test_compliance.py`
- Modify: `backend/tests/test_ai_client_determinism.py`
- Modify: `backend/app/ai/client.py`
- Modify: `backend/app/scripts/run_ai_engine.py`

- [x] **Step 1: Write the failing provider-policy tests**

Replace the development-mock expectation with explicit closed-mode coverage:

```python
@pytest.mark.parametrize("app_env", [None, "", "development", "production"])
def test_non_test_runtime_without_api_key_fails_loudly(monkeypatch, app_env):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        LLMClient.from_env()


def test_test_runtime_without_api_key_uses_deterministic_mock(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    client = LLMClient.from_env()
    assert client._mock is True
    assert client.model_version.startswith("mock-")
```

Change the three no-key reproducibility tests in
`test_ai_client_determinism.py` to set `APP_ENV=test`; their purpose is seed
and temperature plumbing, not development fallback.

- [x] **Step 2: Run the provider-policy tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_compliance.py::test_non_test_runtime_without_api_key_fails_loudly \
  tests/test_compliance.py::test_test_runtime_without_api_key_uses_deterministic_mock
```

Expected: non-test cases for unset/development fail because the current client
still creates `mock-*` clients there; the explicit test case passes.

- [x] **Step 3: Implement the minimal closed-mode factory**

In `LLMClient.from_env()`, allow the no-key mock branch only when
`APP_ENV.strip().lower() == "test"`. For every other value raise:

```python
raise RuntimeError(
    "LLM_API_KEY is required outside APP_ENV=test; "
    "live runtimes never fall back to mock output"
)
```

Update the module/class/factory and `run_ai_engine` usage text so they describe
mock mode as test-only and live credentials as required for CLI execution.

- [x] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest -q \
  tests/test_compliance.py \
  tests/test_ai_client_determinism.py \
  tests/test_ai_engine.py \
  tests/test_research_worker_entrypoint.py
```

Expected: all tests pass without network access.

- [x] **Step 5: Commit the provider policy**

```bash
git add \
  backend/app/ai/client.py \
  backend/app/scripts/run_ai_engine.py \
  backend/tests/test_compliance.py \
  backend/tests/test_ai_client_determinism.py
git commit -m "fix: require live llm outside tests"
```

### Task 2: Activate and verify local live configuration

**Files:**
- Modify locally, never stage: `backend/.env`

- [x] **Step 1: Set the local runtime policy without exposing secrets**

Set or replace only this key in the gitignored local file:

```dotenv
APP_ENV=production
```

Do not print, stage, or rewrite the LLM/Gildata credential values. Confirm only
the presence of the required key names with a redacted script.

- [x] **Step 2: Verify factories construct live clients without network calls**

From `backend`, load `.env`, construct both clients, and print only:

```text
llm_mode=external
llm_model=<configured model identifier>
gildata_client=configured
```

Expected: the model version has no `mock-` prefix and Gildata construction does
not raise.

### Task 3: Perform controlled real-provider acceptance

**Files:**
- No repository files modified; use a temporary SQLite database and ephemeral validation scripts.

- [x] **Step 1: Send one minimal real LLM request**

Load `backend/.env`, construct `LLMClient.from_env()`, and call `chat_json` with
a non-sensitive instruction to return `{"status": "ok"}`. Assert a dictionary
is returned and report only the configured model and returned key names.

- [x] **Step 2: Validate real Gildata MCP access**

Construct `GildataMCPClient.from_env()` in a context manager. Call
`list_tools()` and assert `FinQuery` is available. Then call:

```python
client.call_tool(
    "FinQuery",
    {"query": "查询贵州茅台600519最新交易日、收盘价和交易日期"},
)
```

Parse the inner JSON, require a success code and a non-empty results list, and
report only tool names, result count, and validation status. Never print the
licensed payload or request URL.

- [x] **Step 3: Run a real isolated AI-engine workflow**

Create a temporary directory with `mktemp -d`, point `DATABASE_URL` at a SQLite
file inside it, and run:

```bash
.venv/bin/python -m app.scripts.run_ai_engine --seed --skip-extract
```

After completion, query only `AIRun.status`, `AIRun.kind`, and
`AIRun.model_version`. Assert at least one audit row exists, every model version
equals the configured live model and none starts with `mock-`. The normal
application database is never opened. Move the temporary directory to the
user Trash after validation.

- [x] **Step 4: Run complete offline regression verification**

Run:

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/python -m compileall -q app
cd ..
git diff --check
```

Expected: the complete backend suite passes; PostgreSQL-only tests may skip
when `TEST_DATABASE_URL` is absent. Pytest keeps `APP_ENV=test`, so this command
does not contact either real provider.

- [x] **Step 5: Commit the implementation plan progress only if changed**

Mark completed checkboxes in this plan and commit it with the implementation.
Never stage `backend/.env` or any temporary provider response.
