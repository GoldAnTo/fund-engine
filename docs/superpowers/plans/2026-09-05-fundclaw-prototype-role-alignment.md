# FundClaw Prototype Role Alignment Implementation Plan

**Goal:** Correct the previously delivered HTML preview to match the user's approved screenshot and 2026-09-04 workbench design, making four professional roles the primary workspace.

**Architecture:** Keep this correction in the existing isolated prototype directory. Create a versioned self-contained HTML preview, preserving v1. Use local role/task/evidence fixtures, with explicit simulation labels and no API calls. Do not modify the root checkout's newer React implementation or the backend's execution-role constraints.

**Tech Stack:** Semantic HTML, CSS, browser JavaScript; browser interaction checks for this throwaway visual prototype.

## Tasks

- [x] Create `docs/prototypes/fundclaw-team-workspace-v2.html`: reference-style global header, thread/decision rail, central professional-role work cards, evidence/questions/review inspector, fixed composer.
- [x] Give industry, finance, strategy and AI quality reviewer separate ownership, status, task, evidence and output fields. Provide expandable action records and visible dependency/blocker explanations.
- [x] Wire role attachment selection to its evidence, right-panel tabs, role filters, a recipient selector and local-only message receipts. Label all financial content as sample material; never claim a real model run, file export, source verification or human approval.
- [x] Publish a new versioned screen through the already accepted browser companion. Check 1536 × 1024 and 390 × 844 layouts, four-role visibility, role expansion, evidence navigation, panel controls, composer recipient and focus.
- [x] Update prototype README with the corrected design baseline and backend-contract gap. Keep the original preview as a historical artifact, clearly superseded.

## Acceptance boundary

The professional role dimension is not a one-to-one rename of P0 scope/evidence/analysis/compilation execution roles. Real integration needs explicit role task ownership, evidence/output bindings, and role-specific state. This prototype does not implement or claim that backend contract.
