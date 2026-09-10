# Professional Team UI Implementation Plan

**Goal:** Connect the real professional-team API to the private FundClaw workbench, including role tasks, versions, citations, model usage, directed messages, lifecycle commands and human review.

**Architecture:** `team.ts` strictly validates the scoped API DTO. `HttpGatewayClient` shares existing authenticated, no-store, bounded JSON reads and idempotent posts. `useGatewayTeam` owns a non-overlapping three-second read loop and synchronous authorization-key redaction. `ProfessionalRoleFrame` renders real role tasks and forms; the existing reader receives controlled citation requests and retains private original-detail authorization/focus behavior. Existing scope-changing messages stay on the native conversation endpoint.

**Tech stack:** React/TypeScript, native form controls, existing CSS tokens, Testing Library/Vitest, Node 24.15.0; no new dependencies.

## 1. Contract and transport

- [x] Add fixture-backed tests for scope mismatch, denied output exposure, malformed references, negative usage, receipt scope and exact idempotent POST bodies.
- [x] Implement `GatewayTeamClient` as optional capabilities on the existing client interface so old test adapters explicitly show team-unavailable instead of inventing work.
- [x] Add `getTeam`, `sendTeamMessage`, `commandTeam`, `reviewTeam` to `HttpGatewayClient`; all write inputs preserve `expected_revision` and `Idempotency-Key`.
- [x] Add SSE handshake/idle deadlines with heartbeat-driven resetting and last-confirmed-cursor reconnect. Tests must distinguish local observation from backend cancellation.

## 2. Scoped team controller

- [x] Write polling tests proving non-overlap, hidden-document suspension, abort, late-result rejection, source-key synchronous clearing and terminal auth handling.
- [x] Implement scoped three-second polling with visibility cleanup, immediate refresh after confirmed writes and no optimistic output changes.
- [x] Keep unknown submissions' original text, recipient, expected revision, payload and key; classify 400/422 as rejected, 409 as conflict, 401/403 as authorization loss and 404 as expired scope.

## 3. Professional tasks and output reading

- [x] Replace planning content with server-backed role tasks, explicit dependency statuses, original instructions, selected team revision and per-role latest task at or before that revision.
- [x] Render output summaries, findings, gaps, limitations and QC checks from the API. Show `withheld` without retained content. Evidence buttons request the existing authorized original reader; do not use citation text as a source locator.
- [x] Render actual model attempts and token totals, with unknown values labelled unknown. Native collection state remains separately labelled from professional-team pause state.
- [x] Add real start/pause/resume/cancel/retry controls. Human review requires comments and the four current available role-output IDs; past versions stay read-only.

## 4. Composer and route integration

- [x] Existing research composer sends to selected team role, using frozen revision. Explicit `调整范围：` messages continue to create a native successor run.
- [x] New research view explains the real four-role workflow without fake tasks. Input forms show 20,000-character limits, preserve overlong text and distinguish server validation failure from unknown delivery.
- [x] Add run selection and key private components by run so historical team/reader results cannot appear under another run.
- [x] Run relevant contract/controller/UI regressions and full frontend tests/typecheck. Root task owns browser interaction, backend and final production build verification.


## Completion note — 2026-09-08

Frontend implementation is wired to the existing professional-team API contracts. The workbench now reads scoped team state, shows role tasks and immutable revisions, opens citations through the authorized evidence reader by exact evidence-link ID, sends ordinary follow-up instructions through the team endpoint with `recipient`, `expected_revision` and idempotency, preserves explicit `调整范围：` messages for the native Gateway endpoint, and handles team lifecycle commands and four-output human review without optimistic task success.

`team_progress` SSE frames are parsed as notification-only transport events: they must not include an SSE `id`, do not advance the safe replay cursor, update per-run progress only when `revision` or `event_sequence` advances, and trigger a guarded selected-team refresh while the three-second non-overlapping poll remains as fallback.

Follow-up browser hardening accepted the live MiniMax-backed team DTO shape saved in `frontend/src/tests/fixtures/fundclaw-team-http-final.json`: attempts may include the backend-owned `repaired` boolean while still rejecting arbitrary extra keys. Same-role dependency links now select the exact parent task version, saved human reviews render by bound revision, stable backend reason codes, including `waiting_for_native_completion`, are translated into Chinese recovery guidance with raw codes kept in technical details, the cancel control states that active native Gateway work is cancelled too, and the team frame is positioned before the native reading/evidence area.

Cross-refresh idempotency now persists only minimal pending-submit metadata in sessionStorage: operation, idempotency key, principal/scope/body digests, original `expected_revision` when applicable and created time. It does not persist original prompts, comments, credentials or evidence text. Unknown delivery and aborts retain the pending key, confirmed success and known server rejections clear it, remounted start/team submissions reuse the original key only when the user re-enters the same scoped content, and storage failures display an explicit degradation notice without auto-replaying requests.

Verification run with Node 24.15.0:

- `npm test -- --run src/tests/GatewayRoutes.test.tsx src/tests/ProfessionalRoleFrame.test.tsx src/tests/useTeamMutation.test.tsx src/tests/pendingIdempotency.test.ts src/tests/GatewayResearchContent.test.tsx` — 98 passed.
- `npm test -- --run` — 246 passed across 14 files.
- `npm run typecheck` — passed.
- `npm run build` — passed; Vite produced `dist/index.html`, CSS and JS bundles.
