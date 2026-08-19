# Auto Research Correctness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the correctness defects that can produce invalid forecast observations, bypass research protocol gates, leave runs stuck, or show factors outside the configured monitor scope.

**Architecture:** Keep the existing ledger and orchestration models. Put each invariant at the narrowest shared seam: UTC normalization at the read DTO boundary, protocol outcome restriction in `ResearchProtocolService`, run closure in one reconciler, and monitor factor filtering in the monitor query.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, pytest, React 18, TypeScript, Vitest.

---

## File map

- Modify `backend/app/queries/market_expression.py`: serialize persisted SQLite datetimes as explicit UTC.
- Modify `backend/app/services/event_research_scope.py`: make automatically created factor theses protocol-required.
- Modify `backend/app/services/research_protocol.py`: expose allowed assessment conclusions for each gate state.
- Modify `backend/app/ai/assessment_gen.py`: enforce protocol conclusions after model output and before persistence.
- Modify `backend/app/services/auto_research.py`: replace assessment-specific closure with a run reconciler.
- Modify `backend/app/api/v1/commands/proposals.py`: reconcile affected runs after proposal review.
- Modify `backend/app/api/v1/commands/reviews.py`: use the same reconciler after assessment review.
- Modify `backend/app/queries/case_monitor.py`: restrict confirmed factors to the effective monitor version.
- Modify `frontend/src/app/researchOsApi.ts`: retain the v1 error message and request id.
- Modify `frontend/src/features/case/MarketExpressionContent.tsx`: show actionable request failures.
- Test in the existing focused backend and frontend suites listed below.

### Task 1: Preserve UTC in source-statement API output

**Files:**
- Modify: `backend/app/queries/market_expression.py`
- Test: `backend/tests/test_forecast_verdicts.py`
- Test: `backend/tests/test_verify_live_event_ui.py`

- [ ] **Step 1: Write the failing round-trip test**

Extend `test_service_freezes_matching_admitted_forecast_evidence` after its existing fixture commit and before recording the actual observation:

```python
cmd_session.expire_all()
options = cmd_client.get(f"/api/v1/research-cases/{case_id}/source-statements")
assert options.status_code == 200, options.text
actual_source = next(
    item for item in options.json()["items"]
    if item["id"] == str(actual_statement.id)
)
assert actual_source["available_at"].endswith(("Z", "+00:00"))
```

- [ ] **Step 2: Run the test and verify the current failure**

Run: `cd backend && uv run pytest tests/test_forecast_verdicts.py -k restores_utc -v`

Expected: FAIL because the serialized `available_at` has no UTC offset.

- [ ] **Step 3: Normalize only at the read boundary**

In `backend/app/queries/market_expression.py`, add:

```python
from datetime import UTC, datetime


def _api_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
```

Change both `SourceStatementOptionDTO` and `ExpressionSourceDTO` construction sites to:

```python
available_at=_api_datetime(document.available_at) if document else None,
```

Do not weaken `ForecastVerdictService.record_actual`; client-supplied naive timestamps must remain invalid.

- [ ] **Step 4: Run focused service and live UI tests**

Run: `cd backend && uv run pytest tests/test_forecast_verdicts.py tests/test_verify_live_event_ui.py -v`

Expected: PASS, including the real forecast actual-observation path.

- [ ] **Step 5: Commit**

```bash
git add backend/app/queries/market_expression.py backend/tests/test_forecast_verdicts.py backend/tests/test_verify_live_event_ui.py
git commit -m "fix: preserve UTC in forecast source round trips"
```

### Task 2: Require protocol for scope-created theses

**Files:**
- Modify: `backend/app/services/event_research_scope.py`
- Test: `backend/tests/test_event_research_scope.py`

- [ ] **Step 1: Write the failing invariant test**

```python
def test_scope_created_factor_thesis_requires_research_protocol(cmd_client, cmd_session):
    case_id = uuid.UUID(_create_event(cmd_client)["case_id"])
    scope = EventResearchScopeService(cmd_session).update(
        case_id,
        factors=["供应链传导是否影响目标公司利润"],
        changed_by="human:tester",
        change_reason="建立研究范围",
    )
    thesis = cmd_session.scalar(
        select(Thesis).where(
            Thesis.research_case_id == case_id,
            Thesis.statement == scope.factors[0].statement,
        )
    )
    assert thesis is not None
    assert thesis.research_protocol_required is True
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd backend && uv run pytest tests/test_event_research_scope.py -k requires_research_protocol -v`

Expected: FAIL with `False is True`.

- [ ] **Step 3: Set the invariant when creating a Thesis**

Add this field in `_sync_active_factor_theses`:

```python
thesis = Thesis(
    research_case_id=case_id,
    statement=statement,
    created_by=changed_by,
    created_at=created_at,
    creator_type="human",
    review_state="confirmed",
    research_protocol_required=True,
)
```

Existing theses are not silently rewritten; add a separate data-repair migration only if production data inspection finds affected active factors.

- [ ] **Step 4: Run scope and protocol suites**

Run: `cd backend && uv run pytest tests/test_event_research_scope.py tests/test_research_protocol.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/event_research_scope.py backend/tests/test_event_research_scope.py
git commit -m "fix: require protocol for scope-created factors"
```

### Task 3: Enforce single-metric assessment outcomes

**Files:**
- Modify: `backend/app/services/research_protocol.py`
- Modify: `backend/app/ai/assessment_gen.py`
- Test: `backend/tests/test_research_protocol.py`
- Test: `backend/tests/test_ai_engine.py`

- [ ] **Step 1: Write a failing policy test**

```python
def test_single_metric_monitoring_restricts_formal_assessment_outcomes():
    result = ResearchabilityResult(
        status="single_metric_monitoring",
        reason_codes=["insufficient_primary_metrics"],
        effective_binding_id=None,
        next_action="仅可持续监测",
    )
    assert ResearchProtocolService.allowed_assessment_conclusions(result) == {
        "insufficient_evidence"
    }
```

Add an AI test whose fake model returns `supported` under a single-metric protocol and assert the persisted conclusion is `insufficient_evidence` and the gaps contain `insufficient_primary_metrics`.

- [ ] **Step 2: Run both tests and verify failure**

Run: `cd backend && uv run pytest tests/test_research_protocol.py tests/test_ai_engine.py -k single_metric -v`

Expected: FAIL because no outcome restriction is currently applied.

- [ ] **Step 3: Add a central conclusion policy**

In `ResearchProtocolService` add:

```python
@staticmethod
def allowed_assessment_conclusions(
    result: ResearchabilityResult,
) -> set[str]:
    if result.status == "blocked":
        return set()
    if result.status == "single_metric_monitoring":
        return {"insufficient_evidence"}
    return {"supported", "contradicted", "insufficient_evidence"}
```

In `AssessmentGenerator.generate`, retain `gate`, then immediately after reading the model result enforce:

```python
allowed = ResearchProtocolService.allowed_assessment_conclusions(gate)
conclusion = result["conclusion"]
gaps = list(result.get("gaps", []))
if conclusion not in allowed:
    conclusion = "insufficient_evidence"
    if "insufficient_primary_metrics" not in gaps:
        gaps.append("insufficient_primary_metrics")
```

- [ ] **Step 4: Run AI and protocol suites**

Run: `cd backend && uv run pytest tests/test_research_protocol.py tests/test_ai_engine.py tests/test_auto_research_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/research_protocol.py backend/app/ai/assessment_gen.py backend/tests/test_research_protocol.py backend/tests/test_ai_engine.py
git commit -m "fix: constrain single metric research conclusions"
```

### Task 4: Reconcile runs after every review type

**Files:**
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/api/v1/commands/proposals.py`
- Modify: `backend/app/api/v1/commands/reviews.py`
- Test: `backend/tests/test_proposal_review_api.py`
- Test: `backend/tests/test_review_commands_api.py`

- [ ] **Step 1: Write a failing proposal-last test**

Seed a waiting run with one decided assessment and one pending proposal, decide the proposal, and assert:

```python
assert response.status_code == 201, response.text
cmd_session.refresh(run)
assert run.status == "succeeded"
assert run.stage == "complete"
```

Add the inverse case where another proposal is still pending and assert the run remains `waiting_for_review`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd backend && uv run pytest tests/test_proposal_review_api.py tests/test_review_commands_api.py -k reconcile -v`

Expected: FAIL because only assessment review currently closes a run.

- [ ] **Step 3: Introduce the common reconciler**

Replace `complete_runs_after_assessment_review` with:

```python
def reconcile_run(self, run_id: uuid.UUID, *, trigger_ref: str) -> bool:
    run = self.session.scalar(
        select(ResearchRun).where(ResearchRun.id == run_id).with_for_update()
    )
    if run is None or run.status != "waiting_for_review":
        return False
    if self._has_open_reviewable_output(run):
        return False
    self.repo.update_run(run, status="succeeded", stage="complete")
    ResearchRunEventRepository(self.session).append(
        run.id,
        stage="review_complete",
        status="completed",
        message="本次运行的人工审核项已全部完成。",
        payload_json={"trigger_ref": trigger_ref, "status": "succeeded"},
    )
    return True

def run_ids_for_output(self, *, key: str, value: uuid.UUID) -> set[uuid.UUID]:
    matches: set[uuid.UUID] = set()
    for task in self.session.scalars(
        select(ResearchTask).where(ResearchTask.result.is_not(None))
    ):
        result = task.result or {}
        raw_values = result.get(key, [])
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        if str(value) in {str(item) for item in values if item}:
            matches.add(task.run_id)
    return matches
```

Use `key="proposed_proposal_ids"` after proposal review and `key="assessment_id"` after assessment review, then call `reconcile_run` for every matching id.

- [ ] **Step 4: Run all orchestration review tests**

Run: `cd backend && uv run pytest tests/test_auto_research_api.py tests/test_proposal_review_api.py tests/test_review_commands_api.py -v`

Expected: PASS with no waiting run after its final review is decided.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/auto_research.py backend/app/api/v1/commands/proposals.py backend/app/api/v1/commands/reviews.py backend/tests/test_proposal_review_api.py backend/tests/test_review_commands_api.py
git commit -m "fix: reconcile research runs after all reviews"
```

### Task 5: Limit monitor factors to its effective version

**Files:**
- Modify: `backend/app/queries/case_monitor.py`
- Test: `backend/tests/test_case_monitor_api.py`

- [ ] **Step 1: Write the failing historical-factor test**

Create two confirmed theses, save a monitor containing only the second, and assert:

```python
response = cmd_client.get(f"/api/v1/research-cases/{case.id}/monitor")
assert response.status_code == 200
assert response.json()["confirmed_factors"] == [
    {"id": str(current.id), "statement": current.statement}
]
```

- [ ] **Step 2: Verify the test fails**

Run: `cd backend && uv run pytest tests/test_case_monitor_api.py -k historical_factor -v`

Expected: FAIL because both historical theses are returned.

- [ ] **Step 3: Filter by the effective monitor factor ids**

Implement:

```python
def confirmed_factors(self, case_id: uuid.UUID) -> list[Thesis]:
    query = (
        select(Thesis)
        .where(Thesis.research_case_id == case_id)
        .where(Thesis.review_state == "confirmed")
    )
    monitor = self.effective(case_id)
    if monitor is not None:
        factor_ids = []
        for raw_id in monitor.factor_ids:
            try:
                factor_ids.append(uuid.UUID(str(raw_id)))
            except (TypeError, ValueError):
                continue
        if not factor_ids:
            return []
        query = query.where(Thesis.id.in_(factor_ids))
    return list(self._session.scalars(query.order_by(Thesis.created_at, Thesis.id)))
```

- [ ] **Step 4: Run monitor suites**

Run: `cd backend && uv run pytest tests/test_case_monitor.py tests/test_case_monitor_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/queries/case_monitor.py backend/tests/test_case_monitor_api.py
git commit -m "fix: show only effective monitor factors"
```

### Task 6: Preserve backend error details in the UI

**Files:**
- Modify: `frontend/src/app/researchOsApi.ts`
- Modify: `frontend/src/features/case/MarketExpressionContent.tsx`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Add a failing request-error UI test**

Mock the actual-observation POST with:

```typescript
return new Response(JSON.stringify({
  error: {
    code: "validation_failed",
    message: "available_at must include a timezone",
    request_id: "req-forecast-1",
    details: {},
  },
}), { status: 422, headers: { "content-type": "application/json" } });
```

Submit the form and assert the rendered message contains both the backend message and `req-forecast-1`.

- [ ] **Step 2: Run the test and verify failure**

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: FAIL because `request()` discards the response body.

- [ ] **Step 3: Add a typed request error**

In `researchOsApi.ts` add:

```typescript
export class ResearchOsRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ResearchOsRequestError";
  }
}

type ErrorBody = {
  error?: { code?: string; message?: string; request_id?: string };
  detail?: unknown;
};

async function responseError(response: Response): Promise<ResearchOsRequestError> {
  let body: ErrorBody = {};
  try { body = await response.json() as ErrorBody; } catch { /* non-JSON upstream */ }
  const detail = typeof body.detail === "string" ? body.detail : undefined;
  const message = body.error?.message ?? detail ?? `Research OS request failed (${response.status})`;
  return new ResearchOsRequestError(
    message,
    response.status,
    body.error?.code,
    body.error?.request_id ?? response.headers.get("x-request-id") ?? undefined,
  );
}
```

Change the request check to:

```typescript
if (!response.ok) throw await responseError(response);
```

In `MarketExpressionContent.tsx`, format errors with:

```typescript
function requestFailure(error: unknown, fallback: string): string {
  if (!(error instanceof ResearchOsRequestError)) return fallback;
  return error.requestId ? `${error.message}（请求 ${error.requestId}）` : error.message;
}
```

Use the caught error in `saveTarget`, `saveActual`, and `evaluate` rather than dropping it.

- [ ] **Step 4: Run frontend verification**

Run: `cd frontend && npm test -- --run src/tests/ResearchOsPages.test.tsx && npm run typecheck`

Expected: both commands PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/researchOsApi.ts frontend/src/features/case/MarketExpressionContent.tsx frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "fix: surface research API request details"
```

### Task 7: Phase verification

**Files:**
- No production file changes.

- [ ] **Step 1: Run the affected backend suite**

Run: `cd backend && uv run pytest tests/test_forecast_verdicts.py tests/test_verify_live_event_ui.py tests/test_event_research_scope.py tests/test_research_protocol.py tests/test_ai_engine.py tests/test_auto_research_api.py tests/test_proposal_review_api.py tests/test_review_commands_api.py tests/test_case_monitor.py tests/test_case_monitor_api.py -v`

Expected: PASS.

- [ ] **Step 2: Run full frontend checks**

Run: `cd frontend && npm test && npm run typecheck`

Expected: PASS.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && uv run pytest`

Expected: PASS with only the repository's documented skips.
