# Historical Fund Disclosure Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fund disclosure sync run an immutable, point-in-time query for a selected report period and recover abandoned runs without losing their original scope.

**Architecture:** Add a report-period snapshot to configuration and runs while preserving legacy rows with null values. The ingestion adapter will query and retain only the configured canonical fund code and exact report period. The application service will append a terminal audit event for stale started runs and retry from the frozen run scope.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Pydantic, pytest, React/TypeScript.

---

### Task 1: Freeze report period in API and persistence

**Files:**
- Create: `backend/alembic/versions/0048_fund_disclosure_sync_report_period.py`
- Modify: `backend/app/models/fund_disclosure_sync.py`
- Modify: `backend/app/schemas/v1/fund_disclosure_sync.py`
- Modify: `backend/app/services/fund_disclosure_sync.py`
- Test: `backend/tests/test_fund_disclosure_sync_api.py`

- [x] **Step 1: Write failing API tests for a required report period and immutable run snapshot.**

```python
response = client.put(config_url, json={
    "actor": "reviewer@example.com", "fund_codes": ["515050"],
    "frequency": "monthly", "report_period": "2024-12-31",
    "change_reason": "historical replay",
})
assert response.status_code == 200
assert response.json()["report_period"] == "2024-12-31"
run = client.post(run_url, json={"actor": "reviewer@example.com"}).json()
assert run["report_period"] == "2024-12-31"
```

- [x] **Step 2: Run the focused test and confirm the old API rejects or omits the field.**

Run: `PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m pytest backend/tests/test_fund_disclosure_sync_api.py -q`

Expected: a failing assertion for `report_period`.

- [x] **Step 3: Add nullable legacy-safe `report_period` columns, DTOs, request validation, and copy the period into new runs.**

```python
report_period: Mapped[date | None] = mapped_column(Date, nullable=True)

class SaveFundDisclosureSyncConfigRequest(BaseModel):
    report_period: date

if config.report_period is None:
    raise ValueError("fund disclosure sync configuration has no frozen report period")
run = FundDisclosureSyncRun(..., report_period=config.report_period)
```

The migration must add nullable columns so historical configuration and run records remain readable without inventing a period.

- [x] **Step 4: Re-run focused API tests.**

Run: `PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m pytest backend/tests/test_fund_disclosure_sync_api.py -q`

Expected: PASS.

### Task 2: Make provider ingestion point-in-time and canonical-code safe

**Files:**
- Modify: `backend/app/scripts/ingest_gildata_fund_holdings.py`
- Modify: `backend/app/services/fund_disclosure_sync.py`
- Test: `backend/tests/test_fund_disclosure_sync_api.py`

- [x] **Step 1: Add a failing ingestion test with `515050.OF`, `515050.SH`, and a later report period.**

```python
stats = ingest(..., fund_codes=["515050"], report_period=date(2024, 12, 31), ...)
assert stats.holdings == 1
assert stats.out_of_scope_rows == 2
assert all("2024年第4季度" in query for query in fake_client.queries)
```

- [x] **Step 2: Run the focused test and confirm the current latest-period query fails the assertion.**

Run: `PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m pytest backend/tests/test_fund_disclosure_sync_api.py -q`

Expected: FAIL because the old ingestion queries “最近一期” and retains all returned groups.

- [x] **Step 3: Require a report period in the CLI and ingestion function, query its calendar quarter, normalize suffixes, and discard nonmatching rows before announcement lookup.**

```python
def _canonical_fund_code(value: str) -> str:
    return _fund_code(value).split(".", 1)[0]

query = f"查询基金{code} {report_period.year}年第{(report_period.month - 1) // 3 + 1}季度公开披露的股票持仓明细，包括股票代码、股票名称、持仓权重、报告期"
if _canonical_fund_code(raw.fund_code) != requested or raw.report_period != report_period:
    out_of_scope_rows += 1
    continue
```

Add `out_of_scope_rows` to ingestion statistics and audit payloads. Require `--report-period YYYY-MM-DD` for direct script execution.

- [x] **Step 4: Re-run focused ingestion/API tests.**

Run: `PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m pytest backend/tests/test_fund_disclosure_sync_api.py -q`

Expected: PASS.

### Task 3: Append-only stale-run recovery, UI selection, and integration proof

**Files:**
- Modify: `backend/app/services/fund_disclosure_sync.py`
- Modify: `backend/app/api/v1/fund_disclosure_sync.py`
- Modify: `frontend/src/features/case/FundDisclosureSyncTask.tsx`
- Modify: `frontend/src/data/mockResearchOsApi.ts`
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`
- Regenerate: `frontend/openapi.json`

- [x] **Step 1: Add failing tests for a stale `started` run and a retry preserving period.**

```python
assert recovered["events"][-1]["stage"] == "interrupted"
assert recovered["events"][-1]["status"] == "failed"
retry = client.post(f"{run_url}/{run_id}/retry", json={"actor": "reviewer@example.com"}).json()
assert retry["report_period"] == "2024-12-31"
```

- [x] **Step 2: Append an `interrupted`/`failed` event for runs older than five minutes whose last event is nonterminal, then create retries only from an immutable scope.**

```python
if last.status in {"queued", "started", "running"} and run.created_at <= cutoff:
    self._append_event(run, stage="interrupted", status="failed", message="运行超时或进程中断；已保留冻结范围，可按原配置重试")
```

Recover before read/list responses and commit the deterministic audit append. Do not change existing run data or silently turn a legacy null-period run into a valid retry.

- [x] **Step 3: Add a required “目标报告期” date input to configuration, seed it from the suggested holding disclosure, and render it in the active configuration and each run.**

```tsx
<input aria-label="目标报告期" type="date" value={reportPeriod} onChange={(event) => setReportPeriod(event.target.value)} />
```

- [x] **Step 4: Regenerate the contract and run backend/frontend verification.**

Run: `PYTHONPATH="$PWD/backend" backend/.venv/bin/python backend/scripts/dump_openapi.py && PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m pytest backend/tests/test_fund_disclosure_sync_api.py -q && (cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run typecheck && npm run build)`

Expected: all commands PASS.

- [x] **Step 5: Apply migrations to a copied local case database, execute one real 515050 / 2024-12-31 run, and browser-check the run evidence.**

Run: copy `.local/industrial-foxconn-case.db`, upgrade it with Alembic, start the API with the copied database and existing local demo authentication, create config with `report_period=2024-12-31`, trigger a run, then inspect API/browser output.

Expected: a terminal run with only `515050` records dated `2024-12-31`; its audit contains the frozen scope and out-of-scope count; no 2025+ report-period record is written.
