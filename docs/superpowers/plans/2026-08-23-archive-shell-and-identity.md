# Archive Shell and Frozen Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a deep-linked immutable research archive self-identifying and isolated from event-research runtime data.

**Architecture:** Extend the persisted-only revision-history model with the immutable `uw_research_objects` identity already selected by the revision family. Route archive paths outside `AppShell` to a minimal archive-only shell with no event, worker, disclosure or mock imports. Regenerate the API contract safely while preserving the user’s uncommitted OpenAPI change.

**Tech Stack:** Python 3.11, SQLAlchemy 2, FastAPI, Pydantic v2, pytest; React 18, TypeScript, React Router, Vitest, Testing Library.

---

### Task 1: Return immutable object identity with revision history

**Files:**
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Test: `backend/tests/underwriting/test_research_revision_diff.py`
- Test: `backend/tests/underwriting/test_research_revision_diff_api.py`

- [ ] **Step 1: Write RED tests:** assert `revision_history` and its API response include `object_kind`, `canonical_name`, and `external_key` for the immutable object addressed by the revision family.
- [ ] **Step 2: Confirm RED:** run `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py tests/underwriting/test_research_revision_diff_api.py -k history_identity`; it must fail before the fields exist.
- [ ] **Step 3: Implement persisted-only identity:** add the three fields to frozen `RevisionHistory`; resolve exactly the addressed `UnderwritingResearchObject` under `Session.no_autoflush`, raising `ValidationError` for a missing/mismatched row. Add strict `ResearchObjectKind`, `canonical_name`, and `external_key` to `ResearchRevisionHistoryResponse` and map only this read-service result. Do not query a current security, event, pricing, or fixture record.
- [ ] **Step 4: Confirm GREEN and commit:** run `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py tests/underwriting/test_research_revision_diff_api.py -k 'history or archive'`, then commit only Task 1 files as `feat: identify immutable research revision histories`.

### Task 2: Isolate archive routes and render returned identity

**Files:**
- Create: `frontend/src/app/UnderwritingArchiveShell.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/features/underwriting/ResearchArchivePage.tsx`
- Test: `frontend/src/app/routes.test.tsx`
- Test: `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`

- [ ] **Step 1: Write RED tests:** deep-link archive rendering finds `aria-label="不可变研究档案导航"` and does not mount `aria-label="研究工作台导航"`; a checked history response renders its returned canonical name, external key, and object kind.
- [ ] **Step 2: Confirm RED:** run `cd frontend && npm test -- ResearchArchivePage.test.tsx routes.test.tsx`; it must fail while archive routes are inside `AppShell` and do not render identity.
- [ ] **Step 3: Implement the isolated shell:** create `UnderwritingArchiveShell` containing only archive title/directory navigation and `<Outlet />`; put the two underwriting archive routes under it, outside the `AppShell` route. The shell imports no event, worker, fund-disclosure, mock, or research client. Render only `history.canonical_name`, `history.external_key`, and a controlled kind label; never derive identity from URL, current records, or fixtures.
- [ ] **Step 4: Confirm GREEN and commit:** run `cd frontend && npm test -- ResearchArchivePage.test.tsx routes.test.tsx underwritingResearchApi.test.ts && npm run typecheck`, then commit only Task 2 files as `feat: isolate immutable research archive routes`.

### Task 3: Safely generate the contract and release-gate it

**Files:**
- Modify/generated: `frontend/openapi.json`, `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/underwritingResearchApi.ts`
- Modify: `docs/architecture/underwriting-research.md`
- Test: `backend/tests/underwriting/test_openapi_dump.py`

- [ ] **Step 1: Write RED OpenAPI test:** assert `ResearchRevisionHistoryResponse` contains `object_kind`, `canonical_name`, and `external_key`.
- [ ] **Step 2: Confirm RED:** run `cd backend && pytest -q tests/underwriting/test_openapi_dump.py -k revision_history_identity`.
- [ ] **Step 3: Safely regenerate:** save `git diff -- frontend/openapi.json` to a mktemp patch and assert its numstat is exactly `8 8 frontend/openapi.json`; run backend dump plus frontend contract generation/typecheck/build; stage only generated `frontend/openapi.json` and `frontend/src/contracts/v1.ts`, verify staged OpenAPI has no `Unprocessable Content`, then apply the saved user patch and reassert exact `8/8` unstaged diff. Stop without staging if restoration fails. Replace temporary client history types with generated aliases only after generation.
- [ ] **Step 4: Document and verify:** document that identity comes from immutable research-object history and that the archive shell never loads event/current data. Run full underwriting pytest, Python compileall, focused frontend tests, `git diff --check`, and status. Commit feature files plus staged generated files only; leave the eight-hunk user patch unstaged.

## Acceptance checklist

- [ ] A deep-linked archive identifies its immutable company/industry object without a current event or market lookup.
- [ ] Archive routes never mount `AppShell`, event polling, run strips, search, or automatic-research controls.
- [ ] Object identity, history, revision, and adjacent diff remain checked, persisted-only, and fail closed.
- [ ] Generated contract matches history identity and the user OpenAPI patch stays outside every feature commit.
