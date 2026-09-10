# Pending Submission Reliability Fix

Date: 2026-09-09

Scope:
- `frontend/src/gateway/pendingIdempotency.ts`
- `frontend/src/gateway/useTeamMutation.ts`
- `frontend/src/app/GatewayRoutes.tsx` `StartResearchForm` only, plus the now-unused import cleanup
- Regression tests in `frontend/src/tests/pendingIdempotency.test.ts`, `frontend/src/tests/useTeamMutation.test.tsx`, and `frontend/src/tests/GatewayRoutes.test.tsx`

What changed:
- Pending storage now computes principal, scope, and intent digests once and serializes same-key preparation with an in-memory lock, so parallel preparations for the same intent share one idempotency key and original expected revision.
- Storage availability now means writable storage, not just a readable `sessionStorage` object. A harmless probe key is written and removed before claiming durability.
- If storage exists but entry writes fail, the memory entry is still used when `getItem` returns `null`; same-page retries/remounts no longer lose the idempotency key.
- The non-WebCrypto digest path now uses a full SHA-256 implementation instead of a 32-bit FNV-style fallback, so durable entries are never keyed by a weak digest.
- Preparation accepts an `AbortSignal` and checks it before writing memory or session storage.
- `useTeamMutation` now sets the in-flight fence before async hashing, creates the abort controller before preparation, preserves unknown requests for retry, and fences all callbacks/state updates by the current conversation/run/principal token.
- `StartResearchForm` now blocks duplicate submits with a synchronous ref, aborts preparation on unmount/session change, surfaces storage-unavailable notices on mount, and treats start 409 as an unknown/in-progress outcome because the backend can return `gateway_request_in_progress` with only status information available to this UI layer.

Regression coverage added:
- Memory fallback after storage write denial.
- Parallel preparation key coalescing.
- SHA-256 fallback when `crypto.subtle` is unavailable.
- Writable-storage detection and visible warning.
- No team submit after unmount during preparation.
- No stale team callbacks after run-scope change.
- Start-page storage warning before first submit.
- Start 409 preserves the idempotency key for retry.

Verification run with Node `v24.15.0`:
- `PATH=<user-home>/.nvm/versions/node/v24.15.0/bin:$PATH npm --prefix frontend test -- pendingIdempotency useTeamMutation` passed: 2 files, 24 tests.
- `PATH=<user-home>/.nvm/versions/node/v24.15.0/bin:$PATH npm --prefix frontend run typecheck` failed only on the concurrent root UI change: `src/workbench/GatewayConversationPanel.tsx(22,43): Cannot find module './ProfessionalTeamWorkspace' or its corresponding type declarations.`
- `PATH=<user-home>/.nvm/versions/node/v24.15.0/bin:$PATH npm --prefix frontend run build` failed at the same typecheck error before Vite build: missing `./ProfessionalTeamWorkspace`.
- `PATH=<user-home>/.nvm/versions/node/v24.15.0/bin:$PATH npm --prefix frontend test` ran 14 suites: 9 passed, 5 failed. The pending-focused suites passed. Four suites failed to import `GatewayConversationPanel.tsx` because `./ProfessionalTeamWorkspace` is missing. One unrelated existing test failed in `src/tests/gatewayTransport.test.ts`: `HttpGatewayClient > decodes the frozen run scope without accepting raw intake fields` rejected with `GatewaySchemaError: Gateway payload contains unexpected fields`.

Known external blocker:
- `frontend/src/workbench/GatewayConversationPanel.tsx` currently imports `./ProfessionalTeamWorkspace`, but that file is not present yet. This blocks full GatewayRoutes tests, full frontend typecheck, and build until the root three-column UI rebuild lands that component or removes the import.
- `src/tests/gatewayTransport.test.ts` has a separate schema/fixture mismatch outside this pending-submission scope.

Coordination note:
- `GatewayRoutes.tsx` is released for root layout work. My edits are confined to `StartResearchForm` and its import list; no whole-file writes were used.
