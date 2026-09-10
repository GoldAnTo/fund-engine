# Empty-review run terminal state Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End a run that produced no reviewable output as `succeeded`, rather than presenting an impossible human-review action.

**Architecture:** Keep `waiting_for_review` as the exclusive pause state for pending proposals, AI assessments, or atomic-claim candidates.  Reuse the existing `succeeded` terminal status for no-output success, retaining the immutable stop reason and frozen scope.  The active-run endpoint already excludes `succeeded`; frontend presentation receives only its missing label.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, pytest; TypeScript, Vitest.

---

### Task 1: Establish the backend terminal-state contract

**Files:**
- Modify: `backend/tests/test_auto_research_api.py:244-270`
- Modify: `backend/tests/test_case_monitor_api.py:299-325`

- [ ] **Step 1: Write the failing no-output terminal-state test**

  Replace the weak assertion in `test_round_stop` with a deterministic empty
  run that invokes the real service and asserts the complete persistence
  contract:

  ```python
  def test_empty_run_completes_without_a_phantom_review_task(session):
      case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
      session.add(case)
      session.commit()
      run = AutoResearchRepository(session).create_run(
          research_case_id=case.id, max_rounds=1, budget=1000
      )

      AutoResearchService(session).execute(run)
      session.commit()

      session.refresh(run)
      assert run.status == "succeeded"
      assert run.stop_reason == "max_rounds_reached"
      assert list(session.scalars(select(TaskItem).where(TaskItem.research_case_id == case.id))) == []
      completion = session.scalar(
          select(ResearchRunEvent)
          .where(ResearchRunEvent.run_id == run.id)
          .where(ResearchRunEvent.stage == "complete")
      )
      assert completion is not None
      assert completion.payload_json["status"] == "succeeded"
      assert "未产生新增待审材料" in completion.message
  ```

- [ ] **Step 2: Run the single test and verify it fails for the current status**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py::test_empty_run_completes_without_a_phantom_review_task
  ```

  Expected: failure because `run.status` is `waiting_for_review` instead of
  `succeeded`.

- [ ] **Step 3: Extend the active-run API boundary test**

  In `test_active_runs_expose_case_and_frozen_scope_without_reconstructing_current_config`, after its initial active-run assertions, load the started run,
  persist its terminal state, and verify the active strip no longer returns it:

  ```python
  completed = cmd_session.get(ResearchRun, uuid.UUID(started.json()["id"]))
  assert completed is not None
  completed.status = "succeeded"
  completed.stage = "complete"
  completed.stop_reason = "no_new_evidence"
  cmd_session.commit()

  active = cmd_client.get("/api/v1/research-runs/active")
  assert active.status_code == 200
  assert str(completed.id) not in {item["run_id"] for item in active.json()["items"]}
  ```

- [ ] **Step 4: Run the API test and verify it already proves the active-list boundary**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_case_monitor_api.py::test_active_runs_expose_case_and_frozen_scope_without_reconstructing_current_config
  ```

  Expected: pass; it documents that the existing active-query status filter
  needs no change once the service persists `succeeded`.

### Task 2: Make the service distinguish reviewable output from an empty run

**Files:**
- Modify: `backend/app/services/auto_research.py:376-541`
- Test: `backend/tests/test_auto_research_api.py:244-270`

- [ ] **Step 1: Add a narrow reviewable-output predicate**

  Add a private method adjacent to `_handoff_for_review` that reads the run's
  task results and pending atomic claims and returns true only when the handoff
  can create at least one review item:

  ```python
  def _has_reviewable_output(self, run) -> bool:
      for task in self.repo.tasks_for_run(run.id):
          result = task.result or {}
          for raw_id in result.get("proposed_proposal_ids", []):
              try:
                  proposal = self.session.get(Proposal, uuid.UUID(str(raw_id)))
              except (TypeError, ValueError):
                  continue
              if proposal is not None and proposal.kind == "evidence_link" and proposal.status == "pending":
                  return True
          if result.get("assessment_id"):
              return True
      return bool(self._pending_atomic_claims(run.research_case_id))
  ```

- [ ] **Step 2: Use the predicate at successful terminal branches**

  In `execute`, replace each successful terminal assignment that currently
  chooses `waiting_for_review` (`budget_exhausted`, `max_rounds_reached`, and
  `no_new_evidence`) with a small local helper or equivalent expression:

  ```python
  terminal_status = "waiting_for_review" if self._has_reviewable_output(run) else "succeeded"
  ```

  Preserve `failed` for `task_failed`, preserve every existing `stage` and
  `stop_reason`, and keep `_pause_for_atomic_claim_review` unchanged.  After
  the terminal state is persisted, call `_handoff_for_review` only for the
  genuine waiting state.

- [ ] **Step 3: Make the terminal event tell the truth**

  Change the completion event message selection to:

  ```python
  if run.status == "failed":
      completion_message = "运行失败，需检查失败项"
  elif run.status == "succeeded":
      completion_message = "运行已结束，未产生新增待审材料"
  else:
      completion_message = "运行已结束，等待后续人工动作"
  ```

  Keep the existing event stage, event status, and payload keys so replay and
  old consumers remain compatible.

- [ ] **Step 4: Run the new terminal-state regression test and verify it passes**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py::test_empty_run_completes_without_a_phantom_review_task
  ```

  Expected: pass with `succeeded`, `max_rounds_reached`, no task item, and the
  explicit no-review completion event.

- [ ] **Step 5: Run the focused backend regression suite**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py backend/tests/test_event_research_lifecycle.py
  ```

  Expected: all tests pass; the existing atomic-claim pause and event lifecycle
  tests demonstrate that genuine review paths are unchanged.

- [ ] **Step 6: Commit the backend change**

  ```bash
  git add backend/app/services/auto_research.py backend/tests/test_auto_research_api.py
  git commit -m "fix: complete empty research runs without review"
  ```

### Task 3: Present completed runs correctly in the client

**Files:**
- Modify: `frontend/src/domain/runPresentation.ts:20-47`
- Modify: `frontend/src/domain/runPresentation.test.ts:11-34`

- [ ] **Step 1: Write the failing presentation assertion**

  Add the assertion to the metadata-label test:

  ```ts
  expect(runStatusLabel("succeeded")).toBe("已完成");
  ```

- [ ] **Step 2: Run the focused Vitest file and verify it fails**

  Run:

  ```bash
  npm --prefix frontend run test -- --run src/domain/runPresentation.test.ts
  ```

  Expected: failure because `runStatusLabel("succeeded")` currently falls back
  to the raw status value.

- [ ] **Step 3: Add the one status mapping**

  Add the following member next to `completed` in `RUN_STATUS_LABELS`:

  ```ts
  succeeded: "已完成",
  ```

- [ ] **Step 4: Run the presentation test and verify it passes**

  Run:

  ```bash
  npm --prefix frontend run test -- --run src/domain/runPresentation.test.ts
  ```

  Expected: pass.

- [ ] **Step 5: Run client type-check and build**

  Run:

  ```bash
  npm --prefix frontend run typecheck
  npm --prefix frontend run build
  ```

  Expected: both commands exit 0.

- [ ] **Step 6: Commit the presentation change**

  ```bash
  git add frontend/src/domain/runPresentation.ts frontend/src/domain/runPresentation.test.ts
  git commit -m "fix: label successful research runs"
  ```

### Task 4: Re-run the Industrial Foxconn human acceptance path

**Files:**
- Verify only: isolated Industrial Foxconn database and live UI

- [ ] **Step 1: Start the API and worker with the accepted isolated database**

  Run the API and `run_research_worker --once` using
  `/Users/xiongjiali/code/fund-engine/.worktrees/industrial-foxconn-real-acceptance/.local/industrial-foxconn-real-acceptance.db`, retaining the tenant token used in the previous acceptance.

- [ ] **Step 2: From the real monitor UI, trigger one v2 monitor run**

  Confirm the frozen scope lists only the retained forecast factor, records the
  v2 configuration reason, and then select “立即补证一次”.

- [ ] **Step 3: Verify the terminal path in the UI and database-backed API**

  Confirm the run shows `已完成`, retains `max_rounds_reached` or
  `no_new_evidence`, has zero review queue entries, and is absent from the
  active-run strip.  Confirm the timeline completion text says no new review
  material was produced.

- [ ] **Step 4: Run final repository verification**

  Run:

  ```bash
  /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest -q backend/tests/test_auto_research_api.py backend/tests/test_event_research_lifecycle.py
  npm --prefix frontend run test -- --run src/domain/runPresentation.test.ts
  npm --prefix frontend run typecheck
  npm --prefix frontend run build
  ```

  Expected: all commands pass before merging the two commits into `main`.
