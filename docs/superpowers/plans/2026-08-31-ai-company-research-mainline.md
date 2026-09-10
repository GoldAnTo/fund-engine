# AI Company Research Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Phase 1 one-click company-research loop: an automated AI draft, stable product-level run status with visible process, deterministic critical-input confirmation, immutable save/replay/export, and honest answerable/not-answerable golden cases.

**Architecture:** Keep the existing Company Research source, model, workbench, and publication kernel. Add a `CompanyResearchRun` projection service above it, introduce an append-only `critical_inputs` artifact between machine draft and publication, and switch only the new versioned strategy to automatic draft generation. Existing `company-research-default.v1` projects and evidence-review APIs remain readable and operable as legacy advanced flows.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, pytest, React 18, TypeScript 5, Vitest, Testing Library, Playwright/Chrome, SQLite and PostgreSQL verification.

---

## Scope and invariants

This plan implements section 10.1 of `docs/superpowers/specs/2026-08-31-ai-company-research-mainline-design.md`. Assumption tuning and incremental-update workflows remain Phase 2; event/fund/comparison expansion remains Phase 3.

The implementation must preserve these invariants:

- Source facts remain immutable and retain locator, period, unit, availability time, and raw hash.
- Automatic draft eligibility is not represented as human confirmation.
- A user-edited source value becomes a `user_assumption`; the source fact remains unchanged.
- Criticality comes from deterministic model dependency rules, never from LLM self-report.
- Missing critical baselines produce `needs_input` or `not_answerable`, never invented values.
- Publication freezes the exact `critical_inputs` head with the existing artifacts, market snapshots, memo, and manifest.
- Old revisions replay only frozen data and never current heads.
- No identity/RBAC/trading/ranking/event/fund redesign enters this branch.
- Do not edit or regenerate `frontend/openapi.json`; the user's main worktree has an uncommitted version of that file.

## Task 1: Establish the product-level run projection

**Files:**

- Create: `backend/app/underwriting/domain/company_research_run.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Test: `backend/tests/underwriting/test_company_research_run.py`

- [ ] Write failing tests for the exact public status vocabulary and all legacy preparation mappings.

```python
@pytest.mark.parametrize(
    ("internal_status", "progress", "product_status"),
    [
        ("queued", 0, ResearchRunStatus.QUEUED),
        ("preparing_sources", 10, ResearchRunStatus.COLLECTING_SOURCES),
        ("building_model", 35, ResearchRunStatus.ANALYZING_COMPANY),
        ("building_model", 60, ResearchRunStatus.BUILDING_FORECAST),
        ("building_model", 80, ResearchRunStatus.GENERATING_REPORT),
        ("awaiting_judgment_review", 85, ResearchRunStatus.NEEDS_INPUT),
        ("ready_to_freeze", 95, ResearchRunStatus.COMPLETED),
        ("completed", 100, ResearchRunStatus.COMPLETED),
        ("recoverable_failure", 30, ResearchRunStatus.FAILED),
    ],
)
def test_projects_internal_lifecycle_to_closed_product_status(
    internal_status: str,
    progress: int,
    product_status: ResearchRunStatus,
) -> None:
    assert project_research_run_status(
        internal_status=internal_status,
        progress=progress,
        error_code=None,
    ) is product_status
```

Add separate blocked-state cases: governed missing-baseline/gap codes map to `needs_input`; permission, integrity, and deterministic provider failures map to `failed`.

- [ ] Run the focused test and verify it fails because the module does not exist.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_run.py -q`

Expected: `ModuleNotFoundError: app.underwriting.domain.company_research_run`.

- [ ] Implement closed enums and immutable projections.

```python
class ResearchRunStatus(StrEnum):
    QUEUED = "queued"
    COLLECTING_SOURCES = "collecting_sources"
    ANALYZING_COMPANY = "analyzing_company"
    BUILDING_FORECAST = "building_forecast"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"

class ResearchRunStageStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
```

Implement `project_research_run(preparation, event_types, has_machine_memo, confirmation_complete)` with the five exact stage keys `identity`, `sources`, `analysis`, `forecast`, `report`. Reject unknown internal states and inconsistent progress instead of guessing.

- [ ] Add tests proving that raw exception text, provider request bodies, and filesystem paths cannot appear in projected process messages.

- [ ] Run the focused test and verify all cases pass.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_run.py -q`

Expected: all tests pass.

- [ ] Commit the domain projection.

```bash
git add backend/app/underwriting/domain/company_research_run.py backend/app/underwriting/domain/__init__.py backend/tests/underwriting/test_company_research_run.py
git commit -m "feat: define company research run projection"
```

## Task 2: Define deterministic critical inputs as an append-only artifact

**Files:**

- Create: `backend/app/underwriting/domain/company_research_critical_inputs.py`
- Modify: `backend/app/underwriting/domain/company_research_artifact_codec.py`
- Modify: `backend/app/underwriting/domain/company_research_provenance.py`
- Modify: `backend/app/underwriting/persistence/company_research_models.py`
- Modify: `backend/app/underwriting/persistence/company_research_repository.py`
- Modify: `backend/app/underwriting/persistence/__init__.py`
- Test: `backend/tests/underwriting/test_company_research_critical_inputs.py`
- Test: `backend/tests/underwriting/test_company_research_artifact_codec.py`

- [ ] Write failing domain tests for the seven input kinds and their conditional fields.

```python
class CriticalInputKind(StrEnum):
    SOURCE_FACT = "source_fact"
    MANAGEMENT_GUIDANCE = "management_guidance"
    CONSENSUS = "consensus"
    AI_ASSUMPTION = "ai_assumption"
    USER_ASSUMPTION = "user_assumption"
    DERIVED_CALCULATION = "derived_calculation"
    UNKNOWN = "unknown"

class CriticalInputDecision(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REPLACED_WITH_USER_ASSUMPTION = "replaced_with_user_assumption"
    MARKED_UNKNOWN = "marked_unknown"
    ACCEPTED_GAP = "accepted_gap"
```

Test that source facts require an exact source reference, assumptions cannot carry source provenance, calculations require equation and parent keys, and unknowns cannot carry a numeric value.

- [ ] Write failing selection tests using a small artifact graph. Assert the selector includes only inputs reaching revenue, operating profit, FCFF, capital structure, discount/terminal values, security value ranges, assessment direction, or strongest counterevidence.

- [ ] Run the focused tests and verify they fail before implementation.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_critical_inputs.py tests/underwriting/test_company_research_artifact_codec.py -q`

Expected: import/unsupported-kind failures.

- [ ] Implement `CriticalInput`, `CriticalInputImpact`, and `CriticalInputSet` with canonical ordering and a stable `input_fingerprint` computed from kind, value, period, unit, source/equation parents, and dependency paths.

- [ ] Implement `select_critical_inputs(evidence, financial_bridge, scenario_set, valuation_set, judgment_context, research_gaps)` by traversing exact artifact references. Use explicit field rules; do not parse prose to determine criticality.

- [ ] Add `critical_inputs` to `COMPANY_RESEARCH_ARTIFACT_KINDS`, codec encode/decode, lineage validation, artifact ordering, and repository append/read validation. Keep it optional for legacy projects and required only for strategy `company-research-mainline.v1` after a machine memo exists.

- [ ] Add a repository test proving an edited source fact is represented as a user-assumption replacement while the original evidence artifact and source hash remain unchanged.

- [ ] Run focused tests.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_critical_inputs.py tests/underwriting/test_company_research_artifact_codec.py tests/underwriting/test_company_research_persistence.py -q`

Expected: all tests pass.

- [ ] Commit the artifact contract.

```bash
git add backend/app/underwriting/domain backend/app/underwriting/persistence backend/tests/underwriting/test_company_research_critical_inputs.py backend/tests/underwriting/test_company_research_artifact_codec.py backend/tests/underwriting/test_company_research_persistence.py
git commit -m "feat: persist deterministic critical research inputs"
```

## Task 3: Generate the AI draft without per-evidence blocking

**Files:**

- Modify: `backend/app/underwriting/services/company_research_initializer.py`
- Modify: `backend/app/underwriting/services/company_research_preparation.py`
- Modify: `backend/app/underwriting/services/company_research_model_builder.py`
- Create: `backend/app/underwriting/services/company_research_ai_memo.py`
- Modify: `backend/app/underwriting/persistence/company_research_repository.py`
- Modify: `backend/app/underwriting/services/company_research_sources.py`
- Test: `backend/tests/underwriting/test_company_research_worker.py`
- Test: `backend/tests/underwriting/test_company_research_model_builder.py`
- Test: `backend/tests/underwriting/test_company_research_ai_memo.py`
- Test: `backend/tests/underwriting/test_company_research_policy.py`

- [ ] Add a failing initializer test asserting new projects freeze `company-research-mainline.v1`; retain explicit `company-research-default.v1` support for legacy fixtures.

- [ ] Add a failing worker test for this exact path:

```text
queued/evidence_index
→ preparing_sources/evidence_index
→ building_model/model_bundle
→ awaiting_judgment_review/judgment_context
```

Assert no `awaiting_evidence_review` transition and no synthetic `evidence_reviewed` events occur for the new strategy.

- [ ] Add a failing model-builder test proving draft mode accepts strictly authenticated, unreviewed source facts but still excludes explicitly rejected facts and rejects invalid provenance, inconsistent units, future availability, and malformed values.

- [ ] Add failing AI-memo tests using the repository's existing `app.ai.client.LLMClient` boundary. Require closed JSON with summary, business explanation, three driver explanations, counterevidence, gaps, and next checks. Every claim must cite existing fact/artifact/gap keys; reject any number, source, or key absent from the authenticated input summary.

- [ ] Run focused tests to record RED.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_worker.py tests/underwriting/test_company_research_model_builder.py tests/underwriting/test_company_research_ai_memo.py -q -k 'mainline or draft_mode or ai_memo'`

Expected: the source job still waits for evidence review and the builder rejects unreviewed facts.

- [ ] Add an explicit build mode to `CompanyResearchBuildInput`:

```python
class CompanyResearchEvidenceMode(StrEnum):
    HUMAN_REVIEWED = "human_reviewed"
    AUTHENTICATED_AI_DRAFT = "authenticated_ai_draft"
```

In AI-draft mode, treat authenticated source facts as eligible model inputs without adding `review_decision="confirmed"` to the durable evidence payload. Continue to reject any fact whose source contract, cutoff, unit, or value fails validation.

- [ ] Implement the AI memo adapter after deterministic financial artifacts are valid. Pass only source-policy-allowed summaries and stable keys to `LLMClient`; never pass restricted raw bytes. Validate closed output, retry invalid structure at most twice, and freeze provider/model/prompt/input/output hashes into the machine memo. The model may explain existing results but cannot introduce or alter numerical inputs.

- [ ] On provider unavailability or retry exhaustion, preserve the deterministic model and memo, append a typed recoverable process warning, and label the narrative `deterministic_fallback`. Do not label fallback text as AI-generated and do not discard completed financial work.

- [ ] Change `complete_evidence_preparation` so only `company-research-mainline.v1` atomically queues `model_bundle` at progress 25. Preserve the existing waiting-for-review branch for legacy strategies.

- [ ] After the first `complete_model_bundle`, deterministically select and append the version-1 `critical_inputs` artifact in the same transaction. On a rebuilt bundle, append one successor instead, carry forward only decisions with unchanged fingerprints, and reject a duplicate current head. The memo remains `candidate_status="machine_draft"`; preparation remains `awaiting_judgment_review` until critical inputs are resolved.

- [ ] Add bounded progress checkpoints at 35, 60, and 80 without exposing provider internals. Extend the builder with a callback invoked after authenticated analysis inputs, forecast/scenario construction, and memo validation. The worker writes only fenced status/event updates through a separate short transaction; provider callables still receive no session or write API.

- [ ] Test stale claims, crash recovery, duplicate workers, and old-strategy evidence review after the new branch is added.

- [ ] Run worker/model regressions.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_worker.py tests/underwriting/test_company_research_model_builder.py tests/underwriting/test_company_research_ai_memo.py tests/underwriting/test_company_research_policy.py -q`

Expected: all tests pass, including existing claim-fencing and three-attempt retry tests.

- [ ] Commit automatic draft generation.

```bash
git add backend/app/underwriting/services backend/app/underwriting/persistence/company_research_repository.py backend/tests/underwriting/test_company_research_worker.py backend/tests/underwriting/test_company_research_model_builder.py backend/tests/underwriting/test_company_research_policy.py
git commit -m "feat: generate authenticated company research drafts automatically"
```

## Task 4: Add the `CompanyResearchRun` application service and API

**Files:**

- Create: `backend/app/underwriting/services/company_research_run.py`
- Modify: `backend/app/underwriting/services/__init__.py`
- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Test: `backend/tests/underwriting/test_company_research_run_service.py`
- Test: `backend/tests/underwriting/test_company_research_api.py`

- [ ] Write service tests proving the run service composes the authenticated workbench, lifecycle event chain, critical-input head, and selected frozen revision without duplicating source/model/publication logic.

- [ ] Write API tests for `GET /product/company-research/projects/{project_id}/run` with the exact top-level shape:

```json
{
  "project_id": "uuid",
  "company": {},
  "securities": [],
  "status": "analyzing_company",
  "progress": 35,
  "started_at": "aware timestamp",
  "updated_at": "aware timestamp",
  "stages": [],
  "recent_process": [],
  "workspace": {},
  "critical_inputs": {},
  "selected_revision": null
}
```

Assert five ordered stages, at most three recent events by default, a bounded full-process collection of 100 entries, typed retry semantics, and no raw error detail.

- [ ] Run focused tests and verify 404/route failures.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_run_service.py tests/underwriting/test_company_research_api.py -q -k 'research_run'`

Expected: failing route/service assertions.

- [ ] Implement `CompanyResearchRunService.read(project_id, include_process=False)`. Authenticate the workbench first, then validate the event hash chain before projection. Never catch integrity errors and return partial trusted output.

- [ ] Add closed Pydantic response types. Reuse `CompanyResearchWorkspaceResponse` for result artifacts; do not create a second artifact wire format.

- [ ] Add the GET route and map only `include_process=true|false`; reject any other value through FastAPI validation.

- [ ] Test malformed event chains, foreign company/project identities, missing critical-input heads on mainline drafts, legacy workspace reads, and selected-revision consistency.

- [ ] Run focused tests.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_run_service.py tests/underwriting/test_company_research_api.py -q`

Expected: all tests pass.

- [ ] Commit the application boundary.

```bash
git add backend/app/underwriting/services/company_research_run.py backend/app/underwriting/services/__init__.py backend/app/underwriting/api/company_research_schemas.py backend/app/underwriting/api/company_research_router.py backend/tests/underwriting/test_company_research_run_service.py backend/tests/underwriting/test_company_research_api.py
git commit -m "feat: expose company research run API"
```

## Task 5: Confirm, replace, or close critical inputs and gate publication

**Files:**

- Create: `backend/app/underwriting/services/company_research_critical_input_confirmation.py`
- Modify: `backend/app/underwriting/services/company_research_preparation.py`
- Modify: `backend/app/underwriting/services/company_research_publication.py`
- Modify: `backend/app/underwriting/persistence/company_research_repository.py`
- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Test: `backend/tests/underwriting/test_company_research_critical_input_confirmation.py`
- Test: `backend/tests/underwriting/test_company_research_publication.py`
- Test: `backend/tests/underwriting/test_company_research_api.py`

- [ ] Write failing tests for `POST /projects/{project_id}/critical-input-decisions` using request fields `critical_input_key`, `expected_artifact_id`, `expected_input_fingerprint`, `decision`, and conditional replacement fields.

- [ ] Cover all allowed operations:

```text
confirmed                         → append decision successor; no model rebuild
marked_unknown                    → append decision successor; answerability closes safely
accepted_gap                      → append decision successor; gap remains explicit
replaced_with_user_assumption     → preserve source fact; append replacement; rebuild model
```

Reject replacement without value/unit/rationale, replacement of a derived calculation, stale artifact/fingerprint, duplicate contradictory decisions, and edits to a frozen revision.

- [ ] Run focused tests to record RED.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_critical_input_confirmation.py tests/underwriting/test_company_research_publication.py tests/underwriting/test_company_research_api.py -q -k 'critical_input or publication_gate'`

Expected: missing service/route and publication currently ignores critical inputs.

- [ ] Implement a single-decision successor: copy the current `critical_inputs` payload, change exactly one entry, bind the successor input hash to parent content hash/key/fingerprint/decision, and append a `critical_input_decided` event.

- [ ] For a user-assumption replacement, queue a new `model_bundle` attempt with the exact critical-input successor hash. Apply replacements to a copied model input; never mutate evidence. Persist the rebuilt artifacts and a successor critical-input set, retaining decisions whose fingerprints are unchanged and resetting changed dependents to pending.

- [ ] When every blocking input has a terminal decision and the rebuilt memo is current, transition to `ready_to_freeze` at 95. Keep `not_answerable` publishable only when the memo, gaps, and critical decisions consistently explain why valuation/direction are absent.

- [ ] Add `critical_inputs` descriptor and canonical payload to publication preview and revision manifest. Publication must reject a missing, stale, or pending critical-input set for `company-research-mainline.v1` while continuing to replay legacy manifests.

- [ ] Add tests proving later workspace changes cannot alter a frozen critical-input payload or Markdown export hash.

- [ ] Run focused confirmation/publication tests.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_critical_input_confirmation.py tests/underwriting/test_company_research_publication.py tests/underwriting/test_company_research_api.py tests/underwriting/test_historical_replay.py -q`

Expected: all tests pass.

- [ ] Commit confirmation and publication gating.

```bash
git add backend/app/underwriting/services backend/app/underwriting/persistence/company_research_repository.py backend/app/underwriting/api backend/tests/underwriting/test_company_research_critical_input_confirmation.py backend/tests/underwriting/test_company_research_publication.py backend/tests/underwriting/test_company_research_api.py backend/tests/underwriting/test_historical_replay.py
git commit -m "feat: gate research publication on critical inputs"
```

## Task 6: Add a fail-closed frontend run decoder and client methods

**Files:**

- Modify: `frontend/src/data/investmentResearchApi.ts`
- Modify: `frontend/src/data/InvestmentResearchApi.test.ts`
- Modify: `frontend/src/features/investment-research/companyResearchView.ts`
- Modify: `frontend/src/features/investment-research/companyResearchView.test.ts`

- [ ] Add fixture helpers and failing decoder tests for every product status, five-stage ordering, process-event limits, critical-input conditional fields, and nested workspace identity consistency.

- [ ] Add tests rejecting extra keys, duplicate stages/critical keys, raw-error fields, foreign project/company IDs, stale input fingerprints, impossible decision/replacement combinations, and a completed run without a machine memo.

- [ ] Run frontend data tests to record RED.

Run: `cd frontend && npm test -- --run src/data/InvestmentResearchApi.test.ts src/features/investment-research/companyResearchView.test.ts`

Expected: missing run decoder/client methods.

- [ ] Implement `CompanyResearchRun`, `CompanyResearchStage`, `CompanyResearchProcessEntry`, `CompanyResearchCriticalInput`, and `CompanyResearchCriticalInputSet` types plus strict runtime decoders.

- [ ] Add client methods:

```ts
companyResearchRun(projectId: string, includeProcess = false): Promise<CompanyResearchRun>
decideCompanyResearchCriticalInput(projectId: string, request: CriticalInputDecisionRequest): Promise<CompanyResearchRun>
```

The decision response must be decoded as a complete fresh run, so the UI never splices incompatible old and new heads.

- [ ] Replace view helpers that infer lifecycle from preparation fields with helpers consuming server-projected `status` and `stages`. Keep legacy workspace helpers only for frozen-revision rendering.

- [ ] Run the two test files and typecheck.

Run: `cd frontend && npm test -- --run src/data/InvestmentResearchApi.test.ts src/features/investment-research/companyResearchView.test.ts && npm run typecheck`

Expected: all commands exit 0.

- [ ] Commit the client contract.

```bash
git add frontend/src/data/investmentResearchApi.ts frontend/src/data/InvestmentResearchApi.test.ts frontend/src/features/investment-research/companyResearchView.ts frontend/src/features/investment-research/companyResearchView.test.ts
git commit -m "feat: decode company research runs"
```

## Task 7: Simplify the entry to one-click company research

**Files:**

- Modify: `frontend/src/features/investment-research/ResearchHomePage.tsx`
- Modify: `frontend/src/features/investment-research/NewResearchPage.tsx`
- Modify: `frontend/src/features/investment-research/InvestmentResearchShell.test.tsx`
- Modify: `frontend/src/features/investment-research/NewResearchPage.test.tsx`
- Modify: `frontend/src/styles/underwriting-research.css`
- Modify: `backend/app/underwriting/domain/company_research.py`
- Modify: `backend/app/underwriting/services/company_research_initializer.py`
- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Modify: `backend/tests/underwriting/test_company_research_initializer.py`
- Modify: `backend/tests/underwriting/test_company_research_api.py`

- [ ] Write failing interaction tests for: search company/security, choose the resolved Company group, optionally enter one focus question, click `开始 AI 研究`, and navigate directly to the new result page.

- [ ] Assert technical defaults are not editable on the primary path but are visible in an expandable `研究设置` summary after preview.

- [ ] Assert event/fund/archive links remain under `高级工具` and do not share the primary call-to-action hierarchy.

- [ ] Add backend contract tests for an optional canonical `focus_question`: trim Unicode whitespace, reject blank or more than 500 characters, echo it in preview, and bind it into the preview/request/agenda hashes. Assert the question cannot change source policy, model strategy, cutoff, required return, or loss limit.

- [ ] Run focused UI tests to record RED.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_initializer.py tests/underwriting/test_company_research_api.py -q -k 'focus_question' && cd ../frontend && npm test -- --run src/features/investment-research/NewResearchPage.test.tsx src/features/investment-research/InvestmentResearchShell.test.tsx`

Expected: old multi-step boundary form assertions fail the new desired behavior.

- [ ] Replace the primary form with `company_or_security` and optional `focus_question`. Continue using existing preview and initialization endpoints; bind the idempotency key and preview hash exactly as today.

- [ ] Send the focus question through a versioned, length-bounded request field and freeze it into the research agenda input hash. It is agenda context, not an instruction that can relax source policy or answerability.

- [ ] Preserve keyboard flow, labelled inputs, loading copy, retry after response loss, and company/security identity confirmation.

- [ ] Run focused tests and typecheck.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_company_research_initializer.py tests/underwriting/test_company_research_api.py -q && cd ../frontend && npm test -- --run src/features/investment-research/NewResearchPage.test.tsx src/features/investment-research/InvestmentResearchShell.test.tsx && npm run typecheck`

Expected: all commands exit 0.

- [ ] Commit the one-click entry.

```bash
git add backend/app/underwriting/domain/company_research.py backend/app/underwriting/services/company_research_initializer.py backend/app/underwriting/api/company_research_schemas.py backend/app/underwriting/api/company_research_router.py backend/tests/underwriting/test_company_research_initializer.py backend/tests/underwriting/test_company_research_api.py frontend/src/features/investment-research/ResearchHomePage.tsx frontend/src/features/investment-research/NewResearchPage.tsx frontend/src/features/investment-research/InvestmentResearchShell.test.tsx frontend/src/features/investment-research/NewResearchPage.test.tsx frontend/src/styles/underwriting-research.css
git commit -m "feat: simplify company research entry"
```

## Task 8: Build the result-first page with always-visible process

**Files:**

- Create: `frontend/src/features/investment-research/CompanyResearchProgress.tsx`
- Create: `frontend/src/features/investment-research/CompanyResearchResult.tsx`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx`
- Modify: `frontend/src/styles/underwriting-research.css`

- [ ] Add failing rendering tests for the approved hierarchy: title/status, five-stage rail, three recent process items, result judgment, business model, three drivers, Base/Bull/Bear and value range, strongest counterevidence, gaps, next verification, sources, and versions.

- [ ] Add state tests for collecting, analysis, forecast, report generation, recoverable failure, needs-input, completed draft, and frozen revision. Assert completed content remains visible during retry/failure.

- [ ] Add an accessibility test for stage names, current-stage announcement, expandable full process, source disclosure buttons, and focus retention after polling.

- [ ] Run the focused page tests to record RED.

Run: `cd frontend && npm test -- --run src/features/investment-research/ResearchWorkbenchPage.test.tsx`

Expected: missing stage rail/result-first structure.

- [ ] Poll `companyResearchRun`, not separate project/workspace endpoints. Preserve bounded exponential backoff, visibility pause, request-generation fences, and mutation serialization from the current page.

- [ ] Render results before process details. Default the process panel to the newest three entries; fetch `include_process=true` only when expanded.

- [ ] Keep all nine existing detailed modules reachable in `研究依据`, but remove them from the primary left-navigation hierarchy. Do not delete source links, numerical provenance, revision replay, or export controls.

- [ ] Add responsive styles: five columns above 900px, horizontally scrollable labelled rail between 640–899px, stacked stages below 640px; critical result cards must not rely on color alone.

- [ ] Run page tests, typecheck, and build.

Run: `cd frontend && npm test -- --run src/features/investment-research/ResearchWorkbenchPage.test.tsx && npm run typecheck && npm run build`

Expected: all commands exit 0.

- [ ] Commit the result-first page.

```bash
git add frontend/src/features/investment-research/CompanyResearchProgress.tsx frontend/src/features/investment-research/CompanyResearchResult.tsx frontend/src/features/investment-research/ResearchWorkbenchPage.tsx frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx frontend/src/styles/underwriting-research.css
git commit -m "feat: present company research results first"
```

## Task 9: Add the save-before-confirmation drawer

**Files:**

- Create: `frontend/src/features/investment-research/CriticalInputDrawer.tsx`
- Create: `frontend/src/features/investment-research/CriticalInputDrawer.test.tsx`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx`
- Modify: `frontend/src/styles/underwriting-research.css`

- [ ] Write failing component tests for type/value/period/unit/source-or-equation/impact display, progress count, confirm, replace with user assumption, mark unknown, accept gap, and supplemental-source guidance.

- [ ] Test conditional replacement inputs and verify the submit button remains disabled until value, unit, and rationale are valid. A source fact replacement must display that the original disclosure is preserved.

- [ ] Test stale conflicts: close the stale drawer, load the fresh run, focus the changed critical input, and do not retry the old fingerprint automatically.

- [ ] Run drawer/page tests to record RED.

Run: `cd frontend && npm test -- --run src/features/investment-research/CriticalInputDrawer.test.tsx src/features/investment-research/ResearchWorkbenchPage.test.tsx`

Expected: missing component and save still opens the old memo-only flow.

- [ ] Implement an accessible modal drawer opened by `保存研究版本`. Resolve one critical input per request and replace local state only with the returned complete run.

- [ ] When all required items are terminal, continue through the existing judgment memo, publication preview, publish, replay, and verified Markdown export sequence. Do not bypass preview hash or idempotency protections.

- [ ] Preserve the current publication-dialog focus, conflict recovery, double-submit prevention, and export-hash tests.

- [ ] Run focused tests and typecheck.

Run: `cd frontend && npm test -- --run src/features/investment-research/CriticalInputDrawer.test.tsx src/features/investment-research/ResearchWorkbenchPage.test.tsx && npm run typecheck`

Expected: all commands exit 0.

- [ ] Commit the confirmation UI.

```bash
git add frontend/src/features/investment-research/CriticalInputDrawer.tsx frontend/src/features/investment-research/CriticalInputDrawer.test.tsx frontend/src/features/investment-research/ResearchWorkbenchPage.tsx frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx frontend/src/styles/underwriting-research.css
git commit -m "feat: confirm critical inputs before saving research"
```

## Task 10: Add a separate answerable CATL Company Research case

**Files:**

- Create: `backend/app/underwriting/fixtures/catl_answerable_case/__init__.py`
- Create: `backend/app/underwriting/fixtures/catl_answerable_case/manifest.json`
- Create: `backend/app/underwriting/fixtures/catl_answerable_case/source_facts.json`
- Create: `backend/app/underwriting/fixtures/catl_answerable_case/business_map.json`
- Create: `backend/app/underwriting/fixtures/catl_answerable_case/strategy_assumptions.json`
- Create: `backend/app/underwriting/fixtures/catl_answerable_case/market_inputs.json`
- Create: `backend/app/underwriting/adapters/company_research/catl.py`
- Modify: `backend/app/underwriting/services/company_research_boundary.py`
- Modify: `backend/app/underwriting/services/company_research_foundation.py`
- Test: `backend/tests/underwriting/test_catl_company_research_case.py`
- Test: `backend/tests/underwriting/test_catl_source_traceability.py`

- [ ] Write a failing test proving the existing `catl_baseline` remains `catl_economic_model_evidence_only` and cannot be silently upgraded to an answerable Company Research run.

- [ ] Define the new fixture contract around `CN:300750:COMPANY` and `SZSE:300750` with a distinct cutoff and manifest hash. Require issuer filings for historical financials/capital structure, exchange-authorized price data, an explicit CNY base currency, and complete locator/unit/availability fields.

- [ ] Add a capture-validation test that fails if any required revenue, operating profit, tax, depreciation, capex, working-capital, share-count, cash/debt, price, or scenario-assumption input is absent. Unknown values remain gaps and cannot be serialized as zero.

- [ ] Run CATL tests to record RED.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_catl_company_research_case.py tests/underwriting/test_catl_source_traceability.py -q`

Expected: the answerable fixture/adapter is absent.

- [ ] Build the fixture only from authorized frozen sources and derived observations. Store hashes, locators, and normalized facts; do not commit copyrighted raw reports. Add source-byte verification helpers for any retained public raw component.

- [ ] Implement a CATL adapter conforming to the same model-template, governed-assumption, source-compilation, market-input, and answerability interfaces as Alphabet. Dispatch by exact company external key; reject unknown companies.

- [ ] Assert the CATL case produces Base/Bull/Bear, a CNY value range with explicit sensitivity, strongest counterevidence, gaps, and next verification events. Assert every numeric output resolves to source, assumption, equation, or gap provenance.

- [ ] Run CATL and existing Alphabet golden-case tests.

Run: `cd backend && .venv/bin/pytest tests/underwriting/test_catl_company_research_case.py tests/underwriting/test_catl_source_traceability.py tests/underwriting/test_catl_baseline.py tests/underwriting/test_alphabet_golden_case.py tests/underwriting/test_answerability.py -q`

Expected: all tests pass; old CATL stays evidence-only, new CATL is answerable, Alphabet remains honestly not answerable when its governed gaps remain open.

- [ ] Commit the dual-case adapter support.

```bash
git add backend/app/underwriting/fixtures/catl_answerable_case backend/app/underwriting/adapters/company_research/catl.py backend/app/underwriting/services/company_research_boundary.py backend/app/underwriting/services/company_research_foundation.py backend/tests/underwriting/test_catl_company_research_case.py backend/tests/underwriting/test_catl_source_traceability.py
git commit -m "feat: add answerable CATL company research case"
```

## Task 11: Update the real browser verifier for both outcomes

**Files:**

- Modify: `frontend/scripts/live-company-research-support.mjs`
- Modify: `frontend/scripts/live-company-research-support.test.mjs`
- Modify: `frontend/scripts/verify-live-company-research-ui.mjs`
- Modify: `backend/tests/underwriting/test_verify_live_company_research_ui.py`
- Modify: `.github/workflows/backend.yml`
- Modify: `README.md`

- [ ] Add failing support tests for two named cases, expected answerability, critical-input counts, frozen revision identity, and export hash.

- [ ] Replace the old verifier sequence that clicks every evidence decision with the approved browser-only sequence:

```text
search/choose company
→ start AI research
→ observe stage rail and process
→ wait for machine draft
→ open sources and gaps
→ resolve critical inputs
→ save one frozen revision
→ replay revision
→ verify Markdown bytes and SHA-256
```

- [ ] Add explicit network assertions: no browser-side model/publish shortcuts, no unexpected external requests, Bearer header only to the local API, and no mock adapter.

- [ ] Run support and outer-verifier tests to record RED.

Run: `cd frontend && npm run test:live-company-research-support && cd ../backend && .venv/bin/pytest tests/underwriting/test_verify_live_company_research_ui.py -q`

Expected: assertions still target per-evidence review and a single Alphabet case.

- [ ] Implement data-driven CATL answerable and Alphabet not-answerable verifier runs, each with isolated private runtime/database paths and the existing owned-process-group cleanup.

- [ ] Preserve bounded output capture, SIGTERM/SIGKILL ownership checks, Vite proxy authentication, worker supervision, and cleanup tests merged in `d62827d7`.

- [ ] Run the support and outer-verifier tests.

Run: `cd frontend && npm run test:live-company-research-support && cd ../backend && .venv/bin/pytest tests/underwriting/test_verify_live_company_research_ui.py tests/test_remove_private_runtime_contents.py -q`

Expected: all tests pass.

- [ ] Run the real browser verification with repository Python and Chrome.

Run: `cd frontend && PYTHON=../backend/.venv/bin/python PW_BROWSER_CHANNEL=chrome npm run verify:live-company-research`

Expected: both cases finish, each publishes exactly one revision, both exports match their declared SHA-256, CATL is answerable with value range, and Alphabet is `not_answerable` without direction/confidence/value/return range.

- [ ] Commit the live acceptance path.

```bash
git add frontend/scripts/live-company-research-support.mjs frontend/scripts/live-company-research-support.test.mjs frontend/scripts/verify-live-company-research-ui.mjs backend/tests/underwriting/test_verify_live_company_research_ui.py .github/workflows/backend.yml README.md
git commit -m "test: verify company research mainline live"
```

## Task 12: Run complete regression and boundary review

**Files:**

- Modify only if verification exposes an in-scope defect; do not broaden scope.

- [ ] Run formatting and static checks.

Run: `cd backend && .venv/bin/ruff check app tests`

Expected: exit 0.

Run: `cd frontend && npm run typecheck && npm run build`

Expected: both commands exit 0.

- [ ] Run the complete backend suite on SQLite.

Run: `cd backend && .venv/bin/pytest -q`

Expected: all non-environment-skipped tests pass.

- [ ] Run the complete frontend suite.

Run: `cd frontend && npm test`

Expected: all tests pass.

- [ ] Run the PostgreSQL-marked Company Research concurrency tests with the repository's configured `TEST_DATABASE_URL`.

Run: `cd backend && .venv/bin/pytest -q -m pg_only tests/underwriting/test_company_research_worker.py tests/underwriting/test_company_research_persistence.py`

Expected: all selected tests pass; if `TEST_DATABASE_URL` is absent, record the environment block and rely on CI rather than claiming PostgreSQL verification.

- [ ] Re-run the real browser verifier after all regressions.

Run: `cd frontend && PYTHON=../backend/.venv/bin/python PW_BROWSER_CHANNEL=chrome npm run verify:live-company-research`

Expected: both live cases pass with no leaked owned process or private runtime.

- [ ] Review the final diff against explicit exclusions. Use these searches:

```bash
git diff main...HEAD --check
git diff --name-only main...HEAD
rg -n "order|broker|position|portfolio optimization|RBAC|investment committee" backend/app/underwriting frontend/src/features/investment-research
```

Expected: clean diff check; no trading, complex institutional workflow, or unrelated event/fund implementation added.

- [ ] Request a code review focused separately on spec coverage and repository standards, address findings, then rerun every affected focused test.

- [ ] If review produces fixes, stage only the exact files named by `git diff --name-only`, inspect the staged diff, and commit them with message `fix: address company research mainline review`. If review produces no fixes, do not create an empty commit.

## Final acceptance checklist

- [ ] A user supplies only company/security and an optional focus question.
- [ ] The new strategy reaches a complete AI draft without per-evidence confirmation.
- [ ] The page always shows the five stages and recent process while keeping results primary.
- [ ] Every numeric statement resolves to source, assumption, calculation, or unknown/gap.
- [ ] Only deterministically selected critical inputs block save.
- [ ] User replacements become assumptions and trigger a model rebuild; source facts remain unchanged.
- [ ] Single-source or bounded AI failure preserves completed work and exposes safe recovery.
- [ ] CATL demonstrates an answerable result; Alphabet demonstrates an honest refusal to value.
- [ ] Save freezes critical inputs, sources, market snapshots, assumptions, model, memo, and version.
- [ ] Replay/export use only the frozen revision and reproduce the verified hash.
- [ ] Existing legacy review, evidence, revision, replay, export, SQLite, PostgreSQL, and cleanup protections remain green.
