# Tenant-safe Historical Forecast Verdict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put one auditable historical report forecast into the current Research OS: a frozen prediction and baseline, a later source-backed actual, deterministic evaluation, human-published verdict, then stock and historical-fund-disclosure drill-down.

**Architecture:** Add a small immutable forecast-verdict ledger beside `ReportClaim` and `KeyFactor`. Commands require the current Case's tenant admission and Case-owned, displayable sources. The evaluator only creates a machine candidate; only an append-only human verdict appears in the existing Market Expression read model. The existing Company/Stock/Fund/HoldingDisclosure projection remains responsible for asset and historic-disclosure presentation.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, React, TypeScript, Vitest, Playwright.

---

### Task 1: Add immutable, Case-scoped forecast records

**Files:**
- Modify: `backend/app/models/research_expression.py`, `backend/app/models/ledger.py`, `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0041_forecast_verdicts.py`
- Test: `backend/tests/test_forecast_verdicts.py`

- [ ] **Step 1: Write failing persistence tests.**

  Create a target with a reviewed `ReportClaim` and `KeyFactor`, a source-backed actual, an evaluation candidate and a verdict. Assert all four table names are in `IMMUTABLE_TABLES`, `Base.metadata.create_all()` creates them, and a duplicate verdict never updates the prior verdict.

- [ ] **Step 2: Run the focused test.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py -q`

  Expected: FAIL because the immutable forecast records do not exist.

- [ ] **Step 3: Implement the narrow ledger.**

  Add `ForecastTargetVersion`, `ActualMetricObservation`, `ForecastEvaluationCandidate`, and `ForecastVerdict`. Freeze Case/factor/claim/source IDs, entity key, metric, amount/unit, observation period, `available_at`, comparator/tolerance, deterministic input snapshot, reviewer/reason/time and optional verdict supersession. Add migration `0041`, foreign keys, check constraints and immutable triggers consistent with the existing ledger.

- [ ] **Step 4: Re-run the focused test.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit.**

  Run: `git add backend/app/models/research_expression.py backend/app/models/ledger.py backend/app/models/__init__.py backend/alembic/versions/0041_forecast_verdicts.py backend/tests/test_forecast_verdicts.py && git commit -m "feat: add immutable forecast verdict ledger"`

### Task 2: Evaluate only matching, admitted historical evidence

**Files:**
- Create: `backend/app/services/forecast_verdicts.py`
- Modify: `backend/tests/test_forecast_verdicts.py`

- [ ] **Step 1: Write failing domain tests.**

  Exercise `within_tolerance`, `at_least` and `at_most`. Assert a `455000000 CNY` target versus `247245713.03 CNY` actual with 10% tolerance creates `contradicted` under `forecast-numeric-v1`; a tolerance-compliant actual creates `supported`. Assert wrong Case, non-reviewed claim/factor, non-admitted/restricted sources, unit/entity/period mismatch, and cutoff before `available_at` are rejected or yield only `not_due`/`insufficient_evidence`.

- [ ] **Step 2: Run the failing test.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py -q`

  Expected: FAIL because the service is absent.

- [ ] **Step 3: Implement the service boundary.**

  Add explicit command inputs and a pure numeric evaluator. Validate all involved records belong to the same admitted Case, the `ReportClaim`/`KeyFactor` are reviewed, both source statements are Case-owned and their source contracts allow display/AI processing, and actual units/entity/period match the frozen target. Never call an LLM, provider or live market endpoint. `create_verdict` must append `confirmed`, `modified`, or `rejected`; only confirmed/modified are publishable.

- [ ] **Step 4: Re-run the focused test.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py -q`

  Expected: PASS.

- [ ] **Step 5: Commit.**

  Run: `git add backend/app/services/forecast_verdicts.py backend/tests/test_forecast_verdicts.py && git commit -m "feat: evaluate source-backed report forecasts"`

### Task 3: Expose tenant-safe commands and historical reads

**Files:**
- Create: `backend/app/api/v1/forecast_verdicts.py`, `backend/app/schemas/v1/forecast_verdicts.py`
- Modify: `backend/app/api/v1/router.py`, `backend/app/schemas/v1/market_expression.py`, `backend/app/queries/market_expression.py`, `backend/tests/test_forecast_verdicts.py`, `frontend/openapi.json`, `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Write failing API tests.**

  Use a real tenant token to create target, actual, candidate and verdict. Assert a foreign tenant receives 404 on every Case- or target-derived route. Assert the machine candidate is absent from `market-expression`, then a confirmed verdict appears only after publication, with both source locators and values. Assert a historical cutoff before the annual-report `available_at` returns neither actual nor verdict.

- [ ] **Step 2: Run the failing API test.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py tests/test_event_case_tenant_access.py -q`

  Expected: FAIL because routes and read projection do not exist.

- [ ] **Step 3: Implement Case-bound routes and read projection.**

  Provide `POST /research-cases/{case_id}/forecast-targets`, `POST /research-cases/{case_id}/actual-metric-observations`, `POST /forecast-targets/{target_id}/evaluate`, `POST /forecast-evaluations/{candidate_id}/verdicts`, and `GET /research-cases/{case_id}/forecast-verdicts?cutoff=`. All routes require the tenant; target/candidate routes derive and enforce the owning Case before returning anything. Extend `KeyFactorDTO` with a nullable `forecast_verdict` that selects only the effective confirmed/modified verdict visible at the query cutoff. Regenerate the OpenAPI contract using the repository command.

- [ ] **Step 4: Re-run API and contract checks.**

  Run: `bash scripts/sync-contract.sh --update && cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py tests/test_event_case_tenant_access.py -q && cd ../frontend && npm run typecheck`

  Expected: PASS.

- [ ] **Step 5: Commit.**

  Run: `git add backend/app/api/v1/forecast_verdicts.py backend/app/schemas/v1/forecast_verdicts.py backend/app/api/v1/router.py backend/app/schemas/v1/market_expression.py backend/app/queries/market_expression.py backend/tests/test_forecast_verdicts.py frontend/openapi.json frontend/src/contracts/v1.ts && git commit -m "feat: expose forecast verdict research API"`

### Task 4: Make the published result readable in Market Expression

**Files:**
- Modify: `frontend/src/features/case/MarketExpressionContent.tsx`, `frontend/src/styles/research-os-overrides.css`, `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Write a failing real-HTTP response test.**

  Mock only `GET /api/v1/research-cases/:caseId/market-expression` with a published `contradicted` forecast verdict and a related stock/fund disclosure. Assert the selected factor shows “预测未兑现”, target/baseline/actual value, comparator, human reviewer/reason and links to both frozen sources. Assert it also retains report period/disclosure/freshness labels and never displays “实时仓位”, “买入” or causal return language. A response without a verdict must show a neutral pending-human-review state.

- [ ] **Step 2: Run the failing frontend test.**

  Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx`

  Expected: FAIL because the page has no published forecast-verdict presentation.

- [ ] **Step 3: Implement the compact, source-first panel.**

  Add one inline “预测验证” segment to the current selected-factor inspector, above fundamental, market-observation and fund-disclosure sections. It must clearly differentiate a reviewed verdict from a hidden machine candidate, present values and rule without dashboard metrics, and link each visible source through the current Case document route. Reuse the existing warm-paper rail and status semantics.

- [ ] **Step 4: Re-run UI checks.**

  Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run typecheck && npm run build`

  Expected: PASS.

- [ ] **Step 5: Commit.**

  Run: `git add frontend/src/features/case/MarketExpressionContent.tsx frontend/src/styles/research-os-overrides.css frontend/src/tests/ResearchOsPages.test.tsx && git commit -m "feat: present reviewed forecast verdicts"`

### Task 5: Prove the complete historical Case path

**Files:**
- Create: `backend/tests/fixtures/historical_report_forecast/firestar_broker_report_excerpt.json`, `backend/tests/fixtures/historical_report_forecast/firestar_2023_annual_report_excerpt.json`, `backend/tests/fixtures/historical_report_forecast/gf_560010_holding_excerpt.json`, `backend/tests/test_historical_report_forecast_e2e.py`
- Modify: `frontend/e2e/research-os.spec.ts`, `frontend/scripts/verify-live-event-ui.mjs`, `backend/tests/test_verify_live_event_ui.py`, `docs/design/2026-08-08-research-operating-system-baseline.md`

- [ ] **Step 1: Write failing source-frozen E2E tests.**

  Use short frozen excerpts only, with real URLs, hash, page locator, source contract and explicit numeric fields. Through live API commands create an admitted Case, reviewed forecast claim/factor, target, actual, candidate, verdict, reviewed `300894.SZ` binding/fundamental impact and historical `560010` disclosure. Assert the read model returns `contradicted`, never a real-time holding or investment action.

- [ ] **Step 2: Run the failing E2E test.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_historical_report_forecast_e2e.py -q`

  Expected: FAIL until the fixture and full Case path exist.

- [ ] **Step 3: Add the minimum frozen source fixtures and live browser assertion.**

  Keep necessary excerpts below fair-use length; do not download source PDFs during tests. Extend the live verifier to prove that a human-published verdict reaches the default HTTP UI with its frozen values and source links. Update the baseline change log to distinguish this verified single-case capability from batch scoring and provider ingestion.

- [ ] **Step 4: Run final verification.**

  Run: `cd backend && .venv/bin/python -m pytest tests/test_forecast_verdicts.py tests/test_historical_report_forecast_e2e.py tests/test_event_case_tenant_access.py tests/test_verify_live_event_ui.py -q && cd ../frontend && npm test && npm run typecheck && npm run build && npm run e2e && git diff --check`

  Expected: all commands pass; no test depends on external data, mock mode, or a provider credential.

- [ ] **Step 5: Commit.**

  Run: `git add backend/tests/fixtures/historical_report_forecast backend/tests/test_historical_report_forecast_e2e.py frontend/e2e/research-os.spec.ts frontend/scripts/verify-live-event-ui.mjs backend/tests/test_verify_live_event_ui.py docs/design/2026-08-08-research-operating-system-baseline.md && git commit -m "test: verify historical forecast verdict chain"`

## Plan self-review

- The plan keeps forecast sources, numerical evaluation, human publication, company/stock mapping and historical fund disclosure as separate records.
- Target-derived endpoints are tenant-safe before looking up a target or candidate.
- The UI exposes published human verdicts only; it does not add batch scoring, financial advice, real-time fund holdings or external fetches.
