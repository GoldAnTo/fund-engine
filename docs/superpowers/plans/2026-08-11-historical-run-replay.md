# Historical Run Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable, URL-addressable run-history selector to the Case monitor so a researcher can replay one exact historical ResearchRun.

**Architecture:** Keep backend contracts unchanged. `MonitorContent` reads the existing per-case run list through `researchClient`, derives the selected run solely from `?run=`, and uses that ID for the run detail and event reads. The monitor page renders a read-only 20-row history rail beside the existing summary and reuses `RunDrawer` for frozen-scope and assessment detail.

**Tech Stack:** React, TypeScript, React Router query parameters, Vitest, Testing Library, existing Research OS HTTP/mock adapters, CSS.

---

## File structure

- Modify: `frontend/src/features/case/CasePages.tsx` — URL-driven selection, run-list read/error state, history rail, explicit recovery action, and existing start/review refresh integration.
- Modify: `frontend/src/styles/research-os.css` — three-area monitor layout, readable selectable history rows, and responsive single-column fallback.
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx` — browser-level component tests for URL selection, history selection, reload stability, and no-fallback failure states.
- No backend, OpenAPI, domain-type, or adapter changes: `ResearchRunSummary` already carries `id`, `status`, `stage`, `created_at`, and `stop_reason`; `researchClient.listResearchRuns(caseId)` already calls the authorized case-scoped endpoint.

### Task 1: Add red tests for URL-selected historical replay

**Files:**
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx` near the existing monitor tests around `reviews a provisional assessment from the run that produced it`

- [ ] **Step 1: Add a monitor-history fixture helper and a location probe used only by these tests**

```tsx
const historicalRuns = [
  {
    id: "run-reviewed",
    status: "succeeded",
    stage: "complete",
    round: 1,
    stop_reason: "max_rounds_reached",
    created_at: "2026-08-11T01:00:00Z",
    next_action: "查看已审核结论",
  },
  {
    id: "run-waiting",
    status: "waiting_for_review",
    stage: "stopped",
    round: 1,
    stop_reason: "max_rounds_reached",
    created_at: "2026-08-10T01:00:00Z",
    next_action: "人工审核临时评估",
  },
];

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="monitor-location">{location.search}</output>;
}

function renderMonitor(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/events/:caseId/monitor"
          element={<><CaseMonitorPage /><LocationProbe /></>}
        />
      </Routes>
    </MemoryRouter>,
  );
}
```

Add `useLocation` to the existing `react-router-dom` test import. In each test, use a `MockResearchAdapter`, spy `listResearchRuns` to return `historicalRuns`, spy `getResearchRun` to return the requested summary plus the required `ResearchRunDetail` fields, and use the existing fetch mock pattern for monitor, events, worker status, and researchability.

- [ ] **Step 2: Write the valid deep-link test before implementation**

```tsx
it("replays the exact historical run named by the monitor URL", async () => {
  renderMonitor("/events/event-tsm/monitor?run=run-reviewed");

  expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
  expect(screen.getByText("人工已完成临时 AI 评估审核；本次运行没有剩余待审项。")).toBeVisible();
  expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed");
});
```

- [ ] **Step 3: Run the deep-link test and verify it fails for the current state-driven selection**

Run:

```bash
npm run test -- --run src/tests/ResearchOsPages.test.tsx -t "replays the exact historical run named by the monitor URL"
```

Expected: FAIL because the current monitor ignores `?run=` and replaces selection with `latest_run`.

- [ ] **Step 4: Add red tests for default selection, clicking history, reload stability, and invalid selection**

```tsx
it("writes the latest run into an empty monitor URL and keeps it after remount", async () => {
  const first = renderMonitor("/events/event-tsm/monitor");
  await waitFor(() =>
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed"),
  );
  first.unmount();
  renderMonitor("/events/event-tsm/monitor?run=run-reviewed");
  expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
});

it("selects a history row by changing the URL and opening that run detail", async () => {
  const user = userEvent.setup();
  renderMonitor("/events/event-tsm/monitor?run=run-reviewed");
  await user.click(await screen.findByRole("button", { name: /待人工审核.*run-waiting/ }));
  expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-waiting");
  expect(await screen.findByText("ResearchRun · run-waiting")).toBeVisible();
});

it("does not replace an invalid run URL with the latest run", async () => {
  renderMonitor("/events/event-tsm/monitor?run=run-missing");
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("无法回放此运行");
  expect(alert).toHaveTextContent("run-missing");
  expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-missing");
  expect(screen.queryByText("ResearchRun · run-reviewed")).not.toBeInTheDocument();
});
```

Use one accessible name per history row, for example `已完成 · 完成 · run-reviewed`, so the test does not rely on a non-semantic `<li>` click target. Add a second invalid-case assertion where `getResearchRun("run-reviewed")` rejects: it must show `无法回放此运行` while retaining `?run=run-reviewed`, not render `run-waiting` as a substitute.

- [ ] **Step 5: Run the focused red set and commit the test contract**

Run:

```bash
npm run test -- --run src/tests/ResearchOsPages.test.tsx -t "historical run|invalid run URL|latest run into an empty"
```

Expected: FAIL until Task 2 and Task 3 are implemented.

Commit:

```bash
git add frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "test: define historical run replay behavior"
```

### Task 2: Make monitor selection URL-driven and load case-scoped history

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx:3697-4148`

- [ ] **Step 1: Replace local selected-run ownership with `?run=` helpers**

At the start of `MonitorContent`, replace the `selectedRunId` state with query state and introduce the history states:

```tsx
const [searchParams, setSearchParams] = useSearchParams();
const selectedRunId = searchParams.get("run");
const [runHistory, setRunHistory] = useState<ResearchRunSummary[]>([]);
const [runHistoryLoaded, setRunHistoryLoaded] = useState(false);
const [runHistoryLoadError, setRunHistoryLoadError] = useState(false);
const selectedRunIdRef = useRef<string | null>(selectedRunId);

useEffect(() => {
  selectedRunIdRef.current = selectedRunId;
}, [selectedRunId]);

function selectRun(runId: string, options: { replace?: boolean; openDrawer?: boolean } = {}) {
  setSearchParams((current) => {
    const next = new URLSearchParams(current);
    next.set("run", runId);
    return next;
  }, { replace: options.replace ?? false });
  if (options.openDrawer ?? true) setDrawer(true);
}
```

Import `useRef` and the existing `ResearchRunSummary` type. Remove `activeRunId` from `CaseMonitorPage` and `MonitorContent`; it is no longer an allowed competing selection source.

- [ ] **Step 2: Add independent monitor/history reads and default only an empty URL**

Replace `loadMonitor({ retainSelectedRun })` with a monitor-only read that never assigns the selected run. Add a separate `loadRunHistory`:

```tsx
const loadMonitor = () => {
  setMonitorLoadError(false);
  return researchOsApi.monitor(caseId).then((value) => {
    setDetail(value);
    if (!selectedRunIdRef.current && value.latest_run?.id) {
      selectRun(value.latest_run.id, { replace: true, openDrawer: false });
    }
  }).catch(() => setMonitorLoadError(true));
};

const loadRunHistory = () => {
  setRunHistoryLoadError(false);
  setRunHistoryLoaded(false);
  return researchClient.listResearchRuns(caseId).then((items) => {
    setRunHistory(items.slice(0, 20));
    setRunHistoryLoaded(true);
  }).catch(() => {
    setRunHistory([]);
    setRunHistoryLoadError(true);
  });
};

useEffect(() => {
  void loadMonitor();
  void loadRunHistory();
}, [caseId]);
```

Keep monitor and history failures independent. Do not call `selectRun` from an update whose `selectedRunIdRef.current` is non-null; that is the stale-response guard.

- [ ] **Step 3: Block invalid selection from becoming another run and use the URL ID for all refreshes**

Derive the explicit selection failure before the events/detail effects:

```tsx
const selectedRunKnown = !selectedRunId || !runHistoryLoaded || runHistory.some(
  (run) => run.id === selectedRunId,
);
const cannotReplaySelectedRun = Boolean(selectedRunId && !selectedRunKnown);
const runReplayLoadFailed = Boolean(selectedRunId && runDetailLoadError);
```

When `cannotReplaySelectedRun` is true, clear `events` and `runDetail` and skip `runEvents` / `getResearchRun`; when it is false, those effects must take `selectedRunId` directly from the query parameter. In `loadRunDetail` keep setting `runDetail` to `null` on rejection so stale detail cannot remain visible.

Change start, cancel, and assessment-review refresh to preserve the invariant:

```tsx
const created = await researchOsApi.startMonitorRun(caseId);
selectRun(created.id);
await Promise.all([loadMonitor(), loadRunHistory(), loadEvents(created.id)]);

await Promise.all([loadMonitor(), loadRunHistory(), loadEvents(selectedRunId)]);

await Promise.all([loadMonitor(), loadRunHistory(), loadRunDetail(selectedRunId), loadEvents(selectedRunId)]);
```

Do not set a selected ID in any of these paths; only `selectRun` changes `?run=`.

- [ ] **Step 4: Run the URL-focused tests and verify their data behavior passes**

Run:

```bash
npm run test -- --run src/tests/ResearchOsPages.test.tsx -t "historical run|invalid run URL|latest run into an empty"
```

Expected: Some rendering assertions may still fail until Task 3; URL remains exact, deep-link detail reads use that ID, and invalid IDs do not request a substitute run.

- [ ] **Step 5: Commit the data-flow change**

```bash
git add frontend/src/features/case/CasePages.tsx
git commit -m "feat: select monitor runs from the URL"
```

### Task 3: Render the persistent history rail and explicit recovery states

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx:3898-4148`
- Modify: `frontend/src/styles/research-os.css:9-11`

- [ ] **Step 1: Render the history rail with semantic row buttons before the existing run card**

Inside `.ros-monitor-grid`, add this section before the current `.ros-run-card`:

```tsx
<aside className="ros-run-history" aria-label="运行历史">
  <p className="ros-eyebrow">运行历史</p>
  <h2>冻结记录</h2>
  {runHistoryLoadError ? (
    <section className="ros-empty ros-empty--compact" role="alert">
      <strong>运行历史暂不可读取</strong>
      <p>已选运行的详情不会因此被其他记录替代。</p>
      <button className="ros-button ros-button--secondary" type="button" onClick={() => void loadRunHistory()}>
        重新读取运行历史
      </button>
    </section>
  ) : (
    <ol>
      {runHistory.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            className={item.id === selectedRunId ? "is-selected" : undefined}
            aria-current={item.id === selectedRunId ? "true" : undefined}
            aria-label={`${runStatusLabel(item.status)} · ${runStageLabel(item.stage)} · ${item.id}`}
            onClick={() => selectRun(item.id)}
          >
            <strong>{runStatusLabel(item.status)} · {runStageLabel(item.stage)}</strong>
            <span>{new Date(item.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</span>
            <small>{item.stop_reason || "尚无停止原因"}</small>
          </button>
        </li>
      ))}
    </ol>
  )}
</aside>
```

For an empty successful history response, render `尚无运行记录` in the same rail. The list must never contain a start, cancel, review, or edit button.

- [ ] **Step 2: Render the no-fallback replay error and explicit latest action**

Place this alert above the grid, after existing monitor/read errors:

```tsx
{selectedRunId && (cannotReplaySelectedRun || runReplayLoadFailed) && (
  <section className="ros-empty ros-empty--compact" role="alert">
    <strong>无法回放此运行</strong>
    <p>运行 {selectedRunId} 不在当前案例可访问的历史中，或详情暂不可读取；页面不会自动切换为另一条运行。</p>
    {detail?.latest_run?.id && (
      <button
        className="ros-button ros-button--secondary"
        type="button"
        onClick={() => selectRun(detail.latest_run.id)}
      >
        返回最新运行
      </button>
    )}
  </section>
)}
```

When this alert is active, do not render the selected run’s events as if they were valid. The current card may show its existing neutral “运行状态暂不可读取” message, but it must not render a different run’s ID, stage, status, assessment, or event log.

- [ ] **Step 3: Add scoped CSS without changing other Research OS pages**

Append monitor-specific rules next to `.ros-monitor-grid`:

```css
.ros-monitor-grid{grid-template-columns:minmax(210px,.42fr) minmax(0,1.05fr) minmax(280px,.8fr)}
.ros-run-history{padding:16px;border:1px solid var(--ros-strong);border-radius:10px;background:var(--ros-surface);box-shadow:var(--ros-shadow)}
.ros-run-history h2{margin:5px 0 12px;font-size:17px}.ros-run-history ol{margin:0;padding:0;list-style:none;border-top:1px solid var(--ros-rule)}
.ros-run-history li{border-bottom:1px solid var(--ros-rule)}.ros-run-history button{display:grid;width:100%;gap:4px;padding:12px 0;border:0;background:transparent;color:inherit;text-align:left;cursor:pointer}
.ros-run-history button.is-selected{padding:10px;margin:0 -10px;border-radius:7px;background:var(--ros-moss-soft);color:var(--ros-moss)}
.ros-run-history span,.ros-run-history small{color:var(--ros-soft);font-size:11px}.ros-run-history button.is-selected span,.ros-run-history button.is-selected small{color:inherit}
```

Keep `.ros-monitor-grid` in the existing `@media(max-width:1050px)` selector so it collapses to one column. Do not alter the global drawer, card, or non-monitor grid rules.

- [ ] **Step 4: Run the complete monitor test file and verify the new contract passes**

Run:

```bash
npm run test -- --run src/tests/ResearchOsPages.test.tsx
```

Expected: PASS, including existing immediate-run, stop-reason, and provisional-assessment tests.

- [ ] **Step 5: Commit the visual and recovery behavior**

```bash
git add frontend/src/features/case/CasePages.tsx frontend/src/styles/research-os.css frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "feat: replay historical monitor runs"
```

### Task 4: Verify the real Industrial Foxconn case

**Files:**
- No source change unless verification exposes a reproducible defect; if it does, add a focused regression test in `frontend/src/tests/ResearchOsPages.test.tsx` before changing production code.

- [ ] **Step 1: Run static and build verification**

Run:

```bash
npm run typecheck
npm run build
npm run test -- --run src/tests/ResearchOsPages.test.tsx
```

Expected: all commands exit 0. Preserve and report any pre-existing unrelated warnings separately from failures.

- [ ] **Step 2: Perform the real browser path against the isolated Industrial Foxconn service**

Open the real Case monitor and validate both deep links:

```text
/events/43d6eb7c-9ef0-4b6c-a76f-7cce0cbaa1cc/monitor?run=967d6dc2-cd29-4205-aea3-f7dedc613e92
/events/43d6eb7c-9ef0-4b6c-a76f-7cce0cbaa1cc/monitor?run=ec3f6d5c-7683-4495-ba20-5378ee0dc541
```

For each link, record the selected ID, status/stage, frozen-scope drawer content, and event timeline. For the succeeded run, confirm the event text `人工已完成临时 AI 评估审核；本次运行没有剩余待审项。`; switch to the waiting run and back, then refresh the succeeded deep link. Enter a fabricated `?run=not-a-real-run`, confirm the alert keeps that ID, then use `返回最新运行` and confirm only that explicit action changes the URL.

- [ ] **Step 3: Commit only if verification required a source correction; otherwise leave Task 3 as the implementation commit**

```bash
git status --short
git log --oneline -3
```

Expected: clean worktree and a commit history containing the test contract, URL selection, and history replay commits.

## Final acceptance checklist

- [ ] No code changes touched backend contracts, persistence, or historical audit records.
- [ ] The query parameter is the sole selected-run authority; monitor refreshes and review refreshes do not overwrite it.
- [ ] The history list is case scoped, read only, and capped at 20 rows.
- [ ] A list read failure does not erase an already selected run detail.
- [ ] Invalid or detail-failed selections display their requested ID and never silently substitute the latest or an older run.
- [ ] The two real Industrial Foxconn runs can be switched and deep-linked after a browser refresh.
