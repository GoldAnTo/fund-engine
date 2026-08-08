# Safe Report Intake Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve report/PDF recovery without letting a supplement, parser or extractor create a formal statement, scope, market task or research run before the approved trust and researchability gates.

**Architecture:** Implement this as P0-0 on top of `codex/event-impact-penetration`, where the submitted report workflow lives. An immutable source-contract record and document-source record govern each original or pasted snapshot; a supplement is a separately frozen document linked to its original, never a span appended to that original. The report intake service returns a typed next action and the frontend shows a dedicated action card; candidate review and research readiness are deliberately delegated to P0-A and P0-B.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, React, TypeScript, Vitest, pytest, SQLite/PostgreSQL.

---

## Scope, dependencies, and release order

This plan implements only the safe intake/recovery boundary in the confirmed [trusted-report-intake specification](../specs/2026-08-08-trusted-report-intake-recovery-design.md). It is independently releasable because it removes unsafe automatic writes while retaining the user-visible recovery path.

It must run in this order:

1. execute this plan on a branch based on `codex/event-impact-penetration` after merging `main` documentation;
2. rebase and execute [P0-A extraction trust gate](2026-08-08-report-extraction-trust-gate.md), renumbering its migration after this plan's `0037` migration;
3. rebase and execute [P0-B metric/outcome gate](2026-08-08-metric-registry-outcome-gate.md), again using the next free migration revision;
4. write separate plans for tenant/RBAC enforcement and mechanism-template/verification-rule authoring before declaring commercial source governance or a Case `evidence_ready` state complete.

Do not execute the existing P0-A/P0-B migration filenames `0019`/`0020` verbatim on this branch: its current Alembic head is `0036_report_embed_grants.py`. The worker must allocate the next revision from the actual head at execution time and update that plan's file references in the same documentation-only preparation commit.

P0-0 does not expose a new unauthenticated document-download API. It stores tenant and permission facts so later RBAC can enforce them; existing broad read endpoints must not be advertised as tenant-safe until that separate authorization plan is implemented.

### File map

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0037_source_contract_report_snapshots.py` | Immutable source contracts, document source records and supplement links; PostgreSQL immutability triggers. |
| `backend/app/models/ledger.py` | ORM models and immutable-table registration. |
| `backend/app/repositories/source_contracts.py` | Append-only source-contract/source-record/supplement-link writes and effective-permission reads. |
| `backend/app/services/source_contracts.py` | Permission intersection, source admission validation and snapshot creation contract. |
| `backend/app/services/report_research.py` | Removes direct statement/scope/task writes from report intake; freezes independent supplements and returns typed intake outcomes. |
| `backend/app/schemas/v1/report_research.py` | Strict source-contract, intake-state and next-action response DTOs. |
| `backend/app/api/v1/report_research.py` | Contract-aware create/supplement/read endpoints and controlled error mapping. |
| `frontend/src/domain/eventResearch.ts` | Report intake and next-action types, including strict recovery route state. |
| `frontend/src/data/httpResearchAdapter.ts` | One wire DTO mapper for intake outcomes and scoped supplement requests. |
| `frontend/src/data/mockResearchAdapter.ts` | Deterministic pending-supplement and pending-extraction fixtures. |
| `frontend/src/pages/prototype/ReportResearchCreateScreen.tsx` | Explicit resume/new/cancel branches; no automatic research wording. |
| `frontend/src/pages/prototype/ReportResearchIntakeScreen.tsx` | Dedicated “next action” screen for one report Case. |
| `frontend/src/App.tsx` | Route `/reports/:caseId/intake`. |
| `backend/tests/test_source_contracts.py` | Immutability, permission intersection, supplement independence and service tests. |
| `backend/tests/test_report_research.py` | Intake side-effect and API regression tests. |
| `frontend/src/tests/ReportResearchCreateScreen.test.tsx` | Resume/new/cancel and redirect tests. |
| `frontend/src/tests/ReportResearchIntakeScreen.test.tsx` | State copy and unique-primary-action tests. |

### Stable contracts introduced by P0-0

```python
SourceAdmissionType = Literal[
    "licensed_provider", "uploaded_file", "pasted_snapshot", "public_url"
]
ReportIntakeState = Literal["needs_supplement", "pending_candidate_extraction"]
ReportIntakeAction = Literal["supplement_text", "extract_candidates"]

@dataclass(frozen=True, slots=True)
class SourcePermission:
    may_display: bool
    may_search: bool
    may_ai_process: bool
    may_export: bool
    may_api_use: bool
    retention_until: datetime | None = None
```

`SourceContractVersion` is immutable and has `provider_name`, `tenant_id`, five capability booleans, `region`, `effective_from`, `effective_until`, `retention_until`, `deletion_policy`, `downstream_restrictions`, `approved_by`, `reason` and `created_at`. `DocumentSourceRecord` is immutable and records the document, contract, admission type, tenant, source actor, optional provider record ID, verification state and acquisition request metadata. `DocumentSupplementLink` is immutable and has `original_document_version_id`, `supplement_document_version_id`, `claimed_page_reference`, `created_by` and `created_at`.

## Task 1: Reconcile the implementation baseline before changing behavior

**Files:**
- Modify: no application files
- Verify: `docs/superpowers/specs/2026-08-08-trusted-report-intake-recovery-design.md`

- [ ] **Step 1: Verify the divergence is documentation-only on `main`**

Run:

```bash
git diff --name-only 080acdbd458a0cbd888984e372c7feb51da38ae2..main | rg -v '^docs/'
```

Expected: no output. Any application file output stops this plan and requires an explicit merge-conflict design review.

- [ ] **Step 2: Create the dedicated integration worktree and merge the confirmed documents**

```bash
git worktree add -b codex/trusted-report-intake-recovery ../fund-engine-trusted-intake codex/event-impact-penetration
git -C ../fund-engine-trusted-intake merge --no-ff main -m "merge: adopt confirmed research intake design"
```

Expected: the worktree is on `codex/trusted-report-intake-recovery`, has Alembic revision `0036`, and contains this plan and its specification.

- [ ] **Step 3: Confirm the report workflow baseline**

Run:

```bash
cd ../fund-engine-trusted-intake
backend/.venv/bin/python -m pytest backend/tests/test_report_research.py backend/tests/test_report_research_scope_api.py -q
cd frontend && npm test -- --run src/tests/ReportResearchCreateScreen.test.tsx src/tests/HttpResearchAdapter.test.ts
```

Expected: the focused report suite passes before the behavior change.

- [ ] **Step 4: Commit the merge baseline only when a merge commit was created**

```bash
git -C ../fund-engine-trusted-intake status --short
git -C ../fund-engine-trusted-intake log -1 --oneline
```

Expected: clean worktree. Do not create an empty commit.

## Task 2: Add immutable source contracts and independent supplement provenance

**Files:**
- Create: `backend/alembic/versions/0037_source_contract_report_snapshots.py`
- Modify: `backend/app/models/ledger.py`
- Create: `backend/app/repositories/source_contracts.py`
- Test: `backend/tests/test_source_contracts.py`

- [ ] **Step 1: Write failing provenance and immutability tests**

```python
def test_supplement_is_a_new_document_with_its_own_contract_and_link(session, services):
    original, original_record = services.admit_original_pdf(
        raw=b"%PDF-original", tenant_id="tenant-a", contract_id=services.restricted.id
    )
    supplement, record, link = services.admit_supplement_snapshot(
        original_document_id=original.id,
        raw="订单增长。".encode(), tenant_id="tenant-a",
        supplement_contract_id=services.permissive.id,
        claimed_page_reference="第 3 页", created_by="alice",
    )
    assert supplement.id != original.id
    assert record.admission_type == "pasted_snapshot"
    assert link.original_document_version_id == original.id
    assert link.supplement_document_version_id == supplement.id
    assert services.effective_permission(record).may_export is False

def test_source_contract_and_supplement_link_reject_updates_and_deletes(session, records):
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(SourceContractVersion).values(tenant_id="tenant-b"))
    with pytest.raises(ImmutableLedgerError):
        session.execute(delete(DocumentSupplementLink))
```

- [ ] **Step 2: Run the new tests and verify failure**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_source_contracts.py -q`

Expected: collection fails because the models and service do not exist.

- [ ] **Step 3: Add append-only storage and PostgreSQL guards**

Create revision `0037_source_contract_report_snapshots.py` with the three tables named above. Use foreign keys to `document_versions` and `source_contract_versions`, unique `(document_version_id, tenant_id, source_contract_version_id)`, unique `supplement_document_version_id`, and indexes on `tenant_id` plus original/supplement document IDs. Add all three tables to `IMMUTABLE_TABLES` in `ledger.py`, using the same update/delete trigger pattern as `0036_report_embed_grants.py`.

```python
class DocumentSupplementLink(Base):
    __tablename__ = "document_supplement_links"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    original_document_version_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("document_versions.id"), nullable=False)
    supplement_document_version_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("document_versions.id"), nullable=False, unique=True)
    claimed_page_reference: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Implement repository-only append methods**

`SourceContractRepository` exposes `append_contract()`, `add_document_source_record()`, `add_supplement_link()`, `contract_for_record()` and `source_record_for_document()`. It exposes no update/delete method. In `add_supplement_link()` reject self-links and originals/supplements not attached to the same Case in the service layer rather than silently changing case ownership.

- [ ] **Step 5: Run model and repository tests**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_source_contracts.py tests/test_documents.py -q`

Expected: PASS; a supplement has a distinct document hash/provenance record and all ledger rows reject mutation.

- [ ] **Step 6: Commit the storage slice**

```bash
git add backend/alembic/versions/0037_source_contract_report_snapshots.py backend/app/models/ledger.py backend/app/repositories/source_contracts.py backend/tests/test_source_contracts.py
git commit -m "feat: add source contracts and report snapshots"
```

## Task 3: Admit source snapshots with restrictive permission intersection

**Files:**
- Create: `backend/app/services/source_contracts.py`
- Modify: `backend/app/services/report_research.py`
- Test: `backend/tests/test_source_contracts.py`

- [ ] **Step 1: Write failing admission tests**

```python
def test_supplement_permission_is_the_intersection_of_original_and_pasted_contracts(services):
    effective = services.intersect(
        SourcePermission(may_display=True, may_search=True, may_ai_process=True, may_export=False, may_api_use=False),
        SourcePermission(may_display=True, may_search=False, may_ai_process=True, may_export=True, may_api_use=True),
    )
    assert effective == SourcePermission(True, False, True, False, False)

def test_unknown_or_expired_contract_cannot_enable_ai_processing(services):
    with pytest.raises(ValidationError, match="source contract does not permit AI processing"):
        services.require_ai_processing(services.expired_contract)
```

- [ ] **Step 2: Run the admission tests and verify failure**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_source_contracts.py -q`

Expected: FAIL because `SourcePermission` and admission methods do not exist.

- [ ] **Step 3: Implement pure permission and admission services**

Implement immutable `SourcePermission` and `intersect_permissions()`: every boolean is logical AND; `retention_until` is the earliest non-null date; region and downstream restrictions are retained as the strictest compatible values. `require_ai_processing()` rejects a missing, expired or `may_ai_process=False` contract. `admit_supplement_snapshot()` freezes the pasted bytes into a new document with a generated non-HTTP `pasted://report-supplement/<sha256>` identity, attaches it to the original Case, writes a `DocumentSourceRecord`, writes a `DocumentSupplementLink`, and creates its own `SourceSpan` with `input_kind="recovery_text"`, `claimed_page_reference` and `parser="user-pasted-report-v1"`. It must not call `ResearchService.add_statement()`.

- [ ] **Step 4: Add report-service test that forbids direct formal writes**

```python
def test_recovery_creates_no_statement_scope_or_market_task(cmd_client, cmd_session, source_contract):
    failed = create_failed_pdf(cmd_client, source_contract.id)
    recovered = cmd_client.post(f"/api/v1/report-research/{failed.case_id}/documents/{failed.document_id}/supplement", json={"content": "研报观点：供应商甲受益。", "source_contract_id": str(source_contract.id)})
    assert recovered.status_code == 201
    assert cmd_session.scalars(select(SourceStatement)).all() == []
    assert cmd_session.scalars(select(ReportResearchScopeVersion)).all() == []
    assert cmd_session.scalars(select(ResearchTask).where(ResearchTask.task_type == "report_market_impact")).all() == []
```

- [ ] **Step 5: Replace intake side effects with an intake outcome**

In `ReportResearchService.create_text()`, `create_pdf()` and `supplement_text()`, retain document freezing, blob persistence, span creation and case attachment. Remove calls to `_extract_claim_statement_ids()`, `_create_initial_scope()` and `_schedule_market_impact()`. Return:

```python
ReportIntakeOutcome(
    case=case,
    document=original_document,
    intake_state="needs_supplement" if parse_failed else "pending_candidate_extraction",
    next_action="supplement_text" if parse_failed else "extract_candidates",
    blocking_reason="PDF has no readable text" if parse_failed else None,
)
```

Keep `ReportClaimExtractor` temporarily for P0-A migration compatibility, but do not invoke it from any report intake or recovery route.

- [ ] **Step 6: Run focused backend regressions**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_source_contracts.py tests/test_report_research.py tests/test_report_research_scope_api.py -q`

Expected: PASS; PDF bytes remain retrievable through existing controlled behavior, recovery creates a distinct snapshot, and no automatic formal research object is written.

- [ ] **Step 7: Commit the service slice**

```bash
git add backend/app/services/source_contracts.py backend/app/services/report_research.py backend/tests/test_source_contracts.py backend/tests/test_report_research.py
git commit -m "feat: make report recovery source-safe"
```

## Task 4: Expose typed intake state and one explicit next action

**Files:**
- Modify: `backend/app/schemas/v1/report_research.py`
- Modify: `backend/app/api/v1/report_research.py`
- Modify: `backend/app/queries/report_wiki.py`
- Test: `backend/tests/test_report_research.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_failed_pdf_returns_supplement_action_not_ready_scope(cmd_client, source_contract):
    response = upload_failed_pdf(cmd_client, source_contract.id)
    assert response.status_code == 201
    assert response.json()["intake_state"] == "needs_supplement"
    assert response.json()["next_action"] == {
        "kind": "supplement_text",
        "label": "补充正文并标注页码",
        "unlock_message": "完成后可抽取待审核陈述。",
    }
    assert "initial_scope_version" not in response.json()

def test_intake_read_model_never_serves_wiki_before_reviewed_scope(cmd_client, pending_case):
    response = cmd_client.get(f"/api/v1/report-research/{pending_case.id}/intake")
    assert response.status_code == 200
    assert response.json()["next_action"]["kind"] == "extract_candidates"
```

- [ ] **Step 2: Run the API tests and verify failure**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_report_research.py -q`

Expected: FAIL because the state and next-action DTOs do not exist.

- [ ] **Step 3: Implement strict DTOs and read endpoint**

Replace `ReportResearchState` with `ReportIntakeState` and add:

```python
class CreateReportResearchRequest(V1Model):
    # Existing title/publisher/content fields remain unchanged.
    source_contract_id: uuid.UUID

class SupplementReportResearchRequest(V1Model):
    content: str = Field(min_length=1)
    source_contract_id: uuid.UUID
    page_reference: str | None = Field(default=None, max_length=200)

class ReportIntakeNextActionDTO(V1Model):
    kind: Literal["supplement_text", "extract_candidates"]
    label: str
    unlock_message: str

class ReportIntakeResponse(V1Model):
    case: ReportResearchCaseDTO
    primary_document: ReportResearchDocumentDTO
    supplement_documents: list[ReportResearchDocumentDTO]
    intake_state: ReportIntakeState
    blocking_reason: str | None
    next_action: ReportIntakeNextActionDTO
```

Expose `GET /report-research/{case_id}/intake`. The endpoint returns `404` for an unknown Case, and `409 report_intake_not_ready` when a caller requests the Wiki before a reviewed scope exists. Remove `initial_scope_version` and `source_statement_ids` from intake create/supplement responses; those are not facts the intake is allowed to create.

Add required `source_contract_id` to JSON create requests, PDF upload query parameters and supplement JSON requests. Reject an unknown contract with the existing typed validation error and reject a contract from another tenant in the future trusted tenant-context dependency; P0-0 records the tenant boundary but does not claim to authenticate it.

- [ ] **Step 4: Run backend API and Wiki regressions**

Run: `cd backend && ./.venv/bin/python -m pytest tests/test_report_research.py tests/test_report_wiki_api.py -q`

Expected: PASS; pending intake has one action and existing reviewed scopes still render their immutable Wiki.

- [ ] **Step 5: Commit the API slice**

```bash
git add backend/app/schemas/v1/report_research.py backend/app/api/v1/report_research.py backend/app/queries/report_wiki.py backend/tests/test_report_research.py
git commit -m "feat: expose report intake next actions"
```

## Task 5: Make recovery UI explicit and route to the action card

**Files:**
- Create: `frontend/src/pages/prototype/ReportResearchIntakeScreen.tsx`
- Modify: `frontend/src/pages/prototype/ReportResearchCreateScreen.tsx`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/tests/ReportResearchCreateScreen.test.tsx`
- Test: `frontend/src/tests/ReportResearchIntakeScreen.test.tsx`

- [ ] **Step 1: Write failing UI tests for each recovery branch**

```tsx
it("clears the recovery target and URL before creating a new report", async () => {
  renderCreate("/reports/new?resume_case_id=old-case&resume_document_id=old-doc&resume_input_kind=pdf_upload&resume_reason=needs_text_or_pages");
  await user.click(screen.getByRole("button", { name: "放弃恢复并新建资料" }));
  await user.type(screen.getByLabelText("研报标题"), "新研报");
  await user.type(screen.getByLabelText("研报正文"), "观点：订单增长。" );
  await user.click(screen.getByRole("button", { name: "冻结资料并进入下一步" }));
  expect(adapter.createReportResearch).toHaveBeenCalledOnce();
  expect(adapter.supplementReportResearch).not.toHaveBeenCalled();
});

it("shows one action card for a pending candidate extraction", async () => {
  renderIntake({ intakeState: "pending_candidate_extraction", nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" } });
  expect(screen.getByRole("button", { name: "开始抽取待审核陈述" })).toBeVisible();
  expect(screen.getAllByRole("button", { name: /开始|审核|补充/ })).toHaveLength(1);
});
```

- [ ] **Step 2: Run the UI tests and verify failure**

Run: `cd frontend && npm test -- --run src/tests/ReportResearchCreateScreen.test.tsx src/tests/ReportResearchIntakeScreen.test.tsx`

Expected: FAIL because the action screen and explicit abandon flow do not exist.

- [ ] **Step 3: Implement one strict route-state helper and DTO mapper**

Define `RecoveryRouteState` with `parseRecoveryRouteState(search: URLSearchParams): RecoveryRouteState | null`. It accepts only all four fields and the two known reasons; malformed URLs return `null`. Define `clearRecoveryRoute()` to set React recovery state and target to `null`, then replace the route with `/reports/new`.

Create one `mapReportIntake(dto)` function in `httpResearchAdapter.ts`; do not repeat intake wire types in list/create/supplement methods. The adapter sends `source_contract_id` for original intake and supplement calls.

- [ ] **Step 4: Implement screen behavior**

`ReportResearchCreateScreen` has these buttons only when relevant:

```text
needs_supplement: Continue supplement / Abandon recovery and create new material / Cancel
new material: Freeze material and enter next step
```

After successful create or supplement, route to `/reports/<caseId>/intake`, not `/reports/<caseId>`. `ReportResearchIntakeScreen` displays status, blocking reason, one primary action and its unlock message. Replace all “创建并开始自动研究” and “自动开始收集可核验数据” copy with state-specific wording.

- [ ] **Step 5: Run frontend regressions**

Run: `cd frontend && npm test -- --run src/tests/ReportResearchCreateScreen.test.tsx src/tests/ReportResearchIntakeScreen.test.tsx src/tests/HttpResearchAdapter.test.ts`

Expected: PASS; a new report cannot accidentally supplement an old Case, reload continues an explicit recovery, and each pending state has exactly one primary action.

- [ ] **Step 6: Commit the frontend slice**

```bash
git add frontend/src/pages/prototype/ReportResearchIntakeScreen.tsx frontend/src/pages/prototype/ReportResearchCreateScreen.tsx frontend/src/domain/eventResearch.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/data/researchClient.ts frontend/src/App.tsx frontend/src/tests/ReportResearchCreateScreen.test.tsx frontend/src/tests/ReportResearchIntakeScreen.test.tsx
git commit -m "feat: guide report intake to its next action"
```

## Task 6: Run the P0-0 release gate and hand off dependencies

**Files:**
- Modify: `docs/superpowers/plans/2026-08-08-report-extraction-trust-gate.md`
- Modify: `docs/superpowers/plans/2026-08-08-metric-registry-outcome-gate.md`
- Test: `backend/tests/test_source_contracts.py`

- [ ] **Step 1: Run the complete focused suite**

Run:

```bash
cd backend && ./.venv/bin/python -m pytest tests/test_source_contracts.py tests/test_report_research.py tests/test_report_research_scope_api.py tests/test_report_wiki_api.py -q
cd ../frontend && npm test -- --run src/tests/ReportResearchCreateScreen.test.tsx src/tests/ReportResearchIntakeScreen.test.tsx src/tests/HttpResearchAdapter.test.ts
```

Expected: all selected tests pass. A single skipped PostgreSQL race test is allowed only when the suite labels it `pg_only`; all SQLite assertions must pass.

- [ ] **Step 2: Rebase the two dependent plan documents' migration references**

Change P0-A's first migration reference from `0020_atomic_claim_candidates.py` to `0038_atomic_claim_candidates.py`, and P0-B's first migration reference from `0019_metric_registry_outcome_binding.py` to `0039_metric_registry_outcome_binding.py`. Add each plan's P0-0 dependency statement; do not change their domain behavior.

- [ ] **Step 3: Verify no direct intake-to-formal path remains**

Run:

```bash
rg -n "_extract_claim_statement_ids\(|_create_initial_scope\(|_schedule_market_impact\(" backend/app/services/report_research.py
```

Expected: these methods may remain as uncalled compatibility code until P0-A removes them, but `create_text`, `create_pdf` and `supplement_text` must contain none of these calls.

- [ ] **Step 4: Commit the release gate and dependency handoff**

```bash
git add docs/superpowers/plans/2026-08-08-report-extraction-trust-gate.md docs/superpowers/plans/2026-08-08-metric-registry-outcome-gate.md
git commit -m "docs: sequence report intake trust gates"
```

## P0-0 completion criteria

P0-0 is complete only when every result below holds:

1. a supplement is an immutable, separately hashed document snapshot with an explicit parent link and page claim;
2. its effective permission is no broader than both source contracts;
3. report intake and recovery write no `SourceStatement`, `ReportClaim`, `ReportResearchScopeVersion`, `ResearchTask` or `ResearchRun`;
4. every intake response has an explicit next action, and no response calls an unresolved intake “ready for research”;
5. the UI cannot silently keep a recovery Case target after the user chooses to create new material;
6. the branch has a clean focused test run and P0-A/P0-B are rebased to migrations after `0037`.
