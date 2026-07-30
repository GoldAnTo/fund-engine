# AI-Compute Release Gate

The release gate (`backend/scripts/verify_ai_compute_slice.py`) runs six
explicit checks against the seeded AI-compute evidence ledger to verify that
the vertical slice is auditable end-to-end before a release is cut.

## Running

```bash
cd backend
python scripts/verify_ai_compute_slice.py
```

The script reads `DATABASE_URL` (default `sqlite:///./evidence_gate.db`),
recreates the schema, seeds the frozen slice, runs all checks, and writes a
JSON result to `docs/evaluation/runs/<timestamp>.json` (never overwriting a
prior result).  Exit code is `0` on pass, `1` on fail.

## Checks

### 1. `document_versions_present`

**Definition:** At least six frozen `DocumentVersion` records exist in the
ledger, confirming that the minimum set of source documents has been ingested.

**Pass condition:** `count(DocumentVersion) >= 6`

**Failure example:** Only 3 document versions were seeded because two fixture
files failed to parse; the check reports `insufficient_document_versions`.

---

### 2. `assessment_source_spans_complete`

**Definition:** Every `AIAssessment` can be traced back through the full chain
`AIAssessment -> EvidenceSnapshot -> EvidenceLink -> SourceStatement ->
SourceSpan` without a broken link.  No assessment conclusion should exist that
cannot be drilled down to the exact verbatim source text.

**Pass condition:** For every assessment, every evidence link in its snapshot
resolves to a statement that resolves to a span with non-empty verbatim text.

**Failure example:** A `SourceSpan` was deleted (simulated in tests by a raw
DBAPI `DELETE` that bypasses the append-only guard).  The check reports
`untraceable_assessment: assessment <id> -> span <id> missing`.

---

### 3. `holding_disclosures_dated`

**Definition:** Every `HoldingDisclosure` carries both a `report_period` (the
period the holding report covers) and a `published_at` (when the report was
publicly available).  Both dates are required for point-in-time exposure
calculations.

**Pass condition:** `report_period IS NOT NULL AND published_at IS NOT NULL`
for every disclosure.

**Failure example:** A disclosure was inserted with `published_at = NULL`
(simulated in tests by recreating the table with a nullable column and
inserting via raw DBAPI).  The check reports
`undated_disclosure: <id> published_at is None`.

---

### 4. `future_material_excluded`

**Definition:** A historical cutoff excludes disclosures whose `published_at`
falls after that cutoff from both the `ExposureService` and the
`WorkbenchService`.  This verifies that point-in-time views never leak
material that was not yet public.

**Pass condition:** Using cutoff `2026-04-01` (before the 2026-04-22
disclosure publications but after the 2025-07-24 stale disclosure), no
disclosure with `published_at > cutoff` appears in any fund's exposure rows
or any case's workbench holding-disclosure rows.

**Failure example:** The exposure service's `published_at <= cutoff` filter
was removed; the check reports
`future_disclosure_visible: disclosure <id> appeared in exposure for fund <id>
at cutoff 2026-04-01`.

---

### 5. `ai_human_boundary_visible`

**Definition:** Every `AIAssessment` is explicitly marked as provisional
(`displayed_as_provisional = True`), and human reviews exist as separate
`ReviewDecision` records.  The original AI conclusion is never overwritten;
a review appends a new record that references the original assessment.

**Pass condition:** `displayed_as_provisional` is `True` for every
assessment.  If a `ReviewDecision` exists, the original assessment's
`conclusion` is still populated (the append-only design guarantees this).

**Failure example:** An assessment's `displayed_as_provisional` was set to
`False` via a raw DBAPI update.  The check reports
`assessment <id>: displayed_as_provisional is False`.

---

### 6. `projection_rebuilds`

**Definition:** The Neo4j graph projection can be fully rebuilt from the
ledger alone, and the projected node count matches the ledger row count.

**Pass condition (Neo4j available):** After `clear_projection()` +
`rebuild_all()`, `node_count("EvidenceLink")` equals the number of
`EvidenceLink` rows in the ledger.

**Skip condition (Neo4j unavailable):** When no projector is provided (the
default in environments without Neo4j), the check returns `skipped = True`
and does **not** cause the gate to fail.

**Failure example:** The projection contains 14 EvidenceLink nodes but the
ledger has 15 rows because a new projection writer skipped one entity type.
The check reports
`projection EvidenceLink count 14 != ledger 15`.

## Result format

```json
{
  "passed": true,
  "checks": [
    {
      "name": "document_versions_present",
      "passed": true,
      "evidence": { "document_version_count": 7, "minimum_required": 6 },
      "failures": []
    }
  ],
  "failures": [],
  "generated_at": "20260730T095026Z"
}
```

`failures` at the top level is a list of check **names** that failed
(excluding skipped checks).  Each check's own `failures` list contains
detailed human-readable failure descriptions.
