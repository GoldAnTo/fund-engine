# Prototype Research Dispatch and Case Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the confirmed research-dispatch and Case-network prototype into the production entry surface, without inventing cross-Case facts or allowing intake material to bypass review gates.

**Architecture:** Add one read-only `report-research` list projection, then normalize it with the existing event-research list in a frontend-only `ResearchDeskItem` union. The dispatch page renders only API-backed action, status and timestamp fields. Existing event and report workbenches remain the authoritative pages; a shared Case header provides a dispatch return link and a typed selector whose route carries the selected Case kind, so switching never merges data from two Cases.

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic/OpenAPI; React 18 + React Router + TypeScript + Vitest/Testing Library; existing warm-paper prototype CSS.

---

## Security amendment (2026-08-09)

`DocumentVersion` is globally content-addressed, so the same bytes may be
admitted under independent tenant contracts. A tenant-scoped `DocumentSourceRecord`
therefore cannot, by itself, establish ownership of a Case. Every new report
Case must append exactly one `ReportCaseInitialAdmission` pointing to the exact
initial source record and tenant that opened it. Dispatch authorization, recovery
supplement authorization, and the Case's primary document must start from that
frozen admission; later records for identical bytes never confer Case access.

The migration deliberately does not infer this ownership for legacy Cases:
where historical identical-byte provenance is ambiguous, inference would create
a false authorization fact. Legacy report Cases remain outside the protected
dispatch projection until an explicit, auditable admission/backfill workflow is
provided. This is a safety boundary, not a deletion of their ledger data.

The dispatch list must apply its `limit` while selecting ranked Case IDs in SQL
from the latest tenant-authorized immutable input timestamps. It may then batch
load those selected Cases' supporting inputs; it must not materialize every
tenant Case before slicing the response.

---

## Prototype contract and non-goals

The visual contract is [`research-desk-and-case-network.html`](../../.superpowers/brainstorm/94894-1786178483/content/research-desk-and-case-network.html): the root is a dispatch desk, not an event table or a Case; the primary queue is ranked by actionable impact; the Case header returns to the desk and switches independent Cases; relationships may be shown only when a reviewed relationship API exists.

This slice must not fabricate CaseRelations, reviewed report scopes, fund exposure, a current conclusion, or an executable monitor/run. Pending report intake remains a material state and links only to `/reports/:caseId/intake`.

## Task 1: Add an API-backed report Case list projection

**Files:**

- Modify: `backend/app/schemas/v1/report_research.py`
- Modify: `backend/app/queries/report_wiki.py`
- Modify: `backend/app/api/v1/report_research.py`
- Modify: `backend/tests/test_report_research.py`

- [ ] **Step 1: Write failing projection tests**

```python
def test_list_report_research_returns_safe_intake_summary_only(cmd_client, source_contract):
    created = _create_report_with_contract(cmd_client, source_contract)

    response = cmd_client.get("/api/v1/report-research")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item == {
        "case_id": str(created.case.id),
        "title": "授权研报",
        "document_id": str(created.document.id),
        "input_kind": "pasted_text",
        "intake_state": "pending_candidate_extraction",
        "next_action": "extract_candidates",
        "blocking_reason": "资料已冻结，尚未进入可发布的正式研究阶段。",
        "updated_at": item["updated_at"],
    }
    assert "scope" not in item
    assert "conclusion" not in item


def test_list_report_research_orders_newest_first(cmd_client, source_contract):
    older = _create_report_with_contract(cmd_client, source_contract, title="较早")
    newer = _create_report_with_contract(cmd_client, source_contract, title="较新")

    items = cmd_client.get("/api/v1/report-research").json()["items"]

    assert [item["case_id"] for item in items][:2] == [str(newer.case.id), str(older.case.id)]
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH="$PWD/backend" /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest backend/tests/test_report_research.py -q`

Expected: the first test fails because `GET /report-research` is not registered.

- [ ] **Step 3: Define stable list DTOs**

In `backend/app/schemas/v1/report_research.py`, add an explicit closed vocabulary and DTOs. Do not reuse the creation response or expose scopes/claims.

```python
class ReportResearchListItemDTO(V1Model):
    case_id: uuid.UUID
    title: str
    document_id: uuid.UUID
    input_kind: Literal["pdf_upload", "pasted_text", "web_content", "recovery_text"]
    intake_state: ReportResearchState
    next_action: ReportIntakeNextAction
    blocking_reason: str | None
    updated_at: datetime


class ReportResearchListResponse(V1Model):
    items: list[ReportResearchListItemDTO]
```

- [ ] **Step 4: Implement a read-only query and route**

Add `ReportWikiQueries.list_intakes()` that selects only a Case, its primary `DocumentVersion`, and the latest immutable intake outcome inputs. It calls the existing outcome classifier for each Case, sorts by `ResearchCase.updated_at.desc()`, and maps the same safe `state`, `next_action`, and `blocking_reason` as `intake()`. Register `GET /report-research` **before** `GET /{case_id}/intake`.

```python
@router.get("", response_model=ReportResearchListResponse)
def list_report_research(db: Session = Depends(get_db)) -> ReportResearchListResponse:
    return ReportResearchListResponse(
        items=[
            ReportResearchListItemDTO(**item)
            for item in ReportWikiQueries(db).list_intakes()
        ]
    )
```

The query must return an empty list, never synthesize a Case, and never call extraction, scope creation, task scheduling, or a market-impact service.

- [ ] **Step 5: Verify GREEN and regenerate the contract**

Run:

```bash
PYTHONPATH="$PWD/backend" /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest backend/tests/test_report_research.py -q
ln -s /Users/xiongjiali/code/fund-engine/backend/.venv backend/.venv
PYTHONPATH="$PWD/backend" bash scripts/sync-contract.sh --update
PYTHONPATH="$PWD/backend" bash scripts/sync-contract.sh
rm backend/.venv
```

Expected: focused backend tests pass and the generated OpenAPI includes `GET /report-research` plus the two list schemas.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/v1/report_research.py backend/app/queries/report_wiki.py backend/app/api/v1/report_research.py backend/tests/test_report_research.py frontend/openapi.json frontend/src/contracts/v1.ts
git commit -m "feat: list report research intake cases"
```

## Task 2: Normalize real Cases for the dispatch desk

**Files:**

- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/domain/prototypeTypes.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Test: `frontend/src/tests/HttpResearchAdapter.test.ts`
- Test: `frontend/src/tests/MockResearchAdapter.test.ts`

- [ ] **Step 1: Write failing adapter tests**

```tsx
it("maps report Case list items without inventing a conclusion or scope", async () => {
  fetchMock.mockResolvedValueOnce(jsonResponse({
    items: [{
      case_id: "report-1", title: "授权研报", document_id: "doc-1",
      input_kind: "pasted_text", intake_state: "needs_supplement",
      next_action: "supplement_text", blocking_reason: "原件正文不可读",
      updated_at: "2026-08-09T00:00:00Z",
    }],
  }));

  await expect(adapter.listReportResearch()).resolves.toEqual([{
    kind: "report", id: "report-1", title: "授权研报", route: "/reports/report-1/intake",
    status: "等待补充正文", nextAction: "补充正文并标注页码",
    blockingReason: "原件正文不可读", updatedAt: "2026-08-09T00:00:00Z",
  }]);
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- --run src/tests/HttpResearchAdapter.test.ts src/tests/MockResearchAdapter.test.ts`

Expected: fail because `listReportResearch()` and the normalized Case contract do not exist.

- [ ] **Step 3: Define the frontend-only union and mapper**

Add this domain type near `EventResearchListItem`; do not make a report into an event.

```ts
export type ResearchDeskItem = {
  kind: "event" | "report";
  id: string;
  title: string;
  subtitle: string;
  status: string;
  nextAction: string | null;
  blockingReason: string | null;
  route: string;
  updatedAt: string;
};
```

Implement `listReportResearch()` using the generated `ReportResearchListResponse` and the same strict state/action white-list used by `getReportResearchIntake()`. Map event items in a new `listResearchDeskItems()` facade by `Promise.all([listEventResearch(), listReportResearch()])`, sorting by descending `updatedAt`. The mock adapter returns deterministic report and event records but must label all fixture summaries as demo data in its own source comments, not in UI copy.

- [ ] **Step 4: Verify GREEN**

Run: `cd frontend && npm test -- --run src/tests/HttpResearchAdapter.test.ts src/tests/MockResearchAdapter.test.ts`

Expected: pass; unknown report list states/actions reject with the existing compatibility error.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/domain/eventResearch.ts frontend/src/domain/prototypeTypes.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/data/researchClient.ts frontend/src/tests/HttpResearchAdapter.test.ts frontend/src/tests/MockResearchAdapter.test.ts
git commit -m "feat: normalize research cases for dispatch"
```

## Task 3: Replace the event-list root with the research dispatch prototype

**Files:**

- Create: `frontend/src/pages/prototype/ResearchDispatchScreen.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/components/PrototypeShell.tsx`
- Modify: `frontend/src/styles-prototype.css`
- Test: `frontend/src/tests/ResearchDispatchScreen.test.tsx`
- Test: `frontend/src/tests/PrototypeShell.test.tsx`

- [ ] **Step 1: Write failing UI tests**

```tsx
it("renders the highest-priority real Case before the remaining independent Cases", async () => {
  adapter.listResearchDeskItems = vi.fn().mockResolvedValue([
    deskItem({ id: "event-1", nextAction: "审核 1 条关键证据", blockingReason: "可能改变已发布判断" }),
    deskItem({ id: "report-1", kind: "report", nextAction: "补充正文并标注页码" }),
  ]);

  renderDispatch();

  expect(await screen.findByRole("heading", { name: "今天，先推进哪一个判断？" })).toBeVisible();
  expect(screen.getByRole("link", { name: "审核 1 条关键证据" })).toHaveAttribute("href", "/events/event-1/review");
  expect(screen.getByRole("link", { name: "补充正文并标注页码" })).toHaveAttribute("href", "/reports/report-1/intake");
  expect(screen.queryByText(/共享结论/)).not.toBeInTheDocument();
});

it("uses dispatch as the root navigation and labels materials as an inbox", () => {
  render(<MemoryRouter initialEntries={["/"]}><Routes><Route element={<PrototypeShell />}><Route index element={<div />} /></Route></Routes></MemoryRouter>);
  expect(screen.getByRole("link", { name: "研究调度" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "资料收件箱" })).toHaveAttribute("href", "/reports/new");
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- --run src/tests/ResearchDispatchScreen.test.tsx src/tests/PrototypeShell.test.tsx`

Expected: fail because the root redirects to `/events` and the dispatch screen does not exist.

- [ ] **Step 3: Implement dispatch hierarchy and navigation**

`ResearchDispatchScreen` must use semantic sections rather than a metric-card grid:

```tsx
<header className="dispatch-header">
  <div><p className="section-kicker">研究调度</p><h1>今天，先推进哪一个判断？</h1></div>
  <div className="dispatch-header__actions"><Link to="/events/new">从事件开始</Link><Link to="/reports/new">导入或粘贴资料</Link></div>
</header>
<section aria-labelledby="priority-title">{/* only items with nextAction; first is highest priority */}</section>
<section aria-labelledby="case-list-title">{/* all independently routed items */}</section>
<aside aria-label="研究资产">{/* count by kind/status only; no fabricated CaseRelation count */}</aside>
```

Rank only by explicit human action, then non-null blocking reason, then `updatedAt`. The UI says `关联研究尚未接入审核关系数据` instead of copying the prototype’s numerical relationship claims. Add loading skeleton, error retry, and an empty state whose two links are `从事件开始` and `导入或粘贴资料`.

Change the shell root route to `ResearchDispatchScreen`; use top/bottom navigation labels `研究调度`, `待我处理`, `资料收件箱`, `资料库`, and `监测与版本`. Make the breadcrumb resolve `/reports/*` as `资料收件箱 / 研报资料` and `/` as `研究调度 / 优先队列`. Preserve search behavior.

- [ ] **Step 4: Verify GREEN**

Run: `cd frontend && npm test -- --run src/tests/ResearchDispatchScreen.test.tsx src/tests/PrototypeShell.test.tsx`

Expected: pass; rendered Case links retain their own route kind and the empty state contains both authorized entry paths.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/prototype/ResearchDispatchScreen.tsx frontend/src/main.tsx frontend/src/components/PrototypeShell.tsx frontend/src/styles-prototype.css frontend/src/tests/ResearchDispatchScreen.test.tsx frontend/src/tests/PrototypeShell.test.tsx
git commit -m "feat: make research dispatch the product home"
```

## Task 4: Add a shared independent-Case header to existing workbenches

**Files:**

- Create: `frontend/src/components/ResearchCaseSwitcher.tsx`
- Modify: `frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx`
- Modify: `frontend/src/pages/prototype/ReportResearchScreen.tsx`
- Modify: `frontend/src/pages/prototype/ReportResearchIntakeScreen.tsx`
- Modify: `frontend/src/styles-prototype.css`
- Test: `frontend/src/tests/ResearchCaseSwitcher.test.tsx`
- Test: `frontend/src/tests/EventResearchWorkbenchScreen.test.tsx`
- Test: `frontend/src/tests/ReportResearchScreen.test.tsx`

- [ ] **Step 1: Write failing route-isolation tests**

```tsx
it("switches an event Case to its own route without carrying the current workbench state", async () => {
  renderSwitcher({ current: "event-1", items: [
    deskItem({ id: "event-1", kind: "event", route: "/events/event-1" }),
    deskItem({ id: "report-1", kind: "report", route: "/reports/report-1/intake" }),
  ]});

  await user.selectOptions(screen.getByLabelText("切换研究"), "report:report-1");

  expect(screen.getByTestId("location")).toHaveTextContent("/reports/report-1/intake");
  expect(screen.queryByText("event-1 的当前判断")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend && npm test -- --run src/tests/ResearchCaseSwitcher.test.tsx src/tests/EventResearchWorkbenchScreen.test.tsx src/tests/ReportResearchScreen.test.tsx`

Expected: fail because no shared selector exists.

- [ ] **Step 3: Implement the Case header contract**

Create `ResearchCaseSwitcher` with this explicit API:

```ts
export function ResearchCaseSwitcher({ currentRoute, items }: {
  currentRoute: string;
  items: ResearchDeskItem[];
}): JSX.Element
```

It loads the same `listResearchDeskItems()` projection, renders `<select aria-label="切换研究">`, and navigates to the selected item’s `route`. It has a `返回研究调度` link to `/`. It does not store or pass thesis, review, document, scope, action, or relation data between routes. Each workbench uses it only after its own authoritative data request succeeds. Pending report intake retains the intake screen and selector but never links to `/reports/:caseId` unless an explicit reviewed scope exists.

- [ ] **Step 4: Verify GREEN**

Run: `cd frontend && npm test -- --run src/tests/ResearchCaseSwitcher.test.tsx src/tests/EventResearchWorkbenchScreen.test.tsx src/tests/ReportResearchScreen.test.tsx`

Expected: switching changes only the URL; the destination page refetches its own Case data and pending reports remain in intake.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ResearchCaseSwitcher.tsx frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx frontend/src/pages/prototype/ReportResearchScreen.tsx frontend/src/pages/prototype/ReportResearchIntakeScreen.tsx frontend/src/styles-prototype.css frontend/src/tests/ResearchCaseSwitcher.test.tsx frontend/src/tests/EventResearchWorkbenchScreen.test.tsx frontend/src/tests/ReportResearchScreen.test.tsx
git commit -m "feat: add independent Case switching"
```

## Task 5: Visual and release verification against the archived prototype

**Files:**

- Modify: `docs/design/2026-08-08-research-operating-system-baseline.md`
- Test: `backend/tests/test_report_research.py`
- Test: `frontend/src/tests/ResearchDispatchScreen.test.tsx`

- [ ] **Step 1: Record the delivered decision**

Append a dated baseline change entry: the root is dispatch; report material uses an API-backed inbox list; cross-Case relations stay unavailable until reviewed relation data exists; Case switching is route-only and never transfers state.

- [ ] **Step 2: Run all focused release checks**

Run:

```bash
PYTHONPATH="$PWD/backend" /Users/xiongjiali/code/fund-engine/backend/.venv/bin/python -m pytest backend/tests/test_report_research.py backend/tests/test_report_embed_api.py -q
ln -s /Users/xiongjiali/code/fund-engine/backend/.venv backend/.venv
PYTHONPATH="$PWD/backend" bash scripts/sync-contract.sh
rm backend/.venv
cd frontend && npm test -- --run src/tests/ResearchDispatchScreen.test.tsx src/tests/ResearchCaseSwitcher.test.tsx src/tests/ReportResearchCreateScreen.test.tsx src/tests/ReportResearchIntakeScreen.test.tsx src/tests/HttpResearchAdapter.test.ts
npm run typecheck
npm run build
```

Expected: all focused tests pass; only explicitly marked PostgreSQL skips are allowed; contract gate reports `contract is in sync.`

- [ ] **Step 3: Browser acceptance**

At desktop and 390px wide, inspect `/` and both Case route kinds. Confirm: root asks “今天，先推进哪一个判断？”; each visible Case has one correctly routed next action; the switcher returns no foreign Case content; pending report points to intake; no relationship count or conclusion is fabricated.

- [ ] **Step 4: Commit**

```bash
git add docs/design/2026-08-08-research-operating-system-baseline.md
git commit -m "docs: record dispatch prototype delivery"
```
