# Company Research Publication Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the live Alphabet Company Research workflow from durable judgment review through human confirmation, immutable publication, frozen replay, and deterministic Markdown export.

**Architecture:** Add a dedicated `CompanyResearchPublicationService` above the existing immutable Company Research artifacts and generic revision persistence primitives. Confirmation appends a human-confirmed memo successor and moves the preparation to 95%; publication freezes authenticated artifact identities into a Company-Research-specific manifest and revision before moving the preparation to 100%. The frontend uses only the new high-level Company Research endpoints and verifies export content hashes before download.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL/SQLite, pytest, React 19, TypeScript, Vitest, Testing Library, Vite.

---

## File Map

### Backend domain and persistence

- Modify `backend/app/underwriting/domain/company_research.py` — represent machine and human-confirmed memo states with closed validation.
- Modify `backend/app/underwriting/domain/company_research_artifact_codec.py` — encode/decode the extended memo contract.
- Modify `backend/app/underwriting/persistence/company_research_repository.py` — expose a fresh locked publication snapshot, append a confirmed memo, and atomically update lifecycle/audit state.
- Modify `backend/app/underwriting/persistence/product_repository.py` — add Company Research revision head/append helpers while reusing boundary, manifest, assessment, and draft-CAS primitives.
- Modify `backend/tests/underwriting/test_company_research_artifact_codec.py` — lock the closed memo payload variants.
- Modify `backend/tests/underwriting/test_company_research_persistence.py` — lock append-only confirmation and publication-state semantics.

### Backend service and HTTP boundary

- Create `backend/app/underwriting/services/company_research_publication.py` — own confirmation, preview, publication, replay, export, canonical hashes, and savepoints.
- Modify `backend/app/underwriting/api/company_research_schemas.py` — add closed request/response DTOs.
- Modify `backend/app/underwriting/api/company_research_router.py` — expose five high-level endpoints.
- Create `backend/tests/underwriting/test_company_research_publication.py` — service-level RED/GREEN coverage.
- Modify `backend/tests/underwriting/test_company_research_api.py` — public HTTP workflow and error-shape coverage.
- Modify `backend/tests/underwriting/test_revision_publisher.py` — preserve the generic-publication rejection.

### Frontend

- Modify `frontend/src/contracts/v1.ts` — add generated-equivalent DTO declarations without overwriting the user's dirty `frontend/openapi.json`.
- Modify `frontend/src/data/investmentResearchApi.ts` — strict decoders and high-level API methods.
- Modify `frontend/src/data/InvestmentResearchApi.test.ts` — endpoint, identity, state, and hash-contract tests.
- Modify `frontend/src/features/investment-research/companyResearchView.ts` — presentation helpers for 85/95/100 actions.
- Modify `frontend/src/features/investment-research/companyResearchView.test.ts` — pure helper coverage.
- Modify `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx` — confirmation, preview, publish, replay, and export UI.
- Modify `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx` — interaction, race, and accessibility coverage.

### Protected files

Do not stage, rewrite, regenerate in place, or discard:

```text
docs/superpowers/plans/2026-08-23-archive-shell-and-identity.md
frontend/openapi.json
```

If OpenAPI comparison is needed, generate to a temporary path and inspect only the Company Research fragment.

---

### Task 1: Extend the memo domain contract

**Files:**

- Modify: `backend/app/underwriting/domain/company_research.py`
- Modify: `backend/app/underwriting/domain/company_research_artifact_codec.py`
- Test: `backend/tests/underwriting/test_company_research_artifact_codec.py`

- [ ] **Step 1: Write failing codec tests for human confirmation**

Add tests with these exact behaviors:

```python
def test_company_research_memo_codec_accepts_a_closed_human_confirmation():
    payload = _machine_memo_payload()
    payload.update(
        candidate_status="human_confirmed",
        reviewer="human:local-user",
        markdown="Evidence remains insufficient.\n",
    )
    decoded = CompanyResearchArtifactCodec.decode("memo", payload)
    assert decoded.candidate_status == "human_confirmed"
    assert decoded.reviewer == "human:local-user"
    assert decoded.markdown == "Evidence remains insufficient.\n"
    assert CompanyResearchArtifactCodec.encode(decoded) == payload


@pytest.mark.parametrize(
    ("candidate_status", "reviewer", "markdown"),
    [
        ("machine_draft", "human:local-user", None),
        ("machine_draft", None, "text"),
        ("human_confirmed", None, "text"),
        ("human_confirmed", "human:local-user", "  "),
        ("human_confirmed", "another-reviewer", "text"),
    ],
)
def test_company_research_memo_codec_rejects_mixed_or_open_confirmation_shapes(
    candidate_status, reviewer, markdown
):
    payload = _machine_memo_payload()
    payload.update(
        candidate_status=candidate_status,
        reviewer=reviewer,
        markdown=markdown,
    )
    with pytest.raises(CompanyResearchValidationError):
        CompanyResearchArtifactCodec.decode("memo", payload)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
uv run pytest tests/underwriting/test_company_research_artifact_codec.py \
  -k 'human_confirmation or mixed_or_open_confirmation_shapes' -q
```

Expected: failures because `candidate_status="human_confirmed"`, `reviewer`, and `markdown` are not in the current memo contract.

- [ ] **Step 3: Implement the minimal closed memo variant**

Change the memo fields to:

```python
@dataclass(frozen=True, slots=True)
class CompanyResearchMemoArtifact:
    assessment_status: str
    business_map_ref: CompanyResearchArtifactReference
    driver_map_ref: CompanyResearchArtifactReference
    financial_bridge_ref: CompanyResearchArtifactReference
    scenario_set_ref: CompanyResearchArtifactReference
    valuation_set_ref: CompanyResearchArtifactReference | None
    gap_keys: tuple[str, ...]
    strongest_counterevidence: tuple[SourceLineageReference, ...]
    next_verification_events: tuple[str, ...]
    research_gaps: tuple[ResearchGap, ...] = ()
    candidate_status: str = "machine_draft"
    reviewer: str | None = None
    markdown: str | None = None
```

Enforce this exact conditional shape in `__post_init__`:

```python
if self.candidate_status == "machine_draft":
    if self.reviewer is not None or self.markdown is not None:
        raise CompanyResearchValidationError("machine memo cannot contain human confirmation")
elif self.candidate_status == "human_confirmed":
    if self.reviewer != "human:local-user":
        raise CompanyResearchValidationError("confirmed memo reviewer is invalid")
    if (
        not isinstance(self.markdown, str)
        or not self.markdown.strip()
        or self.markdown != self.markdown.replace("\r\n", "\n").replace("\r", "\n")
        or len(self.markdown) > 100_000
    ):
        raise CompanyResearchValidationError("confirmed memo markdown is invalid")
else:
    raise CompanyResearchValidationError("company research memo status is invalid")
```

Keep all existing typed reference, gap, counterevidence, and assessment validation unchanged.

- [ ] **Step 4: Run codec and model-builder suites GREEN**

Run:

```bash
cd backend
uv run pytest \
  tests/underwriting/test_company_research_artifact_codec.py \
  tests/underwriting/test_company_research_model_builder.py -q
```

Expected: all tests pass; existing machine memo payloads remain byte-for-byte compatible.

- [ ] **Step 5: Commit**

```bash
git add \
  backend/app/underwriting/domain/company_research.py \
  backend/app/underwriting/domain/company_research_artifact_codec.py \
  backend/tests/underwriting/test_company_research_artifact_codec.py
git commit -m "feat: model confirmed company research memos"
```

---

### Task 2: Confirm judgment atomically

**Files:**

- Modify: `backend/app/underwriting/persistence/company_research_repository.py`
- Create: `backend/app/underwriting/services/company_research_publication.py`
- Test: `backend/tests/underwriting/test_company_research_persistence.py`
- Test: `backend/tests/underwriting/test_company_research_publication.py`

- [ ] **Step 1: Write RED service tests for confirmation and zero-write rejection**

Create a fixture from the existing reviewed Alphabet/model-bundle helpers and add:

```python
def test_confirm_judgment_appends_one_confirmed_memo_and_moves_to_95(session):
    state = _awaiting_judgment_workspace(session)
    result = _service(session).confirm_judgment(
        project_id=state.project_id,
        expected_lock_version=state.draft.lock_version,
        expected_memo_id=state.memo.id,
        expected_memo_content_hash=state.memo.content_hash,
        markdown="Current formal evidence is insufficient.\n",
    )
    assert result.preparation.status == "ready_to_freeze"
    assert result.preparation.current_step == "memo"
    assert result.preparation.progress == 95
    assert result.draft.lock_version == state.draft.lock_version + 1
    assert result.memo.version == state.memo.version + 1
    assert result.memo.supersedes_id == state.memo.id
    assert result.memo.payload["candidate_status"] == "human_confirmed"
    assert result.memo.payload["assessment_status"] == "not_answerable"


@pytest.mark.parametrize("mutation", _publication_input_mutations())
def test_confirm_judgment_rejects_tampered_or_stale_input_without_writes(session, mutation):
    state = _awaiting_judgment_workspace(session)
    mutation(session, state)
    before = _publication_counts_and_heads(session, state.project_id)
    with pytest.raises((ValidationError, ConflictError, IntegrityError)):
        _service(session).confirm_judgment(
            project_id=state.project_id,
            expected_lock_version=state.draft.lock_version,
            expected_memo_id=state.memo.id,
            expected_memo_content_hash=state.memo.content_hash,
            markdown="Current formal evidence is insufficient.\n",
        )
    session.commit()
    assert _publication_counts_and_heads(session, state.project_id) == before
```

The mutation matrix must include stale draft, wrong memo ID/hash, non-machine memo, substituted evidence successor, missing review event, changed research gaps, wrong historical basis, and foreign model parent.

- [ ] **Step 2: Run the confirmation tests and verify RED**

Run:

```bash
cd backend
uv run pytest tests/underwriting/test_company_research_publication.py \
  -k 'confirm_judgment' -q
```

Expected: failures because the publication service and confirmation transition do not exist.

- [ ] **Step 3: Add a fresh locked publication snapshot**

Add a repository dataclass containing project, preparation, job, draft, Company/Securities, memberships, historical basis, current artifact heads, full artifact chains, and full audit events. Implement:

```python
def lock_publication_state(self, project_id: UUID) -> CompanyResearchPublicationState:
    self.lock_project_ownership(project_id)
    preparation = self.preparation_for_project(project_id, fresh=True, lock=True)
    job = self.job_for_preparation(preparation.id, fresh=True, lock=True)
    draft = self.workspace_draft(project_id, fresh=True, lock=True)
    return CompanyResearchPublicationState(
        project=self.project(project_id, fresh=True, lock=True),
        preparation=preparation,
        job=job,
        draft=draft,
        company=self.company_for_project(project_id, fresh=True),
        securities=self.securities_for_project(project_id, fresh=True),
        memberships=self.memberships_for_project(project_id, fresh=True),
        historical_basis=self.authenticated_historical_basis_for_draft(draft),
        artifact_chains=self.all_artifact_chains(project_id, fresh=True, lock=True),
        events=self.events_for_preparation(preparation.id, fresh=True, lock=True),
    )
```

Use the repository's existing project → preparation → job → draft lock order and existing SQLite writer reservation seam.

- [ ] **Step 4: Implement `confirm_judgment` in one savepoint**

Define the public input/result dataclasses and method:

```python
def confirm_judgment(
    self,
    *,
    project_id: UUID,
    expected_lock_version: int,
    expected_memo_id: UUID,
    expected_memo_content_hash: str,
    markdown: str,
) -> CompanyResearchJudgmentConfirmation:
    normalized = _normalize_markdown(markdown)
    with self._session.begin_nested():
        state = self._repository.lock_publication_state(project_id)
        authenticated = self._authenticate_publication_state(state)
        self._require_confirmation_expectations(
            state, authenticated.memo, expected_lock_version,
            expected_memo_id, expected_memo_content_hash,
        )
        confirmed = replace(
            authenticated.memo_value,
            candidate_status="human_confirmed",
            reviewer="human:local-user",
            markdown=normalized,
        )
        row = self._repository.append_artifact_successor(
            project_id=project_id,
            kind="memo",
            parent=authenticated.memo,
            payload=CompanyResearchArtifactCodec.encode(confirmed),
            source_refs=authenticated.memo.source_refs,
            created_at=self._now(),
        )
        draft = self._repository.compare_and_swap_publication_draft(
            project_id, expected_lock_version, self._now()
        )
        self._repository.advance_to_ready_to_freeze(state.preparation, self._now())
        self._repository.append_event(
            preparation_id=state.preparation.id,
            event_type="judgment_confirmed",
            payload=_confirmation_event_payload(authenticated.memo, row),
            created_at=self._now(),
        )
        return CompanyResearchJudgmentConfirmation(state.preparation, draft, row)
```

`_authenticate_publication_state` must reuse the same governed evidence, gaps, model-bundle semantic closure, artifact chronology, boundary, and audit reconciliation used by recovery/model/workbench; do not duplicate canonical hash algorithms.

- [ ] **Step 5: Add idempotency and catch/commit failure tests**

Test that an exact repeated confirmation returns the existing confirmed memo without a second event or lock increment. Inject failures at memo append, draft CAS, preparation transition, and event append; catch the exception, commit the caller session, and assert every row/head/count remains unchanged.

- [ ] **Step 6: Run focused persistence/service suites GREEN**

```bash
cd backend
uv run pytest \
  tests/underwriting/test_company_research_persistence.py \
  tests/underwriting/test_company_research_publication.py \
  -k 'confirmation or publication_state' -q
```

- [ ] **Step 7: Commit**

```bash
git add \
  backend/app/underwriting/persistence/company_research_repository.py \
  backend/app/underwriting/services/company_research_publication.py \
  backend/tests/underwriting/test_company_research_persistence.py \
  backend/tests/underwriting/test_company_research_publication.py
git commit -m "feat: confirm company research judgments"
```

---

### Task 3: Preview, freeze, replay, and export immutable revisions

**Files:**

- Modify: `backend/app/underwriting/persistence/product_repository.py`
- Modify: `backend/app/underwriting/services/company_research_publication.py`
- Test: `backend/tests/underwriting/test_company_research_publication.py`
- Test: `backend/tests/underwriting/test_revision_publisher.py`

- [ ] **Step 1: Write RED tests for zero-write preview and honest assessment**

```python
def test_publication_preview_is_zero_write_and_keeps_not_answerable_closed(session):
    state = _ready_to_freeze_workspace(session)
    before = _all_session_rows(session)
    pending = _pending_unrelated_row(session)
    preview = _service(session).preview(
        project_id=state.project_id,
        expected_lock_version=state.draft.lock_version,
    )
    assert preview.assessment.answerability == "not_answerable"
    assert preview.assessment.direction is None
    assert preview.assessment.confidence is None
    assert preview.value_range is None
    assert preview.return_range is None
    assert preview.blockers == tuple(state.confirmed_memo.payload["gap_keys"])
    assert _all_session_rows(session) == before
    assert pending in session.new
```

- [ ] **Step 2: Write RED tests for publication, idempotency, rollback, and replay**

Add separate tests proving:

```python
revision = service.publish(
    project_id=project_id,
    expected_lock_version=preview.expected_lock_version,
    expected_manifest_hash=preview.manifest_hash,
    idempotency_key="alphabet-first-freeze",
)
assert revision.sequence == 1
assert revision.answerability == "not_answerable"
assert revision.preparation_status == "completed"
assert revision.progress == 100
assert revision.current_step is None
assert service.publish(
    project_id=project_id,
    expected_lock_version=preview.expected_lock_version,
    expected_manifest_hash=preview.manifest_hash,
    idempotency_key="alphabet-first-freeze",
).id == revision.id
assert service.revision(project_id, revision.id) == revision
```

Also cover wrong manifest hash, stale draft, reused key/different manifest, concurrent different keys, and injected failure after each of assessment, boundary, manifest, revision, draft CAS, publication event, and preparation completion. Each caught failure followed by caller commit must leave no new publication row and status 95.

- [ ] **Step 3: Add Company Research revision primitives**

In `ProductRepository`, add:

```python
def company_research_revision_head(
    self, project_id: UUID, *, lock: bool = False
) -> UnderwritingResearchVersion | None:
    statement = (
        select(UnderwritingResearchVersion)
        .where(
            UnderwritingResearchVersion.project_id == project_id,
            UnderwritingResearchVersion.version_kind == "company_research",
        )
        .order_by(
            UnderwritingResearchVersion.sequence.desc(),
            UnderwritingResearchVersion.id.desc(),
        )
        .limit(1)
    )
    return self._session.scalar(statement.with_for_update() if lock else statement)
```

Implement `append_company_research_revision` with:

```text
version_kind = company_research
manifest_schema = company-research.revision-manifest.v1
publication_status = user_frozen
sequence = locked head sequence + 1
supersedes_id = locked head ID
content_hash = canonical hash of project/object/basis/boundary/manifest/assessment/parent/status
```

Reuse `append_assessment`, `append_boundary`, `append_manifest`, and
`reset_draft_after_publish`. Do not alter generic `append_product_revision`.

- [ ] **Step 4: Implement canonical preview and publication**

Define closed dataclasses for assessment, artifact reference, preview, revision, and export. Preview must build one canonical manifest with artifact descriptors shaped as:

```python
{
    "kind": row.kind,
    "id": str(row.id),
    "version": row.version,
    "input_hash": row.input_hash,
    "content_hash": row.content_hash,
}
```

Publish must lock and reauthenticate the same state, recompute the manifest, compare its hash to the request, and run this sequence inside one nested transaction:

```text
append frozen assessment
append revision boundary
append revision manifest
append company research revision
CAS draft base_revision_id and lock_version
append company_research_published event
set preparation completed/current_step null/progress 100
```

Read idempotency before creating rows and revalidate the stored manifest/revision before returning an existing result.

- [ ] **Step 5: Implement fail-closed replay**

`revision(project_id, revision_id)` must require the exact version kind and manifest schema, authenticate revision/boundary/manifest/assessment hashes, load artifacts by the IDs frozen in the manifest, and compare kind, project, version, input hash, and content hash. It must never resolve a current head.

- [ ] **Step 6: Write RED then GREEN deterministic export tests**

The RED assertion must call export twice and require exact equality:

```python
first = service.export(project_id, revision.id)
second = service.export(project_id, revision.id)
assert first == second
assert first.filename == f"alphabet-company-research-{revision.id}.md"
assert first.media_type == "text/markdown"
assert sha256(first.content.encode("utf-8")).hexdigest() == first.content_hash
assert "not_answerable" in first.content
assert "No authenticated market price is bundled." in first.content
assert "Item 7, Results of Operations" in first.content
```

Render sections in a fixed order and sort only fields whose domain contract is a set. Preserve evidence fact and review order.

- [ ] **Step 7: Preserve the generic publication guard**

Extend the existing test to assert generic preview/publish still raise:

```text
generic publication is not available for company research projects
```

before and after a Company Research revision exists.

- [ ] **Step 8: Run service, repository, and generic publisher suites GREEN**

```bash
cd backend
uv run pytest \
  tests/underwriting/test_company_research_publication.py \
  tests/underwriting/test_company_research_persistence.py \
  tests/underwriting/test_revision_publisher.py \
  tests/underwriting/test_product_project.py -q
```

- [ ] **Step 9: Commit**

```bash
git add \
  backend/app/underwriting/persistence/product_repository.py \
  backend/app/underwriting/services/company_research_publication.py \
  backend/tests/underwriting/test_company_research_publication.py \
  backend/tests/underwriting/test_revision_publisher.py
git commit -m "feat: freeze company research revisions"
```

---

### Task 4: Expose the complete HTTP workflow

**Files:**

- Modify: `backend/app/underwriting/api/company_research_schemas.py`
- Modify: `backend/app/underwriting/api/company_research_router.py`
- Modify: `backend/tests/underwriting/test_company_research_api.py`

- [ ] **Step 1: Write RED public API tests**

Add one end-to-end test that uses HTTP for all mutations and reads:

```python
confirmation = client.post(
    f"{root}/{project_id}/judgment-confirmations",
    json={
        "schema_version": "underwriting.v1",
        "expected_lock_version": workspace["draft"]["lock_version"],
        "expected_memo_id": memo["id"],
        "expected_memo_content_hash": memo["content_hash"],
        "markdown": "Current formal evidence is insufficient.\n",
    },
)
assert confirmation.status_code == 200
assert confirmation.json()["preparation"]["status"] == "ready_to_freeze"

preview = client.post(
    f"{root}/{project_id}/publication-preview",
    json={"schema_version": "underwriting.v1", "expected_lock_version": 4},
)
assert preview.status_code == 200

published = client.post(
    f"{root}/{project_id}/publish",
    headers={"Idempotency-Key": "alphabet-live-freeze"},
    json={
        "schema_version": "underwriting.v1",
        "expected_lock_version": 4,
        "expected_manifest_hash": preview.json()["manifest_hash"],
    },
)
assert published.status_code == 201
revision_id = published.json()["id"]
assert client.get(f"{root}/{project_id}/revisions/{revision_id}").json() == published.json()
assert client.get(f"{root}/{project_id}/revisions/{revision_id}/export").status_code == 200
```

Snapshot and compare the exact evidence/gaps/decisions before confirmation and after publication.

- [ ] **Step 2: Add closed DTOs**

Add request/response models with `extra="forbid"` inherited from the existing base:

```python
class ConfirmCompanyResearchJudgmentRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)
    expected_memo_id: UUID
    expected_memo_content_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    markdown: StrictStr = Field(min_length=1, max_length=100_000)


class PreviewCompanyResearchPublicationRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)


class PublishCompanyResearchRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)
    expected_manifest_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
```

Response DTOs must use exact status literals for confirmation (`ready_to_freeze`) and revision completion (`completed`), closed optional investment fields, exact artifact descriptor shapes, and lowercase SHA-256 patterns.

- [ ] **Step 3: Add five router endpoints**

Expose:

```text
POST /projects/{project_id}/judgment-confirmations
POST /projects/{project_id}/publication-preview
POST /projects/{project_id}/publish
GET  /projects/{project_id}/revisions/{revision_id}
GET  /projects/{project_id}/revisions/{revision_id}/export
```

Use `commit_write` for confirmation and publish only. Use the existing `_read` wrapper for preview, replay, and export. Require `Idempotency-Key` on publish.

- [ ] **Step 4: Add bounded error and identity tests**

Test 409 for stale lock/idempotency conflicts, 422 for manifest mismatch and malformed payloads, 404 for foreign revision IDs, exact project/revision binding, and no internal provider text in any error body.

- [ ] **Step 5: Run API and service suites GREEN**

```bash
cd backend
uv run pytest \
  tests/underwriting/test_company_research_api.py \
  tests/underwriting/test_company_research_publication.py -q
```

- [ ] **Step 6: Commit**

```bash
git add \
  backend/app/underwriting/api/company_research_schemas.py \
  backend/app/underwriting/api/company_research_router.py \
  backend/tests/underwriting/test_company_research_api.py
git commit -m "feat: expose company research publication API"
```

---

### Task 5: Add strict frontend contracts and API methods

**Files:**

- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/investmentResearchApi.ts`
- Modify: `frontend/src/data/InvestmentResearchApi.test.ts`

- [ ] **Step 1: Write RED decoder and request tests**

Add response fixtures and assert the exact paths/methods/status codes:

```typescript
await api.confirmCompanyResearchJudgment(projectId, confirmationBody);
await api.previewCompanyResearchPublication(projectId, { schema_version: "underwriting.v1", expected_lock_version: 4 });
await api.publishCompanyResearch(projectId, publishBody, "alphabet-live-freeze");
await api.companyResearchRevision(projectId, revisionId);
await api.exportCompanyResearchRevision(projectId, revisionId);

expect(calls).toEqual([
  [`${root}/${projectId}/judgment-confirmations`, "POST"],
  [`${root}/${projectId}/publication-preview`, "POST"],
  [`${root}/${projectId}/publish`, "POST"],
  [`${root}/${projectId}/revisions/${revisionId}`, "GET"],
  [`${root}/${projectId}/revisions/${revisionId}/export`, "GET"],
]);
```

Add invalid-response cases for wrong project/revision, wrong status, unknown keys, uppercase hash, inconsistent `not_answerable` investment fields, mismatched artifact summary, and export content/hash mismatch.

- [ ] **Step 2: Run the frontend API tests and verify RED**

```bash
cd frontend
npm test -- InvestmentResearchApi.test.ts -t 'company research publication'
```

Expected: failures because the DTOs, decoders, and methods do not exist.

- [ ] **Step 3: Add generated-equivalent contract types**

Generate current backend OpenAPI to a temporary file, diff only the five new routes and schemas, and add the equivalent declarations to `frontend/src/contracts/v1.ts`. Do not modify `frontend/openapi.json`.

- [ ] **Step 4: Add exact runtime decoders**

Add closed-key checks and cross-field validation for confirmation, preview, revision, and export. For a `not_answerable` preview/revision require direction, confidence, value range, and return range to be null.

Add API methods using `requestJson`; bind every response to the requested project/revision and bind publish output to the preview request. Send `Idempotency-Key` only on publish.

- [ ] **Step 5: Verify export SHA-256 before returning content**

Implement:

```typescript
async function sha256Utf8(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
```

`exportCompanyResearchRevision` must throw `invalid_response` unless the recomputed digest equals `content_hash`.

- [ ] **Step 6: Run decoder tests and typecheck GREEN**

```bash
cd frontend
npm test -- InvestmentResearchApi.test.ts
npm run typecheck
```

- [ ] **Step 7: Commit**

```bash
git add \
  frontend/src/contracts/v1.ts \
  frontend/src/data/investmentResearchApi.ts \
  frontend/src/data/InvestmentResearchApi.test.ts
git commit -m "feat: bind company research publication API"
```

---

### Task 6: Complete the workbench user flow

**Files:**

- Modify: `frontend/src/features/investment-research/companyResearchView.ts`
- Modify: `frontend/src/features/investment-research/companyResearchView.test.ts`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.tsx`
- Modify: `frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx`

- [ ] **Step 1: Write RED pure-view tests for 85/95/100**

Add a pure action-state helper and assert:

```typescript
expect(publicationAction(awaitingWorkspace)).toEqual({ kind: "confirm_judgment", label: "确认当前判断" });
expect(publicationAction(readyWorkspace)).toEqual({ kind: "preview_freeze", label: "预览冻结版本" });
expect(publicationAction(completedWorkspace)).toEqual({ kind: "replay_export", label: "查看冻结版本" });
```

Reject impossible status/step/progress combinations and ensure completed snapshots are monotonic successors of ready-to-freeze snapshots.

- [ ] **Step 2: Write RED component tests for the full interaction**

Drive the real component with mocked HTTP adapter methods:

```text
85: editable memo + 确认当前判断
click confirm -> disabled mutation state -> refreshed 95 workspace
95: 预览冻结版本
preview dialog: Alphabet, GOOG, GOOGL, cutoff, not_answerable, immutable warning
click 冻结并发布 -> exactly one publish call -> refreshed 100 workspace
100: revision ID, publication time, 查看冻结版本, 导出 Markdown
click export -> client verifies hash -> one Blob download
```

Assert keyboard focus returns to the triggering button after a failed confirmation or publication. Assert all mutation buttons remain disabled during an active request and stale poll responses cannot replace a confirmed/published workspace.

- [ ] **Step 3: Run focused UI tests and verify RED**

```bash
cd frontend
npm test -- companyResearchView.test.ts ResearchWorkbenchPage.test.tsx \
  -t 'publication|judgment|freeze|export'
```

- [ ] **Step 4: Implement the 85% confirmation panel**

On the overview and version/memo module, render an accessible textarea initialized from the machine assessment. Submit current draft lock, memo ID/hash, and normalized Markdown to the high-level confirmation method. Refresh the entire workspace after success and announce `判断已确认，可以冻结版本。` through the existing success region.

- [ ] **Step 5: Implement the 95% preview and confirmation dialog**

The preview dialog must show the exact server response. The publish button must use one UUID idempotency key retained across response-loss retries, plus the preview's lock version and manifest hash. A changed workspace invalidates and closes the preview.

- [ ] **Step 6: Implement completed replay and export**

Store the returned revision ID, reload it through the revision endpoint, and render the frozen assessment/memo/artifact summary. Export only the selected frozen revision. Use a filename from the verified export envelope and revoke the temporary object URL after the click.

- [ ] **Step 7: Run UI, decoder, typecheck, and build GREEN**

```bash
cd frontend
npm test -- \
  companyResearchView.test.ts \
  ResearchWorkbenchPage.test.tsx \
  InvestmentResearchApi.test.ts
npm run typecheck
npm run build
```

- [ ] **Step 8: Commit**

```bash
git add \
  frontend/src/features/investment-research/companyResearchView.ts \
  frontend/src/features/investment-research/companyResearchView.test.ts \
  frontend/src/features/investment-research/ResearchWorkbenchPage.tsx \
  frontend/src/features/investment-research/ResearchWorkbenchPage.test.tsx
git commit -m "feat: complete company research publication UI"
```

---

### Task 7: Verify, merge, and run the live Alphabet closure

**Files:**

- No production file should be added in this task.
- Store temporary live snapshots outside the repository.

- [ ] **Step 1: Run fresh backend verification**

```bash
cd backend
uv run pytest \
  tests/underwriting/test_company_research_artifact_codec.py \
  tests/underwriting/test_company_research_persistence.py \
  tests/underwriting/test_company_research_publication.py \
  tests/underwriting/test_company_research_api.py \
  tests/underwriting/test_company_research_workbench.py \
  tests/underwriting/test_company_research_preparation_worker.py \
  tests/underwriting/test_revision_publisher.py \
  tests/underwriting/test_research_revision_diff.py \
  tests/underwriting/test_product_project.py -q
uv run ruff check app tests/underwriting/test_company_research_publication.py
uv run python -m compileall -q app
```

Expected: zero failures. PostgreSQL-only tests may skip only when `TEST_DATABASE_URL` is unset.

- [ ] **Step 2: Run fresh frontend verification**

```bash
cd frontend
npm test -- \
  InvestmentResearchApi.test.ts \
  companyResearchView.test.ts \
  ResearchWorkbenchPage.test.tsx \
  ResearchShell.test.tsx
npm run typecheck
npm run build
```

Expected: zero failures and a successful production build.

- [ ] **Step 3: Audit the protected files and diff**

```bash
git diff --check
git status --short
git diff -- frontend/openapi.json \
  docs/superpowers/plans/2026-08-23-archive-shell-and-identity.md
```

The two protected files must contain only the user's pre-existing changes and must not be staged.

- [ ] **Step 4: Snapshot the live 85% project before mutation**

Save workspace JSON and a database projection under a fresh `mktemp -d`. Record exact evidence/gaps IDs, versions, input/content hashes, source refs, payloads, ordered 6/1 decisions, historical basis fields, draft ID/lock, preparation status, and all existing model artifact heads.

- [ ] **Step 5: Rebuild and verify the one-click runtime**

```bash
scripts/one-click-runtime.sh up
scripts/verify-one-click-runtime.sh
```

Wait for transient health probes to recover; do not resend mutations during health polling.

- [ ] **Step 6: Execute each live mutation exactly once through the UI**

Use the visible workbench at:

```text
http://127.0.0.1:8080/research/projects/19a046e8-2f48-4e1e-949d-cdd84a66bb5e
```

Confirm the honest memo once, verify 95%, open the publication preview, publish once, and verify 100%. Do not use low-level draft or generic publisher routes.

- [ ] **Step 7: Verify replay, export, restart, and invariants**

Download the Markdown export, recompute its SHA-256, restart the runtime, replay the same revision, export it again, and require exact response/content/hash equality. Compare the final live workspace and database projection to the pre-mutation snapshot; only the confirmed memo successor, publication rows/events, preparation projection, base revision, and expected draft lock increments may differ.

- [ ] **Step 8: Commit any final test-only corrections separately**

If live verification exposes only a fixture expectation mismatch, reproduce it as RED, make the smallest test correction, rerun the complete affected suite, and use:

```bash
git commit -m "test: align company publication acceptance"
```

Do not alter production behavior without a new RED regression proving the live defect.
