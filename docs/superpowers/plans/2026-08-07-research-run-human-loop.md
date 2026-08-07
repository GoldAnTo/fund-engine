# Research Run Human Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a real API-backed automatic-research, proposal-review, assessment-review, conclusion loop with durable background execution.

**Architecture:** `ResearchRun` remains the user-facing lifecycle record; a new persistent worker claims queued runs and drives the existing orchestration in bounded steps. Prototype UI owns start/cancel/case selection and proposal review, while the existing case workbench remains the assessment-review seam.

**Tech Stack:** FastAPI, SQLAlchemy 2, SQLite/PostgreSQL, React 18, TypeScript, Vitest, Playwright.

---

### Task 1: Lock orchestration isolation with regression tests

**Files:**
- Modify: `backend/tests/test_auto_research_api.py`
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/scripts/run_ai_engine.py`

- [ ] Add tests showing a run only extracts versions belonging to its case and that run progress counts its case's formal evidence links.
- [ ] Run the targeted tests and observe the current isolation/count failure.
- [ ] Add a case-filtered pending-version query and replace the invalid case-id-as-thesis-id count with a case-scoped count.
- [ ] Re-run the targeted tests.

### Task 2: Make ResearchRun durable background work

**Files:**
- Modify: `backend/app/models/operational.py`
- Modify: `backend/app/repositories/auto_research.py`
- Modify: `backend/app/services/auto_research.py`
- Create: `backend/app/services/research_run_worker.py`
- Create: `backend/app/scripts/run_research_worker.py`
- Modify: `backend/app/api/v1/auto_research.py`
- Modify: `backend/tests/test_auto_research_api.py`

- [ ] Add a failing test that start returns queued without inline provider work and a worker later reaches review/terminal state.
- [ ] Add claim metadata and atomic worker claim/recovery semantics to ResearchRun.
- [ ] Implement worker polling/one-shot command and cancellation checks at extract/task boundaries.
- [ ] Return job/run events suitable for polling and test start/cancel/recovery behavior.

### Task 3: Build the proposal review UI seam

**Files:**
- Modify: `frontend/src/domain/prototypeTypes.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/pages/prototype/ReviewWorkbenchScreen.tsx`
- Modify: `frontend/src/pages/prototype/AutoResearchRunsScreen.tsx`
- Create: `frontend/src/tests/AutoResearchHumanLoop.test.tsx`

- [ ] Add failing UI tests for starting/cancelling a case run and publishing a pending proposal.
- [ ] Add typed proposal-queue loading and decision methods.
- [ ] Add run start/cancel/case selector, proposal decision form, and navigation back to case/conclusion.
- [ ] Verify the focused UI tests.

### Task 4: Align development modes and runtime tests

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/playwright.config.ts`
- Modify: `README.md`
- Delete: `frontend/src/tests/DocumentLibraryPage.test.tsx`
- Delete: `frontend/src/tests/ReviewWorkbenchPage.test.tsx`
- Delete: `frontend/src/tests/WorkspaceOverviewPage.test.tsx`
- Create: `frontend/e2e/real-api-human-loop.spec.ts`
- Create: `scripts/start-live-e2e.sh`

- [ ] Add engine/version and explicit `dev:mock`/`dev:live` scripts.
- [ ] Replace stale legacy component tests with prototype-page tests.
- [ ] Seed a temporary SQLite backend and run a Playwright project without `?client=mock` through the human loop.
- [ ] Run full backend/frontend/release-gate verification.
