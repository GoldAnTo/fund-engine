# Workflow Projection Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Task 8 workflow projection honest for legacy decisions, linearly bounded for source ledgers, uniformly UTC-aware, and stable under concurrent forward pagination.

**Architecture:** Put closed workflow vocabulary, UTC normalization, decision classification, ledger mapping, and cursor encoding behind a small workflow-policy module. Keep acquisition persistence details behind its existing public module seam, but replace product joins with fixed independent batch reads and deterministic in-memory assembly. The main response exposes exact counts plus at most 20 ledger summaries; authenticated events and ledger endpoints provide stable forward pagination.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2, SQLite/PostgreSQL, pytest, OpenAPI TypeScript generation.

---

### Task 1: Legacy decision truth

**Files:**
- Create: `backend/app/domain/research_workflow.py`
- Modify: `backend/app/queries/research_workflow.py`
- Test: `backend/tests/test_research_workflow_api.py`

- [ ] Insert a pre-upgrade `complete_research_protocol` payload containing only legacy reason/thesis fields and assert GET workflow returns 200, `recovery.status/reason == "incomplete_decision_context"`, a server-recovery system action, `user_action is None`, and unchanged diagnostic payload.
- [ ] Run the legacy test and verify the current direct dictionary indexing fails with HTTP 500.
- [ ] Add a closed `classify_protocol_decision(payload)` function that returns either a validated typed decision or an incomplete diagnostic result without synthesizing recommendation content.
- [ ] Elevate incomplete context through recovery/stage/system-action mapping while leaving the persisted state and payload untouched; preserve existing typed payload mapping byte-for-byte.
- [ ] Run legacy and current typed decision tests and verify both pass.

### Task 2: Linear acquisition ledger reads

**Files:**
- Modify: `backend/app/domain/acquisition.py`
- Modify: `backend/app/repositories/acquisition.py`
- Modify: `backend/app/services/acquisition.py`
- Test: `backend/tests/test_acquisition_service.py`
- Test: `backend/tests/test_research_workflow_api.py`

- [ ] Seed multiple attempts, references/artifacts/bindings, decisions/assignments, and exceptions on one job and instrument SQL row counts; assert repository input and output grow as sums, never cross-products.
- [ ] Compile captured SELECT statements and assert no statement joins both attempts and references, or decisions and exceptions.
- [ ] Run the tests and verify current product joins violate the assertions.
- [ ] Replace the two product joins with fixed independent queries for jobs, attempts, references/artifacts/bindings, decisions/assignments/evidence, exceptions, and manual mappings; assemble records by IDs in deterministic order.
- [ ] Run the linearity, eight-status, drilldown, query-count, and tenant-isolation tests.

### Task 3: Bounded main ledger and authenticated pagination

**Files:**
- Modify: `backend/app/domain/research_workflow.py`
- Modify: `backend/app/schemas/v1/research_workflow.py`
- Modify: `backend/app/queries/research_workflow.py`
- Modify: `backend/app/api/v1/research_workflow.py`
- Test: `backend/tests/test_research_workflow_api.py`

- [ ] Seed more than 20 ledger records and assert main workflow returns exact counts, `items` limited to 20, `total`, and `has_more`.
- [ ] Assert GET `/api/v1/event-research/{case_id}/workflow/ledger` supports optional closed-status filter and opaque cursor pages with complete typed item fields, tenant/case isolation, stable order, no duplicates, and no omissions within the first page high-watermark.
- [ ] Run tests and verify the endpoint is missing and the main response is unbounded.
- [ ] Add a versioned opaque cursor containing filter, after-key, and high-watermark; reject malformed/filter-mismatched cursors with the project validation envelope.
- [ ] Map full ledger records through one mapper and expose the 20-item summary plus paged endpoint through the same policy vocabulary.
- [ ] Run bounded response and ledger pagination tests.

### Task 4: UTC-aware workflow wire contract

**Files:**
- Modify: `backend/app/domain/research_workflow.py`
- Modify: `backend/app/schemas/v1/research_workflow.py`
- Modify: `backend/app/queries/research_workflow.py`
- Test: `backend/tests/test_research_workflow_api.py`
- Test: `backend/tests/test_research_orchestration_postgres.py`

- [ ] Seed independently known naive SQLite values and assert every workflow/events/ledger datetime string ends in `Z` or `+00:00`, with the expected UTC wall time.
- [ ] Add equivalent PostgreSQL response/projection coverage when `TEST_DATABASE_URL` is available.
- [ ] Run UTC tests and verify event, conclusion, monitor, started, and updated fields currently serialize without offsets where not normalized.
- [ ] Add one `as_utc` policy helper and Pydantic pre-validation for every workflow DTO datetime so naive database values are interpreted as UTC and aware values are converted to UTC.
- [ ] Route query parsing, event, acquisition, conclusion, monitor, ledger, recovery, and top-level timestamps through the helper.
- [ ] Run SQLite/PG UTC tests.

### Task 5: Forward event cursor concurrency

**Files:**
- Modify: `backend/app/schemas/v1/research_workflow.py`
- Modify: `backend/app/queries/research_workflow.py`
- Modify: `backend/app/api/v1/research_workflow.py`
- Test: `backend/tests/test_research_workflow_api.py`

- [ ] Request exactly `limit` events, assert the non-empty page returns its last sequence as `next_cursor` with `has_more == false`, append a later event, and assert continuation after the cursor returns it exactly once.
- [ ] Assert `after` and legacy `cursor` are equivalent and providing both is rejected.
- [ ] Run the concurrency test and verify current `next_cursor` is null at the exact boundary.
- [ ] Make the endpoint explicitly forward-only: resolve `after` from the new parameter or legacy cursor, query `sequence > after`, return `has_more`, and always return the last sequence for non-empty pages.
- [ ] Run event pagination, authentication, missing-workflow, and case-isolation tests.

### Task 6: Closed vocabulary and generated contracts

**Files:**
- Modify: `backend/app/domain/research_workflow.py`
- Modify: `backend/app/domain/acquisition.py`
- Modify: `backend/app/schemas/v1/research_workflow.py`
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/contracts/v1.ts`
- Test: `backend/tests/test_research_workflow_api.py`

- [ ] Assert schema status vocabulary, acquisition record annotation, query counts, and status filters all equal the same exported eight-value tuple.
- [ ] Remove duplicate status literals from schema/query/repository and derive validation/count initialization from the policy tuple.
- [ ] Run `bash scripts/sync-contract.sh --update`, frontend typecheck, and frontend build.

### Task 7: Verification and exact commit

**Files:** all files above.

- [ ] Run focused workflow API/commands, acquisition linearity, schema/migration, SQLite, and available PostgreSQL tests.
- [ ] Run affected acquisition/orchestration regressions and full backend excluding `test_verify_live_event_ui.py`.
- [ ] Run Ruff on changed Python files and `git diff --check`.
- [ ] Commit all changes once with exact message `fix: bound and stabilize workflow projection` and verify a clean worktree.
