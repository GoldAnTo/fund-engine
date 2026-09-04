# FundClaw Research Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing frontend experience with one mock-driven FundClaw research workbench that matches the approved three-column research gateway design.

**Architecture:** The Vite entry point renders one self-contained `ResearchWorkbench` page rather than the current React Router and API adapter stack. A separate mock-data module owns threads, tasks, evidence and right-panel data, while the component owns the selection, filter and composer state. Scoped CSS provides the desktop grid and responsive drawer behavior.

**Tech Stack:** React 18, TypeScript, Vite, CSS with OKLCH tokens, Vitest, Testing Library.

---

## File structure

- `frontend/src/main.tsx`: Simplify bootstrap to render the new workbench directly.
- `frontend/src/workbench/mockData.ts`: Typed local fixtures for threads, feed records, files and evidence.
- `frontend/src/workbench/ResearchWorkbench.tsx`: Semantic, interactive single-page workbench.
- `frontend/src/workbench/ResearchWorkbench.css`: Desktop grid and responsive panel behavior.
- `frontend/src/styles/tokens.css`, `global.css`, `app.css`: Replace the old visual system and import the new surface styles.
- `frontend/src/tests/ResearchWorkbench.test.tsx`: Behavioral tests for selection, filtering, tabs and composer feedback.

### Task 1: Define mock data and initial render

**Files:**
- Create: `frontend/src/workbench/mockData.ts`
- Create: `frontend/src/workbench/ResearchWorkbench.tsx`
- Create: `frontend/src/tests/ResearchWorkbench.test.tsx`

- [ ] **Step 1: Write the failing initial-render test**

```tsx
it("renders the active semiconductor research thread and its evidence", () => {
  render(<ResearchWorkbench />);
  expect(screen.getByRole("heading", { name: "科创板半导体设备国产化机会研究" })).toBeInTheDocument();
  expect(screen.getByText("证据与来源")).toBeInTheDocument();
  expect(screen.getByText(/中微公司：刻蚀设备在客户端验证进展/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Verify the test is red**

Run: `cd frontend && npm test -- ResearchWorkbench.test.tsx`

Expected: FAIL because the component and fixture module do not exist.

- [ ] **Step 3: Add the typed fixture contract**

```ts
export type WorkbenchThread = { id: string; title: string; status: string; updatedAt: string; active?: boolean };
export type FeedKind = "goal" | "agent" | "reviewer";
export type FeedItem = { id: string; kind: FeedKind; title: string; status: string; summary: string; references: number; files?: { name: string; type: string }[] };
export type Evidence = { id: string; title: string; date: string; source: string; summary: string; label: string; tone: "support" | "related" | "counter" };
export const workbenchThreads: WorkbenchThread[] = [/* five Chinese research threads */];
export const workbenchFeed: FeedItem[] = [/* goal plus four analysis roles */];
export const workbenchEvidence: Evidence[] = [/* four evidence cards */];
```

Keep all mock copy in this module. Implement the first semantic render with `nav[aria-label="研究空间"]`, `main[aria-label="研究工作台"]`, and `aside[aria-label="研究证据"]`.

- [ ] **Step 4: Verify green**

Run: `cd frontend && npm test -- ResearchWorkbench.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/workbench/mockData.ts frontend/src/workbench/ResearchWorkbench.tsx frontend/src/tests/ResearchWorkbench.test.tsx
git commit -m "feat: add FundClaw workbench data and shell"
```

### Task 2: Replace the frontend entry and visual system

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/styles/app.css`
- Create: `frontend/src/workbench/ResearchWorkbench.css`
- Modify: `frontend/src/workbench/ResearchWorkbench.tsx`

- [ ] **Step 1: Write the failing visual-class contract**

```tsx
it("uses the three-column workbench class contract", () => {
  const { container } = render(<ResearchWorkbench />);
  expect(container.querySelector(".workbench-sidebar")).toBeInTheDocument();
  expect(container.querySelector(".workbench-main")).toBeInTheDocument();
  expect(container.querySelector(".workbench-evidence")).toBeInTheDocument();
});
```

- [ ] **Step 2: Verify red**

Run: `cd frontend && npm test -- ResearchWorkbench.test.tsx`

Expected: FAIL until class names are placed on the actual three landmarks.

- [ ] **Step 3: Build the complete reference composition**

Use these foundation tokens and grid:

```css
:root {
  --wb-canvas: oklch(0.982 0.007 86);
  --wb-surface: oklch(0.995 0.004 86);
  --wb-ink: oklch(0.225 0.028 162);
  --wb-accent: oklch(0.31 0.074 158);
  --wb-line: oklch(0.894 0.012 84);
}
.workbench {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr) 365px;
  grid-template-rows: 57px minmax(0, 1fr);
  background: var(--wb-canvas);
}
```

Render a 57px topbar (FundClaw, search, utilities, user), the selected thread in the left rail, research scope chips and activity feed in the center, and related evidence cards in the right panel. Use semantic buttons, visible focus rings, SVG icons, compact status labels, no pure black/white, no decorative animation, and respect `prefers-reduced-motion`.

Replace `main.tsx` RouterProvider bootstrap with direct `<ResearchWorkbench />`; do not import legacy router, client or pages. Make `app.css` only import the workbench stylesheet so no old-page CSS can affect this view.

- [ ] **Step 4: Verify green and type safety**

Run: `cd frontend && npm test -- ResearchWorkbench.test.tsx && npm run typecheck`

Expected: both commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/main.tsx frontend/src/styles frontend/src/workbench
git commit -m "feat: style FundClaw research workbench"
```

### Task 3: Add stateful workbench interactions

**Files:**
- Modify: `frontend/src/workbench/ResearchWorkbench.tsx`
- Modify: `frontend/src/tests/ResearchWorkbench.test.tsx`

- [ ] **Step 1: Write failing interaction tests**

```tsx
it("filters the research feed to evidence items", async () => {
  const user = userEvent.setup();
  render(<ResearchWorkbench />);
  await user.click(screen.getByRole("button", { name: /证据 6/ }));
  expect(screen.getByText("分析师-产业")).toBeInTheDocument();
  expect(screen.queryByText("质控审核员")).not.toBeInTheDocument();
});

it("switches the evidence panel to unresolved questions", async () => {
  const user = userEvent.setup();
  render(<ResearchWorkbench />);
  await user.click(screen.getByRole("tab", { name: /待解决问题/ }));
  expect(screen.getByText(/先进制程设备验证周期/)).toBeInTheDocument();
});

it("appends a researcher message from the composer", async () => {
  const user = userEvent.setup();
  render(<ResearchWorkbench />);
  await user.type(screen.getByLabelText("向研究团队发送消息"), "补充北方华创订单核验");
  await user.click(screen.getByRole("button", { name: "发送" }));
  expect(screen.getByText("补充北方华创订单核验")).toBeInTheDocument();
});
```

- [ ] **Step 2: Verify red**

Run: `cd frontend && npm test -- ResearchWorkbench.test.tsx`

Expected: FAIL because the filter, panel tab and composer behavior are absent.

- [ ] **Step 3: Implement local state**

```tsx
const [feedFilter, setFeedFilter] = useState<"all" | "summary" | "claim" | "evidence" | "decision">("all");
const [evidenceTab, setEvidenceTab] = useState<"evidence" | "questions" | "review">("evidence");
const [draft, setDraft] = useState("");
const [messages, setMessages] = useState<string[]>([]);

function sendMessage(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  const message = draft.trim();
  if (!message) return;
  setMessages((current) => [...current, message]);
  setDraft("");
}
```

Use WAI-ARIA tabs with `aria-selected` and associated tab panels. Filter data from typed feed categories rather than matching text. Add task-card collapse and selected evidence styling only after the preceding tests are green.

- [ ] **Step 4: Verify green**

Run: `cd frontend && npm test -- ResearchWorkbench.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/workbench/ResearchWorkbench.tsx frontend/src/tests/ResearchWorkbench.test.tsx
git commit -m "feat: add workbench filters and composer"
```

### Task 4: Responsive and production verification

**Files:**
- Modify only if inspection identifies a concrete defect: `frontend/src/workbench/ResearchWorkbench.tsx`, `frontend/src/workbench/ResearchWorkbench.css`, or `frontend/src/tests/ResearchWorkbench.test.tsx`.

- [ ] **Step 1: Add and verify responsive CSS**

At widths under 1180px, use a single-column main surface and turn side panels into fixed, togglable drawers. At widths under 760px, reduce nonessential topbar text, make action buttons icon-first with accessible names, and keep the composer above the safe-area inset. Never hide an interaction behind hover.

Run: `cd frontend && npm run typecheck`

Expected: exit 0.

- [ ] **Step 2: Run the full unit suite**

Run: `cd frontend && npm test`

Expected: all Vitest tests pass.

- [ ] **Step 3: Build production assets**

Run: `cd frontend && npm run build`

Expected: TypeScript passes and Vite emits `dist/` without errors.

- [ ] **Step 4: Browser inspection**

Run: `cd frontend && npm run dev -- --host 127.0.0.1`

Capture 1536×1024, 1180×820 and 390×844. Check topbar height, column boundaries, evidence-card overflow, typography rhythm, keyboard focus, drawer controls and composer visibility. Fix identified defects, then repeat the relevant unit test and screenshot.

- [ ] **Step 5: Final verification and commit**

Run: `cd frontend && npm test && npm run build`

Expected: all tests and the production build exit 0.

```bash
git add frontend/src/main.tsx frontend/src/styles frontend/src/workbench frontend/src/tests/ResearchWorkbench.test.tsx
git commit -m "fix: polish FundClaw workbench responsiveness"
```

