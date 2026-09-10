# Event Research Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect scope confirmation to real, goal-driven source acquisition, automatic evidence synthesis, an explicitly unreviewed system conclusion, monitoring, and one event-centered workflow projection that tells the user what the system is doing and what—if anything—the user must do.

**Architecture:** Add a durable `ResearchOrchestrationService` as the sole event-workflow state owner. It coordinates existing `AutoResearchService`, `AcquisitionModule`, conclusion, and monitor services only through their public seams; it never calls source adapters or edits acquisition tables directly. The server persists immutable query plans and workflow events, derives coverage per frozen goal, and exposes one projection consumed by a focused React workflow component.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, existing acquisition adapters/worker, React 18, TypeScript, Vitest, Testing Library, pytest.

---

## Preconditions and boundaries

- Implement in `/Users/xiongjiali/code/fund-engine/.worktrees/event-research-mainline` on branch `codex/event-research-mainline`, based on `73b8058`.
- Read `docs/superpowers/specs/2026-08-15-event-orchestrated-research-mainline-design.md` before Task 1.
- Preserve the existing governed acquisition seam. `ResearchOrchestrationService` may call `AcquisitionModule.request/read`; it may not import SSE, SZSE, Gildata, HTTP transport, or acquisition repository internals.
- Current real source scope is SSE, SZSE, and configured Gildata. Do not describe CNINFO, HKEX, company IR, or general web discovery as implemented until a real adapter and live test exist.
- Technical retries reuse the exact frozen query plan. Only a new acquisition round can create an expanded plan, with a recorded trigger and diff.
- `automatically_admitted` evidence may drive system synthesis and a system draft; it must never be relabeled as `reviewed` or assigned a human reviewer.
- No default manual evidence or conclusion gate. The workbench must label generated output `系统生成，未经人工审核` and keep optional review commands available.
- The legacy `EventResearchLifecycle` remains a compatibility projection during migration; the new workflow projection is the only source for the new UI.
- Migration `0055` belongs to this plan. If the branch gains another migration first, determine the Alembic head and use the next revision without creating multiple heads.

## File map

- Create `backend/app/models/research_orchestration.py` for workflow, event, query-plan, and goal-coverage records.
- Create `backend/app/repositories/research_orchestration.py` for locking, event sequence, checkpoint, and idempotent transition persistence.
- Create `backend/app/services/research_orchestration.py` for workflow commands and reconciliation.
- Create `backend/app/services/research_acquisition.py` for translating frozen scope goals to the acquisition seam.
- Create `backend/app/services/acquisition_coverage.py` for goal-level coverage decisions.
- Create `backend/app/queries/research_workflow.py`, `backend/app/schemas/v1/research_workflow.py`, and `backend/app/api/v1/research_workflow.py` for the unified read/command API.
- Create `frontend/src/domain/eventWorkflow.ts`, `frontend/src/features/case/EventWorkflowOverview.tsx`, `frontend/src/styles/event-workflow.css`, and focused tests.
- Modify `backend/app/services/auto_research.py`, `backend/app/services/acquisition_runner.py`, `backend/app/services/event_research_scope_evidence.py`, `backend/app/services/event_conclusion.py`, and `backend/app/scripts/run_research_worker.py` only at their public orchestration boundaries.
- Modify `frontend/src/features/events/EventCreatePage.tsx`, `frontend/src/features/case/CasePages.tsx`, `frontend/src/data/httpResearchAdapter.ts`, `frontend/src/domain/eventResearch.ts`, and `frontend/src/main.tsx` minimally.

## Task 1: Freeze the orchestration vocabulary and persistence contract

**Files:**
- Create: `backend/app/models/research_orchestration.py`
- Create: `backend/alembic/versions/0055_research_orchestration.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/ledger.py`
- Test: `backend/tests/test_research_orchestration_schema.py`
- Test: `backend/tests/test_research_orchestration_postgres.py`

- [ ] **Step 1: Write the failing metadata test**

```python
EXPECTED = {
    "research_orchestrations",
    "research_orchestration_events",
    "acquisition_query_plans",
    "acquisition_goal_coverages",
}
assert EXPECTED <= set(Base.metadata.tables)
```

Also assert unique constraints on `(tenant_id, research_case_id)`, `(orchestration_id, sequence)`, `(acquisition_job_id, acquisition_round)`, and `(research_run_id, scope_version_id, goal_id)`.

- [ ] **Step 2: Run the test and verify missing tables**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_orchestration_schema.py -v`

Expected: FAIL because the four tables are absent.

- [ ] **Step 3: Implement explicit workflow records**

Use constrained strings rather than frontend-derived states:

```python
WorkflowState = Literal[
    "intake", "awaiting_scope_confirmation", "planning_acquisition",
    "acquiring", "freezing_sources", "assessing_coverage",
    "synthesizing_evidence", "adjudicating_thesis", "generating_report",
    "monitoring", "retry_wait", "recovering", "needs_scope_decision",
    "exhausted", "cancelled", "failed",
]
```

`ResearchOrchestration` stores tenant, case, current scope/run, state, user-visible stage, current system action and reason, checkpoint JSON, optional next-action kind/label/payload, heartbeat, recovery status, integer version, and timestamps. `ResearchOrchestrationEvent` is append-only and stores sequence, transition, actor, message, payload, idempotency key, and time. `AcquisitionSeries` is the stable tenant/Case/run/scope/thesis/goal aggregate. `AcquisitionQueryPlan` is immutable and stores series, one round-specific job, round, goal, planner/policy versions, frozen inputs, ordered queries, same-series previous-plan ID, expansion trigger, and diff JSON. `AcquisitionGoalCoverage` stores required/observed authority and independent-source counts, contrary-search completion, status, reason codes, and evidence-link IDs. Migration `0056` moves predecessor enforcement from same-job lineage to same-series lineage without rewriting `0055`.

- [ ] **Step 4: Implement migration and append-only protection**

Set `down_revision = "0054"`. Add check constraints for every state/status, foreign keys to Case/scope/run/acquisition job, and immutable UPDATE/DELETE triggers for events/query plans. Register immutable tables in `IMMUTABLE_TABLES`.

- [ ] **Step 5: Verify SQLite and PostgreSQL behavior**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_orchestration_schema.py -v`

Run with PostgreSQL: `cd backend && TEST_DATABASE_URL=postgresql+psycopg://evidence:evidence@127.0.0.1:5432/evidence .venv/bin/python -m pytest tests/test_research_orchestration_postgres.py -v`

Expected: PASS; PostgreSQL rejects UPDATE/DELETE of immutable records.

- [ ] **Step 6: Commit the persistence contract**

```bash
git add backend/app/models backend/alembic/versions/0055_research_orchestration.py backend/tests/test_research_orchestration_schema.py backend/tests/test_research_orchestration_postgres.py
git commit -m "feat: persist event research orchestration"
```

## Task 2: Implement one locked, idempotent orchestration state owner

**Files:**
- Create: `backend/app/repositories/research_orchestration.py`
- Create: `backend/app/services/research_orchestration.py`
- Test: `backend/tests/test_research_orchestration_service.py`

- [ ] **Step 1: Write transition, concurrency, and replay tests**

```python
first = service.confirm_scope(case_id, principal=principal, idempotency_key="scope:v1")
second = service.confirm_scope(case_id, principal=principal, idempotency_key="scope:v1")
assert second.id == first.id
assert second.state == "planning_acquisition"
assert event_count(second.id, "scope_confirmed") == 1
```

Cover illegal transitions, two concurrent confirmations, event sequence monotonicity, compatibility lifecycle updates, and rollback when outbox emission fails.

- [ ] **Step 2: Run and verify import failure**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_orchestration_service.py -v`

Expected: FAIL because repository and service do not exist.

- [ ] **Step 3: Implement the deep repository**

Expose only `get_for_update`, `create_if_absent`, `transition`, `append_event`, `record_heartbeat`, and `find_reconcilable`. `transition` must compare the expected version, update the compatibility lifecycle, append one event, and call `emit_event` in the same transaction.

- [ ] **Step 4: Implement legal transition commands**

The service public API contains five typed methods: `confirm_scope(case_id, principal, idempotency_key)`, `record_acquisition_progress(case_id, snapshot, idempotency_key)`, `reconcile(case_id, principal)`, `decide_scope(case_id, principal, decision, idempotency_key)`, and `recover(case_id, principal, stale_before)`. Each returns the locked `ResearchOrchestration` projection.

Store concrete user-facing action text with every transition. For automatic states, `next_action_kind` is null and the projection renders `当前无需操作`; for `needs_scope_decision`, persist one action with attempted rounds, unresolved goals, recommendation, and impact.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_orchestration_service.py -v`

Expected: PASS.

```bash
git add backend/app/repositories/research_orchestration.py backend/app/services/research_orchestration.py backend/tests/test_research_orchestration_service.py
git commit -m "feat: add event workflow state owner"
```

## Task 3: Make scope confirmation start the automatic mainline

**Files:**
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/services/event_research.py`
- Modify: `backend/app/services/auto_research.py`
- Create: `backend/app/schemas/v1/research_workflow.py`
- Create: `backend/app/api/v1/research_workflow.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_research_workflow_commands.py`

- [ ] **Step 1: Write the failing API test**

```python
response = client.post(
    f"/api/v1/event-research/{case_id}/workflow/confirm",
    headers=auth_headers,
    json={"idempotency_key": f"scope:{scope_id}"},
)
assert response.status_code == 202
assert response.json()["state"] == "planning_acquisition"
assert response.json()["next_action"] is None
```

Assert the server uses the frozen current scope, rejects a stale scope version, and creates exactly one research run under repeated requests.

- [ ] **Step 2: Run and verify 404**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_workflow_commands.py -v`

Expected: FAIL with route not found.

- [ ] **Step 3: Separate run preparation from execution enqueueing**

Add `enqueue: bool = True` to `AutoResearchService.start`. The orchestration path supplies `trigger="scope_confirmation"`, `enqueue=False`, and `commit=False` so the research worker cannot synthesize before acquisitions finish. Existing callers retain current behavior.

- [ ] **Step 4: Add the confirm command**

In one transaction: lock Case/lifecycle, verify current scope and active theses, create/reuse orchestration, prepare run, transition to `planning_acquisition`, append `scope_confirmed`, and emit `research.acquisition.requested`. Do not accept actor, reviewer, thesis text, source policy, or tenant from request JSON.

- [ ] **Step 5: Remove the default protocol gate for new event Cases**

Set `CreateEventResearchRequest.research_protocol_required` default to `False` and make `EventCreatePage` explicit only when a policy requires it. Preserve protocol-required behavior for existing Cases.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_workflow_commands.py tests/test_auto_research_api.py -v`

Expected: PASS.

```bash
git add backend/app/schemas/v1 backend/app/services/event_research.py backend/app/services/auto_research.py backend/app/api/v1 backend/tests/test_research_workflow_commands.py
git commit -m "feat: start research from scope confirmation"
```

## Task 4: Persist goal-bound query plans and make retries deterministic

**Files:**
- Modify: `backend/app/domain/acquisition.py`
- Modify: `backend/app/acquisition/policy.py`
- Modify: `backend/app/repositories/acquisition.py`
- Modify: `backend/app/services/acquisition.py`
- Modify: `backend/app/services/acquisition_runner.py`
- Test: `backend/tests/test_acquisition_query_plan.py`
- Test: `backend/tests/test_acquisition_runner.py`

- [ ] **Step 1: Write failing frozen-plan tests**

Create a job, force a retryable search failure, retry it, and assert one plan row and byte-equivalent ordered queries. Create round 2 with an explicit expansion trigger and assert a second plan references round 1 and records added/removed queries.

- [ ] **Step 2: Run and verify missing plan persistence**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_query_plan.py -v`

Expected: FAIL because the runner replans on execution.

- [ ] **Step 3: Extend the immutable request contract**

Add non-null `scope_version_id`, stable `goal_id`, `planner_version`, and `previous_query_plan_id` to `AcquisitionRequest`. The idempotency key format is `run:{run_id}:scope:{scope_id}:goal:{goal_id}:round:{round}`.

- [ ] **Step 4: Freeze before the first search**

`AcquisitionModule.request` plans once, persists the ordered plan, stores its ID in the job request snapshot, and then queues the job. `AcquisitionRunner` loads that row for every attempt and fails closed if the snapshot or policy version differs. It never calls the planner during a retry.

Each round creates a distinct immutable `AcquisitionJob`. `AcquisitionSeries` owns lineage across those jobs and enforces one plan per job plus one plan per series/round.

- [ ] **Step 5: Record searchable run evidence**

Append events for `query_plan_frozen`, each search result page, retry/source switch, and `query_plan_expanded`. Safe event payloads include goal, adapter, query index, result count, policy version, and reason; they exclude credentials and cookies.

- [ ] **Step 6: Run acquisition regressions and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_query_plan.py tests/test_acquisition_runner.py tests/test_acquisition_repository.py tests/test_acquisition_service.py -v`

Expected: PASS.

```bash
git add backend/app/domain/acquisition.py backend/app/acquisition/policy.py backend/app/repositories/acquisition.py backend/app/services/acquisition.py backend/app/services/acquisition_runner.py backend/tests/test_acquisition_query_plan.py backend/tests/test_acquisition_runner.py
git commit -m "feat: freeze goal-driven acquisition plans"
```

## Task 5: Dispatch real acquisition goals from the frozen event scope

**Files:**
- Create: `backend/app/services/research_acquisition.py`
- Modify: `backend/app/services/research_orchestration.py`
- Modify: `backend/app/scripts/run_research_worker.py`
- Test: `backend/tests/test_research_acquisition.py`
- Test: `backend/tests/test_research_worker_entrypoint.py`

- [ ] **Step 1: Write failing goal-dispatch tests**

For every active thesis, assert support and contradiction goals; add an alternative-explanation goal for the event-level research question. Assert entity, code, metric, period, cutoff, allowed roles, scope, run, round, and stable goal ID come from frozen server records.

- [ ] **Step 2: Run and verify missing bridge**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_acquisition.py -v`

Expected: FAIL because `ResearchAcquisitionService` is absent.

- [ ] **Step 3: Implement the acquisition bridge**

`ResearchAcquisitionService.dispatch_round` translates goals and calls only `AcquisitionModule.request`. Repeated reconciliation reuses idempotency keys. It records returned job IDs in the orchestration checkpoint and transitions to `acquiring`.

- [ ] **Step 4: Reconcile before research-job claims**

At the start of `run_research_worker.run_once`, recover stale orchestration leases and reconcile a bounded batch. Acquisition still runs in `run_acquisition_worker`; the research worker only reads public acquisition job views. When all jobs are terminal it transitions to `assessing_coverage`; it does not wait inside a process.

- [ ] **Step 5: Verify restart-safe dispatch and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_acquisition.py tests/test_research_worker_entrypoint.py -v`

Expected: PASS, including a process-restart simulation that reopens a new SQLAlchemy session and creates no duplicate jobs.

```bash
git add backend/app/services/research_acquisition.py backend/app/services/research_orchestration.py backend/app/scripts/run_research_worker.py backend/tests/test_research_acquisition.py backend/tests/test_research_worker_entrypoint.py
git commit -m "feat: connect event goals to source acquisition"
```

## Task 6: Decide goal coverage and map automatically admitted evidence

**Files:**
- Create: `backend/app/services/acquisition_coverage.py`
- Modify: `backend/app/services/event_research_scope_evidence.py`
- Modify: `backend/app/queries/event_research.py`
- Modify: `backend/app/services/research_orchestration.py`
- Test: `backend/tests/test_acquisition_coverage.py`
- Test: `backend/tests/test_event_research_scope.py`

- [ ] **Step 1: Write failing coverage tests**

Cover authority requirement, independent source identity, contradiction search, cutoff, metric/period mismatch, duplicate publication keys, conflicting variants, maximum rounds, and zero-new-independent-source exhaustion. Assert total document count alone cannot produce `ready`.

- [ ] **Step 2: Run and verify failures**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_coverage.py -v`

Expected: FAIL because goal coverage is not evaluated.

- [ ] **Step 3: Implement versioned coverage policy**

Return exactly:

```python
CoverageDecision = Literal["ready", "continue", "needs_decision", "exhausted"]
```

`ready` requires all mandatory goals, authority where specified, contradiction-search completion, temporal consistency, and required independent sources. `continue` creates the next acquisition round with the unresolved goal IDs and explicit expansion trigger. `needs_decision` is used only when the next round must alter scope/source/time boundaries. `exhausted` records unresolved facts as unknown.

- [ ] **Step 4: Map automatic evidence without forging review**

Allow `review_state in {"reviewed", "automatically_admitted"}` in current-scope mapping and workbench evidence reads. Create append-only scope assignments from admission provenance and goal ID. Keep the original review state in every DTO and conclusion citation.

- [ ] **Step 5: Run focused and regression tests, then commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_acquisition_coverage.py tests/test_event_research_scope.py tests/test_event_research_api.py -v`

Expected: PASS.

```bash
git add backend/app/services/acquisition_coverage.py backend/app/services/event_research_scope_evidence.py backend/app/services/research_orchestration.py backend/app/queries/event_research.py backend/tests/test_acquisition_coverage.py backend/tests/test_event_research_scope.py
git commit -m "feat: evaluate event evidence coverage"
```

## Task 7: Automatically synthesize, draft the report, and enter monitoring

**Files:**
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/services/event_conclusion.py`
- Modify: `backend/app/services/case_monitor.py`
- Modify: `backend/app/services/research_orchestration.py`
- Test: `backend/tests/test_event_research_automatic_completion.py`

- [ ] **Step 1: Write the end-state test first**

Given ready coverage and automatically admitted evidence, reconcile until the workflow reaches `monitoring`. Assert one synthesis run, one `ai_draft`, one active monitor version, no human review row, no fabricated reviewer, and a label-compatible output state.

- [ ] **Step 2: Run and verify the manual gates block completion**

Run: `cd backend && .venv/bin/python -m pytest tests/test_event_research_automatic_completion.py -v`

Expected: FAIL at reviewed-only coverage or draft-ready manual state.

- [ ] **Step 3: Enqueue synthesis only after ready coverage**

When coverage becomes ready, transition to `synthesizing_evidence`, enqueue the prepared ResearchRun exactly once, and let the existing research worker perform extraction/assessment. Add an orchestration callback/reconciliation checkpoint for completed synthesis.

- [ ] **Step 4: Generate a bounded system draft**

`EventConclusionService.create_draft` accepts mapped `reviewed` and `automatically_admitted` links, snapshots their IDs, and uses language based on evidence state. If no factor is sufficiently supported, create an explicit insufficient-evidence draft rather than selecting a winner. The draft remains `ai_draft`, reviewer remains null, and the UI/API labels it `系统生成，未经人工审核`.

- [ ] **Step 5: Activate monitoring without publishing a human conclusion**

Create or reuse a default active `CaseMonitorVersion` from frozen factors and allowed source types, transition orchestration to `monitoring`, and append `report_generated` plus `monitoring_started`. Optional publish/review commands remain unchanged.

- [ ] **Step 6: Run tests and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_event_research_automatic_completion.py tests/test_auto_research_api.py tests/test_monitor_scheduler.py -v`

Expected: PASS.

```bash
git add backend/app/services/auto_research.py backend/app/services/event_conclusion.py backend/app/services/case_monitor.py backend/app/services/research_orchestration.py backend/tests/test_event_research_automatic_completion.py
git commit -m "feat: complete event research automatically"
```

## Task 8: Expose one truthful workflow projection

**Files:**
- Create: `backend/app/queries/research_workflow.py`
- Modify: `backend/app/schemas/v1/research_workflow.py`
- Modify: `backend/app/api/v1/research_workflow.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_research_workflow_api.py`

- [ ] **Step 1: Write failing projection tests**

Assert `GET /api/v1/event-research/{case_id}/workflow` returns event identity, six server-mapped stages, current system action/reason, recent immutable events, exactly zero-or-one user action, acquisition rounds, frozen query plans, source ledger counts, goal coverage, conclusion, monitoring, heartbeat, and recovery details.

- [ ] **Step 2: Assert failure truthfulness**

When workflow data is missing, expect a typed `workflow_not_initialized` response with a safe initialize action. When the current worker heartbeat is stale, expect `recovering` or `failed`; never copy `EventResearchLifecycle.status_summary` into a fake live status.

- [ ] **Step 3: Implement server-owned stage mapping**

Map internal states to `建立事件 / 确认命题 / 主动补证 / 证据归并 / 命题判定 / 报告与监测` in the query layer. Return stable machine codes and Chinese display text. Paginate detailed events separately under `/workflow/events` while the main endpoint returns the latest 12.

- [ ] **Step 4: Run API tests and sync OpenAPI**

Run: `cd backend && .venv/bin/python -m pytest tests/test_research_workflow_api.py -v`

Run: `./scripts/sync-contract.sh --update`

Expected: tests pass and generated frontend contracts change only for deliberate API additions.

- [ ] **Step 5: Commit the projection**

```bash
git add backend/app/queries/research_workflow.py backend/app/schemas/v1/research_workflow.py backend/app/api/v1/research_workflow.py backend/app/api/v1/router.py backend/tests/test_research_workflow_api.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: expose unified research workflow"
```

## Task 9: Make the event workbench show process and one next action

**Files:**
- Create: `frontend/src/domain/eventWorkflow.ts`
- Create: `frontend/src/features/case/EventWorkflowOverview.tsx`
- Create: `frontend/src/styles/event-workflow.css`
- Create: `frontend/src/tests/EventWorkflowOverview.test.tsx`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/features/events/EventCreatePage.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Write focused component tests**

Test an acquiring event, a `needs_scope_decision` event, monitoring, stale heartbeat, event switch, source-ledger expansion, and narrow viewport. Assert the screen contains one primary user action at most and displays `当前无需操作` while the system is running.

- [ ] **Step 2: Run and verify missing component**

Run: `npm --prefix frontend test -- --run src/tests/EventWorkflowOverview.test.tsx`

Expected: FAIL because the component and adapter method are absent.

- [ ] **Step 3: Add domain and HTTP methods**

Add `getEventWorkflow(caseId)`, `confirmEventWorkflow(caseId, scopeVersionId)`, and `decideEventScope(caseId, decision)` to the real adapter. Poll the workflow every five seconds only while a state is active; stop polling for terminal states and on unmount. Do not derive status from the old workbench if the workflow request fails.

- [ ] **Step 4: Build the focused workflow component**

Render the existing event selector/context first, then the six-stage strip, system workspace, user workspace, recent process events, goal coverage, and source ledger. Use progressive disclosure for raw query/fetch details. Every evidence row retains source role, final URL, retrieval time, hash, dedup/admission result, and drill-down link.

- [ ] **Step 5: Connect event creation to confirmation and overview**

After Case creation and optional file upload complete, call workflow confirmation with the returned current scope version and navigate to `/events/{caseId}`. Show the real transition result before navigation failure recovery; never route a successful upload to the document list as the only next step.

- [ ] **Step 6: Integrate styles without overwriting user work**

Import `event-workflow.css` in `main.tsx`. Add only a small mount point to `CasePages.tsx`; keep workflow selectors/presentation in new files and do not fold them into the existing 5,000-line component.

- [ ] **Step 7: Verify UI and commit**

Run: `npm --prefix frontend test -- --run src/tests/EventWorkflowOverview.test.tsx src/domain/eventResearchPresentation.test.ts`

Run: `npm --prefix frontend run typecheck && npm --prefix frontend run build`

Expected: PASS.

```bash
git add frontend/src/domain frontend/src/features/case/EventWorkflowOverview.tsx frontend/src/features/case/CasePages.tsx frontend/src/features/events/EventCreatePage.tsx frontend/src/styles/event-workflow.css frontend/src/tests/EventWorkflowOverview.test.tsx frontend/src/data/httpResearchAdapter.ts frontend/src/main.tsx
git commit -m "feat: show transparent event research workflow"
```

## Task 10: Prove the mainline and guard architectural boundaries

**Files:**
- Create: `backend/tests/test_event_research_mainline_postgres.py`
- Create: `backend/tests/test_orchestration_boundaries.py`
- Create: `docs/reports/2026-08-15-event-research-mainline-verification.md`
- Verify: backend and frontend suites

- [ ] **Step 1: Add a PostgreSQL mainline integration test**

Use fake source adapters only at the existing `AcquisitionModule` boundary for deterministic integration testing. Confirm scope, run acquisition/research workers in separate sessions, and assert the durable sequence `planning_acquisition → acquiring → assessing_coverage → synthesizing_evidence → generating_report → monitoring` plus immutable evidence provenance.

- [ ] **Step 2: Add import-boundary tests**

Use AST imports to assert orchestration modules never import `app.acquisition.sources`, `app.datasources.gildata`, `app.datasources.exchanges`, or `app.repositories.acquisition`. Assert frontend workflow code does not import mock adapters.

- [ ] **Step 3: Run complete verification**

Run:

```bash
cd backend && .venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

Expected: all tests and builds pass; no whitespace errors.

- [ ] **Step 4: Record evidence and placeholder scan**

Write the exact commands, counts, skipped live dependencies, and remaining provider coverage into the verification report. Run:

```bash
rg -n "TO[D]O|TB[D]|implement[ ]later|mock[ ]fallback|fake[ ]live" backend/app frontend/src docs/reports/2026-08-15-event-research-mainline-verification.md
```

Expected: no unfinished implementation markers in changed production files; test-only fakes are named and scoped.

- [ ] **Step 5: Commit verification artifacts**

```bash
git add backend/tests/test_event_research_mainline_postgres.py backend/tests/test_orchestration_boundaries.py docs/reports/2026-08-15-event-research-mainline-verification.md
git commit -m "test: verify event research mainline"
```

## Completion criteria

- Confirming the frozen event scope starts real governed acquisition without another user click.
- The same technical retry reuses one immutable query plan; a new round records its expansion and reason.
- Automatically admitted evidence is visible, mapped, cited, and never presented as human-reviewed.
- Ready coverage advances automatically through system draft and monitoring; bounded insufficiency becomes one explicit decision or an exhausted result.
- The Case overview shows the current event, current system action, completed process, one-or-zero user action, source ledger, and recovery truth from one backend projection.
- No source adapter is called outside the acquisition module and no frontend code invents live status.
