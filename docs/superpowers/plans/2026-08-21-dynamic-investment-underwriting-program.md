# Dynamic Investment Underwriting Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a production-shaped, historically replayable investment-underwriting system that proves research depth, memory, reuse, and decision usefulness through a CATL vertical slice and an Alphabet reuse slice.

**Architecture:** Add a new `underwriting` bounded context that shares only the repository's FastAPI, SQLAlchemy, PostgreSQL, React, Vite, and test infrastructure. It owns a separate API prefix, append-only tables, domain language, frontend entry, and fixtures; no Underwriting module may import legacy ResearchCase, Event Research, Theme, fund-holding, or automatic-research domain services. Delivery is split into five gated waves so each wave produces testable software and the next wave cannot hide a weak foundation with more UI or content.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL/SQLite tests, React 18, TypeScript, Vite, Vitest, Testing Library, Playwright.

---

## 1. Non-negotiable delivery rules

- Every write is append-only; corrections append successors.
- Every formal view is reconstructed from a `HistoricalBasis` and rejects future information.
- Industry, Company, and Security identities are separate.
- No price conclusion is allowed without an `InvestmentMandate` and an answerable basis.
- `not_answerable` is a knowledge state, not a bearish conclusion.
- No single target price, AI composite score, forced price attribution, automatic order, or position sizing enters the first release.
- CATL demonstrates depth and longitudinal change; Alphabet demonstrates reuse. Alphabet may add mechanism packs and valuation adapters, but not a new kernel, ledger, version type, or page architecture.
- Each wave ends with a frozen demo basis, contract tests, failure tests, and a commit. A later wave starts only after the prior gate passes.

## 2. Bounded-context ownership

### Backend

- `backend/app/underwriting/domain/`: controlled vocabulary, pure policies, state machines, and financial equations.
- `backend/app/underwriting/persistence/`: append-only SQLAlchemy models and repositories.
- `backend/app/underwriting/services/`: application orchestration; the only layer allowed to combine ledgers.
- `backend/app/underwriting/api/`: `/api/underwriting/v1` schemas and routes.
- `backend/app/underwriting/fixtures/`: frozen source manifests and deterministic CATL/Alphabet research inputs.
- `backend/tests/underwriting/`: unit, persistence, API, historical replay, financial reconciliation, and cross-industry contract tests.

### Frontend

- `frontend/src/underwriting/domain/`: wire-independent TypeScript types and presentation policies.
- `frontend/src/underwriting/data/`: HTTP client and frozen development adapter.
- `frontend/src/underwriting/app/`: standalone router and shell.
- `frontend/src/underwriting/features/`: mandate, industry, company, valuation, change, and calibration views.
- `frontend/src/underwriting/styles/`: tokens and responsive styles derived from the approved prototype.
- `frontend/src/underwriting/tests/`: component and adapter tests.
- `frontend/e2e/underwriting.spec.ts`: end-to-end acceptance for desktop and mobile.

### Shared infrastructure allowed

- Database session construction and error envelope.
- Source-byte storage and hash utilities after an adapter boundary is introduced.
- Build, test, OpenAPI generation, request IDs, and local runtime scripts.

### Imports forbidden inside `app.underwriting`

```text
app.domain.event_research
app.services.auto_research
app.services.automatic_research_*
app.models.event_research
app.queries.event_research
app.services.fund_disclosure_sync
```

The isolation contract also rejects public symbols containing `ResearchCase`, `EventResearch`, `ThemeRole`, `FundDisclosure`, or `AutomaticResearch`.

## 3. Delivery dependency graph

```text
Wave 1 Trusted Kernel
  └── Wave 2 CATL Economic Model
        └── Wave 3 Valuation + Decision Workbench
              └── Wave 4 Change + Calibration
                    └── Wave 5 Alphabet Reuse + Release Gate
```

Parallel work is allowed only within a wave when tasks do not share schema or contracts. Database schema, domain types, API DTOs, and frontend contracts remain sequential seams.

## 4. Wave 1 — Trusted kernel

**Executable plan:** `docs/superpowers/plans/2026-08-21-underwriting-kernel-foundation.md`

**Outcome:** A new research object can be identified, assigned an investment mandate and historical basis, receive typed entries in four ledgers, be evaluated by the answerability gate, and be replayed without future leakage.

**Artifacts:**

- Controlled domain enums and immutable value objects.
- Separate Industry / Company / Security identity registry.
- Versioned InvestmentMandate and HistoricalBasis.
- Reality / Belief / Decision / Calibration append-only entries.
- ResearchVersion and optimistic successor contract.
- AnswerabilityGate with seven blocker codes.
- `/api/underwriting/v1` command and snapshot endpoints.
- PostgreSQL append-only triggers plus SQLite test enforcement.

**Gate:**

- [ ] Import-isolation test passes.
- [ ] UPDATE and DELETE fail at application and PostgreSQL layers.
- [ ] A basis at `T1` cannot see an entry available at `T2`.
- [ ] Two company securities share Company identity but retain independent Security identity.
- [ ] `not_answerable` cannot produce either entry-eligible action.
- [ ] Correcting an entry preserves the original and creates a valid `supersedes` link.

## 5. Wave 2 — CATL economic model

**Planned outcome:** A frozen real-data basis explains the power-battery industry and how CATL earns revenue, profit, free cash flow, and value before any price judgment appears.

**Actual Wave 2 record (evidence-only boundary):** The implemented CATL 2024
basis freezes authentic accessible source metadata and known annual-report
anchors, records explicit `Unknown` industry/capacity gaps, and persists six
candidate mechanisms with provenance. It publishes a replayable historical
research version as `not_answerable`, with `missing_key_baseline` and
`mechanism_unidentified` blockers and the only allowed action
`wait_for_validation`. This is intentionally not a completed industry state,
scenario set, company exposure model, earnings engine, formal mechanism set,
valuation, price judgment, or recommendation. The boundary prevents an
apparently deep but unsupported model from becoming an investment claim.

**Primary files:**

- Create `backend/app/underwriting/domain/metrics.py`: versioned metric definitions, units, periods, and reconciliation rules.
- Create `backend/app/underwriting/domain/mechanisms.py`: MechanismPack lifecycle and company overrides.
- Create `backend/app/underwriting/domain/industry.py`: IndustryState and ScenarioSet contracts.
- Create `backend/app/underwriting/domain/earnings.py`: segment-level EarningsEngine and four core classifications.
- Create `backend/app/underwriting/persistence/research_models.py`: mechanism, industry-state, earnings-engine, forecast-input, and falsifier versions.
- Create `backend/app/underwriting/services/source_freeze.py`: authorized frozen-source manifest ingestion.
- Create `backend/app/underwriting/services/source_policy.py`: source authorization, display/export rights, provider capability, retention, and reproducibility gates.
- Create `backend/app/underwriting/services/industry_state.py`: effective-capacity, utilization, price, cost-curve, and profit-pool orchestration.
- Create `backend/app/underwriting/services/earnings_engine.py`: segment financial bridge and balance checks.
- Create `backend/app/underwriting/fixtures/catl_baseline/manifest.json`: source hash, publication time, first-available time, authority, locator, and display policy.
- Create `backend/app/underwriting/fixtures/catl_baseline/observations.json`: normalized observations linked to manifest source IDs.
- Create `backend/app/underwriting/fixtures/catl_baseline/mechanisms.json`: three-to-five candidate mechanisms and at least one counter-model.
- Create `backend/tests/underwriting/test_catl_industry_state.py`.
- Create `backend/tests/underwriting/test_catl_earnings_engine.py`.
- Create `backend/tests/underwriting/test_catl_source_traceability.py`.

**Required mechanisms:**

1. EV and storage end-demand to battery shipment.
2. Nominal capacity to effective capacity, utilization, inventory, and pricing.
3. Material cost pass-through to cell ASP and unit margin.
4. Customer certification and overseas production to obtainable share and profitability.
5. Capital expenditure, depreciation, and working capital to free cash flow.

Every MechanismPack moves through the controlled lifecycle `candidate → adapted → calibrated → human_confirmed → formal → challenged → retired_or_replaced`. Only `formal` packs may affect forecasts or valuation.

**Gate:**

- [ ] Every formal metric has definition, unit, period semantics, source role, observed period, and available time.
- [ ] Nominal capacity is never substituted for effective capacity.
- [ ] Revenue, operating profit, capital expenditure, working capital, tax, and free cash flow reconcile within declared tolerance.
- [ ] RevenueCore, ProfitCore, CashCore, and ValueCore are calculated independently.
- [ ] Each formal mechanism has direction, lag, range, applicability, alternative explanation, and falsifier.
- [ ] Missing a key baseline shortens the result and returns `not_answerable`.
- [ ] Source authorization, provider capability, metric-definition changes, and unresolved source conflicts are visible and fail closed.

**Wave 2 completion evidence (limited scope):** migrations `0060`/`0061`
persist append-only research/economic artifacts; the CATL snapshot API exposes
the persisted evidence, gaps, candidate mechanisms, and blockers without
loading fixture data at read time. Source and observation freezing reject
future-available inputs and unauthenticated/altered fixture artifacts. The
remaining unchecked items above are deliberately deferred, not implicitly
satisfied by the evidence-only release.

## 6. Wave 3 — Valuation and decision workbench

**Outcome:** The same CATL basis produces an `ExpectationSurface`, House `ValueDistribution`, three-to-five-year return distribution, price/value/thesis/uncertainty differential, and exactly one eligible no-holding action.

**Primary files:**

- Create `backend/app/underwriting/domain/forecast.py`: Actual / Guidance / Consensus / Implied / House / Scenario / Estimate separation.
- Create `backend/app/underwriting/domain/valuation.py`: cycle-normalized DCF adapter and expectation-surface solver.
- Create `backend/app/underwriting/domain/underwriting.py`: ThesisHealth, PriceState, ResearchDebt, PayoffShape, and EligibleAction policy.
- Create `backend/app/underwriting/services/forecasting.py`.
- Create `backend/app/underwriting/services/valuation.py`.
- Create `backend/app/underwriting/services/underwriting.py`.
- Create `backend/tests/underwriting/test_expectation_surface.py`.
- Create `backend/tests/underwriting/test_value_distribution.py`.
- Create `backend/tests/underwriting/test_underwriting_policy.py`.
- Create `frontend/underwriting.html`: independent Vite entry.
- Create `frontend/src/underwriting/main.tsx` and `frontend/src/underwriting/app/UnderwritingRoutes.tsx`.
- Create `frontend/src/underwriting/features/ResearchEntryPage.tsx`.
- Create `frontend/src/underwriting/features/MandateSetupPage.tsx`.
- Create `frontend/src/underwriting/features/CompanyWorkbenchPage.tsx`.
- Create `frontend/src/underwriting/features/IndustryWorkbenchPage.tsx`.
- Create `frontend/src/underwriting/features/ValuationPanel.tsx`.
- Create `frontend/src/underwriting/features/AnswerabilityPanel.tsx`.

**Gate:**

- [ ] The expectation-surface solver returns multiple materially distinct price-consistent operating combinations or marks the inference unidentifiable.
- [ ] ValueDistribution comes from internally consistent scenarios, not percentage offsets from a target price.
- [ ] PE and PB appear only with economic justification and are never sufficient evidence of cheapness.
- [ ] The action policy is deterministic, has reason codes, and cannot bypass mandate or answerability.
- [ ] The page defaults to current judgment, price requirements, strongest counter-evidence, blockers, and next verification; details progressively disclose.
- [ ] Desktop and 390-pixel views have no horizontal overflow and preserve evidence/source boundaries.
- [ ] Keyboard, focus, contrast, status text/shape, reduced-motion, and structured alternatives meet the agreed WCAG 2.2 AA baseline.

## 7. Wave 4 — Change and calibration

**Outcome:** A second frozen CATL basis shows exactly what changed and scores the prior research process without rewriting history.

**Primary files:**

- Create `backend/app/underwriting/domain/change.py`: typed fact, mechanism, forecast, value, price, uncertainty, debt, and action deltas.
- Create `backend/app/underwriting/domain/calibration.py`: forecast, behavior, and model error classifications.
- Create `backend/app/underwriting/services/change_set.py`.
- Create `backend/app/underwriting/services/calibration.py`.
- Create `backend/app/underwriting/fixtures/catl_update_1/manifest.json`.
- Create `backend/app/underwriting/fixtures/catl_update_1/observations.json`.
- Create `backend/tests/underwriting/test_catl_historical_replay.py`.
- Create `backend/tests/underwriting/test_catl_change_set.py`.
- Create `backend/tests/underwriting/test_catl_calibration.py`.
- Create `backend/tests/underwriting/test_industry_company_propagation.py`.
- Create `frontend/src/underwriting/features/ChangeSetPage.tsx`.
- Create `frontend/src/underwriting/features/CalibrationPage.tsx`.

**Gate:**

- [ ] T1 replay remains byte-for-byte stable after T2 ingestion.
- [ ] `DeltaPrice`, `DeltaValueDistribution`, `DeltaUncertainty`, and `DeltaThesisHealth` are shown separately.
- [ ] Extending a falsifier deadline creates a successor and a behavioral calibration event.
- [ ] Corrections append; no old source, belief, forecast, value, or action is overwritten.
- [ ] ChangeSet explains why EligibleAction changed or explicitly records that it did not.
- [ ] Industry changes create company review impacts only; they cannot mutate a Company forecast or action without a confirmed successor.
- [ ] Price attribution remains `identified`, `model_inferred`, or `unexplained`; unexplained residual is preserved and attribution is never forced to 100%.

## 8. Wave 5 — Alphabet reuse and release gate

**Outcome:** Alphabet and GOOGL/GOOG run on the same kernel and page architecture while adopting different mechanisms and valuation adapters.

**Primary files:**

- Create `backend/app/underwriting/fixtures/alphabet_baseline/manifest.json`.
- Create `backend/app/underwriting/fixtures/alphabet_baseline/observations.json`.
- Create `backend/app/underwriting/fixtures/alphabet_baseline/mechanisms.json`.
- Create `backend/app/underwriting/domain/adapters/two_sided_market.py`.
- Create `backend/app/underwriting/domain/adapters/cloud_scale.py`.
- Create `backend/app/underwriting/domain/adapters/ai_capital_intensity.py`.
- Create `backend/app/underwriting/domain/adapters/platform_regulation.py`.
- Create `backend/app/underwriting/domain/adapters/sotp_dcf.py`.
- Create `backend/tests/underwriting/test_alphabet_reuse_contract.py`.
- Create `backend/tests/underwriting/test_company_security_separation.py`.
- Create `backend/scripts/verify_underwriting_release.py`.
- Create `frontend/e2e/underwriting.spec.ts`.
- Modify `docs/integration/frontend-api-binding.md`: add only the separate Underwriting API and screen contract.

**Gate:**

- [ ] An AST contract proves Alphabet adds no kernel, ledger, version, or page type.
- [ ] GOOGL and GOOG share one Company model and have separate Security price/return projections.
- [ ] CATL and Alphabet both pass traceability, replay, answerability, financial reconciliation, and UI acceptance.
- [ ] Release verification fails closed when source authorization, source time, model balance, price, or mechanism applicability is removed.
- [ ] Full backend, frontend, build, and Playwright suites pass twice from a clean checkout.

## 9. Program checkpoints

At the end of each wave, publish one evidence-backed checkpoint containing:

- commit SHA and migration head;
- exact test commands and pass counts;
- frozen data cutoff and manifest hash;
- known ResearchDebt and AnswerabilityState;
- schema/API changes;
- screenshots for user-facing changes;
- explicit list of design requirements not yet activated.

### Wave 2 checkpoint — evidence-only CATL baseline

- **Scope completed:** source/observation/mechanism freezing, derived-metric
  lineage, immutable persistence, replayable evidence-only CATL API, and
  answerability blockers.
- **Deliberate limitations:** no formal industry state or scenarios, exposure,
  earnings reconciliation, valuation, price/return projection, action other
  than `wait_for_validation`, or Alphabet reuse.
- **Next-wave boundary:** obtain and authenticate the missing capacity/industry
  baselines, independently review mechanisms to `formal`, then build balanced
  industry/earnings models before enabling any valuation or decision policy.

No checkpoint may use “complete research” while any key mechanism or baseline is `not_answerable`.

## 10. Definition of program completion

The program is complete only when one user can:

1. open the independent Underwriting product without seeing legacy Event Research workflow;
2. inspect how the power-battery industry profit pool works;
3. inspect how CATL earns revenue, profit, cash flow, and value by segment;
4. see multiple assumptions consistent with 300750.SZ's frozen current price;
5. compare those assumptions with House scenarios and an explicit required return;
6. receive an evidence-permitted no-holding action rather than a trade instruction;
7. replay a prior basis and understand every material change;
8. inspect forecast, behavior, and model calibration;
9. open Alphabet and obtain the same workflow without duplicated research architecture;
10. observe honest failure closure when the system cannot support a judgment.

## 11. Design-to-delivery traceability

| Confirmed design section | Delivery owner |
|---|---|
| 1–2 conclusion and constitution | Program rules + Wave 5 release gate |
| 3 users and no-holding boundary | Waves 1 and 3 mandate/action contracts |
| 4 three clocks | Wave 3 forecast, valuation, and UI policies |
| 5 Industry / Company / Security | Waves 1 identity and 2 economic models |
| 6 four ledgers | Wave 1 append-only kernel |
| 7 mechanism compiler | Wave 2 MechanismPack lifecycle |
| 8 new-information update protocol | Waves 2 source freeze and 4 ChangeSet |
| 9 earnings and valuation adapters | Waves 2, 3, and 5 |
| 10 four-dimensional underwriting | Wave 3 |
| 11 AnswerabilityGate | Wave 1, enforced again by every later gate |
| 12 versions and replay | Waves 1 and 4 |
| 13 progressive user experience | Wave 3 and Wave 4 change/calibration views |
| 14 CATL vertical slice | Waves 2–4 |
| 15 Alphabet reuse slice | Wave 5 |
| 16 component boundaries | Bounded-context ownership + all wave file maps |
| 17 errors and exceptions | Wave 1 failure contracts, Wave 2 source/model failures, Wave 3 price failures |
| 18 acceptance | Per-wave gates + Wave 5 release verifier |
| 19 implementation stages | Five waves in this plan |
| 20 risks and controls | Answerability, source policy, calibration, lifecycle, and progressive disclosure gates |
| 21 success definition | Program completion checklist above |

No confirmed design section is deferred outside the five-wave program. What is deferred is implementation detail for a later wave until its predecessor freezes the interfaces that detail depends on.
