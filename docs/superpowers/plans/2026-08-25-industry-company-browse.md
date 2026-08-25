# Auditable Industry—Company Browsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make “查看相关公司” return only Companies explicitly related to an Industry, while keeping Company as the only research-project subject.

**Architecture:** Reuse the existing directed `uw_object_relations` edge `industry_exposes_company`, never text inference. Extend the trusted product foundation with an Industry and exact relation; add one read-only Company-group API; call it from the two-step page.

**Tech Stack:** Python, SQLAlchemy, FastAPI/Pydantic, React/TypeScript/Vitest, temporary OpenAPI generation.

---

### Task 1: Load explicit Industry→Company foundation edges

**Files:**
- Modify: `backend/app/underwriting/fixtures/product_foundation/__init__.py`
- Modify: `backend/app/underwriting/fixtures/product_foundation/manifest.json`
- Modify: `backend/app/underwriting/services/product_foundation_fixture.py`
- Test: `backend/tests/underwriting/test_product_project.py`
- Test: `backend/tests/underwriting/test_product_persistence.py`

- [ ] **Step 1: Write RED tests**

```python
def test_foundation_loads_explicit_industry_company_relation(session) -> None:
    loaded = ProductFoundationFixtureService(session, now=lambda: NOW).load()
    assert ProductRepository(session).relation_exists(
        parent_id=loaded.objects["GLOBAL:INTERNET_SERVICES:INDUSTRY"].id,
        child_id=loaded.objects["US:ALPHABET:COMPANY"].id,
        relation_type="industry_exposes_company",
    )

def test_foundation_rejects_extra_or_wrong_direction_industry_relation(session) -> None:
    # An Industry→unlisted Company or Company→Industry edge raises ValidationError
    # and the loader savepoint leaves no partial relation write.
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && python -m pytest tests/underwriting/test_product_project.py -k industry_exposes_company -q`

Expected: FAIL: the v1 fixture lacks closed Industry/relation arrays.

- [ ] **Step 3: Implement strict fixture values**

```python
@dataclass(frozen=True, slots=True)
class FoundationIndustry:
    external_key: str
    canonical_name: str
    effective_from: datetime

@dataclass(frozen=True, slots=True)
class FoundationIndustryCompanyRelation:
    industry_key: str
    company_key: str
```

Add non-empty `industries` and `industry_company_relations` manifest arrays. Reject duplicate
keys/pairs and wrong object kinds. Add `GLOBAL:INTERNET_SERVICES:INDUSTRY →
US:ALPHABET:COMPANY`, recompute canonical content hash and bundled byte digest. The loader
exact-set validates only `industry_exposes_company` edges among fixture IDs and adds missing
ones; it keeps the existing `company_has_security` exact-set independent.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && python -m pytest tests/underwriting/test_product_project.py tests/underwriting/test_product_persistence.py -q`

Expected: PASS for idempotent repeated load, wrong/extra relation rollback, and no type crossover.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/fixtures/product_foundation backend/app/underwriting/services/product_foundation_fixture.py backend/tests/underwriting/test_product_project.py backend/tests/underwriting/test_product_persistence.py
git commit -m "feat: load explicit industry company relations"
```

### Task 2: Expose bounded Company groups for an Industry

**Files:**
- Modify: `backend/app/underwriting/persistence/product_repository.py`
- Modify: `backend/app/underwriting/services/product_project.py`
- Modify: `backend/app/underwriting/api/product_router.py`
- Test: `backend/tests/underwriting/test_product_project.py`
- Test: `backend/tests/underwriting/test_product_api.py`

- [ ] **Step 1: Write RED service/API tests**

```python
def test_industry_companies_returns_only_explicit_company_groups(session) -> None:
    items = ResearchProjectService(session, now=lambda: NOW).industry_companies(
        industry_id=internet_id, as_of=NOW, limit=20
    )
    assert [(row.kind.value, row.external_key) for row in items] == [
        ("company", "US:ALPHABET:COMPANY"),
        ("security", "NASDAQ:GOOGL"),
        ("security", "NASDAQ:GOOG"),
    ]

def test_get_industry_companies_rejects_non_industry(client, company_id) -> None:
    assert client.get(f"/api/underwriting/v1/product/industries/{company_id}/companies").status_code == 422
```

Also cover unknown ID (404), no edges (200 with empty items), a non-Company relation child
(fail closed), effective identities at `as_of`, group-atomic limits, and GET no-write behavior.

- [ ] **Step 2: Verify RED**

Run: `cd backend && python -m pytest tests/underwriting/test_product_api.py -k industry_companies -q`

Expected: FAIL because route and service are absent.

- [ ] **Step 3: Implement bounded read-only query**

```python
def industry_company_groups(self, industry_id: UUID, as_of: datetime, limit: int):
    # Verify the parent object is kind "industry".
    # Select direct children only where relation_type == "industry_exposes_company".
    # Reject non-company children, load effective identities at as_of, and pass the
    # Company rows through _pack_search_groups so selected groups retain all Securities.
```

Expose `ResearchProjectService.industry_companies(industry_id, as_of, limit)`. Add
`GET /industries/{industry_id}/companies` to the product router, reuse
`ProductObjectSearchResponse`, normal `as_of`/limit bounds and the read error mapper, and
never call `commit_write`, `commit`, or `rollback`.

- [ ] **Step 4: Verify GREEN**

Run: `cd backend && python -m pytest tests/underwriting/test_product_project.py tests/underwriting/test_product_api.py -q`

Expected: PASS with complete Company groups or no result.

- [ ] **Step 5: Commit**

```bash
git add backend/app/underwriting/persistence/product_repository.py backend/app/underwriting/services/product_project.py backend/app/underwriting/api/product_router.py backend/tests/underwriting/test_product_project.py backend/tests/underwriting/test_product_api.py
git commit -m "feat: browse companies by explicit industry relation"
```

### Task 3: Make “查看相关公司” perform the real browse request

**Files:**
- Test: `backend/tests/underwriting/test_openapi_dump.py`
- Modify: `frontend/src/contracts/v1.ts`
- Modify: `frontend/src/data/investmentResearchApi.ts`
- Test: `frontend/src/data/InvestmentResearchApi.test.ts`
- Modify: `frontend/src/features/investment-research/NewResearchPage.tsx`
- Test: `frontend/src/features/investment-research/NewResearchPage.test.tsx`

- [ ] **Step 1: Write RED client/page tests**

```tsx
it("browses explicit Industry companies before any Company preview", async () => {
  api.industryCompanies.mockResolvedValue(alphabetCompanyGroup)
  render(<NewResearchPage initialIndustryId={industryId} />)
  await user.click(screen.getByRole("button", { name: "查看相关公司" }))
  expect(api.industryCompanies).toHaveBeenCalledWith(industryId)
  expect(await screen.findByText("Alphabet Inc.")).toBeVisible()
  expect(screen.getByText("GOOGL Class A")).toBeVisible()
  expect(api.previewCompanyResearch).not.toHaveBeenCalled()
})
```

Runtime guards reject unknown keys, non-Company anchors, duplicate IDs, split Company groups,
and malformed UUIDs. Page tests cover empty results and prove an Industry has no start action.

- [ ] **Step 2: Verify RED using temporary OpenAPI**

```bash
tmp_dir=$(mktemp -d)
cd backend && python scripts/dump_openapi.py --output "$tmp_dir/openapi.json"
cd ../frontend && npx openapi-typescript "$tmp_dir/openapi.json" -o /tmp/v1.ts
npm test -- --run src/data/InvestmentResearchApi.test.ts src/features/investment-research/NewResearchPage.test.tsx
```

Expected: FAIL because `industryCompanies` does not exist.

- [ ] **Step 3: Implement strict client and UI behavior**

```ts
industryCompanies(industryId: string, options: { asOf?: string; limit?: number } = {}) {
  assertUuid(industryId)
  return requestJson(
    `${this.root}/industries/${encodeURIComponent(industryId)}/companies?...`,
    isCompleteCompanySearchGroup,
    200,
    { method: "GET" },
  )
}
```

Generate `v1.ts` from temporary OpenAPI and copy only generated contract changes; never
write/stage `frontend/openapi.json`. The Industry button calls this API, renders full Company
cards or an honest empty state, and only a selected Company enters the existing preview. Invalidate
operation epochs on browse/retry/navigation/unmount so stale responses cannot replace newer intent.

- [ ] **Step 4: Verify**

```bash
cd backend && python -m pytest tests/underwriting/test_openapi_dump.py tests/underwriting/test_product_api.py -q
cd ../frontend && npm test -- --run src/data/InvestmentResearchApi.test.ts src/features/investment-research/NewResearchPage.test.tsx
npm run typecheck
npm run build
cd .. && git diff --check
```

Expected: PASS; `git diff -- frontend/openapi.json` is empty and the user file is unstaged.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/underwriting/test_openapi_dump.py frontend/src/contracts/v1.ts frontend/src/data/investmentResearchApi.ts frontend/src/data/InvestmentResearchApi.test.ts frontend/src/features/investment-research/NewResearchPage.tsx frontend/src/features/investment-research/NewResearchPage.test.tsx
git commit -m "feat: browse related companies from industry"
```
