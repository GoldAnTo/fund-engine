# Provider Capability Run Transparency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each Case-scoped fund-disclosure replenishment run state exactly which provider capabilities and fields were confirmed, used, unavailable, or excluded.

**Architecture:** Keep provider capability facts inside the immutable `FundDisclosureSyncRunEvent` sequence rather than inventing a global permission switch. A probe converts the provider's exposed tool metadata into a bounded snapshot before ingestion; the existing run receives that snapshot and the frontend renders it beside the frozen scope and per-stage counts. Unknown capability remains unavailable and cannot be represented as successful fund disclosure data.

**Tech Stack:** FastAPI, SQLAlchemy immutable ledger events, Gildata MCP adapter, generated OpenAPI TypeScript contract, React/Vitest/Playwright.

---

### Task 1: Define and capture the capability snapshot

**Files:**
- Modify: `backend/app/datasources/gildata/adapters.py`
- Modify: `backend/app/services/fund_disclosure_sync.py`
- Test: `backend/tests/test_fund_disclosure_sync_api.py`

- [ ] **Step 1: Write the failing service/API test**

```python
def test_fund_sync_run_records_confirmed_tools_fields_and_unavailable_boundary(...):
    response = cmd_client.post(f"/api/v1/research-cases/{case_id}/fund-disclosure-sync/runs")
    capability = response.json()["events"][1]
    assert capability["stage"] == "provider_capability"
    assert capability["payload"]["provider"] == "gildata"
    assert capability["payload"]["used_tools"] == ["FinQuery", "AnnouncementData"]
    assert "fund_holdings" in capability["payload"]["unverified_capabilities"]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fund_disclosure_sync_api.py -q`

Expected: failure because no `provider_capability` event exists.

- [ ] **Step 3: Add a narrow adapter snapshot helper and append it before ingestion**

```python
def fund_disclosure_capability_snapshot(client: Any) -> dict[str, object]:
    tools = {tool["name"] for tool in client.list_tools()}
    return {
        "provider": "gildata",
        "used_tools": [name for name in ("FinQuery", "AnnouncementData") if name in tools],
        "required_fields": ["fund_code", "stock_code", "report_period", "publish_date"],
        "unverified_capabilities": ["fund_holdings"] if "FinQuery" not in tools else [],
    }
```

`FundDisclosureSyncService.execute()` appends a completed or unavailable `provider_capability` event before `query_holdings`; errors from `list_tools()` produce an explicit failed snapshot and then the existing retryable failure event. Do not persist raw provider responses or credentials.

- [ ] **Step 4: Run focused tests and commit**

Run: `backend/.venv/bin/pytest backend/tests/test_fund_disclosure_sync_api.py backend/tests/test_fund_disclosure_sync_scheduler.py -q`

Commit: `git commit -m "feat: record fund sync provider capabilities"`

### Task 2: Preserve capability facts through the API and mock client

**Files:**
- Modify: `backend/app/schemas/v1/fund_disclosure_sync.py`
- Modify: `backend/app/api/v1/fund_disclosure_sync.py`
- Modify: `frontend/src/data/mockResearchOsApi.ts`
- Modify: `frontend/src/contracts/v1.ts` and `frontend/openapi.json` via `bash scripts/sync-contract.sh --update`
- Test: `backend/tests/test_fund_disclosure_sync_api.py`

- [ ] **Step 1: Write the failing contract-level assertion**

```python
assert response.json()["runs"][0]["events"][1]["payload"]["required_fields"] == [
    "fund_code", "stock_code", "report_period", "publish_date"
]
```

- [ ] **Step 2: Run it and verify RED**

Run: `backend/.venv/bin/pytest backend/tests/test_fund_disclosure_sync_api.py -q`

- [ ] **Step 3: Expose only the typed, non-sensitive snapshot**

Keep event payload as the immutable replay carrier; update the mock completed run with the same `provider_capability` stage, `used_tools`, `required_fields`, and explicit unknown/unavailable list. Regenerate the OpenAPI contract and fix all TypeScript consumers.

- [ ] **Step 4: Verify API and type contract, then commit**

Run: `backend/.venv/bin/pytest backend/tests/test_fund_disclosure_sync_api.py -q && bash scripts/sync-contract.sh --update && npm --prefix frontend run typecheck`

Commit: `git commit -m "feat: expose fund sync capability replay"`

### Task 3: Render the capability boundary in the existing Case task

**Files:**
- Modify: `frontend/src/features/case/FundDisclosureSyncTask.tsx`
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`
- Modify: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: Write failing UI tests**

```tsx
expect(await screen.findByText("本次数据能力与字段")).toBeVisible();
expect(screen.getByText(/已使用：FinQuery、AnnouncementData/)).toBeVisible();
expect(screen.getByText(/未验证能力不会被当作基金持仓/)).toBeVisible();
```

- [ ] **Step 2: Run and verify RED**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 3: Add a compact capability section under each run**

Read the `provider_capability` event only. Show provider, used tools, required fields, unavailable/unknown capability reason, and a statement that the user can change only fund scope/frequency; provider authorization is a data-source boundary, not a user checkbox. Preserve the current event list and retry action.

- [ ] **Step 4: Verify visual and end-to-end behavior, then commit**

Run: `npm --prefix frontend test -- --run && npm --prefix frontend run e2e && npm --prefix frontend run build`

Use the local browser to inspect a completed mock fund-sync run at normal and narrow widths; confirm no raw response or unrelated Case data is displayed.

Commit: `git commit -m "feat: show fund sync capability boundary"`

### Final verification

- [ ] Run `backend/.venv/bin/pytest backend/tests -q`.
- [ ] Run `npm --prefix frontend test -- --run`, `npm --prefix frontend run e2e`, and `npm --prefix frontend run build`.
- [ ] Run `git diff --check` and `bash scripts/sync-contract.sh --update`.
- [ ] Confirm a manual run, scheduled run, and retry all display their frozen scope plus the same immutable capability snapshot.
