# Independent Investment Research Product Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the PRD's local, historically replayable company-investment research product through five independently testable increments, completing CATL before validating Alphabet reuse.

**Architecture:** Keep `app.underwriting` as the isolated bounded context and preserve every already-published legacy revision. Add a product aggregate above the existing evidence kernel: ResearchProject and editable WorkspaceDraft feed an atomic ResearchRevision whose RevisionBoundary independently references HistoricalBasis, market snapshots, mandate, scope and model artifacts. Frontend modules consume only generated `/api/underwriting/v1/product/*` contracts and are delivered vertically with the backend capability they expose.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest; React 18, TypeScript, React Router, Vitest, Playwright, generated OpenAPI TypeScript; Docker Compose and POSIX shell runtime scripts.

---

## 1. Authority and supersession

This program implements:

- `docs/superpowers/specs/2026-08-24-independent-investment-research-product-prd.md`;
- `prototype/investment-research-prototype/CONTEXT.md`.

It supersedes the delivery sequence and decision vocabulary in `2026-08-21-dynamic-investment-underwriting-program.md`. Earlier plans remain historical implementation records; they do not override the 2026-08-24 PRD. In particular:

- `EligibleAction` remains readable for legacy revisions but is not a first-version product output;
- legacy `uw_historical_bases.price_as_of` remains hash-verifiable but new product revisions use independent market snapshots;
- `confirmed_view` is not introduced;
- candidate evidence never becomes a formal ModelInput without the governed adoption path in Increment B.

## 2. Module and file ownership

### Existing modules retained

- `backend/app/underwriting/services/kernel.py` — legacy kernel write compatibility only; do not add product orchestration here.
- `backend/app/underwriting/services/research_revision_diff.py` — frozen legacy and product revision reads/diffs.
- `backend/app/underwriting/persistence/models.py` — existing kernel rows plus nullable compatibility columns required by new revisions.
- `backend/app/underwriting/persistence/research_models.py` — existing economic and candidate-evidence rows.
- `frontend/src/features/underwriting/ResearchArchivePage.tsx` — immutable archive; not the editable product workbench.

### New product modules

- `backend/app/underwriting/domain/product_contracts.py` — product enums and value objects; no SQLAlchemy or FastAPI imports.
- `backend/app/underwriting/persistence/product_models.py` — project, scope, agenda, market snapshot, draft, boundary and manifest rows.
- `backend/app/underwriting/persistence/product_repository.py` — persistence operations and optimistic-lock primitives.
- `backend/app/underwriting/services/product_project.py` — project, mandate, scope, agenda and identity use cases.
- `backend/app/underwriting/services/market_snapshots.py` — Price, FX, CapitalStructure and SecurityRights validation/freeze.
- `backend/app/underwriting/services/workspace_draft.py` — mutable draft with optimistic locking.
- `backend/app/underwriting/services/revision_publisher.py` — boundary validation, publication preview and atomic publish.
- `backend/app/underwriting/api/product_schemas.py` — strict product request/response DTOs.
- `backend/app/underwriting/api/product_router.py` — `/api/underwriting/v1/product/*` routes.
- `frontend/src/features/investment-research/` — the independent home, setup and nine-module workbench.
- `frontend/src/data/investmentResearchApi.ts` — generated-contract-only product client.

Large existing files are integrated through one import or router include; product behavior is not appended inline to `kernel.py`, `schemas.py`, `router.py` or `routes.tsx` beyond the smallest composition change.

## 3. Dependency graph

```text
Increment A: product aggregate + independent boundaries + atomic publish + shell
    ↓
Increment B: governed sources, SourceStatement, ModelInput, ResearchGap, industry
    ↓
Increment C: company model, forecast, valuation, answerability and assessment
    ↓
Increment D: update candidates, semantic changes, memo export, backup/restore hardening
    ↓
Increment E: Alphabet adapters, multi-security valuation and cross-market release gate
```

No downstream increment may compensate for an upstream gate failure with fixture-only UI, generated prose or current-state queries.

## 4. Increment A — Product foundation and entry

Detailed plan: `docs/superpowers/plans/2026-08-24-investment-research-foundation.md`.

Deliver:

- ResearchProject, InvestmentMandate, ResearchScope and ResearchAgenda;
- HistoricalBasis writes with `price_as_of=NULL` for new product work;
- PriceSnapshot, FXSnapshot, CapitalStructureSnapshot and SecurityRightsVersion;
- mutable WorkspaceDraft with optimistic locking;
- a minimal immutable ResearchAssessmentVersion that can represent only a fail-closed `not_answerable` foundation result; Increment C adds provisional directional assessment rules;
- RevisionBoundary and canonical manifest;
- atomic ResearchRevision publication with legacy-revision compatibility;
- searchable CATL and minimal Alphabet identity fixtures;
- independent `/research` shell, setup page and read-only workbench skeleton;
- local runtime migration, startup, status, backup and restore smoke coverage.

Gate:

```bash
cd backend
pytest -q tests/underwriting/test_product_contracts.py \
  tests/underwriting/test_product_persistence.py \
  tests/underwriting/test_product_project.py \
  tests/underwriting/test_market_snapshots.py \
  tests/underwriting/test_workspace_draft.py \
  tests/underwriting/test_revision_publisher.py \
  tests/underwriting/test_product_api.py \
  tests/underwriting/test_historical_replay.py \
  tests/underwriting/test_research_revision_diff.py

cd ../frontend
npm test -- InvestmentResearchShell.test.tsx NewResearchPage.test.tsx InvestmentResearchApi.test.ts
npm run typecheck
npm run build
```

The gate fails if a new product revision hashes legacy `price_as_of`, publication leaves partial rows, a retry duplicates a revision, or `/research` imports Event Research runtime modules.

## 5. Increment B — Governed evidence and CATL industry state

Deliver one vertical slice from acquired document to adopted model input:

- retain SourceReference → RetrievalArtifact → DocumentVersion → SourceSpan → SourceStatement;
- add the five orthogonal classification fields to ModelInput;
- add ResearchGap and ResearchDebt aggregation;
- replace the existing dual-review publication requirement with single-user self-review only in the new product path, while preserving old reviewed candidate records;
- compile CATL industry state only from adopted, cutoff-safe ModelInputs;
- render Sources & Evidence and Industry modules;
- keep formalization blocked when denominators, effective capacity or causal gates fail.

Required dedicated plan file before execution:

`docs/superpowers/plans/2026-08-24-investment-research-evidence-industry.md`

Gate fixtures:

- one accepted extraction with an immutable original and normalized value;
- one rejected extraction;
- one challenged/conflicting SourceStatement pair;
- one blocking ResearchGap;
- one CATL industry state that remains candidate;
- one state that becomes formal only after all required adopted inputs exist.

## 6. Increment C — Company model, forecast, valuation and assessment

Deliver:

- CATL segment quantity/price/revenue/cost/margin and cash-flow bridges;
- orthogonally classified Actual, guidance, consensus, market-implied and House inputs;
- mechanism-driven Base/Bull/Bear forecast sets;
- financial close checks with explicit tolerances;
- FCFF/WACC valuation, reverse DCF and economically valid multiple cross-checks;
- EV-to-equity and diluted per-security bridge from frozen capital structure and rights;
- ValueRange by default; ValueDistribution only for calibrated parameter distributions;
- annualized shareholder-return range including dividend, buyback, dilution, exit and FX assumptions;
- answerability × direction × confidence × publication status;
- Company Model, Forecast, Valuation, Judgment & Counter-evidence workbench modules.

Required dedicated plan file before execution:

`docs/superpowers/plans/2026-08-24-investment-research-valuation-assessment.md`

Gate fixtures:

- `CATL-T1` publishes a provisional direction;
- `CATL-NOT-ANSWERABLE` publishes direction and confidence as null;
- a WACC change is attributed separately from an InvestmentMandate hurdle change;
- a reconciliation error above one basis point blocks publication;
- no API or page exposes target price, position or order fields.

## 7. Increment D — Updates, changes, memo and local operations

Deliver:

- user-triggered update check, manual import and explicitly enabled local schedule;
- durable/idempotent task lifecycle and structured retry;
- UpdateCandidate triage with accept, partial accept, reject and defer;
- accepted changes update WorkspaceDraft but never auto-publish;
- semantic ChangeSet across fact, mechanism, forecast, value, price, uncertainty and assessment;
- price/FX-only MarketMark without automatic ResearchRevision;
- Web, Markdown and PDF memo generation from one canonical manifest;
- local file-store backup/restore, checksum validation and secret exclusion;
- Versions & Changes and Research Memo workbench modules.

Required dedicated plan file before execution:

`docs/superpowers/plans/2026-08-24-investment-research-updates-memo.md`

Gate fixtures:

- `CATL-T2` changes at least one fact, mechanism, forecast and assessment;
- `CATL-PRICE-ONLY` leaves the T1 manifest unchanged;
- retrying one update job creates no duplicate formal row;
- Web/Markdown/PDF version ID, key numbers, citation set and assessment match;
- restored T1/T2 manifests equal their pre-backup hashes.

## 8. Increment E — Alphabet reuse and release

Deliver:

- SEC filing, US market price and USD/CNY FX adapters;
- Company → GOOGL/GOOG identities with time-bounded SecurityRightsVersion;
- US GAAP/fiscal-calendar normalization behind the same ModelInput contract;
- advertising, Cloud and AI-capex mechanisms as adapter/template additions, not copied services;
- per-security value and price comparison;
- complete `/research` flow and regression suite for CATL and Alphabet.

Required dedicated plan file before execution:

`docs/superpowers/plans/2026-08-24-investment-research-alphabet-release.md`

Release gate:

- all five PRD 17.4 fixtures pass;
- core service duplication count is zero;
- legacy archives replay unchanged;
- all immutable PostgreSQL tables reject UPDATE/DELETE;
- one-click runtime starts without commercial data or AI credentials and reports missing optional capabilities honestly;
- no page or export contains a deterministic buy/sell instruction, position size or single-point target price.

## 9. Cross-cutting delivery rules

1. Every behavior starts with a failing test and a focused RED command.
2. Every migration has SQLite metadata coverage, PostgreSQL migration coverage and UPDATE/DELETE trigger coverage where the table is immutable.
3. Every write API accepts an idempotency key or optimistic-lock version.
4. Every generated OpenAPI change is regenerated once, typechecked and committed with its producer code.
5. The existing unstaged `frontend/openapi.json` user change must be fingerprinted before generation and restored outside feature commits until the user resolves it.
6. No formal reader queries “latest” child state; every selected ResearchRevision resolves only its manifest references.
7. WorkspaceDraft is the only mutable product aggregate. Mutation is guarded by `expected_lock_version`.
8. Product routes and frontend features must not import legacy Event Research, fund, tenant or simulated-position services.
9. Each increment ends with a real CATL path, not only unit tests or mock UI.
10. Commits remain task-sized; migrations, generated contracts and UI are not bundled into one release commit.

## 10. PRD traceability

| PRD requirement | Owning increment | Release evidence |
|---|---|---|
| Product goals, users, object-first flow | A | CATL/Alphabet identity search and project setup tests |
| Unified language and orthogonal classification | A defines contracts; B adopts them | Domain enum tests and formal ModelInput coverage |
| Independent HistoricalBasis and market snapshots | A | price-after-cutoff test and manifest references |
| Independent shell and nine-module workbench | A shell; B–D fill modules | Route isolation plus per-module UI tests |
| Sources, statements, gaps and AI proposal boundary | B | extraction/adoption/conflict/gap fixtures |
| Industry mechanisms and company transmission | B–C | formalization and financial-mapping tests |
| Forecast, DCF, reverse DCF and return range | C | CATL-T1 valuation golden fixture |
| Answerability, provisional direction and counter-evidence | C | CATL-T1 and CATL-NOT-ANSWERABLE |
| Immutable updates, ChangeSet and MarketMark | D | CATL-T2 and CATL-PRICE-ONLY |
| Web, Markdown and PDF memo consistency | D | canonical-manifest export comparison |
| Local one-click, backup and restore | A foundation; D hardening | runtime smoke and restored manifest equality |
| Alphabet multi-Security reuse | A identity seam; E complete case | ALPHABET-IDENTITY and release gate |
| Product metrics | A instruments integrity counters; B–E add workflow measures | metrics table with numerator/denominator/window |

## 11. Program completion

- [ ] Increment A gate passes and the foundation plan completion record is committed.
- [ ] Increment B gate passes with governed CATL industry inputs.
- [ ] Increment C gate passes with CATL-T1 and CATL-NOT-ANSWERABLE.
- [ ] Increment D gate passes with CATL-T2, CATL-PRICE-ONLY and memo/restore equality.
- [ ] Increment E gate passes with GOOGL/GOOG identity and cross-market reuse.
- [ ] The eight PRD product metrics have recorded numerator, denominator, window and first-version result.
- [ ] The selected frozen revision remains semantically identical after restart, restore and later-data import.
- [ ] The old archive and old published revisions remain readable and hash-valid.
