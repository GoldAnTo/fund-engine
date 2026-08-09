# Event-First Research OS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current prototype-era frontend with the approved event-first Research OS, backed by real, auditable data for intake, Case research, evidence review, Wiki, market expression and transparent research runs.

**Architecture:** Keep `ResearchCase` as the only durable research unit. Build missing append-only domain/API contracts before rendering their corresponding production page; the React application consumes one `ResearchClient` facade and never imports ORM/provider objects. A compact global run strip and detail drawer become part of the application shell, while Case sub-pages own their query, interaction and empty/error states.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL/SQLite test database, OpenAPI, React 18, TypeScript, React Router, Vitest, Playwright.

---

## Delivery boundaries

- This replaces production routes and retires the existing `frontend/src/pages/` and `frontend/src/pages/prototype/` screens only after their supported user tasks exist in the new route tree.
- No UI may fabricate a `ReportClaim`, `KeyFactor`, fund exposure, source permission or run log. A missing backend capability gets an explicit unavailable state, never a fixture fallback in live mode.
- Existing event lifecycle behavior in `backend/app/services/event_research_scope.py` is out of scope for direct modification because the main checkout has unrelated in-flight work. Extend it through new endpoints/services and separate migration-backed models.
- Execute in two mergeable increments: **A, real event workflow plus visible runs**, then **B, Wiki and verified market expression**. Do not remove legacy source files until each increment has route, unit and browser coverage.

## Target file structure

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/0019_case_monitor_and_run_events.py` | Append-only monitor configuration versions and typed run-event log tables. |
| `backend/app/models/research_monitor.py` | `CaseMonitorVersion` and immutable `ResearchRunEvent` ORM models. |
| `backend/app/services/case_monitor.py` | Validate, version and schedule monitor configuration; start immediate runs from the latest accepted version. |
| `backend/app/queries/case_monitor.py` | Build list/detail read models for monitor configuration and run transparency. |
| `backend/app/api/v1/case_monitor.py` | Monitor read/update endpoints and run event stream endpoint. |
| `backend/app/schemas/v1/case_monitor.py` | V1 request/response DTOs with source, scope, actor and replay metadata. |
| `backend/app/models/research_expression.py` | Append-only `ReportClaim`, `KeyFactor`, `ClaimVerification` and reviewed expression-link records. |
| `backend/alembic/versions/0020_research_expression_and_case_wiki.py` | Schema and immutable-table registration for expression records. |
| `backend/app/queries/case_wiki.py` | Read-only Case Wiki graph with reviewed and candidate layers. |
| `backend/app/queries/market_expression.py` | Separated claim/factor, fundamentals, market-observation and historical-fund-exposure view. |
| `backend/app/api/v1/case_wiki.py` / `market_expression.py` | Case-scoped V1 read endpoints. |
| `frontend/src/app/AppShell.tsx` | New warm-paper shell, global run strip and run detail drawer. |
| `frontend/src/app/routes.tsx` | Single authoritative route map for event desk, create, Case tabs and review. |
| `frontend/src/features/events/*` | Event desk and verified intake feature. |
| `frontend/src/features/case/*` | Case header, conclusion/evidence/review and monitor views. |
| `frontend/src/features/wiki/*` | Case-only Wiki canvas and inspector. |
| `frontend/src/features/market/*` | Market-expression view with strict evidence-layer labels. |
| `frontend/src/data/httpResearchAdapter.ts` / `mockResearchAdapter.ts` | Typed HTTP mapping and explicit mock parity for every new V1 response. |
| `frontend/src/styles/research-os.css` | Shared visual tokens and component styles; no inline style system. |
| `frontend/src/tests/researchOs*.test.tsx` | Component and interaction coverage. |
| `frontend/e2e/research-os.spec.ts` | Mock-mode browser proof of the primary human path. |

### Task 1: Record the approved interface contract and baseline the replacement branch

**Files:**
- Modify: `docs/design/2026-08-08-research-operating-system-baseline.md`
- Create: `docs/superpowers/specs/2026-08-09-global-research-run-transparency.md`
- Test: `frontend/src/tests/LegacyEventRedirect.test.tsx` (temporary inventory only)

- [ ] **Step 1: Add the approved global-run decision to the baseline.**

  Add a dated change-log row and a contract section stating that every active run has a shell-level summary (`case`, `stage`, `scope`, `permitted sources`, `count`, `next user action`) and an expandable detail view (`trigger`, configuration version, inputs, exclusions, events, outputs, errors and replay metadata).

- [ ] **Step 2: Define the non-negotiable V1 UI states in the spec.**

  Record exact states: `queued`, `running`, `waiting_for_review`, `completed_no_material_change`, `completed_with_candidates`, `failed`, and `cancelled`. State that only reviewed entities affect a conclusion, market expression or fund exposure.

- [ ] **Step 3: Commit documentation only.**

  Run: `git add docs/design/2026-08-08-research-operating-system-baseline.md docs/superpowers/specs/2026-08-09-global-research-run-transparency.md && git commit -m "docs: define transparent research-run contract"`

### Task 2: Add immutable CaseMonitor versions and structured run events

**Files:**
- Create: `backend/alembic/versions/0019_case_monitor_and_run_events.py`
- Create: `backend/app/models/research_monitor.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/ledger.py` (immutable-table registration only)
- Test: `backend/tests/test_case_monitor.py`

- [ ] **Step 1: Write the failing monitor invariant tests.**

  Cover: a monitor requires at least one confirmed factor, one allowed source and one next verification event; saving creates version 1; saving a changed configuration creates version 2 without changing version 1; update/delete against either version raises the existing immutable-ledger protection.

  ```python
  def test_changed_monitor_configuration_appends_a_new_version(session):
      first = service.save(case_id, actor="human:lin", config=monitor_config())
      second = service.save(case_id, actor="human:lin", config=monitor_config(frequency="weekday_12_30"))
      assert (first.version, second.version) == (1, 2)
      assert repo.get_version(first.id).frequency == "weekday_08_30"
  ```

- [ ] **Step 2: Run the new test file and verify RED.**

  Run: `cd backend && python -m pytest tests/test_case_monitor.py -q`

  Expected: collection/import failure because the monitor model and service do not yet exist.

- [ ] **Step 3: Add `CaseMonitorVersion` and `ResearchRunEvent` models plus migration.**

  `CaseMonitorVersion` stores `research_case_id`, `version`, `status`, `frequency`, `factor_ids`, `allowed_source_types`, `next_verification_event`, `budget`, `changed_by`, `change_reason`, `created_at`. `ResearchRunEvent` stores `run_id`, monotonically increasing `seq`, `stage`, `status`, `message`, `payload_json`, `created_at`. Register both as immutable after insert; retain the existing mutable operational `ResearchRun` projection.

- [ ] **Step 4: Implement the smallest `CaseMonitorService.save()` and run-event repository methods.**

  ```python
  def save(self, case_id: UUID, *, actor: str, config: CaseMonitorConfig) -> CaseMonitorVersion:
      self._validate_confirmed_scope(case_id, config)
      return self._repo.append_version(case_id=case_id, actor=actor, config=config)
  ```

- [ ] **Step 5: Run the focused backend test to GREEN, then commit.**

  Run: `cd backend && python -m pytest tests/test_case_monitor.py -q`

  Commit: `git add backend && git commit -m "feat: version case monitor configuration"`

### Task 3: Expose transparent run state and monitor configuration through V1

**Files:**
- Create: `backend/app/schemas/v1/case_monitor.py`
- Create: `backend/app/queries/case_monitor.py`
- Create: `backend/app/api/v1/case_monitor.py`
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/services/auto_research.py`
- Test: `backend/tests/test_case_monitor_api.py`

- [ ] **Step 1: Write failing HTTP contract tests.**

  Assert `GET /research-cases/{case_id}/monitor` returns the effective monitor version and latest run summary; `PUT` appends a new version; `GET /research-runs/{run_id}/events` returns ordered structured events rather than the current synthetic one-line summary. Assert a run records its `monitor_version_id` and that unsupported source types return the standard 422 envelope.

- [ ] **Step 2: Run the focused API tests and verify RED.**

  Run: `cd backend && python -m pytest tests/test_case_monitor_api.py -q`

- [ ] **Step 3: Implement DTOs and routes.**

  Add these response shapes without provider raw content:

  ```python
  class ResearchRunEventDTO(V1Model):
      seq: int
      stage: Literal["scope", "retrieve", "freeze", "validate", "candidate", "complete", "failed"]
      status: Literal["started", "completed", "skipped", "failed"]
      message: str
      counts: dict[str, int]
      created_at: datetime
  ```

  Extend `AutoResearchService` to append an event before and after each existing stage, include explicit exclusion/failure reasons, and return only source metadata permitted for the requesting tenant.

- [ ] **Step 4: Regenerate the OpenAPI contract and update generated frontend types.**

  Run: `bash scripts/sync-contract.sh`

- [ ] **Step 5: Run API tests and the contract gate, then commit.**

  Run: `cd backend && python -m pytest tests/test_case_monitor_api.py -q && cd .. && bash scripts/sync-contract.sh`

  Commit: `git add backend frontend/openapi.json frontend/src/contracts/v1.ts && git commit -m "feat: expose transparent research runs"`

### Task 4: Add reviewed claim/factor records and Case-scoped Wiki/market read models

**Files:**
- Create: `backend/alembic/versions/0020_research_expression_and_case_wiki.py`
- Create: `backend/app/models/research_expression.py`
- Create: `backend/app/queries/case_wiki.py`
- Create: `backend/app/queries/market_expression.py`
- Create: `backend/app/api/v1/case_wiki.py`
- Create: `backend/app/api/v1/market_expression.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_case_wiki_api.py`
- Test: `backend/tests/test_market_expression_api.py`

- [ ] **Step 1: Write failing graph-layer tests.**

  Seed one reviewed statement/link, one machine candidate, one factor and one historical holding disclosure. Assert Wiki returns reviewed edges separately from candidate edges; a candidate cannot appear in `market_expression.fund_exposure`; every inspector item returns locator, source permission, available time, relation scope and latest review metadata.

- [ ] **Step 2: Write failing market-separation tests.**

  Assert the market response has separate `fundamentals`, `market_observations`, and `fund_exposure` arrays. Verify a market observation contains event time, available time, benchmark and price source, and no causal-result field. Verify each fund row contains report period, published time, acquired time, source, coverage and stale marker.

- [ ] **Step 3: Run both test files and verify RED.**

  Run: `cd backend && python -m pytest tests/test_case_wiki_api.py tests/test_market_expression_api.py -q`

- [ ] **Step 4: Implement append-only domain models and query projections.**

  Create `ReportClaim` with `claim_kind` (`disclosed_fact`, `forecast`, `research_opinion`), locator-backed source statement, asserted period and review state. Create `KeyFactor` with metric definition, allowed sources, verification window, support/refutation rules and review record. Create `ClaimVerification` with only `supported`, `contradicted`, `insufficient_evidence`, `not_due`. Query projections must filter by `HistoricalBasis` and use only reviewed relationships for company, stock and fund output.

- [ ] **Step 5: Add V1 read routes and OpenAPI generated types.**

  Implement `GET /research-cases/{case_id}/wiki` and `GET /research-cases/{case_id}/market-expression?as_of=&cutoff=`. Do not expose write endpoints until the proposal/review command workflow is separately implemented.

- [ ] **Step 6: Run focused tests and OpenAPI synchronization, then commit.**

  Run: `cd backend && python -m pytest tests/test_case_wiki_api.py tests/test_market_expression_api.py -q && cd .. && bash scripts/sync-contract.sh`

  Commit: `git add backend frontend/openapi.json frontend/src/contracts/v1.ts && git commit -m "feat: add case wiki and market expression reads"`

### Task 5: Define the new frontend client boundary before writing pages

**Files:**
- Create: `frontend/src/domain/researchOs.ts`
- Modify: `frontend/src/domain/prototypeTypes.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Test: `frontend/src/tests/researchOsAdapter.test.ts`

- [ ] **Step 1: Write failing adapter mapping tests.**

  Verify malformed live run events fail closed; monitor config mapping preserves version and source restrictions; Wiki candidate edges stay marked `machine_generated`; fund rows preserve all three dates and a stale boolean.

- [ ] **Step 2: Run the adapter test and verify RED.**

  Run: `cd frontend && npm test -- src/tests/researchOsAdapter.test.ts`

- [ ] **Step 3: Create small domain DTOs and facade methods.**

  Add `getCaseMonitor`, `saveCaseMonitor`, `getResearchRunEvents`, `getCaseWiki`, and `getMarketExpression` to `ResearchClient`. Map all HTTP DTOs to domain objects inside `HttpResearchAdapter`; mocks must return the same shapes and must never be used by HTTP-error fallback.

- [ ] **Step 4: Run the adapter tests to GREEN, then commit.**

  Run: `cd frontend && npm test -- src/tests/researchOsAdapter.test.ts`

  Commit: `git add frontend/src/domain frontend/src/data frontend/src/tests/researchOsAdapter.test.ts && git commit -m "feat: add research os client contracts"`

### Task 6: Replace the application shell and event desk using real event reads

**Files:**
- Create: `frontend/src/app/AppShell.tsx`
- Create: `frontend/src/app/routes.tsx`
- Create: `frontend/src/features/events/EventDeskPage.tsx`
- Create: `frontend/src/features/events/EventCreatePage.tsx`
- Create: `frontend/src/styles/research-os.css`
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/tests/EventDeskPage.test.tsx`
- Test: `frontend/src/tests/EventCreatePage.test.tsx`
- Test: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: Write failing page tests.**

  Cover: the desk ranks the `nextHumanAction` event ahead of automatic work; list rows show “system is doing” and “you need to do”; create flow requires a non-empty input, lets the user edit the extracted question, requires three factors, and navigates only after `createEventResearch` returns a `caseId`.

- [ ] **Step 2: Run the page tests and verify RED.**

  Run: `cd frontend && npm test -- src/tests/EventDeskPage.test.tsx src/tests/EventCreatePage.test.tsx`

- [ ] **Step 3: Implement the event-first route tree.**

  Use `/events`, `/events/new`, `/events/:caseId`, `/events/:caseId/review`, `/events/:caseId/wiki`, `/events/:caseId/market`, `/events/:caseId/monitor`. The shell uses the approved warm-paper tokens and supports keyboard-visible navigation. The intake page distinguishes `pasted_snapshot`, `uploaded_file` and `licensed_provider` rather than labeling all input as a URL.

- [ ] **Step 4: Add the mock-mode browser path.**

  In `research-os.spec.ts`, exercise event selection, candidate extraction, editable scope, Case creation and opening the created Case without a live API request.

- [ ] **Step 5: Run focused tests and commit.**

  Run: `cd frontend && npm test -- src/tests/EventDeskPage.test.tsx src/tests/EventCreatePage.test.tsx && npm run e2e -- research-os.spec.ts`

  Commit: `git add frontend/src/app frontend/src/features/events frontend/src/styles/research-os.css frontend/src/main.tsx frontend/src/tests frontend/e2e/research-os.spec.ts && git commit -m "feat: replace event desk and intake"`

### Task 7: Build the Case workbench, review route and persistent transparent-run drawer

**Files:**
- Create: `frontend/src/features/case/CaseLayout.tsx`
- Create: `frontend/src/features/case/CaseConclusionPage.tsx`
- Create: `frontend/src/features/case/CaseEvidencePage.tsx`
- Create: `frontend/src/features/case/CaseReviewPage.tsx`
- Create: `frontend/src/features/runs/ResearchRunStrip.tsx`
- Create: `frontend/src/features/runs/ResearchRunDrawer.tsx`
- Test: `frontend/src/tests/ResearchRunStrip.test.tsx`
- Test: `frontend/src/tests/CaseReviewPage.test.tsx`

- [ ] **Step 1: Write failing transparent-run tests.**

  Assert the shell strip is visible while a Case run is active, names the Case/current stage and opens the drawer. Assert the drawer renders configuration version, allowed source list, ordered events, exclusions, errors and next action. Assert it does not claim a conclusion changed until a reviewed decision is returned.

- [ ] **Step 2: Run the focused tests and verify RED.**

  Run: `cd frontend && npm test -- src/tests/ResearchRunStrip.test.tsx src/tests/CaseReviewPage.test.tsx`

- [ ] **Step 3: Implement conclusion/evidence/review and drawer components.**

  The conclusion page shows current state, main rebuttal, largest gap, competing factors and one primary action. The review view pairs frozen source/locator with the proposal, scope and mandatory reason. `ResearchRunDrawer` polls only while status is active and stops on a terminal state or unmount.

- [ ] **Step 4: Add browser assertions for run transparency.**

  Assert the run strip remains visible when navigating from conclusion to review; open the drawer and assert an excluded source and a candidate/review boundary are visible.

- [ ] **Step 5: Run focused tests and commit.**

  Run: `cd frontend && npm test -- src/tests/ResearchRunStrip.test.tsx src/tests/CaseReviewPage.test.tsx && npm run e2e -- research-os.spec.ts`

  Commit: `git add frontend/src/features/case frontend/src/features/runs frontend/src/tests frontend/e2e/research-os.spec.ts && git commit -m "feat: add transparent case research flow"`

### Task 8: Build the Case Wiki and market-expression pages

**Files:**
- Create: `frontend/src/features/wiki/CaseWikiPage.tsx`
- Create: `frontend/src/features/wiki/WikiCanvas.tsx`
- Create: `frontend/src/features/wiki/WikiInspector.tsx`
- Create: `frontend/src/features/market/MarketExpressionPage.tsx`
- Create: `frontend/src/features/market/FactorInspector.tsx`
- Test: `frontend/src/tests/CaseWikiPage.test.tsx`
- Test: `frontend/src/tests/MarketExpressionPage.test.tsx`

- [ ] **Step 1: Write failing Wiki tests.**

  Assert reviewed and AI-candidate layers have distinct text and line styles; hiding candidates does not hide reviewed nodes; selecting a node populates inspector fields for source, locator, permission, available time, relation, scope, reviewer and reason; the candidate action links to the Case review route.

- [ ] **Step 2: Write failing market-expression tests.**

  Assert `research_opinion` is not styled as a fact; selecting a key factor changes the factor inspector; market observation includes event window, benchmark and source but no causal sentence; fund rows show report period, published time, acquired time, coverage and stale status.

- [ ] **Step 3: Run page tests and verify RED.**

  Run: `cd frontend && npm test -- src/tests/CaseWikiPage.test.tsx src/tests/MarketExpressionPage.test.tsx`

- [ ] **Step 4: Implement only against the new V1 reads.**

  Render the Wiki with CSS/SVG layout from the read model, not an inferred graph. Render market expression in four sections: report claim, selected key factor/verification, fundamentals vs market observation, and historical fund exposure. Keep `立即补证此因素` disabled with a precise capability message until the backend supports factor-scoped run creation.

- [ ] **Step 5: Run focused tests and browser route coverage, then commit.**

  Run: `cd frontend && npm test -- src/tests/CaseWikiPage.test.tsx src/tests/MarketExpressionPage.test.tsx && npm run e2e -- research-os.spec.ts`

  Commit: `git add frontend/src/features/wiki frontend/src/features/market frontend/src/tests frontend/e2e/research-os.spec.ts && git commit -m "feat: add case wiki and market expression"`

### Task 9: Build the monitor configuration and replay screen

**Files:**
- Create: `frontend/src/features/runs/CaseMonitorPage.tsx`
- Create: `frontend/src/features/runs/MonitorConfigForm.tsx`
- Test: `frontend/src/tests/CaseMonitorPage.test.tsx`
- Test: `frontend/src/tests/MonitorConfigForm.test.tsx`

- [ ] **Step 1: Write failing monitor interaction tests.**

  Assert the page distinguishes scheduled and manual trigger, lists the ordered run stages and outputs, shows no-change/failed/candidate states honestly, and opens a versioned config form. Assert save passes actor, change reason and new values and that historical runs retain their original configuration version.

- [ ] **Step 2: Run tests and verify RED.**

  Run: `cd frontend && npm test -- src/tests/CaseMonitorPage.test.tsx src/tests/MonitorConfigForm.test.tsx`

- [ ] **Step 3: Implement monitor/replay components.**

  Use `getCaseMonitor`, `listResearchRuns`, `getResearchRun`, and `getResearchRunEvents`; make `立即补证一次` call the same monitor-derived server path as scheduled runs. Do not expose an arbitrary provider query or a batch approve action.

- [ ] **Step 4: Run focused tests and commit.**

  Run: `cd frontend && npm test -- src/tests/CaseMonitorPage.test.tsx src/tests/MonitorConfigForm.test.tsx`

  Commit: `git add frontend/src/features/runs frontend/src/tests && git commit -m "feat: add configurable case monitoring"`

### Task 10: Retire old route tree and verify the full replacement

**Files:**
- Modify: `frontend/src/main.tsx`
- Delete: `frontend/src/components/PrototypeShell.tsx`
- Delete: `frontend/src/pages/*.tsx`
- Delete: `frontend/src/pages/prototype/*.tsx`
- Delete: superseded `frontend/src/tests/*Screen*.test.tsx`, `*Page.test.tsx` and legacy E2E specs
- Modify: `frontend/src/styles.css`
- Delete: `frontend/src/styles-prototype.css`
- Modify: `README.md`
- Modify: `docs/integration/frontend-api-binding.md`

- [ ] **Step 1: Write a route-inventory regression test before deletion.**

  Add a table-driven `frontend/src/tests/routes.test.tsx` that renders each new public route and asserts a live adapter failure displays an explicit error state rather than redirecting to mock/legacy content.

- [ ] **Step 2: Run it and verify RED.**

  Run: `cd frontend && npm test -- src/tests/routes.test.tsx`

- [ ] **Step 3: Remove only files with no import or route references.**

  Use `rg -n "from .*PrototypeShell|pages/prototype|pages/" frontend/src frontend/e2e` before every deletion batch. Replace `main.tsx` with the new route tree, delete each retired page/test/spec as its route is replaced, and remove `LegacyEventRedirect` rather than keeping disguised legacy navigation.

- [ ] **Step 4: Run the full frontend verification stack.**

  Run: `cd frontend && npm run typecheck && npm test && npm run build && npm run e2e`

- [ ] **Step 5: Run backend focused/full verification and inspect the deletion diff.**

  Run: `cd backend && python -m pytest tests/test_case_monitor.py tests/test_case_monitor_api.py tests/test_case_wiki_api.py tests/test_market_expression_api.py -q && python -m pytest -q`

  Run: `git diff --check && git status --short && rg -n "PrototypeShell|pages/prototype|LegacyEventRedirect" frontend || true`

- [ ] **Step 6: Commit the completed replacement.**

  Commit: `git add frontend README.md docs/integration/frontend-api-binding.md && git commit -m "feat: replace legacy frontend with research os"`

## Plan self-review

- **Spec coverage:** event intake, review, Case conclusion, Wiki layers/inspector, ReportClaim/KeyFactor verification, separated market/fund layers, monitor configuration, global run transparency and legacy removal each have a dedicated task.
- **Boundary coverage:** source permission, immutable history, AI/human separation, point-in-time fund disclosures and non-causal market observation are enforced in domain/API tests before presentation code.
- **Deletion safety:** legacy files are removed only after the new route test, focused feature tests, build and browser suite are green; the plan names the exact reference scan required before deletion.
- **Type consistency:** frontend calls (`getCaseMonitor`, `saveCaseMonitor`, `getResearchRunEvents`, `getCaseWiki`, `getMarketExpression`) are introduced in Task 5 after the matching backend contracts in Tasks 2–4.
