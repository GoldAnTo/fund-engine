# Gateway Assessment Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Preserve the existing dirty worktree; no commits or branch integration in this delivery.

**Goal:** Every displayed assessment links to its actual authorized frozen evidence and clearly separates historical AI judgments from new quality cautions.

**Architecture:** Add a narrow read-only assessment-review module behind the existing research content authorization boundary. Extend its DTO and the strict frontend parser; use a focused result-summary component and the existing original-evidence reader. The approved design is `docs/superpowers/specs/2026-09-05-gateway-assessment-review-design.md`.

**Tech Stack:** FastAPI/Pydantic/SQLAlchemy, isolated SQLite and dedicated PostgreSQL; React 18/TypeScript/Vitest/Vite.

## Task 1: backend read-only review

Files: create `backend/app/services/research_gateway_assessment_review.py`, `backend/tests/test_research_gateway_assessment_review.py`; extend `backend/app/schemas/v1/research_gateway_content.py`, `backend/app/services/research_gateway_content.py`, exact-field test in `backend/tests/test_research_gateway_content.py`.

- [x] RED: add real fixture API assertions using `complete_authorized_evidence`, e.g. `assert body['assessment_review']['items'][0]['evidence_link_ids'] == snapshot.evidence_link_ids`; missing feature must fail before implementation.
- [x] Implement the approved DTO exactly; final-round done/completed result tasks select their actual assessment and snapshot. Require the validated scope order, exact authorized evidence union, unique IDs and thesis membership. Return unavailable on bad lineage or summaries truncated; null without an authorized report. No raw operational fields or reads of unrelated assessments.
- [x] Pure metadata checks: `len(set(non_null_periods)) > 1` flags mixed periods, any null flags unknown, all user material flags user-only, absence of official disclosure flags no-primary. Always distinguish source independence and retrieval-direction uncertainty; no independent-validation inference.
- [x] GREEN and negative tests: malformed IDs, old-round/other-run/task swaps, revoked contract, mixed/missing periods, user-only, official/unknown authority, unchanged stored assessment/conclusion and no writes. Run `env -u TEST_DATABASE_URL APP_ENV=test .venv/bin/python -m pytest tests/test_research_gateway_assessment_review.py tests/test_research_gateway_content.py tests/test_research_gateway_artifact_authorization.py -q` in backend.
- [x] Independent SPEC then QUALITY review, fix findings before handoff.

## Task 2: frontend contract and reading flow

Files: extend `frontend/src/gateway/researchContent.ts`, `frontend/src/workbench/GatewayResearchContent.tsx` and its tests; create `frontend/src/workbench/GatewayAssessmentReview.tsx`, `frontend/src/tests/GatewayAssessmentReview.test.tsx`; append scoped styles to `GatewayResearchContent.css`.

- [x] RED: real parser/component tests assert the summary heading, original AI verdict, separate metadata flags, exact evidence-button ID and unchanged report after expansion. Missing review uses legacy fallback; malformed references reject the response.
- [x] Parse nullable/omitted `assessment_review` for deployment compatibility, reject present malformed records. Check states, version, bounds, duplicate identities, exact evidence union and thesis mapping; available review requires an available report.
- [x] Render a readable vertical assessment inventory with native details/summary for rationale and original report. Evidence buttons delegate to existing `openEvidence(entry)` so private detail safeguards remain shared. Never parse report prose to construct citations.
- [x] GREEN: run full frontend `node node_modules/vitest/vitest.mjs run`, `node node_modules/typescript/bin/tsc --noEmit`, `node node_modules/vite/bin/vite.js build` using the configured Node runtime. Test unknown flags, empty/unavailable review, authorization changes, XSS text handling and mobile wrapping.
- [x] Independent SPEC then QUALITY review and affected reruns.

## Task 3: integration and honest handoff

- [x] Root runs backend authorization/security/read suites with `APP_ENV=test` and unset `TEST_DATABASE_URL`. If testing PostgreSQL, explicitly verify `fundclaw_gateway_verify_a6f9b6eee84b`; never use `fundclaw_gateway_live_c4b71b84e5b2` for pytest.
- [x] Verify the live API process/cwd/database and idle state before a graceful API-only reload. Preserve tokens in memory and existing acquisition/research workers. No new research or source requests.
- [x] Real browser checks existing conversation `4001171b-f697-4085-b111-2a49935dee93`: summary → specific assessment inputs → exact quote; original draft stays `f9e324cd-e11b-5d7b-9c1e-51a5a1f3279f`, 7 authorized evidence and original conclusion unchanged. Desktop/mobile, refresh, failure case and no console/network errors.
- [x] Record fresh pass counts, limitations and deployment verification; update local docs. Keep all work in existing isolated checkout without committing unrelated changes.

## Final verification — 2026-09-05

- Frontend: **152 tests passed in 8 files**, including **22 new assessment-reader tests** and 42 retained reader tests. Standalone TypeScript and production Vite build exited 0. Frontend test clients in `GatewayRoutes.test.tsx` and `GatewayExecutionProgress.test.tsx` gained the nullable field to preserve the full typed client contract.
- Backend: **189 tests passed**, including **38 assessment-review tests**, content/trace/surface/authorization/API/security/runtime regressions; two pre-existing Starlette/Pydantic warnings remain. All these Gateway `cmd_session` cases use isolated SQLite.
- Database reporting correction: a repeat with an explicitly verified dedicated PostgreSQL URL passed 149 tests before the final SQL-limit regression, but `cmd_session` is hard-coded to SQLite. This is not evidence of 149 PostgreSQL tests and is not added to the above count. Earlier reading-plan/local-doc claims were corrected accordingly.
- Actual PostgreSQL verification used `default_transaction_read_only=on` and asserted `SHOW transaction_read_only`. The new service read the live existing case twice, before and after API reload, with zero INSERT/UPDATE/DELETE statements. It returned 3 final assessments, input counts **5, 1, 1**, and the exact union of 7 authorized evidence IDs.
- Independent SPEC and subsequent QUALITY review passed. Fixed witnessed regressions for empty snapshot inputs, contextual item/global quality flags, retained cross-task filter/focus, and the new result-task query's bounded SQL. Final reviewers reported no remaining findings within scope. Result-task query limit is factor count + 1, at most 201; this does not claim to optimize all existing upstream authorization queries.
- Ruff passed on the five backend files and `git diff --check` passed. No new dependencies, migrations, commits, pushes, model/provider calls or research runs were introduced. Existing dirty work is preserved.

### Running product and browser acceptance

Only the idle API was gracefully reloaded (old PID 37368, new PID 44815, port 8018); identity was retained in memory. Both active-run and active-source-job counts were zero before reload. Acquisition/research workers were not restarted.

Real Chromium used the actual same-origin local service without mocked responses: results summary → each of three assessment input lists → an exact original quote from each → original report expansion → mobile layout → full refresh → existing failed Google case. Desktop 1440×1100 and mobile 390×844 passed with no page/API errors, horizontal overflow, password inputs or browser Authorization headers. Root inspected the resulting screenshots in `/tmp/fundclaw-assessment-check.IWFxeU` (temporary check artifacts).

The original case remains conversation `4001171b-f697-4085-b111-2a49935dee93`, draft `f9e324cd-e11b-5d7b-9c1e-51a5a1f3279f`. Serialized original result SHA-256 before and after deployment is **63f18ec133286482a9b6fa5ddb83aa78ddf55357547be7dec54f46dcc33a0fe6**. The 7 evidence records are unchanged. Actual cautions include mixed periods, no official original disclosure, source independence unverified and retrieval directions unverified; the latter two assessments additionally show user-material-only warnings.

The user's already-open in-app tabs could not be refreshed because macOS remained locked; no success is claimed for that specific old-tab refresh. Fresh navigation and reload in the separate real browser are verified. The user can refresh the original page after unlocking without rerunning research.

### Intentional limits

This feature exposes exact assessment-level inputs, not fabricated sentence-level citations. It performs deterministic source-metadata checks, not a new financial verification or semantic contradiction adjudication. Original AI text can still contain flawed statements; it is preserved as historical, unreviewed content. Missing normalized target period prevents a precise automatic out-of-scope-year verdict. Professional roles, speed, conversational evidence Q&A and executable controls remain outside this delivery.
