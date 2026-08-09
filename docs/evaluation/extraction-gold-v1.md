# Report Extraction Gold v1

This gate scores atomic-claim extraction before model, prompt, table-rule, or parser changes can be released. The committed fixture at `backend/tests/fixtures/extraction_gold_v1.json` is synthetic and executable; it contains no licensed report text or customer material.

The production corpus is private and is not an optional substitute for this public fixture. It must contain 30–50 Chinese reports across selectable-text PDFs, scans/OCR, financial tables, disclosures, and licensed broker research. Each item needs two independent annotations, adjudication, source authority, exact frozen quote, character offsets, quote SHA-256, typed fields, high-impact flag, and a recorded reviewer outcome. Store it only in the licensed research environment designated by the data owner; never commit, export, or send it to an AI provider unless its source contract explicitly allows that use.

## Release thresholds

- Exact quote, start/end offset, and quote-hash rates: `1.00`.
- Duplicate candidate count: `0`.
- Disclosed-fact precision: at least `0.98`.
- Overall atomic precision: at least `0.95`.
- High-impact recall: at least `0.90`.
- Primary-authority violations: `0`.

The scorer reports claim-type confusion, numeric/unit/period/entity-field accuracy, and reviewer approval coverage as diagnostics. A high score does not bypass the human candidate-review gate or turn a licensed report into a primary disclosure.
