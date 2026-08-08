# Report Research Runtime Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make report research usable end-to-end from the product UI and make every market-impact/key-factor result traceable, scoped, and reproducible.

**Architecture:** Keep report scopes and their source/evidence records append-only. Add small command seams for intake, scope changes, relation resolution, source access, and confounder review; make the automatic run calculate market measures from raw China-ledger facts and write only provisional audited outcomes. The frontend consumes display-safe DTOs and never parses raw ledger locators.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic/PostgreSQL, existing auto-research/task ledger, React/TypeScript, Vitest, Playwright.

---

### Task 1: Expose report intake and immutable scope changes

**Files:** Modify `backend/app/api/v1/report_research.py`, `backend/app/schemas/v1/report_research.py`, `backend/app/services/report_research.py`, `frontend/src/data/*`, `frontend/src/main.tsx`; create report intake/scope screen tests.

- [ ] Write failing API tests for scope append/read and UI tests for pasted/web/PDF intake plus a historical-scope selector.
- [ ] Verify RED: no scope command route or create-screen route exists.
- [ ] Add case-scoped command endpoints that validate selected claims/relations and append, rather than mutate, a `ReportResearchScopeVersion`; add client/mock contracts.
- [ ] Build `/reports/new` and workbench scope controls. Saving a question/path creates a successor scope and reloads it; no destructive edit exists.
- [ ] Verify API/component/E2E green and commit.

### Task 2: Calculate auditable report market measures from raw facts

**Files:** Modify `backend/app/services/report_market_impact.py`, China market gateway/ledger DTOs, auto-run dispatch and market tests.

- [ ] Write failing tests from dated raw close/volume/turnover/valuation snapshots and a trading calendar for 1D/5D target, peer, and industry measures.
- [ ] Verify RED: collector only accepts precomputed `EVENT_RETURN_*` metrics.
- [ ] Compute returns/control deltas at collection time from visible raw observations, retain source snapshot ids, and write insufficient records for missing dates/metrics.
- [ ] Update the safe ingestion contract to persist the required raw metrics with `available_at` and ensure the report task uses it.
- [ ] Verify raw-fixture, task-run, PIT, and full backend green; commit.

### Task 3: Continue automatic evidence and confounder research

**Files:** Modify `backend/app/services/auto_research.py`, `backend/app/services/report_market_impact.py`, report scope/query models and tests.

- [ ] Write failing run tests showing a completed report run appends a successor scope/provisional relation assessment only when its inputs are source-admitted and visible.
- [ ] Verify RED: report run only collects windows and can never make the confounder component true.
- [ ] Schedule exact-scope evidence/confounder tasks, append provisional non-material/material/unresolved outcomes with source provenance, and make a post-cutoff fact create a successor reassessment rather than alter old scope.
- [ ] Add human confirm/override command/UI state; both write a new assessment rather than update the provisional row.
- [ ] Verify key-candidate, unresolved/material-block, retry/cancel, and scope-history tests green; commit.

### Task 4: Unify relation resolution and protected source access

**Files:** Modify `backend/app/models/report_research.py`, Alembic migration, `backend/app/services/report_market_impact.py`, `backend/app/queries/report_wiki.py`, `backend/app/api/v1/report_research.py`, source-access UI/tests.

- [ ] Write failing tests for an unresolved name matching an existing Company, an audited resolution, and anonymous original-PDF access.
- [ ] Verify RED: market resolves by name transiently while Wiki labels the same path unlisted, and original download is anonymous.
- [ ] Append one audited relation-resolution record; both collector and Wiki consult its effective value. Add case-scoped internal source-detail/original routes with the existing host access seam; preserve embed redaction.
- [ ] Make source locator actions open these safe internal details, not raw JSON/UUIDs.
- [ ] Verify resolution consistency, access rejection, internal detail, and embed non-leakage tests green; commit.

### Task 5: Complete path selection and final user journey

**Files:** Modify `frontend/src/pages/prototype/ReportWikiGraphScreen.tsx`, report workspace tests/E2E/docs.

- [ ] Write failing tests for selecting a non-first factor relation, opening its path, and a visible “open in research system” handoff from the embed contract where policy permits.
- [ ] Verify RED: current path is always the first factor and the embed has no handoff.
- [ ] Add accessible relation picker and selected-path state; ensure external embed handoff is omitted unless a configured safe internal URL exists.
- [ ] Run paste/web/PDF intake journeys, scope revision, raw-market calculation, source detail, key/inconclusive branch, normal UI, and desktop/390 embed E2E.
- [ ] Commit and perform specification review then quality review.

### Final verification

- [ ] `cd backend && .venv/bin/python -m pytest -q`
- [ ] `cd frontend && npm test -- --run && npm run typecheck && npm run build`
- [ ] clean SQLite `alembic upgrade head && current`; run PostgreSQL-only migrations/concurrency tests when `TEST_DATABASE_URL` is configured.
- [ ] Run Chrome E2E at desktop and 390px, including a pasted report, PDF report, scope successor, insufficient and key-candidate paths, and redacted embed.
