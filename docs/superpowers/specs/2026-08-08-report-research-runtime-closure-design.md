# Report Research Runtime Closure Design

## Purpose

Close the remaining runtime gaps between a researcher supplying a report and the system producing an auditable, revisable explanation of its market impact. This supplements the approved report-research design; it does not replace its immutable evidence, China-only asset-expression, or read-only-embed boundaries.

## User flow

1. A researcher opens **Create report research**, supplies pasted text, webpage body, or PDF and optional publisher/publication time.
2. The system freezes the source, extracts claims and relations, creates the first explicit research scope, and starts an auditable research run.
3. The workbench exposes the editable question, selected claim/relation paths, evidence plan, current gap, and historical scopes. Changing any of these appends a successor scope; it never edits an earlier scope.
4. Automatic work gathers only ledger-backed material: it calculates 1D/5D China A-share and controls from raw point-in-time observations, records source-admitted independent material, and creates a provisional, relation-specific confounder assessment. Missing inputs remain explicit gaps.
5. A scope can become a key-factor candidate only from evidence visible by its cutoff. Later material creates a successor-scope reassessment or is marked post-event validation; it cannot rewrite the historical causal result.
6. The researcher can inspect human-readable source locations, review a provisional confounder outcome, change the question/path, or start another scope. A report may remain inconclusive indefinitely without losing its history.

## Boundaries and invariants

- **ReportScope** is the immutable selection of a report document, question, paths, evidence plan, and visibility cutoff. Its editable-looking UI always appends a new scope.
- **MarketMeasure** is calculated from raw dated China-market ledger facts, not a specially preloaded `EVENT_RETURN_*` metric. It preserves all raw snapshot references and uses the report publication time plus the next 1 and 5 trading days.
- **ConfounderAssessment** is relation-specific. Automatic assessment is explicitly labelled provisional/system-generated; human confirmation or override appends another assessment. An unresolved or material assessment blocks the causal key classification.
- **RelationResolution** is the append-only, source-audited decision that a name-only report relation refers to an existing Company. Market collection and Wiki asset mapping use the same effective resolution. If none exists, both keep the node as unlisted/unmapped transmission.
- **SourceAccess** converts frozen SourceSpan and snapshot identifiers into display-safe labels and authenticated, case-scoped detail routes. Original PDF bytes require the same case access policy as the internal research surface. Embed views never expose any source-access route or original file.
- **Embed integration** remains cross-origin: the browser-facing embed app uses a configured HTTP(S) API origin and hash-to-header token transport; static-host `frame-ancestors` policy restricts parent hosts.

## Error and retry behavior

- Extraction, data collection, and scope changes are idempotent under their durable run/scope keys.
- A failed data source creates an explicit insufficient observation or retryable task, never a synthetic price, fund exposure, control, or confounder outcome.
- UI failures show a targeted retry action; retry preserves the previously selected path and scope.

## Acceptance checks

- A user can create a pasted, webpage, and PDF report through the UI and reach the same workbench without hand-calling APIs.
- A user can append and inspect scope versions, including a new question/path set and a historical version.
- A real raw-market fixture yields auditable 1D/5D computed observations; a missing calendar or raw price yields only an insufficient record.
- Name-only relations either get one audited resolution used consistently by market and Wiki, or remain unmapped everywhere.
- A researcher can open an authenticated original/source detail from an internal locator; anonymous access cannot download a report original.
- An automatic relation with admitted evidence, raw market/control data, and a provisional non-material confounder assessment reaches a clearly labelled key-factor candidate; a material/unresolved confounder does not.
- The normal and embed UI both retain their current safety, mobile, and accessibility tests.
