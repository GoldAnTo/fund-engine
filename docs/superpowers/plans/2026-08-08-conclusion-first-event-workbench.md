# Conclusion-First Event Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a researcher adjust event factors without losing verified evidence, then make the event workbench lead with the current conclusion, evidence gap, and one understandable next action.

**Architecture:** Add append-only event research scope versions and active-factor snapshots. A scope-update service validates and persists a new snapshot, keeps existing evidence immutable, marks only unmappable evidence as pending classification, and starts a successor automatic run. The event workbench projection consumes that scope plus lifecycle, review-queue, and conclusion data; the React page renders its five user-facing states from one DTO.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, React, TypeScript, Vitest, Playwright.

---

### Task 1: Persist versioned event research factors

**Files:**
- Modify: `backend/app/models/event_research.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/api/v1/event_research.py`
- Create: `backend/app/services/event_research_scope.py`
- Create: `backend/tests/test_event_research_scope.py`

- [ ] **Step 1: Write failing scope-version tests**

```python
def test_update_scope_appends_a_version_and_keeps_prior_factors(session, event_case):
    result = EventResearchScopeService(session).update(
        event_case.id,
        factors=["自由现金流压力", "估值重定价", "市场风险偏好"],
        changed_by="researcher",
    )
    assert result.version == 2
    assert active_factor_statements(session, event_case.id) == ["自由现金流压力", "估值重定价", "市场风险偏好"]
    assert scope_version_statements(session, event_case.id, 1) != []

def test_remove_factor_keeps_reviewed_evidence_but_marks_it_unmapped(session, event_case, reviewed_link):
    EventResearchScopeService(session).update(event_case.id, factors=["估值重定价", "市场风险偏好", "行业风险"], changed_by="researcher")
    assert session.get(EvidenceLink, reviewed_link.id) is not None
    assert unmapped_evidence_count(session, event_case.id) == 1
```

- [ ] **Step 2: Run the new test file**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_scope.py`

Expected: FAIL because versions and scope update endpoint do not exist.

- [ ] **Step 3: Add immutable scope and factor snapshot models**

```python
class EventResearchScopeVersion(Base):
    __tablename__ = "event_research_scope_versions"
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class EventResearchScopeFactor(Base):
    __tablename__ = "event_research_scope_factors"
    scope_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("event_research_scope_versions.id"), index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
```

Create `UpdateEventResearchScopeRequest(factors: list[str], changed_by: str)` with 3–5 non-empty distinct factors and a response containing `version`, active factors, `reclassified_evidence_count`, and `unmapped_evidence_count`.

- [ ] **Step 4: Implement the scope service and route**

```python
@router.put("/{case_id}/scope", response_model=UpdateEventResearchScopeResponse)
def update_event_scope(case_id: uuid.UUID, payload: UpdateEventResearchScopeRequest, db: Session = Depends(get_db)):
    result = EventResearchScopeService(db).update(case_id, factors=payload.factors, changed_by=payload.changed_by)
    db.commit()
    return result
```

`EventResearchScopeService.update()` must append the scope version, write its ordered factor rows, preserve every `EvidenceLink`, map links whose thesis statement remains active, record removed-factor links as unmapped without modifying their audit history, and refresh the lifecycle gap. It must never alter a published conclusion.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_scope.py tests/test_event_research_api.py`

Expected: PASS.

```bash
git add backend/app/models/event_research.py backend/app/schemas/v1/event_research.py backend/app/api/v1/event_research.py backend/app/services/event_research_scope.py backend/tests/test_event_research_scope.py
git commit -m "feat: version event research factors"
```

### Task 2: Resume automatic research from the new scope

**Files:**
- Modify: `backend/app/services/event_research_scope.py`
- Modify: `backend/app/services/auto_research.py`
- Modify: `backend/app/repositories/event_research.py`
- Modify: `backend/tests/test_event_research_lifecycle.py`
- Modify: `backend/tests/test_event_research_scope.py`

- [ ] **Step 1: Write failing automatic-continuation tests**

```python
def test_scope_update_starts_successor_run_and_sets_human_free_progress(session, exhausted_event):
    result = EventResearchScopeService(session).update(exhausted_event.id, factors=["现金流", "预期差", "行业风险"], changed_by="researcher")
    lifecycle = EventResearchLifecycleRepository(session).get(exhausted_event.id)
    assert result.started_run_id is not None
    assert lifecycle.status == "continuing"
    assert lifecycle.next_human_action is None
    assert "现金流" in lifecycle.status_summary

def test_scope_update_with_no_acceptable_evidence_never_creates_conclusion(session, exhausted_event):
    EventResearchScopeService(session).update(exhausted_event.id, factors=["现金流", "预期差", "行业风险"], changed_by="researcher")
    assert EventConclusionService(session).latest(exhausted_event.id) is None
```

- [ ] **Step 2: Run the focused tests**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_scope.py::test_scope_update_starts_successor_run_and_sets_human_free_progress tests/test_event_research_lifecycle.py`

Expected: FAIL because an exhausted scope update does not start a successor run.

- [ ] **Step 3: Add bounded continuation semantics**

```python
def continue_after_scope_update(self, case_id: uuid.UUID, focus: list[str]) -> uuid.UUID:
    successor = self.start(case_id, max_rounds=3, budget=100, commit=False)
    self._lifecycle_repo.update(
        lifecycle,
        status="continuing",
        active_run_id=successor.id,
        current_round=lifecycle.current_round + 1,
        summary=f"正在围绕{focus[0]}重新核验证据缺口",
        current_gap=f"尚缺少区分{focus[0]}与其他因素的可采纳证据",
        next_human_action=None,
    )
    return successor.id
```

Call it only after the new scope snapshot is flushed. Do not create a conclusion here; existing `continue_after_key_review()` remains the only transition from reviewed evidence to `draft_ready`.

- [ ] **Step 4: Verify and commit**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_scope.py tests/test_event_research_lifecycle.py`

Expected: PASS.

```bash
git add backend/app/services/event_research_scope.py backend/app/services/auto_research.py backend/app/repositories/event_research.py backend/tests/test_event_research_scope.py backend/tests/test_event_research_lifecycle.py
git commit -m "feat: continue event research after factor updates"
```

### Task 3: Project the conclusion-first workbench contract

**Files:**
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/queries/event_research.py`
- Modify: `backend/tests/test_event_research_api.py`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/tests/HttpResearchAdapter.test.ts`

- [ ] **Step 1: Write failing API and adapter tests**

```python
def test_workbench_returns_conclusion_first_progress_and_scope_actions(api_client, event_case):
    body = api_client.get(f"/api/v1/event-research/{event_case.id}/workbench").json()
    assert body["progress"] == {"verified": 2, "pending": 1, "invalid_source": 1}
    assert body["next_action"]["kind"] == "edit_factors"
    assert body["scope"]["version"] == 2
```

```ts
it("maps workbench progress, factor scope and the edit-factors action", async () => {
  const view = await adapter.getEventWorkbench("case-1");
  expect(view.progress).toEqual({ verified: 2, pending: 1, invalidSource: 1 });
  expect(view.nextAction.kind).toBe("edit_factors");
});
```

- [ ] **Step 2: Run the focused tests**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_api.py`

Run: `cd frontend && npm test -- HttpResearchAdapter.test.ts`

Expected: FAIL because workbench has no scope/progress DTO or `edit_factors` action.

- [ ] **Step 3: Extend the read model and client types**

Add `EventWorkbenchProgressDTO(verified, pending, invalid_source, current_gap)`, `EventResearchScopeDTO(version, factors, unmapped_evidence_count)`, and allow `EventNextActionDTO.kind = "edit_factors"`. Compute verified from reviewed same-case evidence, pending/invalid from `EventReviewQueueService`, and choose actions in this exact order: awaiting key review → review evidence; draft ready → review conclusion; awaiting scope/exhausted/cannot conclude → edit factors; otherwise wait.

Map these fields to `EventWorkbench.progress` and `EventWorkbench.scope`; mock all five lifecycle states with accurate action labels and no `example.*` URLs.

- [ ] **Step 4: Verify and commit**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_api.py tests/test_event_review_queue.py`

Run: `cd frontend && npm test -- HttpResearchAdapter.test.ts && npm run typecheck`

Expected: PASS.

```bash
git add backend/app/schemas/v1/event_research.py backend/app/queries/event_research.py backend/tests/test_event_research_api.py frontend/src/domain/eventResearch.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/tests/HttpResearchAdapter.test.ts
git commit -m "feat: expose conclusion-first event workbench"
```

### Task 4: Build factor editing and the conclusion-first workbench UI

**Files:**
- Modify: `frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx`
- Create: `frontend/src/pages/prototype/EventFactorScopeEditor.tsx`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/styles-prototype.css`
- Create: `frontend/src/tests/EventResearchWorkbenchScreen.test.tsx`

- [ ] **Step 1: Write failing screen tests**

```tsx
it("shows the current conclusion, evidence totals and edit-factors as the only action when evidence is insufficient", async () => {
  renderAtEventWorkbench("event-alphabet");
  expect(await screen.findByText("暂不能下结论")).toBeVisible();
  expect(screen.getByText("已核验 2 条")).toBeVisible();
  expect(screen.getByRole("button", { name: "编辑并继续自动研究" })).toBeVisible();
});

it("keeps reviewed evidence context and announces research continuation after saving factors", async () => {
  renderAtEventWorkbench("event-alphabet");
  await userEvent.click(await screen.findByRole("button", { name: "编辑并继续自动研究" }));
  await userEvent.click(screen.getByRole("button", { name: "保存并继续研究" }));
  expect(await screen.findByRole("status")).toHaveTextContent("已保留已核验证据");
});
```

- [ ] **Step 2: Run the screen test**

Run: `cd frontend && npm test -- EventResearchWorkbenchScreen.test.tsx`

Expected: FAIL because the page has no progress summary or scope editor.

- [ ] **Step 3: Implement the screen and editor**

Render the current conclusion first, then compact verified/pending/invalid chips, then the ranked factor cards. Put one adaptive primary action in the right panel. `EventFactorScopeEditor` contains 3–5 factor inputs, move up/down controls, remove/add controls, an explicit note that reviewed evidence is retained, and a `role="status"` success message. Its submit calls a new `researchClient.updateEventResearchScope({ caseId, factors, changedBy })`, closes the editor, and reloads the workbench.

Do not expose runs, budgets, JSON, task IDs, or generic navigation as primary actions. On narrow screens the conclusion remains first, factors second, and action panel last.

- [ ] **Step 4: Verify and commit**

Run: `cd frontend && npm test -- EventResearchWorkbenchScreen.test.tsx && npm run typecheck && npm run build`

Expected: PASS.

```bash
git add frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx frontend/src/pages/prototype/EventFactorScopeEditor.tsx frontend/src/data/researchClient.ts frontend/src/styles-prototype.css frontend/src/tests/EventResearchWorkbenchScreen.test.tsx
git commit -m "feat: add conclusion-first event workbench UI"
```

### Task 5: Prove the full scope-to-conclusion flow

**Files:**
- Modify: `backend/tests/test_event_research_lifecycle.py`
- Modify: `frontend/e2e/event-research.spec.ts`

- [ ] **Step 1: Write the end-to-end regressions**

```python
def test_scope_change_preserves_valid_evidence_then_requires_review_before_draft(api_client, event_with_reviewed_evidence):
    updated = api_client.put(f"/api/v1/event-research/{event_with_reviewed_evidence.id}/scope", json={"factors": ["现金流", "预期差", "市场风险"], "changed_by": "researcher"})
    assert updated.status_code == 200
    assert reviewed_evidence_count(event_with_reviewed_evidence.id) == 1
    assert current_lifecycle(event_with_reviewed_evidence.id).status == "continuing"
```

```ts
test("researcher edits factors and sees retained-evidence feedback", async ({ page }) => {
  await page.goto("/events/event-alphabet?client=mock");
  await expect(page.getByText("暂不能下结论")).toBeVisible();
  await page.getByRole("button", { name: "编辑并继续自动研究" }).click();
  await page.getByRole("button", { name: "保存并继续研究" }).click();
  await expect(page.getByRole("status")).toContainText("已保留已核验证据");
});
```

- [ ] **Step 2: Run the tests and then the full suites**

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q tests/test_event_research_lifecycle.py tests/test_event_research_scope.py`

Run: `cd frontend && npm test && npm run typecheck && npm run build`

Run: `cd backend && /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q`

Expected: all pass; no conclusion is created from unmapped, invalid, or cross-event evidence.

- [ ] **Step 3: Commit regression coverage**

```bash
git add backend/tests/test_event_research_lifecycle.py frontend/e2e/event-research.spec.ts
git commit -m "test: cover conclusion-first scope workflow"
```
