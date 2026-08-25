# CATL 2024 frozen economic-model basis

This fixture is a historical research basis, frozen at `2025-05-15T15:59:59Z`
(`2025-05-15 23:59:59+08:00`). It is **not** current CATL research, an
investment recommendation, a valuation, or a price forecast.

## Evidence boundary

The manifest records four public, high-authority sources. The CNINFO 2024
annual-report PDF was retrieved once and verified against SHA-256
`b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad`.
Copyrighted documents are not committed: the fixture retains locator, hash,
policy metadata, and derived observations only. The issuer filing is
authorized for derived observations; public IEA and government-preserved pages
are retained under their recorded display/reference policies.

`reported` observations are direct annual-report values, converted from CNY
thousands to CNY where stated. The published China 2024 power-battery
installation statistic is recorded as official-industry evidence: 548.4 GWh,
available on 2025-01-20, the actual post date of the government-preserved
page. Its body attributes the underlying alliance release to 2025-01-13, but
that claim is not used as this source's availability time.
`segment.other.cost` is deliberately not labelled as reported: it is the
explicit residual `company.cost_of_revenue -
sum(named_segment_costs)`, with every parent observation preserved. No
unverified capacity, utilization,
price, cost-index, or overseas-share value is invented. Those unresolved
baselines appear as `unknown`, with a source locator and reason, and are never
passed to the authenticated numerical observation batch.

## Reconciliation and limits

Segment revenue may differ from reported company revenue by CNY 1,000 because
the published CNY-thousand table rounds displayed rows. That tolerance is
intentional and must be surfaced, never silently rounded away. Operating
expenses, depreciation, working capital allocation, utilization, comparable
capacity, price, and material cost series remain unresolved gaps. The six
mechanisms are candidates only: their mappings, alternatives, falsifiers, and
source references make the missing proof explicit; they are not formal causal
claims until sequential independent review promotes them.

Later disclosures (including the 2025 half-year report) and any source first
available after the cutoff are excluded. To refresh research, make a new
historical basis rather than altering this fixture.

## Evidence-only publication boundary

`CatlBaselineService` deliberately publishes this fixture as
`catl_economic_model_evidence_only`. It persists the frozen observations and
candidate mechanisms, but does **not** invent the missing capacity,
utilization, price, cost, or review-confirmation inputs merely to create an
industry state, formal mechanism, scenario, or earnings engine. The resulting
answerability record names `missing_key_baseline` and
`mechanism_unidentified`, and its actual eligible action is
`wait_for_validation`; it is a research observation boundary, never an entry
signal. This is a deliberate variance from the original full-model happy
path, and prevents the fixture from claiming a completed causal model it does
not evidence.
