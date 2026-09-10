# Company Financial Model Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make a real-source, editable, saved and replayable conditional Alphabet model draft inside the approved workbench.

**Architecture:** An authenticated financial baseline and deterministic calculator feed an append-only model draft service anchored to the original frozen research version. The forecast and valuation pages share that service. Formal evidence, preparation and publication state remain governed by their existing contracts.

**Tech Stack:** Python Decimal, SQLAlchemy/Alembic, FastAPI, React/TypeScript, pytest, Vitest.

---

### Task 1: Governed financial baseline

Files: `backend/app/underwriting/services/company_research_financial_baseline.py`, `backend/app/underwriting/data/alphabet_financial_baseline/`, `backend/tests/test_company_research_financial_baseline.py`.

- [x] Write failing tests for exact raw-source authentication, cutoff rejection, tampering, scope/period correctness, negative FCF and zero buybacks.
- [x] Implement `load_alphabet_financial_baseline(cutoff_at)` returning `{schema_version, content_hash, sources, facts, research_gaps}`. Facts retain exact source IDs, locator, quote, unit, period and derivation inputs. Recompute derived values; never rename proxies as disclosed operating facts.
- [x] Run `.venv/bin/python -m pytest tests/test_company_research_financial_baseline.py -q` in backend; require all tests passing.

### Task 2: Explicit scenario calculator

Files: `backend/app/underwriting/services/company_research_financial_model.py`, `backend/tests/test_company_research_financial_model.py`.

- [x] Write failing independent numerical examples for FCFF, first-year timing, normalized terminal reinvestment, equity/share bridge, and absent-market behavior. Reject non-finite inputs, unknown fields, wrong horizons, missing reasons, invalid rates, and full-year values below elapsed actual revenue/capex/depreciation.
- [x] Implement `candidate_financial_inputs(baseline, cutoff_at)`, `financial_model_market_snapshot(context)`, and `calculate_financial_model(inputs, baseline, market, cutoff_at)`. Candidate paths are explicit research assumptions, structurally anchored to the new baseline and never loaded from the old strategy fixture.
- [x] Run `.venv/bin/python -m pytest tests/test_company_research_financial_model.py -q` and inspect numerical assertions.

### Task 3: Append-only model draft API

Files: `backend/app/underwriting/persistence/company_research_models.py`, `backend/alembic/versions/0074_company_research_financial_drafts.py`, `backend/app/underwriting/services/company_research_financial_workspace.py`, `backend/app/underwriting/api/company_research_financial_router.py`, targeted backend tests.

- [x] Test published-parent authentication, same-project ownership, expected-latest conflicts, idempotent retry/conflict, payload tampering, old-record replay, and original publication preservation.
- [x] Add GET `projects/{id}/financial-model`, POST `.../drafts`, GET `.../drafts/{draft_id}` and GET `.../drafts/{draft_id}/export` under the existing company-research product prefix. Store baseline, market and exact inputs/results in a hashed envelope; status is `unreviewed`.
- [x] Resolve existing frozen market snapshots read-only; do not call initializer preparation or LLM. Add SQLite/PostgreSQL append-only triggers and migration round-trip checks.
- [x] Run focused service/API/migration checks and company publication regressions.

### Task 4: Forecast and valuation interface

Files: new `CompanyFinancialModelPanel` component/styles/tests, `frontend/src/data/investmentResearchApi.ts`, existing `ResearchWorkbenchPage.tsx` and related tests.

- [x] Test baseline display, editing, save/recompute, history/replay/export, stale responses, and preserving edits after API errors.
- [x] Add the panel to completed frozen projects' original forecast/valuation pages. Show all forward values as assumptions, sources with scope/period, conditional status and value/price gap labels. Keep readable scenario tabs and accessible field labels.
- [x] Run focused Vitest tests, TypeScript and production build.

### Task 5: Independent review and live run

- [x] Review financial definitions and data lineage independently of implementation; fix material issues.
- [x] Back up live DB, apply migration, restart only idle local preview services, and verify health.
- [x] Open the actual Alphabet project, save and calculate a model draft, reload, inspect the valuation and replay/export. Verify original frozen revision/export are identical, no LLM calls were added, and no forecast was marked human-confirmed.
- [x] Save a verification record and user-readable model draft export under `outputs/company-financial-model-20260910/`; update this checklist with actual evidence.

## Completion evidence — 2026-09-10

All five tasks are complete for the conditional draft increment. Final verification: backend 101 passed, frontend 250 passed, TypeScript/build successful, generated contracts identical. Two actual browser-saved drafts, reload/history/scenario switching/export, read-only archived replay and isolated SQLite migration round trip passed. Original frozen report hashes are unchanged and no LLM calls were added. See `docs/2026-09-10-company-financial-model-verification.md` for identities, receipts, known limitations and reproduction commands. PostgreSQL was not executed. Formal assumption review and a new research publication remain subsequent work, outside this increment.
