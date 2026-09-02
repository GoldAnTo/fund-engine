# Archive Shell and Frozen Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a deep-linked immutable research archive self-identifying and isolated from event-research runtime data.

**Architecture:** Extend the persisted-only revision-history model with the immutable `uw_research_objects` identity already selected by the revision family. Route archive paths outside `AppShell` to a minimal archive-only shell with no event, worker, disclosure or mock imports. Regenerate the API contract safely while preserving the user’s uncommitted OpenAPI change.

**Tech Stack:** Python 3.11, SQLAlchemy 2, FastAPI, Pydantic v2, pytest; React 18, TypeScript, React Router, Vitest, Testing Library.

---

### Task 1: Return immutable object identity with revision history

**Files:**
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Test: `backend/tests/underwriting/test_research_revision_diff.py`
- Test: `backend/tests/underwriting/test_research_revision_diff_api.py`

- [ ] **Step 1: Write RED tests.**

```python
def test_revision_history_includes_immutable_object_identity(session, revision_chain) -> None:
    history = ResearchRevisionDiffService(session).revision_history(
        revision_chain.object_id, revision_chain.version_kind,
    )
    assert (history.object_kind, history.canonical_name, history.external_key) == (
        "company", "宁德时代", "300750.SZ",
    )
```

Add an API assertion that `object_kind`, `canonical_name`, and `external_key` are returned with a history response.

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py tests/underwriting/test_research_revision_diff_api.py -k history_identity`

Expected: FAIL because history does not expose object identity.

- [ ] **Step 3: Implement persisted-only identity.**

Add these fields to the frozen `RevisionHistory` type and resolve exactly the addressed `UnderwritingResearchObject` under `Session.no_autoflush`; a missing/mismatched row raises `ValidationError`. Add strict `ResearchObjectKind`, `canonical_name`, and `external_key` fields to `ResearchRevisionHistoryResponse`, and map only the read-service result in the router. Do not query a current security, event, pricing, or fixture record.

- [ ] **Step 4: Confirm GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py tests/underwriting/test_research_revision_diff_api.py -k 'history or archive'`

```bash
git add backend/app/underwriting/services/research_revision_diff.py backend/app/underwriting/api/schemas.py backend/app/underwriting/api/router.py backend/tests/underwriting/test_research_revision_diff.py backend/tests/underwriting/test_research_revision_diff_api.py
git commit -m "feat: identify immutable research revision histories"
```

### Task 2: Isolate archive routes and render returned identity

**Files:**
- Create: `frontend/src/app/UnderwritingArchiveShell.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Modify: `frontend/src/features/underwriting/ResearchArchivePage.tsx`
- Test: `frontend/src/app/routes.test.tsx`
- Test: `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`

- [ ] **Step 1: Write RED tests.**

```tsx
it("renders the archive outside the event AppShell", async () => {
  renderAt("/underwriting/research/company-id/catl");
  expect(await screen.findByLabelText("不可变研究档案导航")).toBeVisible();
  expect(screen.queryByLabelText("研究工作台导航")).not.toBeInTheDocument();
});

it("shows only identity returned by frozen revision history", async () => {
  renderAt("/underwriting/research/company-id/catl");
  expect(await screen.findByRole("heading", { name: "宁德时代" })).toBeVisible();
  expect(screen.getByText("300750.SZ")).toBeVisible();
});
```

- [ ] **Step 2: Confirm RED.**

Run: `cd frontend && npm test -- ResearchArchivePage.test.tsx routes.test.tsx`

Expected: FAIL because the archive is inside `AppShell` and no returned identity is rendered.

- [ ] **Step 3: Implement the isolated shell.**

```tsx
export function UnderwritingArchiveShell() {
  return <main className="ura-shell" aria-label="不可变研究档案导航"><Outlet /></main>;
}

<Route element={<UnderwritingArchiveShell />}>
  <Route path="underwriting/research" element={<ResearchArchiveRoute />} />
  <Route path="underwriting/research/:objectId/:versionKind" element={<ResearchArchiveRoute />} />
</Route>
<Route element={<AppShell />}>…event and case routes only…</Route>
```

The shell imports no event, worker, fund-disclosure, mock, or research client. It has only an archive title and directory link. Display the checked history response’s `canonical_name`, `external_key`, and controlled object-kind label; never derive identity from URL, current records, or fixtures.

- [ ] **Step 4: Confirm GREEN and commit.**

Run: `cd frontend && npm test -- ResearchArchivePage.test.tsx routes.test.tsx underwritingResearchApi.test.ts && npm run typecheck`

```bash
git add frontend/src/app/UnderwritingArchiveShell.tsx frontend/src/app/routes.tsx frontend/src/features/underwriting/ResearchArchivePage.tsx frontend/src/app/routes.test.tsx frontend/src/features/underwriting/ResearchArchivePage.test.tsx
git commit -m "feat: isolate immutable research archive routes"
```

### Task 3: Safely generate the contract and release-gate it

**Files:**
- Modify/generated: `frontend/openapi.json`, `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/underwritingResearchApi.ts`
- Modify: `docs/architecture/underwriting-research.md`
- Test: `backend/tests/underwriting/test_openapi_dump.py`

- [ ] **Step 1: Add RED OpenAPI identity assertion.**

```python
def test_revision_history_contract_includes_research_object_identity(openapi) -> None:
    schema = openapi["components"]["schemas"]["ResearchRevisionHistoryResponse"]
    assert {"object_kind", "canonical_name", "external_key"} <= set(schema["properties"])
```

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_openapi_dump.py -k revision_history_identity`

- [ ] **Step 3: Safely regenerate, stage only generated changes, restore the user patch.**

```bash
user_patch="$(mktemp /tmp/underwriting-openapi-user.XXXXXX.patch)"
git diff -- frontend/openapi.json > "$user_patch"
test "$(git diff --numstat -- frontend/openapi.json)" = $'8\t8\tfrontend/openapi.json'
(cd backend && python scripts/dump_openapi.py)
(cd frontend && npm run gen:contract && npm run typecheck && npm run build)
git add frontend/openapi.json frontend/src/contracts/v1.ts
git diff --cached -- frontend/openapi.json | (! grep -q 'Unprocessable Content')
git apply --whitespace=nowarn "$user_patch"
test "$(git diff --numstat -- frontend/openapi.json)" = $'8\t8\tfrontend/openapi.json'
```

Stop without staging if restoration fails. Replace temporary client history types with generated aliases only after generation.

- [ ] **Step 4: Document and verify.**

Document that archive identity comes from the immutable research object selected by the historical family, and the archive shell never loads event/current data. Run:

```bash
cd backend && pytest -q tests/underwriting
python -m compileall -q app
cd ../frontend && npm test -- ResearchArchivePage.test.tsx routes.test.tsx underwritingResearchApi.test.tsx
git diff --check
git status --short
```

Commit feature files and staged generated files only. The eight-hunk user patch stays unstaged.

## Acceptance checklist

- [ ] A deep-linked archive identifies its immutable company/industry object without looking up current event or market data.
- [ ] Archive routes never mount `AppShell`, event polling, run strips, search, or automatic-research controls.
- [ ] Object identity, history, revision and adjacent diff remain checked, persisted-only, and fail closed.
- [ ] Generated contract matches history identity; the user OpenAPI patch remains outside every feature commit.

