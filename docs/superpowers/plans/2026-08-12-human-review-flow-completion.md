# Human Review Flow Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every mock evidence-review outcome update the whole event workflow, and let a researcher continue from evidence review through conclusion publication without encountering a dead end.

**Architecture:** `MockResearchAdapter` will own the authoritative mutable projection for the TSM demo event. Review queue, event workbench, event list, conclusion history, and publication will derive from that projection. `MockResearchOsApi` will read the same workbench projection when it renders global run status, while `CaseReviewPage` will route successful evidence decisions back to the refreshed workbench and render a real conclusion-review form when the next task is conclusion approval.

**Tech Stack:** React 18, React Router 6, TypeScript, Vitest, Testing Library, Playwright, Vite.

---

## File map

- Modify `frontend/src/data/mockResearchAdapter.ts`: own the demo review decision and publication projection; derive all event-facing reads from it.
- Modify `frontend/src/tests/MockResearchAdapter.test.ts`: lock down the three review outcomes and conclusion publication at the data boundary.
- Modify `frontend/src/data/mockResearchOsApi.ts`: derive active and archived global run presentation from the adapter workbench.
- Modify `frontend/src/tests/ResearchOsApi.test.ts`: verify the global run strip cannot remain in `awaiting_review` after the event advances.
- Modify `frontend/src/app/AppShell.tsx`: immediately refresh global runs and event task counts after a workflow decision.
- Modify `frontend/src/features/case/CasePages.tsx`: redirect after evidence review, show completion feedback, and provide the conclusion-review form.
- Modify `frontend/src/tests/ResearchOsPages.test.tsx`: verify page-level navigation, refreshed progress, next task, and conclusion publication.
- Modify `frontend/e2e/research-os.spec.ts`: prove the complete mock human path in a browser.

### Task 1: Make evidence review an authoritative state transition

**Files:**
- Modify: `frontend/src/data/mockResearchAdapter.ts:3030-3090`
- Modify: `frontend/src/data/mockResearchAdapter.ts:3898-3907`
- Modify: `frontend/src/data/mockResearchAdapter.ts:4148-4240`
- Modify: `frontend/src/data/mockResearchAdapter.ts:4398-4450`
- Test: `frontend/src/tests/MockResearchAdapter.test.ts`

- [ ] **Step 1: Write failing adapter tests for all three outcomes**

Add a table-driven test that creates a fresh adapter for each outcome, submits the TSM proposal, and reads the queue, event list, and workbench again:

```ts
it.each([
  {
    outcome: "confirmed" as const,
    status: "draft_ready",
    nextKind: "review_conclusion",
    verified: 1,
    pending: 0,
  },
  {
    outcome: "needs_more_evidence" as const,
    status: "researching",
    nextKind: "wait",
    verified: 0,
    pending: 0,
  },
  {
    outcome: "rejected" as const,
    status: "exhausted",
    nextKind: "edit_factors",
    verified: 0,
    pending: 0,
  },
])(
  "projects a $outcome evidence decision across the whole event workbench",
  async ({ outcome, status, nextKind, verified, pending }) => {
    const adapter = new MockResearchAdapter();

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome,
      reason: `review reason for ${outcome}`,
      reviewer_id: "human:researcher",
      expected_version: 1,
    });

    const queue = await adapter.getEventReviewQueue("event-tsm");
    const workbench = await adapter.getEventWorkbench("event-tsm");
    const event = (await adapter.listEventResearch()).find(
      (item) => item.id === "event-tsm",
    );

    expect(queue.summary).toMatchObject({ reviewed: 1, pending: 0 });
    expect(queue.items.filter((item) => item.canAccept)).toHaveLength(0);
    expect(workbench.lifecycle.status).toBe(status);
    expect(workbench.nextAction.kind).toBe(nextKind);
    expect(workbench.progress).toMatchObject({ verified, pending });
    expect(workbench.factors[0].pendingProposalCount).toBe(0);
    expect(event).toMatchObject({
      status,
      statusSummary: workbench.lifecycle.summary,
      nextHumanAction: workbench.lifecycle.nextHumanAction,
    });
  },
);
```

Add focused assertions for the distinct semantics:

```ts
it("turns an accepted TSM proposal into a reviewable conclusion draft", async () => {
  const adapter = new MockResearchAdapter();
  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "confirmed",
    reason: "冻结原文支持资本开支上调。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });

  const workbench = await adapter.getEventWorkbench("event-tsm");
  expect(workbench.conclusion.state).toBe("ai_draft");
  expect(workbench.factors[0]).toMatchObject({
    reviewedSupportCount: 1,
    pendingProposalCount: 0,
    currentGap: null,
  });
  expect(workbench.lifecycle).toMatchObject({
    activeRunId: null,
    nextHumanAction: "审核结论草案",
  });
});

it("keeps the review reason as the next evidence gap when more evidence is requested", async () => {
  const adapter = new MockResearchAdapter();
  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "needs_more_evidence",
    reason: "还需要同一期间的反证和实际经营数据。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });

  expect(await adapter.getEventWorkbench("event-tsm")).toMatchObject({
    lifecycle: {
      status: "researching",
      currentGap: "还需要同一期间的反证和实际经营数据。",
      nextHumanAction: null,
    },
    nextAction: { kind: "wait", label: "系统补证中" },
  });
});

it("offers scope revision after the only actionable proposal is rejected", async () => {
  const adapter = new MockResearchAdapter();
  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "rejected",
    reason: "原文不足以支持这条关系。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });

  expect(await adapter.getEventWorkbench("event-tsm")).toMatchObject({
    lifecycle: {
      status: "exhausted",
      activeRunId: null,
      nextHumanAction: "编辑并继续自动研究",
    },
    nextAction: { kind: "edit_factors" },
  });
});
```

- [ ] **Step 2: Run the adapter tests and verify the exact stale-state failure**

Run:

```bash
npm --prefix frontend test -- --run src/tests/MockResearchAdapter.test.ts
```

Expected: FAIL because `getEventWorkbench("event-tsm")` still reports `awaiting_key_review`, `pending: 1`, and `verified: 0` after `reviewProposal`.

- [ ] **Step 3: Add one per-adapter review projection**

In `MockResearchAdapter`, replace the boolean-only state with a decision record:

```ts
type EventTsmReviewDecision = {
  outcome: ProposalReviewPayload["outcome"];
  reason: string;
  reviewerId: string;
};

private eventTsmReviewDecision: EventTsmReviewDecision | null = null;
private eventTsmConclusion: { text: string; reviewer: string } | null = null;
```

Reset both fields in `setScenario`. In `reviewProposal`, save the decision only after matching the TSM proposal:

```ts
async reviewProposal(
  proposalId: string,
  payload: ProposalReviewPayload,
): Promise<void> {
  this.throwIfOffline();
  if (proposalId === "proposal-event-tsm") {
    this.eventTsmProposalPending = false;
    this.eventTsmReviewDecision = {
      outcome: payload.outcome,
      reason: payload.reason,
      reviewerId: payload.reviewer_id,
    };
  }
  for (const run of MOCK_RESEARCH_RUNS) {
    for (const item of run.pending_proposals) {
      if (item.id === proposalId) item.status = "decided";
    }
  }
  return simulateLatency(undefined);
}
```

- [ ] **Step 4: Derive the event lifecycle from the decision**

Add a focused private projection method and use it in `eventResearchItems`, `getEventWorkbench`, and `getEventReviewQueue`:

```ts
private eventTsmProjection(): {
  lifecycle: EventLifecycle;
  verified: number;
  pending: number;
  conclusionState: "cannot_conclude" | "ai_draft" | "published";
} {
  if (this.eventTsmConclusion) {
    return {
      lifecycle: {
        status: "published",
        activeRunId: null,
        currentRound: 1,
        summary: "结论已由研究员发布，进入持续跟踪",
        currentGap: null,
        nextHumanAction: null,
      },
      verified: 1,
      pending: 0,
      conclusionState: "published",
    };
  }
  if (this.eventTsmReviewDecision?.outcome === "confirmed") {
    return {
      lifecycle: {
        status: "draft_ready",
        activeRunId: null,
        currentRound: 1,
        summary: "关键证据已审核，等待结论复核",
        currentGap: null,
        nextHumanAction: "审核结论草案",
      },
      verified: 1,
      pending: 0,
      conclusionState: "ai_draft",
    };
  }
  if (this.eventTsmReviewDecision?.outcome === "needs_more_evidence") {
    return {
      lifecycle: {
        status: "researching",
        activeRunId: "run-mock",
        currentRound: 2,
        summary: "审核已完成，系统正在按补证要求继续研究",
        currentGap: this.eventTsmReviewDecision.reason,
        nextHumanAction: null,
      },
      verified: 0,
      pending: 0,
      conclusionState: "cannot_conclude",
    };
  }
  if (this.eventTsmReviewDecision?.outcome === "rejected") {
    return {
      lifecycle: {
        status: "exhausted",
        activeRunId: null,
        currentRound: 1,
        summary: "当前候选已驳回，需要调整研究范围",
        currentGap: "当前候选被驳回，需要调整因素或补充来源",
        nextHumanAction: "编辑并继续自动研究",
      },
      verified: 0,
      pending: 0,
      conclusionState: "cannot_conclude",
    };
  }
  return {
    lifecycle: {
      status: "awaiting_key_review",
      activeRunId: "run-mock",
      currentRound: 1,
      summary: "已筛出 1 条可处理的关键证据，等待审核",
      currentGap: null,
      nextHumanAction: "审核 1 条关键证据",
    },
    verified: 0,
    pending: 1,
    conclusionState: "cannot_conclude",
  };
}
```

For TSM, use `pending` for `progress.pending` and `pendingProposalCount`, use `verified` for `progress.verified` and the first factor's `reviewedSupportCount`, and mark the evidence citation `reviewState: "reviewed"` only after confirmation. Keep the two blocked-source rows in the queue for audit display, but report `summary.reviewed: 1` and `summary.pending: 0` after any decision.

- [ ] **Step 5: Run the adapter tests until they pass**

Run:

```bash
npm --prefix frontend test -- --run src/tests/MockResearchAdapter.test.ts
```

Expected: PASS, including the three outcome projections.

- [ ] **Step 6: Commit the state transition**

```bash
git add frontend/src/data/mockResearchAdapter.ts frontend/src/tests/MockResearchAdapter.test.ts
git commit -m "fix: advance mock event after evidence review"
```

### Task 2: Complete conclusion review and publication in the adapter

**Files:**
- Modify: `frontend/src/data/mockResearchAdapter.ts:4225-4250`
- Modify: `frontend/src/data/mockResearchAdapter.ts:4435-4450`
- Test: `frontend/src/tests/MockResearchAdapter.test.ts`

- [ ] **Step 1: Write the failing publication test**

```ts
it("publishes the reviewed draft and moves the event into continuous tracking", async () => {
  const adapter = new MockResearchAdapter();
  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "confirmed",
    reason: "一手披露支持当前因素。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });

  await adapter.publishEventConclusion({
    caseId: "event-tsm",
    text: "资本开支上调构成当前市场担忧的重要可验证因素。",
    reviewer: "human:researcher",
  });

  const workbench = await adapter.getEventWorkbench("event-tsm");
  expect(workbench).toMatchObject({
    lifecycle: {
      status: "published",
      activeRunId: null,
      nextHumanAction: null,
    },
    conclusion: {
      state: "published",
      text: "资本开支上调构成当前市场担忧的重要可验证因素。",
    },
  });
  expect(workbench.nextAction).toMatchObject({
    kind: "wait",
    label: "当前没有需要处理的任务",
  });
  expect((await adapter.getEventConclusionHistory("event-tsm")).at(-1)).toMatchObject({
    state: "published",
    reviewer: "human:researcher",
  });
});
```

- [ ] **Step 2: Run the test and verify publication is currently a no-op**

Run:

```bash
npm --prefix frontend test -- --run src/tests/MockResearchAdapter.test.ts -t "publishes the reviewed draft"
```

Expected: FAIL because `publishEventConclusion` returns a response without changing workbench state.

- [ ] **Step 3: Implement publication against the same projection**

```ts
async publishEventConclusion(input: {
  caseId: string;
  text: string;
  reviewer: string;
}): Promise<{ conclusionId: string; state: "published" }> {
  this.throwIfOffline();
  if (input.caseId === "event-tsm") {
    if (this.eventTsmReviewDecision?.outcome !== "confirmed") {
      throw new Error("reviewed evidence is required before conclusion publication");
    }
    if (!input.text.trim()) throw new Error("conclusion text is required");
    this.eventTsmConclusion = {
      text: input.text.trim(),
      reviewer: input.reviewer,
    };
  }
  return simulateLatency({
    conclusionId: `event-conclusion-${input.caseId}`,
    state: "published",
  });
}
```

Update `getEventWorkbench` so the published conclusion uses `eventTsmConclusion.text`, produces `nextAction: { kind: "wait", label: "当前没有需要处理的任务" }`, and update `getEventConclusionHistory` so a confirmed review exposes the draft while a publication appends the immutable published version. The published state must enter the existing “本轮已完成” presentation instead of manufacturing a conclusion-change task when no new evidence exists.

- [ ] **Step 4: Verify publication and all adapter tests**

Run:

```bash
npm --prefix frontend test -- --run src/tests/MockResearchAdapter.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit publication state**

```bash
git add frontend/src/data/mockResearchAdapter.ts frontend/src/tests/MockResearchAdapter.test.ts
git commit -m "feat: complete mock conclusion publication"
```

### Task 3: Synchronize the global run strip with the event projection

**Files:**
- Modify: `frontend/src/data/mockResearchOsApi.ts:100-115`
- Modify: `frontend/src/data/mockResearchOsApi.ts:224`
- Modify: `frontend/src/data/mockResearchOsApi.ts:450-575`
- Modify: `frontend/src/app/AppShell.tsx:75-170`
- Test: `frontend/src/tests/ResearchOsApi.test.ts`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Write failing shared-state run tests**

Import `MockResearchAdapter` and `MockResearchOsApi`, then add:

```ts
it("removes the awaiting-review run after evidence is accepted", async () => {
  const adapter = new MockResearchAdapter();
  const api = new MockResearchOsApi(adapter);

  expect((await api.activeRuns()).items[0]).toMatchObject({
    case_id: "event-tsm",
    status: "awaiting_review",
  });

  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "confirmed",
    reason: "原文支持当前因素。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });

  expect((await api.activeRuns()).items).toEqual([]);
  expect((await api.runs()).items[0]).toMatchObject({
    case_id: "event-tsm",
    status: "succeeded",
    stage: "complete",
  });
  expect((await api.runEvents("run-demo-1")).items.at(-1)).toMatchObject({
    stage: "review",
    status: "completed",
  });
});

it("shows a running system task after the reviewer requests more evidence", async () => {
  const adapter = new MockResearchAdapter();
  const api = new MockResearchOsApi(adapter);
  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "needs_more_evidence",
    reason: "补充反证。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });

  expect((await api.activeRuns()).items[0]).toMatchObject({
    case_id: "event-tsm",
    status: "running",
    stage: "retrieve",
  });
});
```

- [ ] **Step 2: Run the focused tests and verify the stale global strip**

Run:

```bash
npm --prefix frontend test -- --run src/tests/ResearchOsApi.test.ts
```

Expected: FAIL because `MockResearchOsApi.activeRuns()` always returns the static `awaiting_review` run.

- [ ] **Step 3: Extend the mock store seam to read the event workbench**

Extend `MockDocumentSupplementStore` without exposing adapter internals:

```ts
export interface MockDocumentSupplementStore {
  // existing document methods remain unchanged
  getEventWorkbench?(caseId: string): Promise<EventWorkbench>;
}
```

Import `EventWorkbench` as a type. Add a helper inside `MockResearchOsApi`:

```ts
private async eventRunProjection() {
  const workbench = await this.documentStore?.getEventWorkbench?.("event-tsm");
  const status = workbench?.lifecycle.status ?? "awaiting_key_review";
  if (status === "researching" || status === "continuing") {
    return { active: true, status: "running", stage: "retrieve" } as const;
  }
  if (status === "awaiting_key_review") {
    return { active: true, status: "awaiting_review", stage: "review" } as const;
  }
  return { active: false, status: "succeeded", stage: "complete" } as const;
}
```

Use the projection in `activeRuns()`, `runs()`, and `runEvents("run-demo-1")`. `activeRuns()` returns an empty `items` array when `active` is false. `runs()` always returns one archive record and never spreads an undefined active item. `runEvents()` must append a completed review event after confirmation or rejection, and a new running retrieval event after `needs_more_evidence`; it must not keep returning “候选证据等待人工审核” after the decision.

- [ ] **Step 4: Make AppShell refresh both event tasks and runs on workflow completion**

Extract the event-list loader inside `AppShell` and register a named refresh event beside the existing interval:

```ts
useEffect(() => {
  let live = true;
  const load = () => researchClient
    .listEventResearch()
    .then((items) => {
      if (live) setEvents(items);
    })
    .catch(() => {
      if (live) setEvents([]);
    });
  void load();
  const refresh = window.setInterval(load, 20_000);
  const refreshAfterWorkflowDecision = () => void load();
  window.addEventListener(
    "research-os-workflow-refresh",
    refreshAfterWorkflowDecision,
  );
  return () => {
    live = false;
    window.clearInterval(refresh);
    window.removeEventListener(
      "research-os-workflow-refresh",
      refreshAfterWorkflowDecision,
    );
  };
}, []);
```

Register the same event in the active-run effect and invoke its existing `load` function. Keep `research-os-run-refresh` for run-start callers.

- [ ] **Step 5: Run API and shell-related tests**

Run:

```bash
npm --prefix frontend test -- --run src/tests/ResearchOsApi.test.ts src/tests/ResearchOsPages.test.tsx -t "active|run status|running"
```

Expected: PASS with no stale `awaiting_review` assertion.

- [ ] **Step 6: Commit global run synchronization**

```bash
git add frontend/src/data/mockResearchOsApi.ts frontend/src/app/AppShell.tsx frontend/src/tests/ResearchOsApi.test.ts frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "fix: synchronize mock run status with event review"
```

### Task 4: Return successful evidence decisions to the refreshed workbench

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx:2050-2190`
- Modify: `frontend/src/features/case/CasePages.tsx:2430-2540`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx:1888-1940`

- [ ] **Step 1: Replace the queue-only page test with a workflow assertion**

Render both review and conclusion routes against the same adapter:

```tsx
it("returns an accepted evidence decision to the refreshed conclusion task", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
      <Routes>
        <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
        <Route path="/events/:caseId" element={<CaseConclusionPage />} />
      </Routes>
    </MemoryRouter>,
  );

  await user.type(
    await screen.findByLabelText("审核理由"),
    "原文来自冻结的一手公司披露，支持当前因素。",
  );
  await user.click(screen.getByRole("button", { name: "确认采纳" }));

  expect(
    await screen.findByRole("heading", { name: "审核结论草案" }),
  ).toBeVisible();
  expect(screen.getByLabelText("当前事件研究进展")).toHaveTextContent("形成结论");
  expect(screen.getByText("已审核证据 1")).toBeVisible();
  expect(screen.getByText("待审核 0")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("证据已采纳");
});
```

Add equivalent focused tests for `要求补充证据` and `驳回候选`, asserting the destination workbench shows `系统补证中` and `编辑并继续自动研究` respectively.

- [ ] **Step 2: Run the focused page tests and verify they remain on the review page**

Run:

```bash
npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx -t "returns an accepted evidence decision|requests more evidence|rejects the only actionable"
```

Expected: FAIL because `ReviewItem` only reloads the local queue and `CaseFrame` retains its original workbench.

- [ ] **Step 3: Return the outcome from `ReviewItem`**

Change the callback contract:

```ts
type EvidenceReviewOutcome = "confirmed" | "rejected" | "needs_more_evidence";

function ReviewItem({
  item,
  onDecided,
}: {
  item: EventReviewQueue["items"][number];
  onDecided: (outcome: EvidenceReviewOutcome) => void;
}) {
  // existing form state
  async function decide(outcome: EvidenceReviewOutcome) {
    if (!reason.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await researchClient.reviewProposal(item.proposalId, {
        outcome,
        reason: reason.trim(),
        reviewer_id: "human:researcher",
        expected_version: item.proposalVersion,
      });
      onDecided(outcome);
    } catch {
      setError("提交审核决定失败；候选未被自动采纳。请刷新后重试。");
    } finally {
      setSubmitting(false);
    }
  }
}
```

- [ ] **Step 4: Navigate back with a one-time completion notice**

In `ReviewContent`, use `useNavigate` and `useLocation`. Preserve the query string and navigate only after a successful write:

```ts
const navigate = useNavigate();
const location = useLocation();

function finishEvidenceReview(outcome: EvidenceReviewOutcome) {
  const notice = outcome === "confirmed"
    ? "证据已采纳，系统已生成待复核的结论草案。"
    : outcome === "needs_more_evidence"
      ? "补证要求已记录，系统将按冻结范围继续处理。"
      : "候选已驳回，请调整研究范围或补充来源。";
  window.dispatchEvent(new Event("research-os-workflow-refresh"));
  navigate(
    { pathname: `/events/${caseId}`, search: location.search },
    { state: { workflowNotice: notice } },
  );
}
```

The page integration test should render `ResearchOsRoutes` (or an `AppShell` route fixture), submit the evidence decision, and assert that the global “等待人工审核 · 等待审核” strip disappears without advancing fake timers. This locks down the immediate `research-os-workflow-refresh` path rather than relying on the 15-second poll.

In `CaseConclusionPage`, read `location.state?.workflowNotice` and render it once above the columns:

```tsx
{workflowNotice && (
  <p className="ros-success" role="status">
    {workflowNotice}
  </p>
)}
```

Do not navigate in the catch branch; the entered reason and candidate remain visible for retry.

- [ ] **Step 5: Run focused and complete page tests**

Run:

```bash
npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit evidence-review navigation**

```bash
git add frontend/src/features/case/CasePages.tsx frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "fix: return reviewed evidence to the next event task"
```

### Task 5: Add a real conclusion-review task instead of looping to an empty queue

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx:2050-2190`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: Write the failing conclusion publication page test**

Use a fresh adapter, confirm the evidence through its public API, then render the review route:

```tsx
it("lets a researcher publish the conclusion draft and enter continuous tracking", async () => {
  const adapter = new MockResearchAdapter();
  await adapter.reviewProposal("proposal-event-tsm", {
    outcome: "confirmed",
    reason: "一手披露支持当前因素。",
    reviewer_id: "human:researcher",
    expected_version: 1,
  });
  setResearchClient(adapter);
  const user = userEvent.setup();

  render(
    <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
      <Routes>
        <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
        <Route path="/events/:caseId" element={<CaseConclusionPage />} />
      </Routes>
    </MemoryRouter>,
  );

  const draft = await screen.findByLabelText("结论草案");
  expect(draft).toHaveValue("当前结论草案等待人工复核。");
  await user.clear(draft);
  await user.type(draft, "资本开支上调构成当前市场担忧的重要可验证因素。");
  await user.click(screen.getByRole("button", { name: "发布结论并进入持续跟踪" }));

  expect(
    await screen.findByText("资本开支上调构成当前市场担忧的重要可验证因素。"),
  ).toBeVisible();
  expect(screen.getByLabelText("当前事件研究进展")).toHaveTextContent("持续跟踪");
  expect(screen.getByRole("status")).toHaveTextContent("结论已发布");
});
```

- [ ] **Step 2: Run the focused test and verify the missing form**

Run:

```bash
npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx -t "publish the conclusion draft"
```

Expected: FAIL because `/review` only renders an empty evidence queue after confirmation.

- [ ] **Step 3: Pass workbench data into `ReviewContent`**

```tsx
export function CaseReviewPage() {
  return (
    <CaseFrame>
      {(data, caseId) => <ReviewContent caseId={caseId} workbench={data} />}
    </CaseFrame>
  );
}
```

When `workbench.nextAction.kind === "review_conclusion"`, render `ConclusionReviewTask` instead of loading the evidence queue.

- [ ] **Step 4: Implement the conclusion-review form**

```tsx
function ConclusionReviewTask({
  caseId,
  workbench,
}: {
  caseId: string;
  workbench: EventWorkbench;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [text, setText] = useState(workbench.conclusion.text);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function publish() {
    if (!text.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await researchClient.publishEventConclusion({
        caseId,
        text: text.trim(),
        reviewer: "human:researcher",
      });
      window.dispatchEvent(new Event("research-os-workflow-refresh"));
      navigate(
        { pathname: `/events/${caseId}`, search: location.search },
        { state: { workflowNotice: "结论已发布，当前事件进入持续跟踪。" } },
      );
    } catch {
      setError("结论没有发布；草案仍保留，请检查后重试。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="ros-review-workbench">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">人工复核</p>
          <h2>审核结论草案</h2>
          <p>发布前请确认结论没有超出已审核证据的边界。</p>
        </div>
      </header>
      <label>
        结论草案
        <textarea
          aria-label="结论草案"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <button
        className="ros-button ros-button--primary"
        type="button"
        disabled={!text.trim() || saving}
        onClick={() => void publish()}
      >
        {saving ? "正在发布…" : "发布结论并进入持续跟踪"}
      </button>
      {error && <p className="ros-error" role="alert">{error}</p>}
    </section>
  );
}
```

- [ ] **Step 5: Run page tests**

Run:

```bash
npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit the conclusion-review UI**

```bash
git add frontend/src/features/case/CasePages.tsx frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "feat: complete human conclusion review"
```

### Task 6: Prove the complete browser workflow

**Files:**
- Modify: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: Add the end-to-end human flow**

```ts
test("researcher completes evidence review and publishes the conclusion", async ({ page }) => {
  await page.goto("/events/event-tsm?client=mock");

  await page.getByRole("link", { name: "进入证据审核" }).click();
  await page.getByLabel("审核理由").fill(
    "已核对冻结原文与定位，该陈述支持当前资本开支因素。",
  );
  await page.getByRole("button", { name: "确认采纳" }).click();

  await expect(page).toHaveURL(/\/events\/event-tsm\?client=mock/);
  await expect(page.getByLabel("当前事件研究进展").locator('[aria-current="step"]'))
    .toContainText("形成结论");
  await expect(page.getByText("已审核证据 1")).toBeVisible();
  await expect(page.getByText("待审核 0")).toBeVisible();
  await expect(page.getByRole("heading", { name: "审核结论草案" })).toBeVisible();

  await page.getByRole("link", { name: "复核结论草案" }).click();
  await page.getByLabel("结论草案").fill(
    "资本开支上调构成当前市场担忧的重要可验证因素。",
  );
  await page.getByRole("button", { name: "发布结论并进入持续跟踪" }).click();

  await expect(page).toHaveURL(/\/events\/event-tsm\?client=mock/);
  await expect(page.getByLabel("当前事件研究进展").locator('[aria-current="step"]'))
    .toContainText("持续跟踪");
  await expect(page.getByRole("status")).toContainText("结论已发布");
  await expect(page.getByText("等待人工审核 · 等待审核")).toHaveCount(0);
  await expect(page.getByText("本轮已完成")).toBeVisible();
  await expect(page.getByRole("heading", { name: "当前没有需要处理的任务" }))
    .toBeVisible();
});
```

- [ ] **Step 2: Run the focused E2E test**

Run:

```bash
npm --prefix frontend run e2e -- --grep "completes evidence review"
```

Expected: PASS.

- [ ] **Step 3: Run all event-first E2E tests**

Run:

```bash
npm --prefix frontend run e2e -- --grep "Event-first Research OS"
```

Expected: PASS, including event switching, narrow-screen switching, and the complete human workflow.

- [ ] **Step 4: Commit the browser regression**

```bash
git add frontend/e2e/research-os.spec.ts
git commit -m "test: cover complete human research workflow"
```

### Task 7: Full verification and integration readiness

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run type checking**

```bash
npm --prefix frontend run typecheck
```

Expected: exit 0.

- [ ] **Step 2: Run the complete unit and integration suite**

```bash
npm --prefix frontend test -- --run
```

Expected: all test files and tests pass.

- [ ] **Step 3: Run the production build**

```bash
npm --prefix frontend run build
```

Expected: Vite build succeeds with no TypeScript errors.

- [ ] **Step 4: Run the complete human-flow E2E one final time**

```bash
npm --prefix frontend run e2e -- --grep "completes evidence review"
```

Expected: PASS.

- [ ] **Step 5: Check repository hygiene**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended implementation files are changed.

- [ ] **Step 6: Commit any final test-only corrections**

If verification required a test-only adjustment, commit only those files:

```bash
git add frontend/src/tests/MockResearchAdapter.test.ts frontend/src/tests/ResearchOsApi.test.ts frontend/src/tests/ResearchOsPages.test.tsx frontend/e2e/research-os.spec.ts
git commit -m "test: harden human review flow regression"
```

If no final corrections were needed, do not create an empty commit.
