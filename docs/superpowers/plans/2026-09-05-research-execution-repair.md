# Research execution recovery and visibility implementation plan

> For agentic workers: use subagent-driven-development for bounded native/projection fixes; keep frontend changes separate. Implement and verify each regression test before its production fix.

**Goal:** Repair the user's stalled Google-news research and show the real material/source tasks, processing state, safe counters and blocking cause live.

**Architecture:** Retain the existing isolated Gateway and automatic-research pipeline. Fix the portable locator replay query and worker exception lifecycle. Extend each authorized run snapshot with safe execution metadata and send an authenticated `execution_progress` SSE frame when it changes. Render task state separately from professional AI roles and from stream connectivity.

**Tech stack:** Python, SQLAlchemy/PostgreSQL, FastAPI/SSE, React/TypeScript, pytest/Vitest/Playwright.

## Confirmed reproduction

Conversation `fea81035-5259-474f-a0b5-a9950e6f5e76`, native run `e67b6026-b854-4379-8367-9e0c4dc64668`, is in round **1** (not round 0). Five `intake_material` acquisition jobs exist: one claimed/running, four queued. Worker PID 96679 exited with PostgreSQL SQLSTATE 42883 at `AcquisitionRunner._prepare_intake_material`: JSON equality has no PostgreSQL operator. Its claim remains leased until 05:30 UTC. No provider call or extraction attempt occurred in this job.

HTTP snapshot reports only generic `source_unavailable` and sequence 14. A read-only Node assertion against that endpoint fails because it cannot find actionable execution progress or a specific cause. The submitted financial news remains an unverified user claim, not a verified official disclosure.

## Task 1: portable intake replay and durable worker failure

Files: `backend/app/services/acquisition_runner.py`, `backend/app/scripts/run_acquisition_worker.py`, `backend/tests/test_automatic_research_pipeline.py`, `backend/tests/test_acquisition_worker_recovery.py`.

- [x] Reproduce the actual `SourceSpan.locator_v1 == dict` query against the isolated verification PostgreSQL database, asserting material processing/replay does not throw and does not duplicate spans.
- [x] Replace unsupported JSON comparison with portable, equivalently validated locator matching; do not weaken locator or frozen-material identity checks.
- [x] Add a failing worker regression that raises during `run_claim` after a committed claim. Assert a safe durable retry/failure event, finite retry policy and loop survival without raw exception details.
- [x] Run SQLite and PostgreSQL intake/recovery tests; preserve stale lease fencing and existing non-Gateway behavior.

## Task 2: authorized live execution projection

Files: `backend/app/services/research_gateway_execution.py`, `backend/app/services/research_gateway_projection.py`, `backend/app/services/research_gateway_stream.py`, `backend/app/schemas/v1/research_gateway.py`, focused Gateway tests. If material-first binding rejects the valid mixed bound/unbound task matrix, fix that exact validator condition with negative authorization tests.

- [x] Add failing projection tests: five validated material jobs, mixed active/queued states, safe counters, provider attempts, and missing/stale worker heartbeat.
- [x] Add optional `runs[].execution`: projection_state, stage, updated_at, reason_code, next_action, worker_state, worker_last_seen_at, worker_scope=`acquisition_service`, tasks. Tasks contain IDs, controlled task/status/stage/source kind/name, attempt, timestamps, safe counts and actual provider keys only.
- [x] Revalidate exact case/run/tenant, frozen scope/cutoff/policy and task/job bindings before showing job metadata. On invalid lineage show unavailable with no task contents; never expose raw task results, prompts, exception detail or source text.
- [x] Send `execution_progress` with `{run_spec_id, execution}` on the first authenticated poll and subsequent changes. It has no event ID and does not advance the role-event replay cursor. Each payload replaces previous execution data, including withdrawn fields.
- [x] Keep credential/ownership and artifact-withdrawal checks before yields. Add tests for revoked ownership, source policy/lineage mismatch and metadata withdrawal.

## Task 3: readable execution UI and strict transport

Files: `frontend/src/gateway/contracts.ts`, `executionProgress.ts`, `HttpGatewayClient.ts`, `conversationProjection.ts`, `useGatewayConversation.ts`, `frontend/src/workbench/GatewayExecutionPanel.tsx`, `GatewayConversationPanel.tsx`, `GatewayWorkbench.css`, `frontend/src/tests/GatewayExecutionProgress.test.tsx`.

- [x] Write failing snapshot/stream decoding tests with the exact execution contract; reject unknown fields/enums, raw traces, invalid dates and negative counts. Accept older snapshots without execution.
- [x] Add live progress replacement to the existing hook without advancing native/role status or replay sequence from an execution frame. Ignore unknown run IDs and late frames after revocation; revoke clears all private progress.
- [x] Show current task, task counts, real last-progress timestamp and acquisition-service heartbeat. Explicitly distinguish user-provided material from external retrieval, and provider attempts from planned configuration.
- [x] Add task rows for material preparation, extraction, admission, external search/fetch and final state. Display safe blocker/next-action guidance, not fake percentage or unsupported execution controls.
- [x] Keep professional-role cards honest. Use progressive disclosure for long historical stage logs, avoiding hundreds of repeated generic rows above useful current state.
- [x] Run frontend tests, type checking through the production build, real-browser live replacement/reload checks, and hook/stream authorization-revocation regression tests (do not revoke the user's live access for QA).

## Follow-through: prevent source work that cannot pass admission

Further live inspection after process recovery found all external requests had empty frozen `entity_names`. The semantic admission gate correctly requires a subject, so these requests could never produce admissible evidence. The fix does not relax admission or rewrite frozen research:

- [x] Recheck a missing/unsupported model company name once against the literal input; only accept supported text, never infer a ticker/year or replace other facts from the retry. Honor the explicit `研究主体：公司名称；` field.
- [x] Fail a missing/blank subject before planning/provider/model work, including resumed extraction, through the existing fenced lifecycle. Persist `research_subject_missing`, preserve history and counts, and do not retry this permanent scope defect.
- [x] Project that exact safe cause after full binding/ownership checks and show the explicit new-scope instruction in the UI.
- [x] Supply Gildata research-report provider identity from the trusted adapter descriptor while retaining the report publisher.
- [x] Preserve the Gildata report publisher in its historical publication-key slot, independently of trusted transport identity. Eight regressions cover cross-publisher separation, historical-key compatibility, real variant rejection, replay and cross-URL contract protection. Independent review closed the collision finding.

## Live verification record

- Original research and frozen user text retained; no replacement research was automatically created. Confirmed abandoned owners were absent before their exact claims were recovered using fresh fenced transactions.
- After fixing the JSON query, the original five material jobs advanced and external provider calls resumed; the browser received live task/heartbeat replacements without manual refresh.
- Once the missing-subject impossibility was discovered, the old acquisition loop was stopped, its one interrupted claim was recovered, and the new guard ended unfulfillable jobs. The original native run reached `failed / no_usable_evidence`, with 50 terminal tasks (9 partial, 41 failed), zero active leases and zero authorized admitted evidence. This is an honest failure, not a completed report.
- A separate read-only real-model extraction of the exact Google input now returns literal `谷歌`, material intent, no invented ticker and no invented year/date. It did not create a new research record.
- Real-browser checks at 1440, 884 and 390px found no page errors, no main-progress obstruction after mobile layout repair, successful refresh/stream reconnection, and no browser Authorization header. The specific missing-subject cause and new-scope instruction are visible.
- PostgreSQL targeted regression after fixture alignment: **403 passed** across 15 acquisition, Gateway, extraction/intake and Gildata test files. Source fixtures for locator/contract tests now contain explicit literal subjects, while separate regressions cover the empty-subject guard. Google input bytes were not changed.
- Final publication-identity/freezing/worker supplement: **164 passed on PostgreSQL**, overlapping the preceding suite. Two old checkpoint assertions compared aware PostgreSQL timestamps to naive SQLite values; they now compare the exact UTC instant without changing production timestamps. The full retrieved-document file also passed on SQLite (**55 passed**).
- Final frontend suite: **76 passed**, with TypeScript checking and production build successful. Failed/partial/cancelled tasks count toward ended work but are not represented as successful results; the final browser displays `50 / 50` ended tasks and the concrete missing-subject cause.
- Gateway API, acquisition worker and research worker were all reloaded from the same scoped worktree/database after verifying both research runs and all acquisition jobs were terminal. No live source metadata, publication key or admission outcome was rewritten.

## Remaining capability and evidence limits

Current configured providers are Gildata, SSE and SZSE, not an American official-disclosure adapter. Subject extraction recovery alone does not make the supplied Google numbers verified or guarantee a report. Existing legacy candidate rejection reasons also include period/metric/segment mismatch; do not bypass these gates. One pre-existing same-URL/same-content temporal reuse rejection was identified for follow-up; it was not changed under the present recovery/visibility fix. Historical rejected artifacts were not rewritten.

## Task 4: recover the exact research and verify live

- [x] After code/tests pass, restart only the scoped Gateway/acquisition processes with the same isolated live database and server credentials. Never expose credentials or use the default database.
- [x] Recover only the confirmed orphaned acquisition claim using the existing fenced repository lifecycle, after rechecking its owner is absent. Preserve its history and original user request; do not create duplicate research.
- [x] Re-run the original HTTP/real-browser check on the same conversation. Confirm job state/attempt/counters advance, task details update over SSE, and any remaining source insufficiency is displayed honestly.
- [x] Run independent spec then quality/security review; retain the worktree without committing unrelated work. Both reviews passed after their findings were fixed and retested. No commit, merge or root-worktree edit was performed.

## Observed remaining temporal limitation (2026-09-05)

A read-only inspection restricted to native run `e67b6026-b854-4379-8367-9e0c4dc64668` confirmed one automatic-admission rejection with `temporal_retrieved_at_after_acquired_at`: its artifact binding is `content_duplicate`, artifact/document content hashes match, canonical/document source URLs match, and the new artifact's `retrieved_at` is later than the original document's immutable `acquired_at`. `automatic_temporal_failures` currently requires every retrieval to precede that first acquisition, while document reuse intentionally preserves the original timestamp. This is a confirmed incompatibility for a legitimate repeat retrieval, not evidence that the source was available before the research cutoff. It remains deferred in this change: do not rewrite frozen timestamps, bypass temporal gates, or describe this rejected candidate as admitted evidence. The run also lacks a frozen research entity; repairing this temporal issue alone cannot make that run's evidence admissible.
