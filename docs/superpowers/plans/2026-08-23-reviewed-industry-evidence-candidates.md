# Reviewed Industry Evidence Candidates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record, independently review, publish, replay, and display bounded industry evidence candidates without allowing them to become formal mechanisms, IndustryState inputs, earnings, valuation, or investment actions.

**Architecture:** Add append-only dossier and review rows. A dossier contains normalized candidate evidence bound to one object, historical basis, and frozen source manifest; two differently-identified reviewers in distinct roles approve it. Publication creates only a parent-sealed `industry_evidence_candidate` revision with `not_answerable`, and the reader/API/UI consume only its selected immutable parents.

**Tech Stack:** Python 3.11, FastAPI/Pydantic v2, SQLAlchemy/Alembic, pytest, React/TypeScript/Vitest, existing underwriting canonical hashes and source freeze.

---

## File map

- `backend/app/underwriting/domain/evidence_candidates.py`: candidate item, dossier and review contracts.
- `backend/app/underwriting/persistence/research_models.py`, `backend/alembic/versions/0062_reviewed_evidence_candidates.py`: immutable rows, constraints and triggers.
- `backend/app/underwriting/persistence/research_repository.py`: transactional append/read operations.
- `backend/app/underwriting/services/candidate_evidence.py`: dual-review publication gate.
- `backend/app/underwriting/services/research_revision_diff.py`: parent descriptor/replay validation.
- `backend/app/underwriting/api/schemas.py`, `router.py`: strict GET-only selected-candidate response.
- `frontend/src/data/underwritingResearchApi.ts`, `frontend/src/features/underwriting/ResearchArchivePage.tsx`: checked client and selected candidate display.
- `backend/tests/underwriting/test_evidence_candidates.py`, revision/API/migration/PG tests; frontend client/page tests.

### Task 1: Persist exact candidate and review contracts

**Files:**
- Create: `backend/app/underwriting/domain/evidence_candidates.py`
- Modify: `backend/app/underwriting/domain/__init__.py`
- Modify: `backend/app/underwriting/persistence/research_models.py`
- Create: `backend/alembic/versions/0062_reviewed_evidence_candidates.py`
- Modify: `backend/tests/underwriting/test_research_persistence.py`
- Modify: `backend/tests/underwriting/test_sqlite_migration_bootstrap.py`
- Modify: `backend/tests/underwriting/test_kernel_postgres.py`

- [ ] **Step 1: Write failing contracts and migration tests**

```python
def test_chart_candidate_requires_transcription_method() -> None:
    with pytest.raises(ValidationError, match="chart_approximation requires transcription_method"):
        CandidateEvidenceItem(..., status=CandidateEvidenceStatus.CHART_APPROXIMATION,
                              transcription_method=None)

def test_0062_creates_append_only_candidate_tables(sqlite_engine) -> None:
    assert table_names(sqlite_engine) >= {
        "uw_evidence_candidate_dossier_versions",
        "uw_evidence_candidate_review_versions",
    }
```

- [ ] **Step 2: Run the red tests**

Run: `cd backend && pytest -q tests/underwriting/test_research_persistence.py -k candidate tests/underwriting/test_sqlite_migration_bootstrap.py -k 0062`

Expected: FAIL because the contracts/tables do not exist.

- [ ] **Step 3: Implement exact contracts, rows and trigger migration**

```python
class CandidateEvidenceStatus(StrEnum):
    SOURCE_REPORTED = "source_reported"
    OFFICIAL_AGGREGATE = "official_aggregate"
    CHART_APPROXIMATION = "chart_approximation"
    ASSUMPTION_BOUND = "assumption_bound"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class CandidateEvidenceReview:
    dossier_id: UUID
    dossier_content_hash: str
    reviewer_identity: str
    reviewer_role: Literal["provenance", "methodology"]
    decision: Literal["approve", "reject", "request_changes"]
    rationale: str
    reviewed_at: datetime
```

Add FKs to object/basis/manifest/dossier, unique dossier successor and review identity/role constraints, indexed object/basis/status reads, and the same PostgreSQL append-only trigger helper used by revision 0061. A candidate item must preserve source, locator, period, available time, scope/exclusions, status-specific method, and a non-numeric Unknown reason. It must never be a `MetricObservation`.

- [ ] **Step 4: Run green persistence tests**

Run: `cd backend && pytest -q tests/underwriting/test_research_persistence.py -k candidate tests/underwriting/test_sqlite_migration_bootstrap.py -k 0062 tests/underwriting/test_kernel_postgres.py -k candidate`

Expected: PASS; PostgreSQL test has an explicit skip without `TEST_DATABASE_URL`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/domain backend/app/underwriting/persistence backend/app/models backend/alembic/versions/0062_reviewed_evidence_candidates.py backend/tests/underwriting
git commit -m "feat: persist reviewed evidence candidates"
```

### Task 2: Enforce append-only dossier and dual-review governance

**Files:**
- Modify: `backend/app/underwriting/persistence/research_repository.py`
- Create: `backend/tests/underwriting/test_evidence_candidates.py`

- [ ] **Step 1: Write failing repository tests**

```python
def test_same_identity_cannot_fill_both_approval_roles(repository, dossier) -> None:
    repository.append_candidate_review(..., reviewer_identity="reviewer:a", reviewer_role="provenance")
    with pytest.raises(ValidationError, match="different reviewer identities"):
        repository.append_candidate_review(..., reviewer_identity="reviewer:a", reviewer_role="methodology")

def test_successor_cannot_reuse_prior_dossier_review(repository, dossier) -> None:
    successor = repository.append_candidate_dossier(..., supersedes_id=dossier.id)
    assert repository.effective_candidate_reviews(successor.id) == []
```

- [ ] **Step 2: Run red tests**

Run: `cd backend && pytest -q tests/underwriting/test_evidence_candidates.py -k 'identity or successor'`

Expected: FAIL because the repository methods do not exist.

- [ ] **Step 3: Implement source-bound writes**

```python
def append_candidate_dossier(self, *, object_id: UUID, basis_id: UUID,
    source_manifest_id: UUID, dossier_key: str, payload: Mapping[str, object],
    created_at: datetime, expected_parent_id: UUID | None) -> UnderwritingEvidenceCandidateDossierVersion: ...

def append_candidate_review(self, *, dossier_id: UUID, dossier_content_hash: str,
    reviewer_identity: str, reviewer_role: str, decision: str,
    payload: Mapping[str, object], created_at: datetime) -> UnderwritingEvidenceCandidateReviewVersion: ...
```

Validate canonical UTC time at/before cutoff, exact verified manifest/source/locator membership, CAS successor linkage, canonical payload hashes, unique roles, and review-to-current-dossier hash binding. Do not commit, update, or delete.

- [ ] **Step 4: Run green repository tests**

Run: `cd backend && pytest -q tests/underwriting/test_evidence_candidates.py -k 'identity or successor or locator or cutoff'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/persistence/research_repository.py backend/tests/underwriting/test_evidence_candidates.py
git commit -m "feat: govern candidate evidence reviews"
```

### Task 3: Publish reviewed candidates but prohibit model promotion

**Files:**
- Create: `backend/app/underwriting/services/candidate_evidence.py`
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Modify: `backend/app/underwriting/services/kernel.py`
- Modify: `backend/tests/underwriting/test_evidence_candidates.py`
- Modify: `backend/tests/underwriting/test_research_revision_diff.py`

- [ ] **Step 1: Write failing publication/replay tests**

```python
def test_publish_requires_two_distinct_approved_roles(session, dossier) -> None:
    with pytest.raises(ValidationError, match="provenance and methodology approvals"):
        CandidateEvidenceService(session).publish(dossier.id)

def test_published_candidate_stays_not_answerable_and_has_no_formal_outputs(session, approved_dossier) -> None:
    result = CandidateEvidenceService(session).publish(approved_dossier.id)
    assert result.answerability.state == "not_answerable"
    assert result.formal_mechanism_ids == ()
    assert result.industry_state_id is None
```

- [ ] **Step 2: Run red tests**

Run: `cd backend && pytest -q tests/underwriting/test_evidence_candidates.py -k publish`

Expected: FAIL because `CandidateEvidenceService` does not exist.

- [ ] **Step 3: Implement the parent-sealed publication gate**

```python
INDUSTRY_EVIDENCE_CANDIDATE_KIND = "industry_evidence_candidate"

def publish(self, dossier_id: UUID) -> CandidateEvidencePublication:
    dossier = self._verified_dossier(dossier_id)
    provenance, methodology = self._approved_reviews(dossier)
    answerability = self._record_not_answerable(dossier)
    return self._publish_revision(
        version_kind=INDUSTRY_EVIDENCE_CANDIDATE_KIND,
        parent_ids=(dossier.id, provenance.id, methodology.id, dossier.source_manifest_id, answerability.id),
    )
```

Extend the revision resolver with strict dossier/review descriptors (stable identity, content hash, timestamp, scope/basis/cutoff). Reject dossier/review parents if they enter mechanism compilation, IndustryState compilation, or earnings inputs. A rejection/request-changes review blocks publication; the service never commits.

- [ ] **Step 4: Run green publication/replay tests**

Run: `cd backend && pytest -q tests/underwriting/test_evidence_candidates.py tests/underwriting/test_research_revision_diff.py -k 'candidate or parent'`

Expected: PASS, including later-successor isolation and rehashed-parent tamper failures.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/services backend/tests/underwriting/test_evidence_candidates.py backend/tests/underwriting/test_research_revision_diff.py
git commit -m "feat: publish reviewed evidence candidates"
```

### Task 4: Add strict read-only candidate API and generated contracts

**Files:**
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Modify: `backend/tests/underwriting/test_research_revision_diff_api.py`
- Modify: `backend/tests/underwriting/test_openapi_dump.py`
- Modify: `frontend/openapi.json`
- Modify: `frontend/src/contracts/v1.ts`

- [ ] **Step 1: Write failing API/OpenAPI tests**

```python
def test_candidate_evidence_is_get_only_and_parent_sealed(client, candidate_revision) -> None:
    body = client.get(f"/api/underwriting/v1/research-versions/{candidate_revision.id}/candidate-evidence").json()
    assert body["reviews"][0]["reviewer_role"] == "methodology"
    assert client.post(f"/api/underwriting/v1/research-versions/{candidate_revision.id}/candidate-evidence").status_code == 405
```

- [ ] **Step 2: Run red test**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py -k candidate_evidence`

Expected: FAIL with 404.

- [ ] **Step 3: Implement strict GET DTO and route**

```python
@router.get("/research-versions/{revision_id}/candidate-evidence", response_model=CandidateEvidenceResponse)
def candidate_evidence(revision_id: UUID, db: Session = Depends(get_db)) -> CandidateEvidenceResponse:
    return CandidateEvidenceResponse.model_validate(
        ResearchRevisionDiffService(db).candidate_evidence(revision_id)
    )
```

Use `extra="forbid"`; return only selected revision identity, scope, candidate items and reviews. Convert missing revision to underwriting 404 and integrity failure to underwriting 422. Recursively exclude action/entry/price/valuation/recommendation fields.

- [ ] **Step 4: Safely generate and test contracts**

First save/assert the unrelated `frontend/openapi.json` 8/8 user patch. Dump and generate, stage only generated changes, restore the user patch, then assert cached output excludes its `Unprocessable Content` hunks.

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py -k candidate_evidence tests/underwriting/test_openapi_dump.py && cd ../frontend && npm run gen:contract && npm run typecheck`

Expected: PASS and the user patch remains unstaged exactly 8/8.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/api backend/tests/underwriting/test_research_revision_diff_api.py backend/tests/underwriting/test_openapi_dump.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: expose reviewed candidate evidence"
```

### Task 5: Render only the checked selected candidate

**Files:**
- Modify: `frontend/src/data/underwritingResearchApi.ts`
- Modify: `frontend/src/data/underwritingResearchApi.test.ts`
- Modify: `frontend/src/features/underwriting/ResearchArchivePage.tsx`
- Modify: `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`

- [ ] **Step 1: Write failing client/UI tests**

```tsx
it("renders a reviewed candidate without current-model fallback", async () => {
  renderArchiveAtCandidateRevision()
  expect(await screen.findByText("候选证据（已审阅，未正式化）")).toBeVisible()
  expect(mockFetch).not.toHaveBeenCalledWith(expect.stringContaining("economic-models"))
})

it("fails closed on candidate revision or review hash mismatch", async () => {
  mockCandidateEvidence({ revision_id: otherRevisionId })
  renderArchiveAtCandidateRevision()
  expect(await screen.findByRole("alert")).toHaveTextContent("冻结研究记录不完整或不匹配")
})
```

- [ ] **Step 2: Run red tests**

Run: `cd frontend && npm test -- src/data/underwritingResearchApi.test.ts src/features/underwriting/ResearchArchivePage.test.tsx`

Expected: FAIL because the client/display does not exist.

- [ ] **Step 3: Implement checked client and display**

```ts
candidateEvidence(revisionId: string) {
  return request<CandidateEvidenceResponse>(
    `/research-versions/${encodeURIComponent(revisionId)}/candidate-evidence`,
  )
}
```

Load the candidate alongside selected revision/diff; validate exact revision/object/basis/content-hash linkage and reject unknown/malformed records. Display scope, status, source/locator, transcription or assumption warning, Unknown reason, and both review roles using controlled labels “候选” and “已审阅，未正式化”. Do not infer facts from current records or render action/price/valuation text.

- [ ] **Step 4: Run green frontend tests**

Run: `cd frontend && npm test -- src/data/underwritingResearchApi.test.ts src/features/underwriting/ResearchArchivePage.test.tsx src/app/routes.test.tsx && npm run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/data/underwritingResearchApi.ts frontend/src/data/underwritingResearchApi.test.ts frontend/src/features/underwriting/ResearchArchivePage.tsx frontend/src/features/underwriting/ResearchArchivePage.test.tsx
git commit -m "feat: render reviewed industry evidence candidates"
```

### Task 6: Document and gate the non-promotion boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/underwriting-research.md`
- Modify: `backend/tests/underwriting/test_evidence_candidates.py`
- Modify: `backend/tests/underwriting/test_kernel_postgres.py`

- [ ] **Step 1: Write a failing release-boundary test**

```python
def test_candidate_evidence_never_unblocks_industry_state_or_action(...) -> None:
    published = publish_approved_candidate(...)
    assert published.answerability.state == "not_answerable"
    assert published.formal_mechanism_ids == ()
    assert published.industry_state_id is None
```

- [ ] **Step 2: Run red test**

Run: `cd backend && pytest -q tests/underwriting/test_evidence_candidates.py -k never_unblocks`

Expected: FAIL until publication outcome is explicitly asserted.

- [ ] **Step 3: Document the exact operational boundary**

Document dual approval, source/time/rights checks, and that candidates remain evidence-only. List the required independent inputs and causal review before a future formal IndustryState. Do not claim actual utilization, price mechanism, valuation or recommendation.

- [ ] **Step 4: Run release verification**

```bash
cd backend && pytest -q tests/underwriting
cd backend && python -m compileall app
cd frontend && npm test && npm run typecheck && npm run build
git diff --check
```

Expected: all backend/frontend gates pass; PostgreSQL immutable test passes with `TEST_DATABASE_URL` or explicitly skips; `frontend/openapi.json` retains only the unrelated 8/8 user patch.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/architecture/underwriting-research.md backend/tests/underwriting/test_evidence_candidates.py backend/tests/underwriting/test_kernel_postgres.py
git commit -m "test: gate reviewed evidence candidates"
```
