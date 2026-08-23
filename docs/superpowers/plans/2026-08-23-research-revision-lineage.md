# Read-only Research Revision Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing immutable `UnderwritingResearchVersion` successor chain legible through deterministic, historical, read-only revision history and ancestor/descendant diffs.

**Architecture:** This is deliberately a read-model increment. `object_id + version_kind` is the existing research family, and each stored `parent_ids` item is resolved only against the exact persisted row named by that revision; it is never replaced by a current/latest row. A service produces canonical immutable artifact descriptors and a sorted diff; FastAPI only serializes that service result. No new write model, database migration, valuation, price, recommendation, or portfolio-action capability is introduced.

**Tech Stack:** Python 3.11, SQLAlchemy 2, FastAPI, Pydantic v2, pytest, existing SQLite/PostgreSQL underwriting test fixtures, generated OpenAPI TypeScript contract.

---

## Scope decisions

- Reuse `uw_research_versions` exactly as it exists. `version_kind` is the family key; `sequence`/`supersedes_id` supply lineage.
- Treat the existing `parent_ids` JSON as the frozen parent set. Recognize UUID references to source manifests, metric definitions/observations, mechanism packs, industry state/scenario/exposure, earnings/forecast/falsifier, ledger entries, and answerability evaluations; recognize only `semantic_snapshot:<64-lowercase-hex>` as a non-UUID synthetic parent.
- Reject an API read with `422` if a stored parent is malformed, absent, duplicated across artifact tables, or belongs to a different historical basis. Returning a plausible diff for corrupt lineage would be worse than failing closed.
- This increment exposes artifact/evidence, mechanism, industry/model, answerability, and unknown-gap changes. It does **not** claim to diff free-form research claims because the current persisted research-version model does not yet store claim payloads or typed change-set rows.
- Parent descriptors contain exact source locators, unit, period, availability, status, and compact public identifiers where the immutable row provides them. They do not carry arbitrary raw payloads, price data, or action labels.

## File map

- Create: `backend/app/underwriting/services/research_revision_diff.py` — read-only lineage validation, typed parent resolution, canonical revision summaries, and deterministic ancestor diff.
- Create: `backend/tests/underwriting/test_research_revision_diff.py` — service-level lineage, determinism, integrity, and no-latest-data tests.
- Create: `backend/tests/underwriting/test_research_revision_diff_api.py` — HTTP ordering, error envelope, and historical replay tests.
- Modify: `backend/app/underwriting/api/schemas.py` — strict read-only history/diff DTOs only.
- Modify: `backend/app/underwriting/api/router.py` — GET routes delegating to the diff service.
- Modify: `backend/tests/underwriting/test_openapi_dump.py` — generated contract coverage for the three new routes and forbidden-field guard.
- Modify/generated: `frontend/openapi.json`, `frontend/src/contracts/v1.ts` — checked-in OpenAPI and TypeScript outputs.
- Modify: `docs/architecture/underwriting-research.md` — explain the existing-chain limitation, no-write boundary, and how readers interpret an unresolved/corrupt lineage failure.

### Task 1: Establish the read-only revision foundation

**Files:**
- Create: `backend/app/underwriting/services/research_revision_diff.py`
- Test: `backend/tests/underwriting/test_research_revision_diff.py`

- [ ] **Step 1: Write failing foundation tests.** Seed a company, one historical basis, a source manifest and metric observation, then publish a first version through `UnderwritingKernelService.publish_research_version`. Assert `revision_summary(id)` returns the stored object/basis/kind/sequence/hash, normalized parent descriptors, cutoff, and manifest hash. Add a malformed parent (`"not-a-parent"`) and a valid UUID whose artifact has another `basis_id`; both must raise `ValidationError` rather than silently drop the reference.

```python
def test_summary_reads_only_the_revision_frozen_parents(session, seeded_revision) -> None:
    service = ResearchRevisionDiffService(session)
    summary = service.revision_summary(seeded_revision.first.id)
    assert summary.sequence == 1
    assert summary.parent_refs[0].artifact_type == "metric_observation"
    assert summary.parent_refs[0].source_locators == ("annual-report:p18",)
    assert summary.cutoff == seeded_revision.basis.cutoff


@pytest.mark.parametrize("parent", ["not-a-parent", "foreign-basis-uuid"])
def test_summary_fails_closed_for_unresolvable_or_cross_basis_parent(session, parent) -> None:
    revision = publish_revision_with_parent(session, parent)
    with pytest.raises(ValidationError, match="research revision parent"):
        ResearchRevisionDiffService(session).revision_summary(revision.id)
```

- [ ] **Step 2: Run the focused test to confirm it fails.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py -k foundation`

Expected: collection failure because `research_revision_diff` does not exist.

- [ ] **Step 3: Implement immutable descriptors and exact parent resolution.** Define frozen dataclasses `RevisionArtifactRef`, `ResearchRevisionSummary`, and `RevisionHistory`. `RevisionArtifactRef` must have `reference`, `artifact_type`, `identity`, `content_hash`, `source_locators`, `unit`, `period_start`, `period_end`, `available_at`, and `status`; none is a valuation/action field. Resolve every UUID by querying every allowed immutable table, require exactly one match, require its `basis_id == revision.basis_id`, and build descriptors from persisted scalar columns only. For a metric observation use identity `(metric_key, definition_version, observed_end, dimension_hash, source_id)` and its real `source_locator`/unit/period. For a ledger unknown gap, use its `family_key`, `entry_type`, and explicitly persisted source fields. Validate the synthetic snapshot token with `re.fullmatch(r"semantic_snapshot:[0-9a-f]{64}", value)`. Sort descriptors by `(artifact_type, identity, reference)`.

- [ ] **Step 4: Add history and family-head reads without any write path.** Implement `revision_summary(revision_id)`, `revision_history(object_id, version_kind)`, and `effective_revision(object_id, version_kind)`. Query `UnderwritingResearchVersion` by its persisted IDs/sequence; require every history row has the same object and kind, starts at sequence 1, and each successor points to exactly the previous ID. Return history ordered `(sequence, id)`. Do not call `publish_research_version`, `append_research_version`, or mutate the session.

- [ ] **Step 5: Run foundation tests and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py -k foundation`

Expected: PASS.

```bash
git add backend/app/underwriting/services/research_revision_diff.py backend/tests/underwriting/test_research_revision_diff.py
git commit -m "feat: read immutable underwriting revision lineage"
```

### Task 2: Implement the deterministic ancestor/descendant diff

**Files:**
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Modify: `backend/tests/underwriting/test_research_revision_diff.py`

- [ ] **Step 1: Write failing diff tests.** Create a two-version chain whose successor replaces a revenue observation, adds one candidate mechanism, removes an `unknown_evidence_gap` ledger parent, and replaces answerability. Publish an unrelated source in the same basis but do not reference it. Assert the diff contains only frozen parents, reports `replaced`/`added`/`removed` typed entries, includes old/new locator/unit/period fields, and has byte-equivalent values across repeated calls.

```python
def test_ancestor_diff_is_typed_sorted_and_does_not_pull_unreferenced_latest_data(session, revision_chain) -> None:
    service = ResearchRevisionDiffService(session)
    first = service.revision_diff(revision_chain.v1.id, revision_chain.v2.id)
    second = service.revision_diff(revision_chain.v1.id, revision_chain.v2.id)
    assert first == second
    assert [(item.group, item.change_type, item.identity) for item in first.entries] == [
        ("evidence", "replaced", "company.revenue|1|2024-12-31T00:00:00+00:00|..."),
        ("mechanism", "added", "price_cost_transmission"),
        ("evidence", "removed", "reality:industry.effective_capacity_gwh"),
        ("answerability", "replaced", "answerability"),
    ]
    assert "unreferenced-source" not in {ref.reference for entry in first.entries for ref in entry.refs()}


def test_diff_rejects_siblings_reverse_order_and_cross_family_pairs(session, revision_chain) -> None:
    service = ResearchRevisionDiffService(session)
    for left, right in ((revision_chain.v2, revision_chain.v1), (revision_chain.v1, revision_chain.sibling), (revision_chain.v1, revision_chain.other_kind)):
        with pytest.raises(ValidationError, match="ancestor"):
            service.revision_diff(left.id, right.id)
```

- [ ] **Step 2: Run the diff tests to confirm they fail.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py -k diff`

Expected: FAIL because `revision_diff` is absent.

- [ ] **Step 3: Implement canonical diff semantics.** Add frozen `RevisionChange` and `ResearchRevisionDiff`. First prove `from_revision` is a strict ancestor of `to_revision` by walking only `to_revision.supersedes_id`; require shared `object_id` and `version_kind`. Index each frozen descriptor by `(artifact_type, identity)`. Emit:

```python
if before is None:
    change_type = "added"
elif after is None:
    change_type = "removed"
elif before.reference != after.reference or before.content_hash != after.content_hash:
    change_type = "replaced"
else:
    continue
```

Group artifact types deterministically as `evidence`, `mechanism`, `industry_model`, then `answerability`; sort with a fixed group rank followed by `(artifact_type, identity, change_type, before.reference or "", after.reference or "")`. Compute `diff_hash = canonical_hash` over the two revision IDs, their stored content hashes, and the fully canonical entry payload. No wall-clock timestamp, current query result, or unordered JSON object may enter the result.

- [ ] **Step 4: Cover replay and integrity edge cases.** Add tests for direct and multi-hop ancestors, duplicate semantic identity within one frozen parent set, illegal/unknown semantic-snapshot token, a cycle in a deliberately corrupted chain, and a successor whose parent list is reordered. The service must reject corrupt chains; reordering the stored equivalent parents must yield the same diff.

- [ ] **Step 5: Run the service suite and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py`

Expected: PASS.

```bash
git add backend/app/underwriting/services/research_revision_diff.py backend/tests/underwriting/test_research_revision_diff.py
git commit -m "feat: diff immutable underwriting research revisions"
```

### Task 3: Expose strict read-only history and diff APIs

**Files:**
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Create: `backend/tests/underwriting/test_research_revision_diff_api.py`
- Modify/generated: `frontend/openapi.json`, `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Write failing HTTP tests.** Test the three reads below from a seeded two-version chain. Assert `schema_version == "underwriting.v1"`, ordered arrays, cutoff/manifest hash and source locators are present, `diff_hash` is stable, and no route accepts POST/PUT/PATCH/DELETE.

```python
def test_revision_history_and_diff_are_read_only_and_historical(api_client, revision_chain) -> None:
    history = api_client.get(f"{BASE}/objects/{revision_chain.object_id}/research-versions/{revision_chain.kind}")
    diff = api_client.get(f"{BASE}/research-versions/{revision_chain.v1.id}/diff/{revision_chain.v2.id}")
    assert history.status_code == diff.status_code == 200
    assert [item["sequence"] for item in history.json()["revisions"]] == [1, 2]
    assert diff.json()["entries"] == sorted(diff.json()["entries"], key=diff_sort_key)
    assert "valuation" not in diff.json()
    assert api_client.post(diff.request.url.path).status_code == 405
```

- [ ] **Step 2: Run the API test to confirm it fails.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py`

Expected: 404 / missing DTO imports.

- [ ] **Step 3: Add strict DTOs and routes.** Add only response DTOs: `ResearchRevisionArtifactResponse`, `ResearchRevisionResponse`, `ResearchRevisionHistoryResponse`, `ResearchRevisionChangeResponse`, and `ResearchRevisionDiffResponse`, all extending `UnderwritingModel` with `extra="forbid"`. Do not use generic raw `payload` fields. Add exactly these GET routes:

```text
GET /api/underwriting/v1/objects/{object_id}/research-versions/{version_kind}
GET /api/underwriting/v1/research-versions/{revision_id}
GET /api/underwriting/v1/research-versions/{from_revision_id}/diff/{to_revision_id}
```

Instantiate `ResearchRevisionDiffService(db)` inside the handlers. Convert `ValidationError` to the existing 422 envelope and missing revisions to the existing 404 envelope. Keep route responses sorted by the service; do not re-query “latest” artifact rows in router code.

- [ ] **Step 4: Add contract prohibition tests and regenerate artifacts.** Recursively inspect the five new Pydantic JSON schemas and assert no field name contains `pe`, `pb`, `dcf`, `price`, `target`, `buy`, `sell`, `stop`, `position`, `return`, `valuation`, `recommend`, or `action`. Then run:

```bash
cd backend && python scripts/dump_openapi.py
cd ../frontend && npm run gen:contract
```

Verify the checked-in `frontend/openapi.json` has all three paths and `frontend/src/contracts/v1.ts` contains the matching GET operations, with no write operation for these paths.

- [ ] **Step 5: Run API/OpenAPI tests and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py tests/underwriting/test_openapi_dump.py tests/underwriting/test_kernel_api.py`

Expected: PASS.

```bash
git add backend/app/underwriting/api/schemas.py backend/app/underwriting/api/router.py backend/tests/underwriting/test_research_revision_diff_api.py backend/tests/underwriting/test_openapi_dump.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: expose read-only underwriting revision diffs"
```

### Task 4: Add gates and operator documentation

**Files:**
- Modify: `backend/tests/underwriting/test_research_revision_diff.py`
- Modify: `backend/tests/underwriting/test_research_revision_diff_api.py`
- Create: `docs/architecture/underwriting-research.md`

- [ ] **Step 1: Write failing end-to-end gate tests.** Import the CATL evidence-only fixture and use its persisted `UnderwritingResearchVersion` as the first side of an API read. Append a test-only successor using the existing kernel service and immutable fixture parents. Assert the original CATL response remains byte-for-byte unchanged after successor publication; the old revision GET names only old parent refs; the diff names only the explicitly added/replaced refs; and the service has no `add_`, `append_`, `publish_`, `update_`, or `delete_` public method.

- [ ] **Step 2: Run the gate tests to confirm the missing behavior.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py tests/underwriting/test_research_revision_diff_api.py -k 'catl or read_only'`

Expected: FAIL until frozen-parent replay and route coverage are complete.

- [ ] **Step 3: Document operational boundaries.** Write `docs/architecture/underwriting-research.md` with: the mapping from existing model columns to reader-facing revision concepts; parent-resolution table and descriptor fields; ancestor-only rule; deterministic ordering/hash recipe; corrupt-lineage `422` behavior; source locator and unknown-gap presentation rules; the limitation that claims/downstream review status require a future write-model project; and an explicit non-goals section listing price, valuation, recommendation, position, and automatic data completion.

- [ ] **Step 4: Run full gates.**

Run:

```bash
cd backend && pytest -q tests/underwriting
python -m compileall -q app
python scripts/dump_openapi.py
cd ../frontend && npm run gen:contract && npm run build
git diff --check
git status --short
```

Expected: all underwriting tests pass, compilation and frontend build pass, regenerated contract is clean, and `git diff --check` has no output.

- [ ] **Step 5: Commit gates and documentation.**

```bash
git add backend/tests/underwriting/test_research_revision_diff.py backend/tests/underwriting/test_research_revision_diff_api.py docs/architecture/underwriting-research.md frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "docs: define read-only underwriting revision lineage"
```

## Final acceptance checklist

- [ ] Every selected revision replays only its stored parent references and historical basis.
- [ ] A two-sided diff is allowed only for strict ancestor/descendant versions in one existing family.
- [ ] Diff output is deterministic, typed, source/period/unit-aware, and explicitly represents unknown evidence gaps.
- [ ] Invalid or cross-basis lineage is visible as a fail-closed error, never hidden or filled from newer data.
- [ ] Three read-only API endpoints and generated OpenAPI/TypeScript contracts exist; no revision write endpoint exists.
- [ ] The implementation contains no valuation, price, target, recommendation, sizing, or portfolio-action field.
- [ ] The CATL historical baseline remains replayable after a successor exists.
