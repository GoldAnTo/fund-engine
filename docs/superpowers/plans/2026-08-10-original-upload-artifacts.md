# Original Upload Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a researcher freeze an uploaded PDF or text file as an immutable original artifact, preserve it when parsing fails, and read its provenance and recovery state from the Case workspace.

**Architecture:** Add an append-only `document_upload_artifacts` ledger table keyed by `DocumentVersion`, storing the original bytes, content MIME, supplied filename, uploader and immutable object-version reference. A multipart command accepts an original file plus the existing source-governance declaration, creates a document and artifact before parsing, then either stores independently located spans or returns `parse_state=failed` without fabricating text. The existing JSON material commands remain text-snapshot commands; the frontend calls the multipart command only for a real file.

**Tech Stack:** FastAPI multipart upload, SQLAlchemy/Alembic, PostgreSQL bytea, pypdf text-layer parser, React/TypeScript, Vitest and Playwright.

---

### Task 1: Persist immutable uploaded originals

**Files:**
- Modify: `backend/app/models/ledger.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0036_document_upload_artifacts.py`
- Test: `backend/tests/test_document_upload_artifacts.py`

- [ ] **Step 1: Write the failing persistence test**

```python
def test_upload_artifact_is_append_only_and_records_exact_bytes(session):
    artifact = DocumentUploadArtifact(
        document_version_id=document.id,
        content_sha256=hashlib.sha256(b"original").hexdigest(),
        object_version="sha256:" + hashlib.sha256(b"original").hexdigest(),
        storage_kind="database_blob",
        file_name="research.pdf",
        mime_type="application/pdf",
        byte_size=len(b"original"),
        raw_bytes=b"original",
        uploaded_by="human:lin",
        retention_policy="case_retained",
        created_at=now,
    )
    session.add(artifact)
    session.commit()
    assert session.get(DocumentUploadArtifact, artifact.id).raw_bytes == b"original"
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(DocumentUploadArtifact).values(file_name="other.pdf"))
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest backend/tests/test_document_upload_artifacts.py -q`

Expected: FAIL because `DocumentUploadArtifact` does not exist.

- [ ] **Step 3: Add the model, immutable-table guard, model import, and migration**

```python
class DocumentUploadArtifact(Base):
    __tablename__ = "document_upload_artifacts"
    __table_args__ = (UniqueConstraint("document_version_id", name="uq_document_upload_artifacts_document"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    document_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_versions.id"), nullable=False, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_version: Mapped[str] = mapped_column(String(96), nullable=False)
    storage_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="database_blob")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    retention_policy: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

The migration creates the table, its document index and the PostgreSQL `no_update_document_upload_artifacts` / `no_delete_document_upload_artifacts` triggers. Add the table name to `IMMUTABLE_TABLES`.

- [ ] **Step 4: Run the persistence test and migration smoke test**

Run: `pytest backend/tests/test_document_upload_artifacts.py -q && cd backend && alembic upgrade head`

Expected: PASS; the clean verification database advances from `0035` to `0036`.

- [ ] **Step 5: Commit the ledger slice**

```bash
git add backend/app/models/ledger.py backend/app/models/__init__.py backend/alembic/versions/0036_document_upload_artifacts.py backend/tests/test_document_upload_artifacts.py
git commit -m "feat: retain immutable uploaded document originals"
```

### Task 2: Freeze and parse uploaded PDF/text material without data loss

**Files:**
- Create: `backend/app/services/document_uploads.py`
- Modify: `backend/app/services/event_research.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/api/v1/event_research.py`
- Test: `backend/tests/test_event_research_uploads.py`

- [ ] **Step 1: Write failing API tests for successful text/PDF intake and failed PDF parsing**

```python
response = cmd_client.post(
    f"/api/v1/event-research/{case_id}/uploaded-materials",
    files={"file": ("research.pdf", scanned_pdf_bytes, "application/pdf")},
    data={"actor": "human:lin", "source_metadata": json.dumps(metadata)},
)
assert response.status_code == 201
document_id = response.json()["document_version_id"]
assert response.json()["parse_state"] == "failed"
detail = cmd_client.get(f"/api/v1/documents/{document_id}").json()
assert detail["document"]["parse_state"] == "failed"
assert cmd_session.scalar(select(DocumentUploadArtifact.raw_bytes).where(DocumentUploadArtifact.document_version_id == UUID(document_id))) == scanned_pdf_bytes
assert cmd_session.scalars(select(SourceSpan).where(SourceSpan.document_version_id == UUID(document_id))).all() == []
```

Add a text-file case that asserts `parse_state == "partial"`, an exact source span, a `text/plain` artifact, and no `ResearchRun` is created. Add a text-layer PDF case asserting parsed page/paragraph locator spans. Add a published-case assertion that the command is rejected with 422, preserving its explicit decision workflow.

- [ ] **Step 2: Run focused API tests and verify they fail**

Run: `pytest backend/tests/test_event_research_uploads.py -q`

Expected: FAIL because `/uploaded-materials` is not registered.

- [ ] **Step 3: Implement a focused upload service and command**

```python
class DocumentUploadService:
    def freeze_case_material(self, *, case_id: UUID, raw: bytes, file_name: str,
        mime_type: str, actor: str, source_metadata: dict[str, object]) -> DocumentVersion:
        document = self._documents.freeze(raw=raw, source_url=f"upload://{sha256(raw).hexdigest()}",
            parser_version=parser_version_for(mime_type), title=file_name,
            byte_size=len(raw), parse_state="partial",
            source_authority=source_metadata.get("authority_level", "user_supplied"))
        self._artifacts.insert_once(document, raw, file_name, mime_type, actor, source_metadata)
        self._documents.attach_to_case(research_case_id=case_id, document_version_id=document.id)
        SourceGovernanceService(self._session).record_event_intake(
            document=document, source_type="uploaded_file", source_metadata=source_metadata, declared_by=actor)
        self._persist_spans_or_mark_failed(document, raw, mime_type)
        return document
```

`parser_version_for` accepts only `application/pdf`, `text/plain`, `text/markdown`, and `text/csv`; unsupported MIME returns `ValidationFailedError` before a document is created. PDF parsing uses `PypdfAdapter`; `PdfParseError`, malformed bytes, or zero spans append no fake span and create the `DocumentVersion` with `parse_state="failed"` before returning. The API receives `UploadFile`, rejects empty or files larger than 20 MiB, parses `source_metadata` JSON into a dict, emits `event_material_attached`, and commits only after artifact, governance record and parse result are present.

- [ ] **Step 4: Run focused API tests**

Run: `pytest backend/tests/test_event_research_uploads.py -q`

Expected: PASS with the artifact, source contract, parse result and Case attachment observable through existing document reads.

- [ ] **Step 5: Commit the upload command slice**

```bash
git add backend/app/services/document_uploads.py backend/app/services/event_research.py backend/app/schemas/v1/event_research.py backend/app/api/v1/event_research.py backend/tests/test_event_research_uploads.py
git commit -m "feat: freeze uploaded case materials before parsing"
```

### Task 3: Surface original-file truthfully in the Case workspace

**Files:**
- Modify: `backend/app/schemas/v1/documents.py`
- Modify: `backend/app/queries/documents.py`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/features/events/EventCreatePage.tsx`
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os.css`
- Test: `backend/tests/test_document_read_api_v1.py`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`
- Test: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: Write failing read/UI tests**

```python
detail = api_client.get(f"/api/v1/documents/{uploaded_document.id}").json()
assert detail["document"]["original_file"] == {
    "file_name": "research.pdf", "mime_type": "application/pdf",
    "byte_size": len(pdf_bytes), "object_version": f"sha256:{digest}",
    "uploaded_by": "human:lin", "retention_policy": "case_retained",
}
```

```tsx
await user.upload(screen.getByLabelText("上传原件文件"), new File(["披露正文"], "disclosure.txt", { type: "text/plain" }));
expect(await screen.findByText("已冻结原件 · disclosure.txt")).toBeVisible();
expect(screen.getByText("文本原件已保存；解析内容作为定位片段另行展示。"))
  .toBeVisible();
```

Add a PDF failure UI assertion for “原件已冻结，正文未解析；可补充正文并标注页码”, and assert it never claims that the original is a readable PDF.

- [ ] **Step 2: Run focused backend/frontend tests and verify they fail**

Run: `pytest backend/tests/test_document_read_api_v1.py -q && npm test -- --run ResearchOsPages`

Expected: FAIL because `original_file` and the multipart UI do not exist.

- [ ] **Step 3: Add read DTO, adapter and multipart user interaction**

```tsx
const formData = new FormData();
formData.set("file", selectedFile);
formData.set("actor", "human:researcher");
formData.set("source_metadata", JSON.stringify(metadata));
const frozen = await researchClient.uploadCaseMaterial(caseId, formData);
navigate(`/events/${caseId}/documents?document=${frozen.documentVersionId}`);
```

The document reader renders “原件文件” only if the server returns an artifact. It displays filename, MIME, byte size, object version, uploader and retention policy. It labels the content as `PDF 原件已冻结` or `文本原件已冻结`; the old “内容快照（当前 V1 未提供原件文件）” stays only for legacy text snapshot records. The inbox shows allowed original-file types and the 20 MiB limit; the existing pasted/authorized-provider flows remain JSON snapshot commands.

- [ ] **Step 4: Run focused UI and browser tests**

Run: `npm test -- --run ResearchOsPages && npm run e2e -- --grep 'original file|上传原件'`

Expected: PASS; the end-to-end flow can upload a text original, navigate to its reader, and inspect immutable original-file metadata.

- [ ] **Step 5: Commit the read and UI slice**

```bash
git add backend/app/schemas/v1/documents.py backend/app/queries/documents.py frontend/src/contracts/v1.ts frontend/src/domain/eventResearch.ts frontend/src/data/httpResearchAdapter.ts frontend/src/features/events/EventCreatePage.tsx frontend/src/features/case/CasePages.tsx frontend/src/styles/research-os.css backend/tests/test_document_read_api_v1.py frontend/src/tests/ResearchOsPages.test.tsx frontend/e2e/research-os.spec.ts
git commit -m "feat: show frozen uploaded originals in case reader"
```

### Task 4: Verify the real stack and preserve the next parser boundary

**Files:**
- Modify: `docs/superpowers/specs/2026-08-08-trusted-report-intake-recovery-design.md`
- Test: `backend/tests/test_event_research_uploads.py`
- Test: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: Add a real-stack acceptance scenario to the existing focused tests**

```python
assert response.json()["parse_state"] == "failed"
assert response.json()["next_action"] == "supplement_original"
assert response.json()["document_version_id"] == detail["document"]["id"]
```

The test must assert the same original `document_version_id` remains after refresh/read, no `ResearchRun`, `SourceStatement`, market task, or stock/fund expression is created.

- [ ] **Step 2: Run backend suite, frontend suite, and a clean-DB migration/runtime path**

Run: `cd backend && pytest -q && alembic upgrade head`

Run: `cd frontend && npm test -- --run && npm run e2e`

Expected: all tests pass; the current migration chain reaches `0036` on the isolated verification database. Start the worktree backend and Vite app against that clean database, upload a text original in the browser, and verify its reader metadata after refresh.

- [ ] **Step 3: Record the supported-parser boundary in the confirmed design**

Add: “本次真实原件上传支持 PDF 文本层和文本/Markdown/CSV；扫描 PDF 保留原件并进入补充正文恢复状态。Office 原件尚未接入解析器，前端不显示为可上传格式。”

- [ ] **Step 4: Commit verification and documentation**

```bash
git add docs/superpowers/specs/2026-08-08-trusted-report-intake-recovery-design.md backend/tests/test_event_research_uploads.py frontend/e2e/research-os.spec.ts
git commit -m "docs: define supported original upload boundary"
```

## Plan self-review

- Spec coverage: Tasks 1-3 implement original bytes, MIME, uploader, object version, retention, immutable lifecycle, visible source provenance, PDF/text parsing and honest recovery state. Task 4 verifies no automatic research side effect and records the Office boundary.
- Scope: Provider records, source-contract enforcement, independent supplements, candidate review and run transparency already exist; this plan does not duplicate them.
- Type consistency: `DocumentUploadArtifact`, `original_file`, and `uploadCaseMaterial` are the only new cross-layer names; the command is limited to non-published Cases, preserving `published-material-decisions`.
