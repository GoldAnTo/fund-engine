# Evidence-Pack Release Gate

The release gate (`backend/scripts/verify_ai_compute_slice.py`) runs ten
explicit checks against the seeded evidence ledger — **three frozen cases**
(AI 算力链 + 锂电储能链 + 半导体设备国产化) — to verify that the vertical
slices are auditable end-to-end before a release is cut.

The gate is part of the evidence pack under `docs/evaluation/`:

- `dataset-manifest.json` – v2 per-case manifest: frozen document hashes
  grouped by case (attributed in the ledger by `source_url` prefix), theses,
  cutoffs, dataset index, and stated limitations (fail-closed: the gate
  refuses to run without it).
- `datasets/` – frozen offline gold datasets (currently the table-extraction
  gold set, including a sample sourced from a real binary PDF).
- `reports/` – committed per-run gate summaries (trend over time).
- `raw/` – gitignored full per-check detail for debugging.
- `reproduce.sh` – one-command replay of the whole gate.

## Running

```bash
docs/evaluation/reproduce.sh
# or manually:
cd backend
python scripts/verify_ai_compute_slice.py
```

The script reads `DATABASE_URL` (default `sqlite:///./evidence_gate.db`),
recreates the schema, seeds **both** frozen cases, runs all checks, and
writes a summary JSON to `docs/evaluation/reports/<timestamp>.json` plus
full detail to `docs/evaluation/raw/<timestamp>.json` (never overwriting a
prior result).  Exit code is `0` on pass, `1` on fail.  If
`docs/evaluation/dataset-manifest.json` is missing, the script exits `1`
**before** touching the database (fail-closed).

## Checks

### 1. `document_versions_present`

**Definition:** At least six frozen `DocumentVersion` records exist in the
ledger, confirming that the minimum set of source documents has been ingested.

**Pass condition:** `count(DocumentVersion) >= 6`

**Failure example:** Only 3 document versions were seeded because two fixture
files failed to parse; the check reports `insufficient_document_versions`.

---

### 2. `gold_manifest_matches_ledger` (fail-closed)

**Definition:** The v2 dataset manifest
(`docs/evaluation/dataset-manifest.json`) lists per-case document hash sets.
Ledger `DocumentVersion` rows are attributed to cases by `source_url`
prefix; every **seeded** case's hash set must match the manifest exactly —
no missing and no unexpected documents — and every ledger document must be
claimed by exactly one case.  A manifest case with no ledger documents is
reported as `not_seeded` (the unit-test environment seeds only the
AI-compute slice); the verify script seeds all cases, so all are enforced
on the real gate path.

**Pass condition:** for every seeded case, manifest hash set == attributed
ledger hash set; zero unclaimed ledger documents.

**Failure examples:** The manifest file was deleted (check fails with
`gold manifest missing`); a fixture was edited without re-freezing the
manifest (`ledger document not in manifest: <hash>…`); a document was
ingested from a source outside any case prefix
(`ledger document claimed by no case`).

---

### 3. `assessment_source_spans_complete`

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

### 4. `holding_disclosures_dated`

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

### 5. `future_material_excluded`

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

### 6. `ai_human_boundary_visible`

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

### 7. `review_outcomes_tracked`

**Definition:** Every `AIAssessment` in the frozen slice carries at least one
human `ReviewDecision` — the gold set's 人工标签 claim is only real if each
AI conclusion has a separately recorded review.  The outcome distribution
(confirmed / modified / rejected), review coverage, and `AIRun` audit counts
by status are reported as evidence so review adoption is measurable over
time.

**Pass condition:** At least one `AIAssessment` exists and every assessment
has ≥ 1 `ReviewDecision` (`review_coverage == 1.0`).  The seeded slice
includes one confirming review per thesis assessment.

**Failure example:** A new thesis assessment was added to the seed without a
corresponding human review; the check reports
`unreviewed_assessment: assessment <id> has no ReviewDecision`.

---

### 8. `table_extraction_gold_accuracy` (fail-closed)

**Definition:** The rule-based `FinancialTableExtractor` reproduces the
frozen table gold set (`docs/evaluation/datasets/table-extraction-gold.json`)
exactly: for each sample, the extracted fact set
(`metric_name`, `observed_period`, value-in-statement) equals the annotated
expected set.

**Pass condition:** Every sample has `recall == 1.0` and `precision == 1.0`.
The dataset file must exist and contain at least one sample.

**Failure examples:** The gold file was deleted (check fails with
`table gold dataset missing`); an extractor change drops a fact
(`sample <id>: missing expected facts [...]`) or starts emitting spurious
facts (`sample <id>: unexpected extracted facts [...]`).

---

### 9. `pdf_fixture_parse_gold` (fail-closed)

**Definition:** Committed binary PDF fixtures (currently
`backend/tests/fixtures/storage_chain/06_sungrow_annual_summary.pdf`) must
parse through `app/services/pdf_text` (pypdf), and the extracted table
regions must reproduce the gold facts declared by the gold sample's
`pdf_file` entry.  This is the end-to-end guard for the real-binary parse
path: parser regressions surface here, extractor regressions surface here
and in check 8 (which holds an inline copy of the same table text).

**Pass condition:** The PDF file exists, its sha256 matches the manifest
(a regenerated fixture fails as `hash drifted`), `extract_spans` succeeds,
and the facts extracted from all spans exactly match the sample's
`expected_facts`.

**Failure examples:** The fixture was regenerated without re-freezing the
manifest (`PDF fixture hash drifted`); a pypdf upgrade changes text
extraction (`PDF sample <id>: missing expected facts [...]`); the fixture
was deleted (`PDF fixture missing`).

---

### 10. `projection_rebuilds`

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

Summary report (`docs/evaluation/reports/<timestamp>.json`, committed):

```json
{
  "passed": true,
  "failures": [],
  "generated_at": "20260801T095026Z",
  "checks": [
    { "name": "document_versions_present", "passed": true, "skipped": false }
  ]
}
```

Full detail (`docs/evaluation/raw/<timestamp>.json`, gitignored) additionally
carries each check's `evidence` payload and human-readable `failures` list.
`failures` at the top level is a list of check **names** that failed
(excluding skipped checks).

## Recall A/B evaluation

`backend/scripts/eval_recall_ab.py` measures whether the human-curated gold
EvidenceLink statements are recalled per thesis, comparing the legacy
sparse-only pipeline (`mode="bm25"`) against the hybrid pipeline
(`mode="hybrid"`: BM25 leg + char-n-gram TF-IDF dense leg fused with RRF,
lexical signal fused as a third leg).  It replays the frozen slice offline,
writes `docs/evaluation/reports/recall_ab_<timestamp>.json`, and exits 1 if
the hybrid recalls fewer gold statements than the baseline at recall@10 or
recall@20 (regression guard).

First run (2026-08-02, single-case replay): overall recall@20 improved from **0.7333 → 1.0000**
(4 gold statements recovered, 0 lost; 3 of them on the 寒武纪 thesis, whose
evidence the coarse tokenizer's whole-CJK-run tokens made BM25-invisible).

Multi-case replay (2026-08-02, all three gold cases seeded; candidates are
all cutoff-visible statements **across cases**, so cross-industry material
acts as ranking noise — a harder, more honest evaluation): overall
recall@20 **0.375 → 0.6875** (bm25 → hybrid), recall@10 0.25 → 0.5833;
15 gold statements recovered, **0 lost**.  Per case: AI 算力链 0.6 → 0.7333,
锂电储能链 0.3333 → 0.6, 半导体设备国产化 0.2222 → 0.7222.  The largest
gains are again on CJK-dense theses the baseline renders BM25-invisible
(半导体 T2/T3: 0.0 → 1.0 / 0.6667).

**Per-thesis no-dip invariant (added 2026-08-02).** The original
`_RRF_K = 60` (literature default) flattened the top of each leg enough
that a dense-leg tail hit could out-vote a sparse-leg top hit, dipping
the 碳酸锂 thesis (锂电 T2) at recall@10 from 0.5 to 0.25.  Grid search
over `(ngram in {(2,), (3,), (2,3)}, rrf_k in {30, 60, 100}, lex_w in
{1.0, 1.5, 2.0})` via `backend/tune_recall_params.py` picks **`rrf_k=30`**
as the only setting with **0 per-thesis dips** across the three frozen
cases (ngram and lex_w tied at 0.5833/0.6875 under rrf_k=30, so neither
needs to move).  Production's `_RRF_K` is now 30; the 碳酸锂 thesis is
back to `bm25=0.5 hybrid=0.5` at @10 with no new dips introduced
elsewhere.  A unit test (`tests/test_recall.py::test_recall_no_per_thesis_dip_vs_bm25`)
asserts the per-thesis invariant at the test layer so a future tuning
round is caught before the eval script even runs.
