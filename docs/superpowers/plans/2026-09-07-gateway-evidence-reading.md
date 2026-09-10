# Gateway Evidence Reading Implementation Plan

> **For agentic workers:** Execute the assigned evidence UI scope inline, with root-agent integration review. Steps use checkbox syntax for tracking.

**Goal:** Display real AI assessments and their authorized frozen inputs as an accessible, interactive evidence path, with concise reading boundaries and intact original-report access.

**Architecture:** `GatewayAssessmentReview` retains the server's verdict and exact `evidenceLinkIds`; `EvidenceMap` groups those inputs by retrieval direction and sends the selected existing DTO to the reader. `GatewayResearchContent` owns private requests, revocation, late-response protection, original-text focus and return focus. No backend schema, inferred relationship, graph dependency or mock product data is introduced.

**Tech Stack:** React 18, TypeScript, semantic HTML/SVG, existing workbench CSS tokens, Testing Library, Vitest; Node 24.15.0.

## Module 1: Evidence paths and interaction

Files: create `frontend/src/workbench/EvidenceMap.tsx`, `EvidenceMap.css`, `frontend/src/tests/EvidenceMap.test.tsx`.

- [x] Write component coverage for grouping support/contradict/alternative/unknown retrieval directions, keyboard activation, exact selected DTO, selected evidence state, empty input and changed evidence scope.
- [x] Run the new integration tests in `GatewayAssessmentReview.test.tsx` before implementation: three fail because the graph is absent and the shared explanation is duplicated. Direct map tests subsequently caught two accessible-group naming errors before the final passing run.
- [x] Implement the bounded interface:

```ts
type Props = {
  evidence: GatewayEvidenceSummary[];
  selectedEvidenceId?: string;
  onOpen: (entry: GatewayEvidenceSummary) => void;
};
```

Use real button nodes labelled `查看引用原文：${entry.title}`; four named direction filters preserve original input order inside each group. A structural HTML list provides the graph's accessible equivalent. SVG connecting paths are decorative and hidden from assistive technology. Unknown directions remain `关系待核验`.

- [x] Re-run component tests and add responsive CSS: column paths on large widths, stacked paths at narrow widths, 44px controls, wrapping long Chinese and identifiers.

## Module 2: Assessment reading hierarchy

Files: modify `frontend/src/workbench/GatewayAssessmentReview.tsx` and `GatewayResearchContent.css`; extend `frontend/src/tests/GatewayAssessmentReview.test.tsx`.

- [x] Add a failing assertion that a shared full quality explanation appears once and an item-only quality explanation remains accessible; preserve visible compact quality labels.
- [x] Put the first exact AI-recorded gap before evidence paths; retain remaining gaps, rationale and original report in native disclosures.
- [x] Replace the repeated plain input list with `EvidenceMap`, passing only the item's exact authorized inputs and retaining the existing `onOpen(entry, { label, ids })` contract.
- [x] Run assessment and map tests together; confirm the original report body is unchanged and opens through its existing disclosure.

## Module 3: Reader authorization and reading limits

Files: modify `frontend/src/workbench/GatewayResearchContent.tsx/.css`; extend assessment integration tests and run existing `frontend/src/tests/GatewayResearchContent.test.tsx` unchanged.

- [x] Add a failing integration test: select a map node by keyboard, open the exact HTTP evidence endpoint, close and return focus to that node; change authorization and assert map/detail removal before the next read resolves.
- [x] Pass `selectedEvidenceId` into the assessment view. Keep request hooks and scope keys intact. Place shared reading rules and raw server warnings in one disclosure; maintain the persistent AI-review label and the source-specific locator limitation.
- [x] Run `npm test -- src/tests/EvidenceMap.test.tsx src/tests/GatewayAssessmentReview.test.tsx src/tests/GatewayResearchContent.test.tsx` and `npm run typecheck` using Node 24.15.0. Final result: 78 tests pass in three files; typecheck exits 0. The npm warning about the user's legacy `python` configuration remains unrelated to these files.
- [x] Send root-agent review notes with tests, API boundaries and browser verification checklist. Root owns browser/desktop viewport and 320px checks plus final integration/build.

## Integration handoff

The production DTO and original reading hooks remain unchanged. There are nine added tests: five direct path-component tests and four integrated assessment-reader tests. Browser visual verification belongs to the root task; this subtask did not open or control a browser. At 1440×900 confirm the first assessment, its first gap and an original-input action fit the reading surface; at 320px and 200% zoom check stacked paths, long titles, direction controls, source opening and focus return. Existing lineage-unavailable and truncated-report fallbacks keep the original report readable and do not manufacture graph edges.
