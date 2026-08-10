# Fund Disclosure Sync Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a visible, Case-scoped fund-disclosure task that supports immediate, weekly and monthly syncs.

**Architecture:** A separate immutable configuration, run and event stream prevents fund imports from being confused with ordinary evidence replenishment. Every run snapshots saved fund codes and Case stock scope, invokes the strict Gildata matcher, and returns counted outcomes. The frontend extends the existing Market Expression rail progressively.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, React, TypeScript, Vitest, Playwright.

---

### Task 1: Model configuration, runs and events

**Files:** Create `backend/app/models/fund_disclosure_sync.py`, `backend/alembic/versions/0040_fund_disclosure_sync.py`; create `backend/tests/test_fund_disclosure_sync_api.py`.

- [ ] Write a failing test that saves two configs and starts a run:

```python
first = service.save_config(case_id, actor="human:researcher", fund_codes=["005827"], frequency="weekly", change_reason="每周核验")
second = service.save_config(case_id, actor="human:researcher", fund_codes=["005827", "110011"], frequency="monthly", change_reason="调整范围")
run = service.start_manual_run(case_id)
assert (first.version, second.version, run.config_version_id) == (1, 2, second.id)
```

- [ ] Run `cd backend && .venv/bin/python -m pytest tests/test_fund_disclosure_sync_api.py -q`; expect missing service/model failure.
- [ ] Create immutable `FundDisclosureSyncConfigVersion`, `FundDisclosureSyncRun`, and `FundDisclosureSyncRunEvent` tables. Config stores Case, monotonically increasing version, weekly/monthly frequency, fund codes, actor, reason and time. Run stores Case/config IDs, manual/scheduled trigger, frozen stock/fund scope and timestamps. Event stores run, sequence, stage, status, message, JSON payload and time.
- [ ] Add every record to `IMMUTABLE_TABLES` and migration 0040 with unique `(research_case_id, version)` and `(run_id, seq)` constraints plus indexes.
- [ ] Re-run the pytest command; expect PASS.
- [ ] Commit with `git add backend/app/models/fund_disclosure_sync.py backend/alembic/versions/0040_fund_disclosure_sync.py backend/tests/test_fund_disclosure_sync_api.py && git commit -m "feat: record fund disclosure sync configurations"`.

### Task 2: Strict service, suggestions and scheduling

**Files:** Create `backend/app/services/fund_disclosure_sync.py`, `backend/app/queries/fund_disclosure_sync.py`; modify `backend/app/scripts/ingest_gildata_fund_holdings.py`; test `backend/tests/test_fund_disclosure_sync_api.py`.

- [ ] Write a failing test that asserts suggestions only come from historical `HoldingDisclosure` rows for stocks bound in the current Case; no suggestions must yield a manual-code fallback state.
- [ ] Write a failing test that runs a fake provider with an unmatched quarterly report and asserts `pending_match_rows == 1` in the final event while Case fund exposure remains empty.
- [ ] Run the focused pytest file; expect query/service method failures.
- [ ] Implement `FundDisclosureSyncQuery.detail(case_id)` with suggestions, effective config, config history and replayable event streams. Suggestions use only known historical disclosures, never provider screening or a presumed recommendation.
- [ ] Implement service stages `scope`, `query_holdings`, `match_report`, `write_disclosure`, `finished` or `failed`. Pass only the frozen config's fund codes to the existing importer. A matching report with missing display permission remains counted, never formal exposure.
- [ ] Implement `next_due_at`: weekly is next Monday 09:00, monthly is first business day 09:00; Cases without active config are skipped. Scheduled runs snapshot configuration before provider work.
- [ ] Run `cd backend && .venv/bin/python -m pytest tests/test_fund_disclosure_sync_api.py tests/test_ingest_gildata_fund_holdings.py -q`; expect PASS. Commit with message `feat: run replayable fund disclosure syncs`.

### Task 3: Case-scoped API and generated contract

**Files:** Create `backend/app/schemas/v1/fund_disclosure_sync.py`, `backend/app/api/v1/fund_disclosure_sync.py`; modify `backend/app/api/v1/router.py`, `frontend/openapi.json`, `frontend/src/contracts/v1.ts`; test `backend/tests/test_fund_disclosure_sync_api.py`.

- [ ] Write failing HTTP tests for `GET /research-cases/{case_id}/fund-disclosure-sync`, `PUT .../config`, `POST .../runs`, and `POST .../runs/{run_id}/retry`; use an alternate bearer token and require a 404.
- [ ] Require 1–50 normalized codes, `weekly|monthly`, and a non-empty reason when saving config. Every route calls `CaseTenantAccess.require_case`.
- [ ] Return suggested funds, config history, frozen run inputs and all event payloads. A failed provider call must return an explicit failed run and no borrowed prior success state.
- [ ] Run `bash scripts/sync-contract.sh --update`, then `cd backend && .venv/bin/python -m pytest tests/test_fund_disclosure_sync_api.py tests/test_event_case_tenant_access.py -q`, then `cd ../frontend && npm run typecheck`; expect all commands to exit 0.
- [ ] Commit API, tests and generated contract with message `feat: expose Case-scoped fund disclosure syncs`.

### Task 4: Visible Market Expression task

**Files:** Modify `frontend/src/app/researchOsApi.ts`, `frontend/src/features/case/MarketExpressionContent.tsx`, `frontend/src/styles/research-os-overrides.css`; modify `frontend/src/tests/ResearchOsPages.test.tsx`.

- [ ] Write a failing test that finds “建议补充的基金披露”, selects a suggested fund, selects weekly, enters a reason, saves, starts “立即补充一次”, and sees “本次补充记录”.
- [ ] Add typed detail/save/start/retry methods to the default HTTP client and mock seam. Missing mock support must reject rather than return a synthetic success.
- [ ] Render one `.ros-fund-sync` section in the existing right rail: checkbox suggestions first, manual-code fallback second, progressive weekly/monthly config next, compact run timeline last. “立即补充一次” refreshes both sync detail and market expression.
- [ ] Render explicit empty, unavailable, loading, running, failed, completed and retry states. Preserve the wording that formal disclosure needs the same fund and report-period seasonal report; never call it real-time holdings.
- [ ] Use warm-paper rules and existing controls, not a card grid or modal. At narrow width controls stack with keyboard-visible focus.
- [ ] Run `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run typecheck && npm run build`; expect PASS. Commit with message `feat: add visible fund disclosure sync task`.

### Task 5: Browser and live-path acceptance

**Files:** Modify `frontend/e2e/research-os.spec.ts`, `backend/tests/test_verify_live_event_ui.py`, and `docs/design/backend-design.md`.

- [ ] Write a failing browser test that saves monthly configuration, starts an immediate sync and observes the exact matching rule in the replay timeline.
- [ ] Extend the live verifier to create an admitted Case, save weekly then monthly versions, use only a fake provider, read all sync events and verify an unmatched row never appears in fund exposure.
- [ ] Run `cd backend && .venv/bin/python -m pytest tests/test_fund_disclosure_sync_api.py tests/test_ingest_gildata_fund_holdings.py tests/test_verify_live_event_ui.py tests/test_event_case_tenant_access.py -q`, then `cd ../frontend && npm test && npm run typecheck && npm run build && npm run e2e`; expect exit 0.
- [ ] Update status docs: suggestions are historical-disclosure based, saved codes bind each run, and no output is a real-time holding. Commit with message `test: verify fund disclosure sync workflow`.

## Plan self-review

- Tasks 1–3 cover immutable configuration, strict execution, schedules, tenant access and API replay.
- Task 4 implements the confirmed progressive task surface and all user-visible states.
- Task 5 proves immediate and scheduled behavior through browser acceptance without a real provider token.
