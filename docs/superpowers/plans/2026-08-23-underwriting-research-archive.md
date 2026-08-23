# Underwriting Research Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make immutable company/industry underwriting research discoverable and legible in a read-only React archive that exposes versions, evidence boundaries, typed changes, and unresolved gaps without price, valuation, or trading guidance.

**Architecture:** Extend `ResearchRevisionDiffService` with a persisted-only archive index over research-version families. FastAPI exposes an envelope-based archive-list GET route; a separate frontend client and route consume it without touching the event-research client. The detail view composes only frozen history/revision/diff responses and compares a selected revision with its immediate ancestor.

**Tech Stack:** Python 3.11, SQLAlchemy 2, FastAPI, Pydantic v2, pytest; React 18, TypeScript, React Router, Vitest, Testing Library, Vite; generated OpenAPI TypeScript.

---

## Non-negotiable boundaries

- Every directory/detail/diff read uses persisted `uw_*` rows under `Session.no_autoflush`; no write, commit, fixture load, live fetch, or current-effective-ledger replacement.
- A corrupt family can appear only as `unreadable`; it must not expose an unverified cutoff, source, parent or conclusion.
- Preserve locators, units, periods, availability and statuses exactly. Unknown gaps and candidates stay visibly unresolved.
- Do not add price, PE, PB, DCF, target, buy, sell, stop, position, return, valuation, recommendation or action fields/copy.
- `frontend/openapi.json` has an unrelated unstaged eight-hunk `Entity → Content` user edit. Generators must restore it and feature commits must not stage it.

## File map

- Modify: `backend/app/underwriting/services/research_revision_diff.py` — frozen archive family descriptors and list.
- Modify: `backend/app/underwriting/api/schemas.py`, `backend/app/underwriting/api/router.py` — strict DTOs and one discovery GET.
- Modify: `backend/tests/underwriting/test_research_revision_diff.py`, `backend/tests/underwriting/test_research_revision_diff_api.py`, `backend/tests/underwriting/test_openapi_dump.py` — read-only service/API/contract gates.
- Create: `frontend/src/data/underwritingResearchApi.ts`, `frontend/src/data/underwritingResearchApi.test.ts` — separate HTTP-only API client.
- Create: `frontend/src/features/underwriting/ResearchArchivePage.tsx`, `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`, `frontend/src/styles/underwriting-research.css`.
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/app/AppShell.tsx`, `frontend/src/main.tsx`, generated `frontend/openapi.json` and `frontend/src/contracts/v1.ts`.
- Modify: `docs/architecture/underwriting-research.md` — operator-facing archive behavior.

### Task 1: Add a persisted-only archive directory read model

**Files:**
- Modify: `backend/app/underwriting/services/research_revision_diff.py`
- Test: `backend/tests/underwriting/test_research_revision_diff.py`

- [ ] **Step 1: Write failing service tests.**

```python
def test_archive_index_reads_persisted_family_heads_without_current_data(session, revision_chain) -> None:
    page = ResearchRevisionDiffService(session).research_archives(
        query="CATL", kind="company", limit=20, cursor=None,
    )
    assert [(row.canonical_name, row.version_kind) for row in page.items] == [
        ("宁德时代", "catl_economic_model_evidence_only"),
    ]
    assert page.items[0].lineage_state == "readable"
    assert page.items[0].latest_revision_id == revision_chain.v2.id
    assert page.items[0].cutoff == revision_chain.v2_basis.cutoff


def test_archive_index_surfaces_corrupt_family_without_unverified_detail(session, corrupt_revision) -> None:
    page = ResearchRevisionDiffService(session).research_archives(
        query=None, kind=None, limit=20, cursor=None,
    )
    item = next(row for row in page.items if row.object_id == corrupt_revision.object_id)
    assert item.lineage_state == "unreadable"
    assert item.latest_revision_id is item.cutoff is item.source_manifest_hash is None
```

Add pagination, kind/query, malformed cursor, and pending-SQLAlchemy-write/no-autoflush assertions.

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py -k archive_index`

Expected: FAIL because `research_archives` does not exist.

- [ ] **Step 3: Add frozen archive types and cursor decoding.**

```python
@dataclass(frozen=True)
class ResearchArchiveItem:
    object_id: UUID
    object_kind: str
    canonical_name: str
    external_key: str
    version_kind: str
    version_count: int
    lineage_state: Literal["readable", "unreadable"]
    latest_revision_id: UUID | None
    latest_sequence: int | None
    cutoff: datetime | None
    source_manifest_hash: str | None

@dataclass(frozen=True)
class ResearchArchivePage:
    items: tuple[ResearchArchiveItem, ...]
    next_cursor: str | None
```

Encode/decode URL-safe base64 canonical JSON `{"v":1,"after":[canonical_name,external_key,object_id,version_kind]}`. Reject malformed or unknown cursor versions with `ValidationError`. Validate `1 <= limit <= 100`.

- [ ] **Step 4: Implement `research_archives`.** Under `self._session.no_autoflush`, join only `UnderwritingResearchObject` and `UnderwritingResearchVersion`; group by `(object_id, version_kind)`, casefold-match query only against name/key, exact-filter kind, and order by `(canonical_name, external_key, str(object_id), version_kind)`. For every family call `revision_history` plus `effective_revision`; on a `ValidationError`, emit only object identity, version count and `unreadable`, with all revision metadata `None`. Do not query fixtures, snapshots or current effective entries. Page after the decoded key and create a next cursor only when more rows exist.

- [ ] **Step 5: Confirm GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff.py -k 'archive_index or revision'`

Expected: PASS.

```bash
git add backend/app/underwriting/services/research_revision_diff.py backend/tests/underwriting/test_research_revision_diff.py
git commit -m "feat: list immutable underwriting research archives"
```

### Task 2: Expose strict archive discovery

**Files:**
- Modify: `backend/app/underwriting/api/schemas.py`
- Modify: `backend/app/underwriting/api/router.py`
- Test: `backend/tests/underwriting/test_research_revision_diff_api.py`
- Test: `backend/tests/underwriting/test_openapi_dump.py`

- [ ] **Step 1: Write failing API/contract tests.**

```python
def test_research_archive_directory_is_read_only_sorted_and_fail_closed(api_client, revision_chain) -> None:
    response = api_client.get(
        "/api/underwriting/v1/research-archives",
        params={"query": "宁德", "kind": "company"},
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == "underwriting.v1"
    assert response.json()["items"][0]["lineage_state"] == "readable"
    assert api_client.post("/api/underwriting/v1/research-archives").status_code == 405


def test_research_archive_bad_cursor_uses_underwriting_422_envelope(api_client) -> None:
    response = api_client.get("/api/underwriting/v1/research-archives", params={"cursor": "not-base64"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
```

Assert generated OpenAPI has the one GET path and strict archive schemas, with no prohibited investment fields.

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py tests/underwriting/test_openapi_dump.py -k archive`

Expected: FAIL with 404/missing DTOs.

- [ ] **Step 3: Add strict response DTOs.**

```python
class ResearchArchiveItemResponse(UnderwritingModel):
    object_id: UUID
    object_kind: ResearchObjectKind
    canonical_name: str
    external_key: str
    version_kind: str
    version_count: int
    lineage_state: Literal["readable", "unreadable"]
    latest_revision_id: UUID | None
    latest_sequence: int | None
    cutoff: datetime | None
    source_manifest_hash: str | None

class ResearchArchiveListResponse(UnderwritingModel):
    items: list[ResearchArchiveItemResponse]
    next_cursor: str | None
```

- [ ] **Step 4: Add exactly one GET route.**

```python
@router.get("/research-archives", response_model=ResearchArchiveListResponse,
            responses={422: {"model": UnderwritingErrorEnvelope}})
def get_research_archives(
    query: str | None = None,
    kind: ResearchObjectKind | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
) -> ResearchArchiveListResponse:
    try:
        page = ResearchRevisionDiffService(db).research_archives(
            query=query, kind=kind, limit=limit, cursor=cursor,
        )
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    return ResearchArchiveListResponse(
        items=[ResearchArchiveItemResponse(**asdict(item)) for item in page.items],
        next_cursor=page.next_cursor,
    )
```

Import only `Query`, `asdict` and required types. Do not query a current revision in the router.

- [ ] **Step 5: Confirm GREEN and commit.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py tests/underwriting/test_openapi_dump.py -k 'archive or revision'`

Expected: PASS.

```bash
git add backend/app/underwriting/api/schemas.py backend/app/underwriting/api/router.py backend/tests/underwriting/test_research_revision_diff_api.py backend/tests/underwriting/test_openapi_dump.py
git commit -m "feat: expose underwriting research archive directory"
```

### Task 3: Add a separate frontend archive client and navigation

**Files:**
- Create: `frontend/src/data/underwritingResearchApi.ts`
- Test: `frontend/src/data/underwritingResearchApi.test.ts`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/app/AppShell.tsx`

- [ ] **Step 1: Write RED client tests.**

```ts
it("reads archive responses from the underwriting base without an event-client fallback", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    expect(url).toContain("/api/underwriting/v1/research-archives?kind=company");
    return jsonResponse({ schema_version: "underwriting.v1", items: [], next_cursor: null });
  }));
  await expect(underwritingResearchApi.listArchives({ kind: "company" }))
    .resolves.toMatchObject({ items: [] });
});
```

Also assert 422 becomes a typed error and no client function uses non-GET methods.

- [ ] **Step 2: Confirm RED.**

Run: `cd frontend && npm test -- underwritingResearchApi.test.ts`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the isolated client.** Export generated-schema aliases and `UnderwritingResearchRequestError(status, code, requestId)`. Define exactly four GET methods: `listArchives`, `history`, `revision`, and `diff`. Use `VITE_UNDERWRITING_API_URL || "/api/underwriting/v1"`, `URLSearchParams`, credentials include and optional bearer token. Provide `setUnderwritingResearchApi` / `resetUnderwritingResearchApi` only for tests. Never import `researchClient`, `researchOsApi`, mock data or fixtures.

- [ ] **Step 4: Add navigation and lazy route.**

```tsx
<Route path="underwriting/research" element={<ResearchArchivePage />} />
<Route path="underwriting/research/:objectId/:versionKind" element={<ResearchArchivePage />} />
```

Add `公司／行业档案` as one `NavLink` under “研究资产” and map the path to the same page label. Do not alter event case routes.

- [ ] **Step 5: Confirm GREEN and commit.**

Run: `cd frontend && npm test -- underwritingResearchApi.test.ts routes.test.tsx`

Expected: PASS.

```bash
git add frontend/src/data/underwritingResearchApi.ts frontend/src/data/underwritingResearchApi.test.ts frontend/src/app/routes.tsx frontend/src/app/AppShell.tsx
git commit -m "feat: add underwriting archive read client"
```

### Task 4: Render the evidence-first archive

**Files:**
- Create: `frontend/src/features/underwriting/ResearchArchivePage.tsx`
- Test: `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`
- Create: `frontend/src/styles/underwriting-research.css`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Write RED interaction/boundary tests.**

```tsx
it("shows an unresolved CATL boundary instead of an investment conclusion", async () => {
  renderAt("/underwriting/research/company-id/catl_economic_model_evidence_only");
  expect(await screen.findByText("研究尚需验证")).toBeVisible();
  expect(screen.getByText("Unknown evidence gap")).toBeVisible();
  expect(screen.getByText("candidate")).toBeVisible();
  expect(screen.queryByText(/买入|卖出|目标价|估值/)).not.toBeInTheDocument();
});

it("uses only the immediate predecessor for the selected version", async () => {
  renderAt("/underwriting/research/company-id/economic");
  await userEvent.click(await screen.findByRole("button", { name: /版本 2/ }));
  expect(api.diff).toHaveBeenCalledWith("revision-1", "revision-2");
});
```

Cover directory search, deep-link detail, latest default, locator/unit/period/status, loading, 404, 422, unreadable, keyboard selection and forbidden-text scan.

- [ ] **Step 2: Confirm RED.**

Run: `cd frontend && npm test -- ResearchArchivePage.test.tsx`

Expected: FAIL because the placeholder does not render the archive.

- [ ] **Step 3: Implement controlled states only.** Directory has explicit search, kind filter, stable result links and load-more only with `next_cursor`; unreadable rows have no verified metadata. Detail loads history, defaults to its last readable revision, calls `revision(selected.id)`, and calls `diff(previous.id, selected.id)` only for its immediate predecessor. Render group headings exactly `证据`, `机制`, `行业模型`, `可回答性`; absent groups say `这一版本没有该类冻结变化`. Render returned `not_answerable` as `研究尚需验证`, `wait_for_validation` as `等待验证资料`, `unknown_evidence_gap` as `Unknown evidence gap`, and candidate/formal as exact statuses. Do not add interpretation.

- [ ] **Step 4: Add responsive CSS.** Limit new rules to `.ura-*`. Use a three-column desktop grid that becomes one column at `max-width: 900px`; long locators/hashes use `overflow-wrap:anywhere`; version selectors are native `button aria-pressed`; loading/empty use `role="status"`, failures use `role="alert"`. Import it once in `main.tsx`; do not alter existing Research OS styles.

- [ ] **Step 5: Confirm GREEN and commit.**

Run: `cd frontend && npm test -- ResearchArchivePage.test.tsx underwritingResearchApi.test.ts routes.test.tsx`

Expected: PASS.

```bash
git add frontend/src/features/underwriting/ResearchArchivePage.tsx frontend/src/features/underwriting/ResearchArchivePage.test.tsx frontend/src/styles/underwriting-research.css frontend/src/main.tsx
git commit -m "feat: render immutable underwriting research archive"
```

### Task 5: Safely generate contracts, document and run release gates

**Files:**
- Modify/generated: `frontend/openapi.json`, `frontend/src/contracts/v1.ts`
- Modify: `docs/architecture/underwriting-research.md`
- Modify: `backend/tests/underwriting/test_openapi_dump.py`, `frontend/src/features/underwriting/ResearchArchivePage.test.tsx`

- [ ] **Step 1: Write final RED gates.** Import CATL evidence-only fixture, add an immutable successor, then assert the old directory/detail data remains frozen and the typed diff has only selected parent changes. Add 390px page no-overflow assertion and a recursive API/UI contract field-name scan for prohibited investment fields.

- [ ] **Step 2: Confirm RED.**

Run: `cd backend && pytest -q tests/underwriting/test_research_revision_diff_api.py tests/underwriting/test_openapi_dump.py -k archive`

Expected: FAIL until archive path/schema and CATL gate are implemented.

- [ ] **Step 3: Document operator behavior.** Update `docs/architecture/underwriting-research.md` with directory readability semantics, cursor/version selection, adjacent-ancestor diff, locator/unknown/candidate rules, 404/422 presentation and continued non-goals.

- [ ] **Step 4: Generate safely.** Capture the user patch before any generator, verify it is exactly eight lines added/eight removed, run dump/contract/typecheck/build, then restore it before staging:

```bash
user_patch="$(mktemp /tmp/underwriting-openapi-user.XXXXXX.patch)"
git diff -- frontend/openapi.json > "$user_patch"
test "$(git diff --numstat -- frontend/openapi.json)" = $'8\t8\tfrontend/openapi.json'
cd backend && python scripts/dump_openapi.py
cd ../frontend && npm run gen:contract && npm run typecheck && npm run build
git apply --whitespace=nowarn "$user_patch"
git diff --check
```

If restoration fails, stop before staging. After restoration, ensure the staged OpenAPI diff does not contain an `Unprocessable Content` hunk.

- [ ] **Step 5: Run release gates and commit.**

```bash
cd backend && pytest -q tests/underwriting
python -m compileall -q app
cd ../frontend && npm test -- ResearchArchivePage.test.tsx underwritingResearchApi.test.ts routes.test.tsx
git diff --check
git status --short
```

Expected: all underwriting/frontend tests, typecheck/build and compilation pass; after committing feature files, the only worktree diff is the preserved user OpenAPI patch.

```bash
git add backend/tests/underwriting/test_research_revision_diff_api.py backend/tests/underwriting/test_openapi_dump.py frontend/openapi.json frontend/src/contracts/v1.ts frontend/src/features/underwriting/ResearchArchivePage.test.tsx docs/architecture/underwriting-research.md
git commit -m "docs: publish underwriting research archive boundaries"
```

## Final acceptance checklist

- [ ] A researcher can discover a company/industry archive without knowing a UUID.
- [ ] Each selected version displays only persisted historical evidence, cutoff and descriptors.
- [ ] A successor yields typed deterministic adjacent changes without modifying the old version.
- [ ] Unknown gaps, candidates, malformed lineage and unavailable research are explicit; none is auto-completed.
- [ ] Every path is read-only, no-autoflush, fixture-free and free of investment fields/copy.
- [ ] Desktop/mobile are keyboard-accessible and do not horizontally overflow.
- [ ] OpenAPI/TypeScript are stable and the unrelated user patch stays outside every feature commit.
