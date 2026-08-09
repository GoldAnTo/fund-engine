# Event Case Tenant Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every event-research Case, its frozen materials and its visible workbench data accessible only through a server-resolved tenant identity.

**Architecture:** A bearer token is mapped to one tenant only through the server environment. An immutable `CaseTenantAdmission` binds a Case to the first source that opened it; `DocumentVersion` remains globally content-addressed but does not imply access. A focused authorization service is injected into event creation, event Case reads/writes and document reads so a foreign tenant sees no Case/document existence. Existing non-event research APIs remain outside this first slice and will be migrated in follow-up slices using the same boundary.

**Tech Stack:** FastAPI dependencies, SQLAlchemy/Alembic, PostgreSQL and SQLite migration tests, pytest/TestClient, generated OpenAPI contract.

---

### Task 1: Trusted tenant identity and immutable Case admission

**Files:**
- Create: `backend/app/api/v1/tenant_context.py`
- Create: `backend/app/services/case_tenant_access.py`
- Create: `backend/alembic/versions/0038_case_tenant_admissions.py`
- Modify: `backend/app/models/ledger.py`
- Modify: `backend/app/errors.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_event_case_tenant_access.py`

- [ ] **Step 1: Write failing tests for missing, invalid and valid bearer credentials**

```python
def test_event_routes_reject_a_missing_or_unknown_tenant_token(cmd_client, monkeypatch):
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"token-a":"team-a"}')
    assert cmd_client.get("/api/v1/event-research").status_code == 401
    assert cmd_client.get(
        "/api/v1/event-research", headers={"Authorization": "Bearer unknown"}
    ).status_code == 403
```

- [ ] **Step 2: Run the new credential test and verify it fails because no tenant dependency exists**

Run: `backend/.venv/bin/python -m pytest tests/test_event_case_tenant_access.py -q`

Expected: FAIL because unauthenticated event routes still return data.

- [ ] **Step 3: Add a fail-closed bearer resolver and error envelopes**

```python
def require_research_tenant(authorization: str | None = Header(default=None)) -> str:
    # Parse server-configured RESEARCH_TENANT_TOKENS JSON and compare opaque
    # bearer tokens with compare_digest.  Never read a tenant from JSON input.
    ...
```

Add `AuthenticationRequiredError` and `PermissionDeniedError`, mapping them to the standard v1 error envelope with 401 and 403 respectively. Add `CaseTenantAdmission(research_case_id, tenant_id, initial_document_version_id, admitted_by, admitted_at)` as an immutable table with one admission per Case and a tenant index. Migration `0038` must set the existing immutable-table trigger, and its SQLite path must work without PostgreSQL trigger SQL.

- [ ] **Step 4: Run focused tests and the Alembic migration chain**

Run: `backend/.venv/bin/python -m pytest tests/test_event_case_tenant_access.py -q && DATABASE_URL=postgresql+psycopg://evidence:evidence@localhost:5432/evidence_research_os_verify backend/.venv/bin/python -m alembic upgrade head`

Expected: PASS.

- [ ] **Step 5: Commit the identity/admission foundation**

```bash
git add backend/app/api/v1/tenant_context.py backend/app/services/case_tenant_access.py backend/alembic/versions/0038_case_tenant_admissions.py backend/app/models/ledger.py backend/app/errors.py backend/app/main.py backend/tests/test_event_case_tenant_access.py
git commit -m "feat: bind event cases to trusted tenant admissions"
```

### Task 2: Authorize event Case creation and all Case-local actions

**Files:**
- Modify: `backend/app/api/v1/event_research.py`
- Modify: `backend/app/services/event_research.py`
- Modify: `backend/app/services/document_uploads.py`
- Modify: `backend/app/queries/event_research.py`
- Modify: `backend/app/services/case_tenant_access.py`
- Test: `backend/tests/test_event_case_tenant_access.py`

- [ ] **Step 1: Write failing cross-tenant creation/read/write tests**

```python
def test_foreign_tenant_cannot_read_or_attach_to_an_event_case(cmd_client, monkeypatch):
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", '{"token-a":"team-a","token-b":"team-b"}')
    created = cmd_client.post("/api/v1/event-research", json=payload, headers=auth("token-a"))
    case_id = created.json()["case_id"]
    assert cmd_client.get(f"/api/v1/event-research/{case_id}/workbench", headers=auth("token-b")).status_code == 404
    assert cmd_client.post(f"/api/v1/event-research/{case_id}/materials", json=material, headers=auth("token-b")).status_code == 404
```

- [ ] **Step 2: Run the cross-tenant test and verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_event_case_tenant_access.py::test_foreign_tenant_cannot_read_or_attach_to_an_event_case -q`

Expected: FAIL because Case-local routes currently accept a foreign caller.

- [ ] **Step 3: Thread the trusted tenant through event routes and services**

Every event route with a Case ID must call `CaseTenantAccess.require_case(case_id, tenant_id)`. Creation must create its admission only after the initial document is frozen and attached. List and network queries must filter to admitted Cases for the tenant. Upload, attach, scope updates, reviews, continuations and published-material decisions must authorize before changing any state. Foreign access deliberately raises `NotFoundError` so it does not reveal Case existence.

- [ ] **Step 4: Run focused authorization and event-regression tests**

Run: `backend/.venv/bin/python -m pytest tests/test_event_case_tenant_access.py tests/test_event_research_uploads.py tests/test_event_source_governance.py -q`

Expected: PASS.

- [ ] **Step 5: Commit event Case boundary**

```bash
git add backend/app/api/v1/event_research.py backend/app/services/event_research.py backend/app/services/document_uploads.py backend/app/queries/event_research.py backend/app/services/case_tenant_access.py backend/tests/test_event_case_tenant_access.py
git commit -m "feat: authorize event case operations by tenant"
```

### Task 3: Authorize event Case document surfaces and prove original-file isolation

**Files:**
- Modify: `backend/app/api/v1/event_research.py`
- Modify: `backend/app/queries/documents.py`
- Modify: `backend/app/services/case_tenant_access.py`
- Test: `backend/tests/test_event_case_tenant_access.py`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Test: `frontend/src/tests/HttpResearchAdapter.test.ts`

- [ ] **Step 1: Write a failing foreign-tenant document list/detail test**

```python
assert cmd_client.get(f"/api/v1/event-research/{case_id}/documents", headers=auth("token-b")).status_code == 404
assert cmd_client.get(f"/api/v1/event-research/{case_id}/documents/{document_id}", headers=auth("token-b")).status_code == 404
```

- [ ] **Step 2: Run the document-isolation test and verify it fails**

Run: `backend/.venv/bin/python -m pytest tests/test_event_case_tenant_access.py -q`

Expected: FAIL because document detail is presently global by version ID.

- [ ] **Step 3: Scope document list/detail to an admitted Case**

Add Case-scoped document routes below `/event-research/{case_id}`. Validate the Case admission before returning document summaries or byte-adjacent provenance; detail must require a document association that belongs to that Case. Update the HTTP adapter so every document read in the active Research OS carries its Case ID; no token is hard-coded in browser code. The pre-existing global `/documents` library remains explicitly outside this event-slice boundary until its callers have migrated to the same admission model.

- [ ] **Step 4: Regenerate contract and run focused frontend/backend tests**

Run: `bash scripts/sync-contract.sh --update && backend/.venv/bin/python -m pytest tests/test_event_case_tenant_access.py tests/test_event_research_uploads.py -q && cd frontend && npm test -- --run`

Expected: PASS with generated contracts committed.

- [ ] **Step 5: Commit document boundary**

```bash
git add backend/app/api/v1/event_research.py backend/app/queries/documents.py backend/app/services/case_tenant_access.py frontend/src/data/httpResearchAdapter.ts frontend/src/domain/types.ts frontend/src/features/case/CasePages.tsx frontend/src/tests/HttpResearchAdapter.test.ts frontend/openapi.json frontend/src/contracts/v1.ts backend/tests/test_event_case_tenant_access.py
git commit -m "feat: scope event documents to case tenant admissions"
```

### Task 4: Full verification and handoff

**Files:**
- Modify: `docs/design/2026-08-08-research-operating-system-baseline.md`

- [ ] **Step 1: Record the implemented boundary and remaining migration scope**

Add a dated change-log entry stating that event Cases, their materials and document views require trusted tenant admission; explicitly list non-event research routes as a subsequent migration, rather than claiming global RBAC completion.

- [ ] **Step 2: Run full verification**

Run: `cd backend && .venv/bin/python -m pytest -q && cd ../frontend && npm test -- --run && npm run build && npm run e2e`

Expected: all suites pass; report the exact counts and any pre-existing warnings.

- [ ] **Step 3: Run a live isolated PostgreSQL check**

Run: `DATABASE_URL=postgresql+psycopg://evidence:evidence@localhost:5432/evidence_research_os_verify backend/.venv/bin/python -m alembic upgrade head`

Expected: migration `0038` applies; a token for team B cannot list, open or append a team A Case.

- [ ] **Step 4: Commit verification documentation**

```bash
git add docs/design/2026-08-08-research-operating-system-baseline.md
git commit -m "docs: record event case tenant boundary"
```
