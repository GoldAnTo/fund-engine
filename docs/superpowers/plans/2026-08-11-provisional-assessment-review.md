# Provisional Assessment Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a researcher review a provisional AI assessment from its frozen monitor run and complete that run only when no review work remains.

**Architecture:** `AutoResearchService.detail` will return typed pending-assessment data assembled from the run's task result, `AIAssessment`, and matching `TaskItem`.  The assessment review command will close its task then ask `AutoResearchService` to complete only affected waiting runs that have no open proposal, assessment, or atomic-claim task.  The monitor UI loads run detail, renders the card, calls the existing review endpoint, then reloads server state.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, pytest; React, TypeScript, Vitest.

---

### Task 1: Expose a run-local pending assessment through the existing run endpoint

**Files:**
- Modify: `backend/app/schemas/v1/auto_research.py:121-159`
- Modify: `backend/app/services/auto_research.py:1170-1245`
- Test: `backend/tests/test_auto_research_api.py`

- [ ] **Step 1: Write the failing service/API regression test**

  Create an in-memory case, run, `AIAssessment`, completed result `ResearchTask`, and open `review_assessment` `TaskItem`.  Assert `AutoResearchService(session).detail(run.id)` returns exactly one typed pending assessment:

  ```python
  assert detail["pending_assessments"] == [{
      "assessment_id": str(assessment.id),
      "conclusion": "insufficient_evidence",
      "rationale": "缺少原始预测值",
      "gaps": ["补充历史预测值"],
      "task_id": str(task.id),
      "task_status": "open",
  }]
  ```

- [ ] **Step 2: Run the focused test and verify it fails**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py::test_run_detail_exposes_open_provisional_assessment_review
  ```

  Expected: failure because `pending_assessments` is absent from the response.

- [ ] **Step 3: Add a typed DTO and populate it from immutable assessment data**

  Add this schema next to `ReviewTaskDTO`:

  ```python
  class PendingAssessmentDTO(V1Model):
      assessment_id: str
      conclusion: str
      rationale: str
      gaps: list[str]
      task_id: str
      task_status: str
  ```

  Add `pending_assessments: list[PendingAssessmentDTO]` to
  `ResearchRunResponse`.  In `AutoResearchService.detail`, parse each
  `assessment_id` in this run's task result; include it only when its matching
  `review_assessment` task is `open` or `in_progress`, and read conclusion,
  rationale, and gaps from `AIAssessment` rather than the mutable task JSON.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run the Step 2 command.  Expected: pass with the assessment's immutable
  conclusion, rationale, gaps, and open task reference.

### Task 2: Complete only runs whose assessment review is the final open gate

**Files:**
- Modify: `backend/app/services/auto_research.py:772-860`
- Modify: `backend/app/api/v1/commands/reviews.py:75-104`
- Test: `backend/tests/test_review_commands_api.py:312-390`

- [ ] **Step 1: Write the failing final-gate test**

  Add a test that creates a waiting run with a result task pointing to an
  assessment and an open matching review task.  Post a valid assessment review
  and assert:

  ```python
  assert run.status == "succeeded"
  assert run.stage == "complete"
  assert run.stop_reason == "max_rounds_reached"
  assert any(
      event.stage == "review_complete"
      and event.payload_json["assessment_id"] == str(assessment.id)
      for event in session.scalars(select(ResearchRunEvent).where(ResearchRunEvent.run_id == run.id))
  )
  ```

  Add a second test with an additional open `review_proposal` task for that
  run and assert `run.status == "waiting_for_review"` after the assessment is
  reviewed.

- [ ] **Step 2: Run both tests and verify they fail at the unchanged terminal state**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_review_commands_api.py -k "assessment_review_completes_final_run_gate or assessment_review_keeps_run_waiting_when_other_review_remains"
  ```

  Expected: the final-gate test fails because the command currently closes the
  task but leaves `ResearchRun.status` as `waiting_for_review`.

- [ ] **Step 3: Implement run-local completion after assessment review**

  Add `AutoResearchService.complete_runs_after_assessment_review(assessment_id)`.
  It finds `ResearchTask` rows whose `result["assessment_id"]` equals the
  reviewed ID, locks each matching waiting run, and completes it only if
  `_has_open_reviewable_output(run)` is false.  That predicate must inspect
  pending evidence-link proposals, open `review_assessment` tasks, and pending
  atomic claims.  For each completed run, set `status="succeeded"`,
  `stage="complete"`, preserve `stop_reason`, and append:

  ```python
  ResearchRunEventRepository(self.session).append(
      run.id,
      stage="review_complete",
      status="completed",
      message="人工已完成临时 AI 评估审核；本次运行没有剩余待审项。",
      payload_json={"assessment_id": str(assessment_id), "status": "succeeded"},
  )
  ```

  In `review_assessment`, call this service after `close_review_task` and
  before `commit_or_rollback(db)`.

- [ ] **Step 4: Run the two new tests and focused backend regression suites**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_review_commands_api.py -k "assessment_review_completes_final_run_gate or assessment_review_keeps_run_waiting_when_other_review_remains"
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py backend/tests/test_review_commands_api.py backend/tests/test_event_research_lifecycle.py backend/tests/test_case_monitor_api.py
  ```

  Expected: all pass, including the existing immutable-review and task-close
  behavior.

- [ ] **Step 5: Commit the backend changes**

  ```bash
  git add backend/app/schemas/v1/auto_research.py backend/app/services/auto_research.py backend/app/api/v1/commands/reviews.py backend/tests/test_auto_research_api.py backend/tests/test_review_commands_api.py
  git commit -m "feat: close runs after final assessment review"
  ```

### Task 3: Render and submit the assessment review card in monitor detail

**Files:**
- Modify: `frontend/src/domain/prototypeTypes.ts:1060-1082`
- Modify: `frontend/src/features/case/CasePages.tsx:3688-4145`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Write the failing UI test**

  Configure the test client so `getResearchRun("run-1")` returns:

  ```ts
  pending_assessments: [{
    assessment_id: "assessment-1",
    conclusion: "insufficient_evidence",
    rationale: "缺少历史预测值",
    gaps: ["补充预测基线"],
    task_id: "task-1",
    task_status: "open",
  }],
  ```

  Render `CaseMonitorPage`, open the run detail, and assert it shows
  “临时 AI 评估待审核”, the conclusion, the gap, a reason textbox, and disabled
  confirmation until the reason is entered.  Submit confirmation and assert
  `reviewAssessment("assessment-1", { outcome: "confirmed", conclusion:
  "insufficient_evidence", reason: "人工确认资料不足", reviewer:
  "human:researcher" })` is called, then the monitor and event loaders are
  called again.

- [ ] **Step 2: Run the UI test and verify it fails**

  Run:

  ```bash
  npm --prefix frontend run test -- --run src/tests/ResearchOsPages.test.tsx
  ```

  Expected: failure because the monitor page does not request run detail or
  render the assessment card.

- [ ] **Step 3: Add typed run detail and the minimal monitor card**

  Add `PendingAssessmentReview` and `pending_assessments` to
  `ResearchRunDetail`.  In `MonitorContent`, load `researchClient.getResearchRun`
  whenever `selectedRunId` changes, retaining a visible read-error state rather
  than treating missing data as no assessment.  Pass the detail to `RunDrawer`.

  Add `AssessmentReviewPanel` inside the drawer.  It shows the provisional
  conclusion, rationale, gaps, frozen run ID, and a reason field.  It offers
  confirm, modify, and reject; modification reveals a conclusion selector.
  Each action requires a reason, calls `researchClient.reviewAssessment`, and
  then reloads monitor detail, run detail, and run events.  The success message
  comes only after the request succeeds.

- [ ] **Step 4: Run focused UI test, type-check, and production build**

  Run:

  ```bash
  npm --prefix frontend run test -- --run src/tests/ResearchOsPages.test.tsx
  npm --prefix frontend run typecheck
  npm --prefix frontend run build
  ```

  Expected: all commands exit 0.

- [ ] **Step 5: Commit the frontend change**

  ```bash
  git add frontend/src/domain/prototypeTypes.ts frontend/src/features/case/CasePages.tsx frontend/src/tests/ResearchOsPages.test.tsx
  git commit -m "feat: review provisional assessments from runs"
  ```

### Task 4: Complete the Industrial Foxconn manual acceptance

**Files:**
- Verify only: the isolated Industrial Foxconn database and monitor page

- [ ] **Step 1: Start the reviewed branch on isolated API and frontend ports**

  Point the API and one-shot worker at
  `/Users/xiongjiali/code/fund-engine/.worktrees/industrial-foxconn-real-acceptance/.local/industrial-foxconn-real-acceptance.db` and open the monitor
  page through the authenticated local frontend proxy.

- [ ] **Step 2: Select the real waiting run and review its provisional assessment**

  Verify the card displays the recorded `insufficient_evidence` conclusion,
  its gaps, run-local frozen scope, and a mandatory reason.  Submit a human
  confirmation with an auditable reason; do not submit a placeholder reason.

- [ ] **Step 3: Verify closure from the browser and API**

  Confirm the review task disappears, the run displays `已完成`, its original
  stop reason remains, the timeline records the human review completion, and
  the global active-run strip no longer lists it.  The pre-existing historical
  waiting run remains untouched.

- [ ] **Step 4: Run final checks**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py backend/tests/test_review_commands_api.py backend/tests/test_event_research_lifecycle.py backend/tests/test_case_monitor_api.py
  npm --prefix frontend run test -- --run src/tests/ResearchOsPages.test.tsx
  npm --prefix frontend run typecheck
  npm --prefix frontend run build
  ```

  Expected: all checks pass before branch integration.
