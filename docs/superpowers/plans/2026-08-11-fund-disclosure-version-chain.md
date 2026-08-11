# Fund Disclosure Version Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve competing historical fund disclosures as immutable versions and deterministically select the correct source for each point-in-time read, while restoring the full live UI human-path verifier.

**Architecture:** `HoldingDisclosure` gains immutable filing metadata and a backward-only predecessor link. A single ordering helper defines historical visibility and filing precedence for repositories, exposure reads, market-expression reads, and API DTOs. Gildata ingestion classifies the frozen announcement and creates the predecessor link only on the new record; it never updates an existing disclosure.

**Tech Stack:** Python 3.11, SQLAlchemy, Alembic, FastAPI/Pydantic, pytest, React/TypeScript, OpenAPI contract generation, Vitest, Playwright.

---

### Task 1: Add immutable disclosure-version storage

**Files:**
- Create: `backend/alembic/versions/0049_holding_disclosure_version_chain.py`
- Modify: `backend/app/models/ledger.py:643-676`
- Modify: `backend/app/schemas/v1/instrument_commands.py:63-94`
- Modify: `backend/app/services/instruments.py:136-210`
- Test: `backend/tests/test_instrument_commands_api.py`
- Test: `backend/tests/test_sqlite_migration_bootstrap.py`

- [ ] **Step 1: Write failing model/API tests**

```python
def test_holding_disclosure_accepts_annual_successor_without_mutating_quarterly_row(...):
    quarterly = create_holding(..., filing_kind="quarterly")
    annual = create_holding(
        ..., filing_kind="annual", supersedes_disclosure_id=quarterly["id"],
    )
    assert annual["filing_kind"] == "annual"
    assert annual["supersedes_disclosure_id"] == quarterly["id"]
    assert reload(quarterly)["supersedes_disclosure_id"] is None
```

Add migration assertions that a fresh SQLite database reaches `0049`, and legacy rows retain `filing_kind="other"` and null predecessor after upgrade.

- [ ] **Step 2: Run the tests to verify RED**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_instrument_commands_api.py tests/test_sqlite_migration_bootstrap.py -q`

Expected: failure because the request/DTO/model fields and migration do not yet exist.

- [ ] **Step 3: Implement minimal immutable metadata**

Add nullable, legacy-safe `filing_kind` (backfill `other`) and `supersedes_disclosure_id` columns. Add a filing-kind check constraint and a self foreign key. Extend create/response DTOs and `InstrumentService.add_holding_disclosure`; validate that a predecessor exists and has the same fund, stock and report period, and reject self links or same/lower-priority predecessors. Do not update an existing row.

- [ ] **Step 4: Run the tests to verify GREEN**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_instrument_commands_api.py tests/test_sqlite_migration_bootstrap.py -q`

Expected: all selected tests pass and Alembic reports a single `0049` head.

- [ ] **Step 5: Commit the storage slice**

```bash
git add backend/alembic/versions/0049_holding_disclosure_version_chain.py backend/app/models/ledger.py backend/app/schemas/v1/instrument_commands.py backend/app/services/instruments.py backend/tests/test_instrument_commands_api.py backend/tests/test_sqlite_migration_bootstrap.py
git commit -m "feat: version historical fund disclosures"
```

### Task 2: Classify frozen filing and append a predecessor only on new ingestion

**Files:**
- Modify: `backend/app/scripts/ingest_gildata_fund_holdings.py:158-341`
- Test: `backend/tests/test_ingest_gildata_fund_holdings.py`

- [ ] **Step 1: Write failing ingestion tests**

```python
def test_annual_fund_announcement_supersedes_existing_quarterly_disclosure(session):
    quarterly = seed_disclosure(session, filing_kind="quarterly", published_at=jan_22)
    result = ingest(session, FakeClient(announcement_title="基金2024年年度报告"), ...)
    annual = newest_disclosure(session)
    assert result.holding_disclosures_written == 1
    assert annual.filing_kind == "annual"
    assert annual.supersedes_disclosure_id == quarterly.id

def test_same_source_rerun_is_idempotent(...):
    assert ingest(...).holding_disclosures_written == 1
    assert ingest(...).holding_disclosures_skipped_duplicate == 1
```

Add coverage for `更正` → `correction`, `季度` → `quarterly`, and an unclassified title → `other`.

- [ ] **Step 2: Run the tests to verify RED**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_ingest_gildata_fund_holdings.py -q`

Expected: failures because the ingestion path has no filing classification or predecessor lookup.

- [ ] **Step 3: Implement classification and append-only linking**

Add a pure `_filing_kind(title)` helper and a deterministic predecessor lookup scoped to fund, stock and report period. Retain the current same-source idempotency rule. Before the new row is inserted, choose only a lower-priority existing disclosure as its predecessor; a later lower-priority arrival remains unlinked rather than rewriting history.

- [ ] **Step 4: Run the tests to verify GREEN**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_ingest_gildata_fund_holdings.py -q`

Expected: all ingestion tests pass with no duplicated same-source record.

- [ ] **Step 5: Commit the ingestion slice**

```bash
git add backend/app/scripts/ingest_gildata_fund_holdings.py backend/tests/test_ingest_gildata_fund_holdings.py
git commit -m "feat: link historical disclosure filing versions"
```

### Task 3: Use a single deterministic point-in-time selector in read models

**Files:**
- Modify: `backend/app/repositories/instruments.py:202-220,316-327`
- Modify: `backend/app/services/exposure.py:29-67`
- Modify: `backend/app/queries/penetration.py:94-157,196-220`
- Modify: `backend/app/queries/companies.py:343-370`
- Modify: `backend/app/queries/market_expression.py`
- Modify: `backend/app/schemas/v1/penetration.py`
- Modify: `backend/app/schemas/v1/market_expression.py`
- Test: `backend/tests/test_read_extensions_api.py`
- Test: `backend/tests/test_market_expression_api.py`
- Test: `backend/tests/test_theme_read_api_v1.py`

- [ ] **Step 1: Write failing point-in-time tests**

```python
def test_fund_exposure_selects_quarterly_before_annual_publication(api_client, ...):
    seed_version_pair(quarterly_at=jan_22, annual_at=mar_31)
    assert exposure(api_client, as_of="2025-02-01").positions[0].filing_kind == "quarterly"

def test_fund_exposure_selects_annual_after_publication_deterministically(api_client, ...):
    seed_version_pair(...)
    response = exposure(api_client, as_of="2025-04-01")
    assert response.positions[0].filing_kind == "annual"
    assert response.positions[0].supersedes_disclosure_id == quarterly_id
```

Cover penetration, fund composition, company/market-expression readers, and a tie with equal period/priority/published time to assert the final ID ordering.

- [ ] **Step 2: Run the tests to verify RED**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_read_extensions_api.py tests/test_market_expression_api.py tests/test_theme_read_api_v1.py -q`

Expected: failures because existing readers order only by `report_period` and do not expose filing metadata.

- [ ] **Step 3: Implement the shared selector and DTO mapping**

Create one pure selector/order key with this precedence: `correction`, `annual`, `quarterly`, `other`; then later `published_at`, later `created_at`, and lexicographically later ID. Every reader must filter by cutoff before using it. Add filing kind and predecessor summary/ID to exposed position DTOs so the UI can explain the selected version without treating it as a new holding.

- [ ] **Step 4: Run the tests to verify GREEN**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_read_extensions_api.py tests/test_market_expression_api.py tests/test_theme_read_api_v1.py -q`

Expected: all selected reads return the same disclosure for repeated requests and correct historical cutoffs.

- [ ] **Step 5: Regenerate the API contract and commit**

Run the repository contract generator, then:

```bash
git add backend/app/repositories/instruments.py backend/app/services/exposure.py backend/app/queries/penetration.py backend/app/queries/companies.py backend/app/queries/market_expression.py backend/app/schemas/v1/penetration.py backend/app/schemas/v1/market_expression.py frontend/openapi.json frontend/src/contracts/v1.ts backend/tests/test_read_extensions_api.py backend/tests/test_market_expression_api.py backend/tests/test_theme_read_api_v1.py
git commit -m "fix: select historical disclosure versions deterministically"
```

### Task 4: Show the selected filing version on live pages

**Files:**
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/features/case/MarketExpressionContent.tsx`
- Modify: `frontend/src/features/case/MarketInstrumentProfiles.tsx`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Write failing page tests**

```tsx
expect(screen.getByText("披露版本：年报")).toBeVisible();
expect(screen.getByText("前序披露：季度报告（2025/1/22）")).toBeVisible();
expect(screen.queryByText("实时仓位")).not.toBeInTheDocument();
```

Include the legacy `other` case, which must remain readable without a fabricated predecessor.

- [ ] **Step 2: Run the tests to verify RED**

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: failures because HTTP/domain types and pages do not render filing-version metadata.

- [ ] **Step 3: Implement minimal presentation**

Map the new contract fields through the HTTP adapter and render concise labels in the fund and market profiles. Keep source links, report period, disclosure date and partial-coverage warning visible; do not add fund recommendation language.

- [ ] **Step 4: Run the tests to verify GREEN**

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run typecheck && npm run build`

Expected: page tests, typecheck and production build pass.

- [ ] **Step 5: Commit the presentation slice**

```bash
git add frontend/src/data/httpResearchAdapter.ts frontend/src/domain/eventResearch.ts frontend/src/features/case/MarketExpressionContent.tsx frontend/src/features/case/MarketInstrumentProfiles.tsx frontend/src/tests/ResearchOsPages.test.tsx frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: explain historical fund disclosure versions"
```

### Task 5: Restore the complete live human-path verification

**Files:**
- Modify: `frontend/scripts/verify-live-event-ui.mjs:347-356`
- Test: `backend/tests/test_verify_live_event_ui.py`

- [ ] **Step 1: Run the existing verifier to record RED**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_verify_live_event_ui.py -q`

Expected: failure with Playwright strict-mode violation because `getByText("原子陈述审核")` matches both heading and reason label.

- [ ] **Step 2: Make the selector semantic and singular**

Replace only the ambiguous wait with:

```js
await page.getByRole("heading", { name: "原子陈述审核" }).waitFor();
```

- [ ] **Step 3: Run the verifier to verify GREEN**

Run: `cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests/test_verify_live_event_ui.py -q`

Expected: the browser completes the default HTTP human flow without mock mode.

- [ ] **Step 4: Commit the verifier repair**

```bash
git add frontend/scripts/verify-live-event-ui.mjs backend/tests/test_verify_live_event_ui.py
git commit -m "test: complete live human workflow verification"
```

### Task 6: Full verification and real-data replay

**Files:**
- Test: `backend/tests`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Run all automated checks**

Run:

```bash
cd backend && PYTHONPATH="$PWD" .venv/bin/python -m pytest tests -q
cd ../frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run typecheck && npm run build
```

Expected: no test failures; explicitly report environment-skipped tests.

- [ ] **Step 2: Run the historical case against a copy of the local database**

Create a temporary copy of `.local/industrial-foxconn-case.db`, migrate it to `0049`, configure `515050 / 2024-12-31`, then use the default HTTP UI to show the selected annual version and the earlier quarterly predecessor. Verify 2025-02-01 replay selects the quarterly record and 2025-04-01 selects annual.

- [ ] **Step 3: Inspect the final diff and commit verification artifacts only if intentional**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and no accidental changes to local databases, tokens or user-owned files.
