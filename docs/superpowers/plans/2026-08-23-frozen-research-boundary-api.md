# Frozen Research Boundary API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return a selected immutable research version’s recorded answerability and explicit Unknown evidence gaps without a CATL-specific, current-state, relation, fixture, or cross-object fallback.

**Architecture:** The read service first validates the selected revision’s existing sealed parent graph. It exposes a typed boundary only from parents in that graph: a strictly sealed answerability record and zero or more strictly parsed unknown-gap reality ledger records. The archive page consumes this new GET-only endpoint, identity-binds the result to the already validated revision, and never infers gaps from status labels.

**Tech Stack:** Python 3.11, SQLAlchemy 2, FastAPI, Pydantic v2, pytest; React 18, TypeScript, React Router, Vitest, generated OpenAPI TypeScript.

---

### Task 1: Seal and read generic answerability from a revision parent graph

**Files:**
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Test: `backend/tests/underwriting/test_research_revision_diff.py`

- [ ] **Step 1: Write RED tests:** a generic v3 revision with one answerability parent returns its exact state, blockers, debt keys, mandate flag, allowed research action, and requirements; a raw payload/created-at/hash tamper causes both summary and boundary reads to fail. Zero or multiple answerability parents is explicit (null or 422 as specified), never inferred answerable.
- [ ] **Step 2: Confirm RED:** `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py -k boundary_answerability`.
- [ ] **Step 3: Implement:** add frozen boundary dataclasses. Replace the generic “unsealable answerability” bypass with strict parent validation: exact scope/basis, controlled fields, finite/valid content, `created_at <= cutoff`, and `answerability_content_hash` recomputation. `revision_boundary(revision_id)` must call `revision_summary` and resolve only IDs listed in its checked parent refs.
- [ ] **Step 4: Confirm GREEN and commit:** run the focused revision/history/archive suite and commit `feat: read frozen research answerability boundaries`.

### Task 2: Parse explicit unknown-gap parent records without cross-object leakage

**Files:**
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Test: `backend/tests/underwriting/test_research_revision_diff.py`
- Test: `backend/tests/underwriting/test_catl_baseline.py`

- [ ] **Step 1: Write RED tests:** selected-parent `ledger/reality/unknown_evidence_gap` is returned only when every payload field is valid, time-consistent, and no later than cutoff. A malformed/late/hash-tampered/duplicate gap returns 422. An unreferenced same-basis gap, a cross-object industry gap, and a later successor never appear.
- [ ] **Step 2: Confirm RED:** `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py tests/underwriting/test_catl_baseline.py -k boundary_gap`.
- [ ] **Step 3: Implement:** parse a strict typed gap payload from only sealed ledger parents; require `ledger_kind=reality`, `family_key=evidence_gap:{metric_key}`, `entry_type=unknown_evidence_gap`, `observation_status=unknown`, exact ledger/payload effective and available timestamps, locator/source/unit/period/dimensions shape, and descriptor/hash agreement. Existing CATL company revision has no cross-object industry gap parent: return its sealed answerability plus `[]`, never perform a relation, current-ledger, or fixture read.
- [ ] **Step 4: Confirm GREEN and commit:** run focused CATL and revision tests; commit `feat: read frozen unknown evidence gaps`.

### Task 3: Expose strict read-only boundary API and generated contract

**Files:**
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Test: `backend/tests/underwriting/test_research_revision_diff_api.py`
- Test: `backend/tests/underwriting/test_openapi_dump.py`
- Modify/generated: `frontend/openapi.json`, `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Write RED API/OpenAPI tests:** `GET /api/underwriting/v1/research-versions/{revision_id}/boundary` returns a strict typed response tied to revision/object/basis/content hash; 404 and validation failures use underwriting envelopes; POST is 405; schema has no price, valuation, recommendation, or trading fields.
- [ ] **Step 2: Confirm RED:** `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py tests/underwriting/test_openapi_dump.py -k boundary`.
- [ ] **Step 3: Implement route/DTOs:** add exactly one GET response, all fields extra-forbid; no writes, commits, fixture loads, relation/current lookup, or `economic-models` reuse.
- [ ] **Step 4: Safely generate:** save and assert the existing `frontend/openapi.json` user patch is exactly 8/8; run dump/generation/typecheck/build; stage generated schema changes before restoring the saved user patch; verify no user `Unprocessable Content` hunk is staged.
- [ ] **Step 5: Confirm GREEN and commit:** commit only feature/generated files as `feat: expose frozen research boundaries`; leave the user patch unstaged.

### Task 4: Render only the checked frozen boundary

**Files:**
- Modify: `frontend/src/data/underwritingResearchApi.ts`
- Modify: `frontend/src/features/underwriting/ResearchArchivePage.tsx`
- Test: `frontend/src/data/underwritingResearchApi.test.ts`
- Test: `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`
- Modify: `docs/architecture/underwriting-research.md`

- [ ] **Step 1: Write RED tests:** selected detail loads boundary alongside revision/diff; a mismatched revision/object/basis/hash, malformed gap, or extra response field fails closed. CATL shows returned `not_answerable` and requirements, while its empty parent-graph gaps say no displayable Unknown gap was recorded in this version rather than claiming no industry gap. Assert no `/economic-models` call and no inferred status copy.
- [ ] **Step 2: Confirm RED:** `cd frontend && npm test -- ResearchArchivePage.test.tsx underwritingResearchApi.test.ts`.
- [ ] **Step 3: Implement:** add GET-only `boundary(id)`; strict client response binding; replace status-derived research-boundary panel with typed answerability/gaps. Use only returned texts/controlled labels; retain all no-price/no-valuation/no-recommendation boundaries.
- [ ] **Step 4: Confirm GREEN and release-gate:** full underwriting pytest, compileall, focused frontend tests/typecheck/build, and `git diff --check`; document exact parent-graph boundary semantics. Preserve the user OpenAPI patch outside commits.

## Acceptance checklist

- [ ] Boundary data derives only from selected sealed parent IDs, not current rows, relations, fixtures, or latest state.
- [ ] Answerability details are immutable/hash-verified and malformed evidence fails closed.
- [ ] Unknown gap records are explicit and structured; absent displayed gaps are not misrepresented as resolved gaps.
- [ ] CATL continues to be `not_answerable / wait_for_validation`; IndustryState is not fabricated.
- [ ] The UI makes the frozen version’s limitations legible without price, valuation, or action guidance.

