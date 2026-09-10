# Company financial model drafts

The approved company workbench already contains a forecast and valuation surface. Continue there by making the next input package concrete: authenticated disclosed financial baselines, editable five-year assumptions, deterministic scenario calculations, and immutable saved model drafts attached to the existing frozen research version.

## Findings and decision

The current 30 blockers represent two checks of the same 15 unavailable operating metrics. Several metrics are not independently disclosed. The existing six financial forecast paths are a separate machine-candidate fixture; confirming historical facts does not authenticate those forecasts. Do not fabricate operating metrics, weaken the old research gate, or overwrite the already published report.

Implement a model draft extension in the existing seven-page workbench. This increment ends with a reviewable, persisted conditional calculation and export. Its status is always `unreviewed`; it is not a second formal research publication, an investment recommendation, or human-confirmed evidence. A later formal publication must explicitly reconcile the new input package and the unresolved business uncertainties with the existing judgment contract. Saving a model draft does not make that judgment implicitly.

## Data and calculation

- Pin FY2025 and H1/Q2 2026 official sources before the parent's 2026-08-25 cutoff. Verify raw hashes, exact extraction and derived formulas. Preserve periods, units, group/segment scope, zero repurchases, negative FCF, and SBC definitions.
- Historical company FCF is CFO minus capex. Scenario FCFF is EBIT times one minus assumed operating cash tax rate, plus P&E depreciation, minus capex and modeled operating working-capital investment. Historical FCF does not silently replace FCFF.
- Three independent five-year group paths cover revenue, operating margin, cash tax rate, depreciation, capex and working-capital change. All forward values remain assumptions with reasons. Revenue is a group path; Cloud margin is never used as group margin.
- Explicit FCFF discount-rate assumption is separate from the mandate's shareholder return objective. Midyear valuation discounts only the assumed remaining fraction of first-year forecast cash flow; explain the uniform within-year allocation approximation and retain its editable fraction.
- Terminal FCFF is normalized from final-year NOPAT, growth and terminal ROIC: terminal NOPAT × (1 − growth/ROIC). This explicitly funds perpetual growth and avoids mechanically perpetuating a tapering capex path. Terminal growth must be below discount rate and terminal ROIC.
- Reuse authenticated existing capital, security and FX snapshots. Show value/price gap as such, never as annualized future return. Keep SBC in operating expenses and do not add it back. Historical diluted weighted-average shares are an explicit approximation, not a forecast of shares outstanding.
- Missing or unsupported inputs fail validation. Unknown granular operating metrics remain visible research gaps even when a conditional calculation can run.

## Persistence and interface

New append-only model draft records bind project, frozen parent revision/manifest, cutoff, baseline, market snapshot, exact inputs and deterministic results. Use expected latest ID for concurrent edits and Idempotency-Key for safe retries. Read/export re-authenticate the saved envelope and recompute its results. No new LLM calls, source capture jobs or mutation of the old preparation/artifact heads.

The forecast page shows financial baselines and sources, three scenario tabs, editable paths and rationales, discount/terminal/timing assumptions, save-and-calculate, and saved draft history. The valuation page shows saved conditional results and links back to assumptions. Original frozen report, formal assessment and replay remain unchanged and clearly identified.

## Acceptance

Real source verification, independent numerical checks, invalid/stale/tampered write and replay checks, project isolation, user-input preservation on failure, frontend build, and live browser save/reload/export. Verify the original frozen export hash is unchanged. Do not claim the whole investment decision workflow is complete merely because conditional values exist.
