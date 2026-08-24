# Dynamic Investment Underwriting Context

This context belongs to the independent investment-research product under `prototype/investment-research-prototype`. It does not reuse the root Fund Engine product language or make its legacy workflow visible to users.

## Research identity

**ResearchObject**:
The economic object being studied. It is exactly one `Industry`, `Company`, or `Security`; the three are related but never interchangeable.
_Avoid_: Topic, ticker as company, industry tag

**Industry**:
A bounded economic system whose demand, effective supply, pricing, cost curve, competition, technology, regulation, and profit pool can be studied independently and versioned over time.
_Avoid_: Background chapter, theme label, macro narrative

**Company**:
The operating entity that combines business segments, assets, liabilities, capital allocation, and industry exposures to create or destroy cash value.
_Avoid_: Stock, ticker, security price

**Security**:
A priced claim on a Company with its own share class, exchange, currency, economic rights, liquidity, and market price. Time-varying company capital structure is not a static Security attribute.
_Avoid_: Company, business value

**InvestmentMandate**:
The investor's declared horizon, base currency, required return, permanent-loss tolerance, and comparison set. It defines the boundary for underwriting attractiveness without requiring a real holding.
_Avoid_: Portfolio, risk-profile questionnaire, style label

**ResearchProject**:
A persistent company-centered research record that relates one primary Company to its target Securities, Industries, drafts, and immutable revisions.
_Avoid_: Research run, report, Industry-only project

**ResearchScope**:
The declared Company, target Securities, related Industries, covered segments, focus, and exclusions for one ResearchProject.
_Avoid_: InvestmentMandate, research question

**HistoricalBasis**:
The frozen source manifest, cutoff, metric definitions, and parsing identities that determine what research information was available. It never contains a market-price timestamp.
_Avoid_: PriceSnapshot, latest database state

**RevisionBoundary**:
The complete boundary for one published revision, combining HistoricalBasis, market snapshots, model definitions, InvestmentMandate, and the parent revision.
_Avoid_: HistoricalBasis alone, current state

**PriceSnapshot**:
An immutable Security price observation with market time, currency, source, availability time, and corporate-action basis.
_Avoid_: Live quote, HistoricalBasis

**CapitalStructureSnapshot**:
The time-bounded cash, debt, minority-interest, diluted-share, and other bridge inputs needed to move from enterprise value to per-Security value.
_Avoid_: Security identity, timeless share count

## Economic understanding

**EarningsEngine**:
The versioned explanation of how each business segment obtains customers, monetizes demand, incurs costs, consumes capital, produces free cash flow, and contributes to company value.
_Avoid_: Business overview, revenue-only segment table

**RevenueCore**:
The segment or activity that contributes the largest relevant revenue base.

**ProfitCore**:
The segment or activity that contributes the largest relevant operating profit.

**CashCore**:
The segment or activity that contributes the largest sustainable free cash flow.

**ValueCore**:
The segment or activity that contributes the largest part of the company's underwritten value distribution.

**MechanismPack**:
A reusable, governed economic mechanism with drivers, metric definitions, financial mappings, lags, applicability conditions, alternatives, and falsifiers. Companies adopt and override versioned packs; they do not inherit conclusions.
_Avoid_: Report template, industry conclusion, keyword factor

**IndustryStateVersion**:
An immutable time-bounded snapshot of an Industry's demand, effective supply, pricing, cost curve, competition, technology, regulation, cycle state, and profit pool.
_Avoid_: Latest industry view, static industry report

## Knowledge and uncertainty

**RealityLedger**:
The append-only record of observations, source statements, derived metrics, provenance, effective time, publication time, conflicts, and unknowns.
_Avoid_: Fact database without source or time

**BeliefLedger**:
The append-only record of claims, mechanisms, alternative explanations, falsifiers, research judgment, and superseding belief versions.
_Avoid_: Editable conclusion, AI confidence score

**DecisionLedger**:
The append-only record of market-implied expectation surfaces, House forecasts, value distributions, research debt, eligible actions, and action conditions at a frozen basis.
_Avoid_: Recommendation feed, trade log

**CalibrationLedger**:
The append-only comparison of prior forecasts and falsifiers with later actual outcomes, including forecast, behavioral, and model errors.
_Avoid_: Performance score without historical basis

**ResearchDebt**:
A versioned collection of unresolved ResearchGaps that constrains a research assessment.
_Avoid_: Unread-document count, generic uncertainty score, one missing value

**ResearchGap**:
One decision-relevant missing, conflicting, stale, single-source, unverified, unauthorized, or unidentifiable dependency. It states what it affects, whether it blocks a direction, and how and when it can be resolved.
_Avoid_: Evidence, zero value, generic todo

**AnswerabilityState**:
The controlled result of the answerability gate: `answerable`, `partially_answerable`, or `not_answerable`. `not_answerable` prevents a price judgment and any entry-eligible action.
_Avoid_: Low confidence conclusion

## Price and decision

**ExpectationSurface**:
The set of materially different operating-assumption combinations consistent with a Security's current price under declared valuation boundaries.
_Avoid_: The market's single implied forecast

**ValueRange**:
An unweighted envelope of internally consistent company or Security values generated by distinct mechanisms and scenarios.
_Avoid_: Probability distribution, target price, mechanical percentage offsets

**ValueDistribution**:
A probability distribution of company or Security values whose parameter distributions have been calibrated and frozen.
_Avoid_: Unweighted Bull/Base/Bear range, target price

**ResearchAssessmentVersion**:
An immutable user-frozen assessment with orthogonal answerability, provisional direction, confidence, publication status, valuation, ResearchDebt, counter-evidence, and validation conditions. `not_answerable` has no direction or confidence.
_Avoid_: Confirmed truth, buy rating, EligibleAction, automatic recommendation

**WorkspaceDraft**:
The editable, auto-saveable research workspace that has not passed publication gates and cannot be presented as a formal revision.
_Avoid_: ResearchRevision, published result

**ResearchRevision**:
An immutable, atomically published manifest of one complete RevisionBoundary and its frozen evidence, models, valuation, assessment, and memo references.
_Avoid_: Latest child versions, autosave, partial publication

**MarketMark**:
A new price or FX observation against an unchanged research basis that does not by itself create a ResearchRevision.
_Avoid_: Research update, overwritten valuation

**ChangeSet**:
The typed difference between two ResearchRevisions, separating fact, mechanism, forecast, value, price, uncertainty, ResearchDebt, and assessment changes.
_Avoid_: New report, generic activity log

## Time

**BusinessClock**:
The three-to-five-year horizon for value creation and competitive economics.

**ExpectationClock**:
The two-to-six-quarter horizon for forecast disagreement and evidence validation.

**PriceClock**:
The current-to-months horizon for entry conditions and market price, never a standalone short-term forecasting mandate.
