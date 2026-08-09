# High-Fidelity Prototype Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the simplified Research OS surface with the approved, high-fidelity event-first research workbench while retaining live API states and auditable monitor data.

**Architecture:** Keep the current route and API contracts. Rebuild the frontend shell and route components around shared paper-surface tokens, dense workbench primitives, and explicit runtime/status states. The approved HTML prototypes are visual and interaction references; real unavailable data must continue to show truthful empty/error states.

**Tech Stack:** React 18, TypeScript, React Router, Vitest, Testing Library, Vite, CSS.

---

## Visual source of truth

- `event-first-research-os-v2.html`: global shell, event desk, Case tabs, Wiki, market and monitor visual language.
- `create-event-research-v1.html`: intake progression and scope confirmation.
- `case-continuous-research-prototype.html`: conclusion / ongoing research right rail.
- `case-wiki-graph-workbench.html`: graph canvas, candidate distinction and inspector.
- `case-market-expression-workbench.html`: factor-to-stock-to-fund expression layout.
- `case-monitor-control-v1.html` and `research-run-transparency-v1.html`: monitor versions, runtime band and reproducible run detail.

### Task 1: Lock the visual contract in frontend tests

**Files:**
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`

- [x] Add a failing event-desk assertion for the approved research-dispatch heading, the highest-impact item, and the operating-system rail.
- [x] Run `npm test -- --run src/tests/ResearchOsPages.test.tsx`; expect the new assertions to fail against the simplified surface.
- [x] Add the smallest component changes that expose those real product landmarks from live event data.
- [x] Re-run the focused test; expect it to pass.

### Task 2: Restore the approved global shell and event desk

**Files:**
- Modify: `frontend/src/app/AppShell.tsx`
- Modify: `frontend/src/features/events/EventDeskPage.tsx`
- Modify: `frontend/src/styles/research-os.css`

- [x] Replace the dark navigation with the warm paper side rail, grouped workbench and research-asset navigation, profile footer, top search affordance, and persistent run band.
- [x] Make the desk lead with the highest-impact research action, then the ordered live Case queue and a research-network status rail; preserve error and empty states.
- [x] Verify keyboard focus and text labels for every navigation/action control.

### Task 3: Restore Case research workbench density

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os.css`

- [x] Rebuild conclusion, review and market pages with the approved case header, tab strip, current-judgment emphasis, factor rows, explicit evidence boundaries, and right-side next-action/monitor panels.
- [x] Rebuild Case Wiki as a graph workbench with visible reviewed versus AI-candidate relationship paths, candidate filtering, and selectable node inspector.
- [x] Render market expression as claim/factor/impact/fund panels using only API-provided records; preserve the disclosed-holdings and non-causality notices.

### Task 4: Restore transparent runs and versioned monitoring

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os.css`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [x] Render the monitor overview as a current-run stage timeline plus versioned configuration rail; configuration edits keep their existing API request and explicit change reason.
- [x] Add the real immediate-replenishment action and an accessible run-detail drawer that replays the selected run's frozen scope, ordered events and stage outputs without substituting current configuration for history.
- [x] Re-run focused tests.

### Task 5: Verify visual and production quality

**Files:**
- Modify only files needed to correct verification findings.

- [x] Run `npm test -- --run` and `npm run build` in `frontend/`.
- [x] Run the Vite app and inspect the event desk, intake and conclusion at desktop width against the approved prototypes; data-dependent Wiki, market and monitor retain their truthful unavailable states when no live API is present.
- [x] Record any intentionally unavailable API data as truthful empty states; do not add display fixtures to production routes.
- [x] Remove obsolete frontend pages, components, styles and E2E coverage so only the event-first workbench remains reachable.
- [x] Commit the frontend restoration and this plan.
