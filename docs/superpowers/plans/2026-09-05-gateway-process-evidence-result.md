# Gateway Process, Evidence, and Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** In the existing conversation, show real execution history, readable currently authorized evidence with frozen original quotations, and an explicit readable research outcome, including existing completed runs.

**Architecture:** Complete the artifact-reader and safe process views in the already validated 2026-09-03 FundClaw Gateway design, reaffirmed by the user's request for process, provenance, and results. Add narrowly scoped read models under private conversation/run routes; retain the native ledger and existing snapshot/SSE as authorities. Do not mount legacy readers, create another ledger, rerun completed cases, invent professional role execution, or change research algorithms in this delivery.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, PostgreSQL/isolated SQLite tests; React 18, TypeScript, Vite, Vitest.

## Acceptance and baseline

- Current real case: conversation `4001171b-f697-4085-b111-2a49935dee93`, run spec `f0681328-8416-416c-bb90-ab153553bdb3`.
- Read-only baseline: snapshot says succeeded and contains seven unique evidence references and one draft reference, but no readable result/evidence. The proposed `/research` endpoint returns HTTP 404.
- Keep existing private owner, tenant, case, run binding, historical cutoff, admission lineage and current source contract checks. No content may be inferred from an ID or native succeeded status alone.
- Read results are system-generated drafts, not independently verified facts, human review, publication, or investment advice. Label search-derived relationships as retrieval directions, not confirmed support/refutation.
- Display the existing frozen result with explicit draft warning and limitations. Do not silently rewrite the historical report to hide known quality problems.
- No external provider/model calls are necessary to read the existing case. All tests use disposable SQLite unless root explicitly selects the dedicated verification PostgreSQL database. Never test against live/default databases.
- Preserve unrelated dirty files. No commits, pushes, migration, or historical record mutation in this delivery.

## Shared wire contract

All routes begin `/api/v1/research-conversations/{conversation_id}/runs/{run_spec_id}`. JSON uses snake_case; the frontend parses into camelCase. Recheck authentication and authorization every read. Respond with `Cache-Control: no-store`; no raw error messages, event payloads, prompts, credentials, lease IDs, filesystem paths, or executable source markup.

### GET `/research`

```typescript
type EvidenceSummary = {
  evidence_link_id: string; task_id: string; thesis_id: string;
  thesis_statement: string; statement: string; document_version_id: string;
  title: string; source_authority: string; source_url: string | null;
  published_at: string | null; observed_period: string | null;
  relationship: string; review_state: 'automatically_admitted';
};
type ResearchContent = {
  conversation_id: string; run_spec_id: string;
  state: 'pending' | 'available' | 'withheld' | 'failed' | 'cancelled';
  reason_code: 'result_pending' | 'result_not_authorized' | 'execution_failed' | 'execution_cancelled' | null;
  draft_id: string | null;
  result: {
    label: '系统生成，未经人工审核'; human_reviewed: false;
    conclusion: string; key_findings: string[]; counter_evidence: string[];
    limitations: string[];
    sources: {title: string | null; url: string | null; role: string; review_state: 'automatically_admitted'}[];
  } | null;
  evidence: EvidenceSummary[]; total_evidence: number; truncated: boolean;
  warnings: string[];
};
```

Return at most 200 evidence summaries, stable ordering, explicit total and truncation. Detail reads remain possible by an authorized ID. Results are present only for the exact authorized native draft and run. Pending, failed, cancelled, missing and revoked result states have explanatory UI, never an invented successful conclusion. Aggregate draft citations are not automatically attached to individual sentences; evidence groups are explicitly tied to their ledger thesis and retrieval task.

### GET `/evidence/{evidence_link_id}`

```typescript
type EvidenceDetail = EvidenceSummary & {
  conversation_id: string; run_spec_id: string;
  quote: string; source_span_id: string;
  locator: {page: number | null; paragraph: number | null};
  content_sha256: string; acquired_at: string;
};
```

Quote must be the admitted candidate's exact quote from its frozen span, never model-regenerated. Extract page/paragraph only from recognized parser locator fields, not arbitrary nested metadata; absent fields stay null. Original links must be public HTTP(S) URLs without userinfo or credentials; omit unsafe URLs. User material retains its actual user_supplied authority and is never relabeled official.

### GET `/tasks/{task_id}/trace`

```typescript
type TaskTrace = {
  conversation_id: string; run_spec_id: string; task_id: string;
  events: {sequence: number; stage: string; status: string; label: string; occurred_at: string}[];
  exceptions: {reason_code: string; message: string; next_action: string; count: number}[];
  truncated: boolean;
};
```

Use the exact task's validated source-job binding. Return at most the latest 100 persisted lifecycle events, displayed chronologically, and bounded grouped exception categories. Stage/status/labels/reasons are server-owned allowlists; unknown classifications map to generic safe labels. Do not show unadmitted/unlicensed source titles, raw provider traces, tool arguments or reasoning. Distinguish progress from heartbeat. Already-authorized evidence is associated back to tasks through `task_id`.

## Task 1: Authorized evidence and result readers

Files: create `backend/app/schemas/v1/research_gateway_content.py`, `backend/app/services/research_gateway_content.py`, `backend/tests/test_research_gateway_content.py`; extend `backend/app/api/v1/research_gateway.py`.

- [x] Write failing service/API tests using `complete_authorized_evidence()` in `test_research_gateway_artifact_authorization.py`. Assert readable exact quote and source identity, result draft and findings, and no raw operational content.
- [x] Run `env -u TEST_DATABASE_URL APP_ENV=test .venv/bin/python -m pytest tests/test_research_gateway_content.py -q` from backend; record failure before implementation.
- [x] Implement the shared wire types and a content service whose reads call `ResearchGateway.read_run_spec`, compare path conversation, and derive content only from `native_artifacts` current authorization. Require an authorized draft before `AutomaticResearchQueries.get`, and verify its run ID. Limit material to exact authorized rows.
- [x] Add tests for same-tenant other owner, other tenant, mismatched path/run/evidence, future cutoff, expired/revoked source permissions, tampered lineage, historical run vs current run, unsafe URL, pending/failed/withheld result, and response no-store.
- [x] Re-run the focused tests plus `test_research_gateway_artifact_authorization.py`, `test_research_gateway_api.py`, and `test_gateway_runtime_isolation.py`.

## Task 2: Safe task trace reader

Files: create `backend/app/schemas/v1/research_gateway_trace.py`, `backend/app/services/research_gateway_trace.py`, `backend/app/api/v1/research_gateway_trace.py`, `backend/tests/test_research_gateway_trace.py`; root integrates its router into `backend/app/gateway_main.py`.

- [x] First test a real bound task whose native events and exceptions contain secret-looking arbitrary payloads; expect only approved labels, timestamps and grouped safe reasons. Test private owner, run/task mismatch and malformed/changed source binding denial.
- [x] Run `env -u TEST_DATABASE_URL APP_ENV=test .venv/bin/python -m pytest tests/test_research_gateway_trace.py -q` before adding implementation.
- [x] Implement a read-only bounded trace query using `read_run_spec`, path comparison and `validated_gateway_source_bindings`. Map persisted stages/statuses/exceptions through fixed labels. Never read raw bytes or expose raw event text/payload.
- [x] Test chronological last-100 history, generic handling for unknown reason/status, and aggregate count correctness without silently treating duplicate events as unique evidence.
- [x] Run the focused tests and verify router authentication/runtime isolation; root mounts only the new private router, no legacy API.

## Task 3: Working research reading UI

Files: create `frontend/src/gateway/researchContent.ts`, `frontend/src/workbench/GatewayResearchContent.tsx`, `frontend/src/workbench/GatewayResearchContent.css`, `frontend/src/tests/GatewayResearchContent.test.tsx`; extend contracts, `HttpGatewayClient.ts`, conversation/execution panels, and existing gateway test clients.

- [x] Write failing component/transport tests for successful result text, evidence selection opening exact original quote, authorized source links and locator, pending/failed/withheld states, retry, no private stale data after run change/revocation, and task trace opening.
- [x] Run `node node_modules/vitest/vitest.mjs run src/tests/GatewayResearchContent.test.tsx` from frontend before implementation.
- [x] Parse all three DTOs strictly and reject mismatched conversation/run/evidence/task IDs. Use the current same-origin client; never put tokens in browser input/storage. Add no new dependencies.
- [x] Add clearly labeled result/evidence reading surfaces within the current quiet research layout. Completed runs show the outcome by default; active runs retain the execution view and show growing authorized evidence. Display the report body as safe text, not executable HTML.
- [x] Add per-task history controls with grouped reasons/next actions and links to available evidence. Use lazy detail reads, abort obsolete requests, bound refresh driven by material SSE changes, and clear sensitive details on identity loss or route/run changes. Preserve scroll/focus while updating.
- [x] Show source authority, publication/observation dates, exact quote and parser page/paragraph when present; explain unavailable locators. Label direction as search purpose rather than a confirmed evidence verdict.
- [x] Test real component interaction at desktop/mobile widths; no blank terminal result, fake completion, disabled-looking dead links, fabricated citations, or repeated full reload per heartbeat.
- [x] Run `node node_modules/vitest/vitest.mjs run` and `node node_modules/typescript/bin/tsc --noEmit`, then `node node_modules/vite/bin/vite.js build`.

## Task 4: Integration and honest handoff

- [x] Root reviews exact diffs and runs all newly added backend tests together with existing Gateway authorization/security tests against SQLite, then the dedicated verification PostgreSQL database if relevant.
- [x] Independent spec review must pass before quality/security review. Resolve important findings and rerun affected tests.
- [x] Reconfirm live work is idle before reloading the API; preserve all conversations, evidence and historical drafts. Do not restart acquisition/research workers unnecessarily.
- [x] Open the existing case, read result, select evidence, verify the exact quote/source identity/locator, and expand a task trace. Verify desktop/mobile layout, focus, console/network errors, and no browser Authorization header.
- [x] Record fresh pass counts, current API/browser checks and remaining limits here. Reading an existing case is not a fresh end-to-end research-quality or execution-speed benchmark.

## Verification record — 2026-09-05

- Backend SQLite: **151 passed**, two existing dependency warnings. Includes content, trace, reading surface, artifact authorization, API, security and runtime isolation. `TEST_DATABASE_URL` was explicitly unset and `APP_ENV=test` used.
- Configured-database repeat: **112 passed**, two existing dependency warnings, after asserting `fundclaw_gateway_verify_a6f9b6eee84b` before pytest. Correction from subsequent fixture inspection: `cmd_session` always creates SQLite despite `TEST_DATABASE_URL`, so this run is not proof of 112 PostgreSQL tests. It overlaps the SQLite suite and must not be added as unique tests. Actual PostgreSQL read behavior is verified separately against a read-only connection and the running API.
- Frontend: **130 passed in 7 files**, including 42 reader tests and 34 exact-path local proxy tests. Standalone TypeScript and Vite production build both exited 0.
- Ruff passed on all eight new backend content/trace/surface files; `git diff --check` passed. This is not a claim that unrelated existing worktree changes pass a whole-repository audit.
- Backend content, trace, proxy and frontend passed independent spec/quality review. Important findings were fixed and retested: source task binding spoofing, bounded SQL summaries, fail-closed partial projections, official source labels, revocation of evidence or task history invalidating the whole reader, late-response isolation and slow-detail preservation. Final quality review: critical 0 / important 0 / minor 0.
- Synchronous client-method failures now enter the reader's existing error/retry path. TDD reproduced a missing `getResearch` method leaving the reader loading, then verified recovery through an explicit retry. No HMR special case, automatic retry or timeout policy was added; this does not establish the old in-app tab's historical cause.

### Live read-only acceptance

The existing conversation remains `4001171b-f697-4085-b111-2a49935dee93`, run spec `f0681328-8416-416c-bb90-ab153553bdb3`, sequence **4161**, status **succeeded**. The original draft remains `f9e324cd-e11b-5d7b-9c1e-51a5a1f3279f`: **1,185 characters** of conclusion and **7** authorized evidence records, `human_reviewed: false`. Both the same-origin snapshot and content reads return 200; content is `no-store`. All seven exact quotations and source identities were checked against the persisted read model under a read-only database connection.

Real Chromium against the running local services, without mocked responses or new research, verified result → evidence → exact original quotation (both user material and licensed research) → task history → task evidence filter, refresh, and navigation to the existing failed Google case without stale result leakage. Desktop 1440 × 1100 and mobile 390 × 844 had no horizontal overflow, page errors, API error responses, browser Authorization headers or token inputs. Screenshots and the repeatable local check are in `/tmp/fundclaw-reading-check.g6eB8M` (temporary verification artifacts, not durable product assets).

Only the idle Gateway API was gracefully reloaded after validating its process, worktree and live database; acquisition/research workers, conversations, evidence and report history were preserved. The previously open in-app tab was observed with the new UI but a pending read; subsequent UI refresh verification was unavailable because macOS became locked. Fresh browser navigation and full reload are verified separately, not represented as a successful refresh of that old tab.

### Remaining limits

- This delivery makes the actual saved process, provenance and outcome readable; it does not add professional-role orchestration, conversational follow-up over evidence or executable pause/cancel/retry controls.
- Existing result-quality problems were not rewritten: the historical 2024 question includes 2025 materials, and retrieval directions are not reliable verdicts of support or contradiction. The UI exposes source authority and dates, retains the original draft, and explicitly warns that automatic admission is not independent financial verification.
- Evidence is mapped to its native task and thesis, not fabricated as a sentence-by-sentence citation map. Missing source URLs and parser locations remain explicitly unavailable.
- Trace history is bounded to the latest 100 events and evidence summaries to 200, with explicit truncation; repeated exception counts are not independent evidence counts.
- This was not a fresh end-to-end research run or speed benchmark. No provider/model calls, migrations, commits or pushes were performed.
