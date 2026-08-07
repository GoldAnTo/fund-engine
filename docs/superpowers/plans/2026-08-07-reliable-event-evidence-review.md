# Reliable Event Evidence Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make event evidence review measurable, source-verifiable, and isolated to the active event so a reviewer can make a meaningful decision without admitting fixture or unrelated material.

**Architecture:** Source admission is a small deterministic domain policy used before proposing evidence and while projecting the review queue. Event research seeds its own pasted-news source record and recall is restricted through `CaseDocumentVersion`, so a new event cannot silently borrow another case's materials. The review queue exposes an event-level summary and per-item source status; the React page renders a selectable queue beside a detailed evidence panel and permits acceptance only for admissible sources.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, PostgreSQL/SQLite tests, React, TypeScript, Vitest.

---

### Task 1: Define deterministic source admission

**Files:**
- Create: `backend/app/services/source_admission.py`
- Create: `backend/tests/test_source_admission.py`

- [ ] **Step 1: Write the failing source-classification tests**

```python
from app.services.source_admission import SourceStatus, classify_source


def test_rejects_fixture_and_example_domains():
    result = classify_source("https://example.test/fixture")
    assert result.status is SourceStatus.INVALID
    assert "测试" in result.reason


def test_accepts_open_https_source_as_unverified_until_content_is_frozen():
    result = classify_source("https://abc.xyz/investor/news/q2")
    assert result.status is SourceStatus.PASTED_UNVERIFIED
    assert result.acceptable_for_review is False


def test_accepts_frozen_event_document_as_reviewable_source():
    result = classify_source("https://abc.xyz/investor/news/q2", content_verified=True)
    assert result.status is SourceStatus.ACCESSIBLE
    assert result.acceptable_for_review is True
```

- [ ] **Step 2: Run the tests to verify the module is missing**

Run: `cd backend && .venv/bin/pytest tests/test_source_admission.py -q`

Expected: import failure for `app.services.source_admission`.

- [ ] **Step 3: Implement the policy as a pure module**

```python
class SourceStatus(StrEnum):
    ACCESSIBLE = "accessible"
    PASTED_UNVERIFIED = "pasted_unverified"
    INVALID = "invalid"


@dataclass(frozen=True)
class SourceAdmission:
    status: SourceStatus
    reason: str

    @property
    def acceptable_for_review(self) -> bool:
        return self.status is SourceStatus.ACCESSIBLE


def classify_source(url: str | None, *, content_verified: bool = False) -> SourceAdmission:
    # Reject missing URLs, example/test domains, and non-http(s) URLs.
    # A user-pasted URL remains unverified until a frozen source document
    # associated with the event has been created from it.
```

- [ ] **Step 4: Run the source-policy tests**

Run: `cd backend && .venv/bin/pytest tests/test_source_admission.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit the policy**

```bash
git add backend/app/services/source_admission.py backend/tests/test_source_admission.py
git commit -m "feat: classify event evidence source admission"
```

### Task 2: Seed the pasted event source and stop cross-event recall

**Files:**
- Modify: `backend/app/services/event_research.py`
- Modify: `backend/app/services/recall.py`
- Modify: `backend/tests/test_event_research_api.py`
- Modify: `backend/tests/test_recall.py`

- [ ] **Step 1: Write failing event-isolation tests**

```python
def test_create_event_records_pasted_news_as_its_own_case_document(api_client):
    created = api_client.post("/api/v1/event-research", json=event_payload()).json()
    documents = api_client.get("/api/v1/documents", params={"case_id": created["case_id"]}).json()
    assert len(documents["items"]) == 1
    assert documents["items"][0]["source_url"] == event_payload()["source_url"]


def test_recall_for_event_thesis_never_returns_statement_from_other_case(session):
    event_thesis, other_case_statement = seed_two_cases_with_similar_text(session)
    recalled = RecallService(session).for_thesis(event_thesis, cutoff=utcnow())
    assert other_case_statement.id not in {item.id for item in recalled}
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_event_research_api.py tests/test_recall.py -q`

Expected: no event-owned document and a recalled statement from the other case.

- [ ] **Step 3: Create an event-owned pasted document and scope retrieval**

```python
# EventResearchService.create, after case creation
document_service = DocumentService(DocumentRepository(self._session))
document = document_service.freeze(
    raw=payload.raw_input.encode("utf-8"),
    source_url=payload.source_url or "event://pasted-news",
    parser_version="user-pasted-v1",
    parse_state="partial",
)
document_service.attach_to_case(
    research_case_id=case.id, document_version_id=document.id,
)
document_service.add_span(
    document_version_id=document.id,
    locator={"kind": "user_pasted_news"},
    verbatim_text=payload.raw_input,
)

# RecallService._visible_candidates
.join(CaseDocumentVersion, CaseDocumentVersion.document_version_id == DocumentVersion.id)
.where(CaseDocumentVersion.research_case_id == thesis.research_case_id)
```

`classify_source()` must treat `parser_version="user-pasted-v1"` as `pasted_unverified`; it is a research lead, not acceptable formal evidence until a retrievable source path creates a non-user-pasted document version.

- [ ] **Step 4: Re-run focused isolation tests**

Run: `cd backend && .venv/bin/pytest tests/test_event_research_api.py tests/test_recall.py -q`

Expected: all selected tests pass and no source from another case is recalled.

- [ ] **Step 5: Commit source seeding and recall isolation**

```bash
git add backend/app/services/event_research.py backend/app/services/recall.py backend/tests/test_event_research_api.py backend/tests/test_recall.py
git commit -m "fix: scope event recall to event sources"
```

### Task 3: Project a reliable event review queue with totals

**Files:**
- Modify: `backend/app/queries/review_queue.py`
- Modify: `backend/app/repositories/event_research.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/api/v1/event_research.py`
- Create: `backend/app/services/event_review_queue.py`
- Create: `backend/tests/test_event_review_queue.py`

- [ ] **Step 1: Write the failing review-summary tests**

```python
def test_event_queue_reports_progress_and_marks_fixture_source_invalid(cmd_client, seeded_event_case):
    response = cmd_client.get(f"/api/v1/event-research/{seeded_event_case.id}/review-queue")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total": 3, "reviewed": 1, "pending": 1, "invalid_source": 1,
        "current_round": 1,
    }
    invalid = next(item for item in body["items"] if item["source_status"] == "invalid")
    assert invalid["can_accept"] is False
    assert "测试" in invalid["source_status_reason"]


def test_invalid_source_does_not_keep_event_lifecycle_waiting_for_human_review(session, seeded_event_case):
    reconcile_event_review_queue(session, seeded_event_case.id)
    assert EventResearchLifecycleRepository(session).pending_key_review_count(seeded_event_case.id) == 1
```

- [ ] **Step 2: Run the new queue tests to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_event_review_queue.py -q`

Expected: event review route is absent and queue items have no source admission data.

- [ ] **Step 3: Add the event-scoped queue DTO and reconciliation**

```python
class EventReviewQueueSummaryDTO(V1Model):
    total: int
    reviewed: int
    pending: int
    invalid_source: int
    current_round: int
    next_action: str | None


class EventReviewQueueItemDTO(ReviewQueueItemDTO):
    source_title: str | None
    source_status: str
    source_status_reason: str
    can_accept: bool
    proposal_reason: str
    position: int
```

The projection must classify every pending proposal from its linked document, return stable `proposed_at, id` ordering, and preserve invalid items for audit. `reconcile_event_review_queue()` closes only the operational `review_proposal` task for invalid-source proposals and emits an outbox event with the admission reason. It does not publish evidence and it does not delete or mutate the proposal payload.

Add `GET /api/v1/event-research/{case_id}/review-queue`, and make lifecycle counts use the same reconciling policy so invalid items never block a case forever.

- [ ] **Step 4: Run queue and lifecycle tests**

Run: `cd backend && .venv/bin/pytest tests/test_event_review_queue.py tests/test_event_research_lifecycle.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the review queue contract**

```bash
git add backend/app/queries/review_queue.py backend/app/repositories/event_research.py backend/app/schemas/v1/event_research.py backend/app/api/v1/event_research.py backend/app/services/event_review_queue.py backend/tests/test_event_review_queue.py
git commit -m "feat: expose event evidence review progress"
```

### Task 4: Prevent invalid sources from becoming formal evidence

**Files:**
- Modify: `backend/app/api/v1/commands/proposals.py`
- Modify: `backend/app/services/proposal_publisher.py`
- Modify: `backend/tests/test_proposal_review_api.py`

- [ ] **Step 1: Write the failing acceptance-guard test**

```python
def test_confirming_invalid_event_source_returns_422_and_publishes_nothing(cmd_client, invalid_source_proposal):
    response = cmd_client.post(
        f"/api/v1/review-proposals/{invalid_source_proposal.id}/decisions",
        json=decision_payload("confirmed"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert count_published_evidence(cmd_client) == 0
```

- [ ] **Step 2: Run the guard test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_proposal_review_api.py::test_confirming_invalid_event_source_returns_422_and_publishes_nothing -q`

Expected: current endpoint returns `201`.

- [ ] **Step 3: Enforce admission before a confirmed or modified decision publishes**

```python
def _assert_publishable_event_source(proposal: Proposal, db: Session) -> None:
    if proposal.research_case_id is None or proposal.kind != "evidence_link":
        return
    admission = EventReviewQueueQueries(db).admission_for_proposal(proposal)
    if not admission.can_accept:
        raise ValidationFailedError(
            f"来源不可核验，不能采纳：{admission.reason}"
        )
```

For `confirmed` and `modified` outcomes, call this guard **before** `ProposalService.decide()` so an invalid source leaves the proposal pending and reviewable. `rejected` remains allowed and records the reviewer’s exclusion decision. Return the stable v1 validation envelope, never an internal error.

- [ ] **Step 4: Run proposal-review tests**

Run: `cd backend && .venv/bin/pytest tests/test_proposal_review_api.py -q`

Expected: all tests pass, including the new invalid-source guard.

- [ ] **Step 5: Commit the formal-evidence guard**

```bash
git add backend/app/api/v1/commands/proposals.py backend/app/services/proposal_publisher.py backend/tests/test_proposal_review_api.py
git commit -m "fix: block invalid sources from evidence publication"
```

### Task 5: Extend the frontend event-review contract and adapters

**Files:**
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/domain/prototypeTypes.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/tests/HttpResearchAdapter.test.ts`

- [ ] **Step 1: Write failing adapter tests**

```typescript
it("maps event review totals, source status, and acceptance capability", async () => {
  server.use(eventReviewQueue({ total: 4, reviewed: 1, pending: 2, invalid_source: 1 }));
  const queue = await adapter.getEventReviewQueue("case-1");
  expect(queue.summary.pending).toBe(2);
  expect(queue.items[0]).toMatchObject({ sourceStatus: "invalid", canAccept: false });
});
```

- [ ] **Step 2: Run the adapter test to verify it fails**

Run: `cd frontend && npm test -- HttpResearchAdapter.test.ts`

Expected: `getEventReviewQueue` does not exist.

- [ ] **Step 3: Add typed queue mapping and honest mock fixtures**

```typescript
export interface EventReviewQueueItem extends EventEvidenceCitation {
  proposalId: string;
  position: number;
  sourceStatus: "accessible" | "pasted_unverified" | "invalid";
  sourceStatusReason: string;
  canAccept: boolean;
  proposalReason: string;
}

export interface EventReviewQueue {
  summary: { total: number; reviewed: number; pending: number; invalidSource: number; currentRound: number; nextAction: string | null };
  items: EventReviewQueueItem[];
}
```

The mock must include one accessible primary source, one restricted source, and one invalid fixture source. It must never label an `example.*` URL accessible.

- [ ] **Step 4: Run frontend adapter tests**

Run: `cd frontend && npm test -- HttpResearchAdapter.test.ts`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the frontend contract**

```bash
git add frontend/src/domain/eventResearch.ts frontend/src/domain/prototypeTypes.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/tests/HttpResearchAdapter.test.ts
git commit -m "feat: map event evidence review status"
```

### Task 6: Replace the single-item review screen with queue and detailed decision UI

**Files:**
- Modify: `frontend/src/pages/prototype/KeyEvidenceReviewScreen.tsx`
- Modify: `frontend/src/styles-prototype.css`
- Modify: `frontend/src/tests/KeyEvidenceReviewScreen.test.tsx`

- [ ] **Step 1: Write failing interaction tests**

```typescript
it("shows total, progress, and the selected evidence position", async () => {
  renderWithAppShell(<KeyEvidenceReviewScreen />, { initialEntries: ["/events/case-1/review?client=mock"] });
  expect(await screen.findByText("关键证据 4 条")).toBeVisible();
  expect(screen.getByText("已处理 1 条 · 待处理 2 条 · 已隔离 1 条")).toBeVisible();
});

it("disables acceptance for an invalid source and explains how to proceed", async () => {
  renderWithInvalidSourceSelected();
  expect(await screen.findByText("不可验证来源")).toBeVisible();
  expect(screen.getByRole("button", { name: "采纳为本因素的正式证据" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "打开原始来源" })).toHaveAttribute("target", "_blank");
});
```

- [ ] **Step 2: Run the screen tests to verify they fail**

Run: `cd frontend && npm test -- KeyEvidenceReviewScreen.test.tsx`

Expected: progress text, source state, and named actions are absent.

- [ ] **Step 3: Implement the review page around the typed event queue**

```tsx
<header>
  <p>第 {queue.summary.currentRound} 轮 · 关键证据 {queue.summary.total} 条</p>
  <h1>先确认来源，再决定是否进入结论依据</h1>
  <p>已处理 {queue.summary.reviewed} 条 · 待处理 {queue.summary.pending} 条 · 已隔离 {queue.summary.invalidSource} 条</p>
</header>
<nav aria-label="待审关键证据">{/* selectable rows with factor and source status */}</nav>
<section aria-label="当前证据详情">{/* excerpt, open-source link, locator, times, AI proposal reason */}</section>
<section aria-label="审核决定">
  <button disabled={!selected.canAccept}>采纳为本因素的正式证据</button>
  <button>不采纳，不计入结论</button>
  <button>退回并继续找真实来源</button>
</section>
```

On each success, refetch the queue, retain the next pending selection, and announce the changed count with `role="status"`. The link uses `target="_blank" rel="noreferrer"`. Never show `example.*` as an unqualified source title.

- [ ] **Step 4: Run screen tests, typecheck, and build**

Run: `cd frontend && npm test -- KeyEvidenceReviewScreen.test.tsx && npm run typecheck && npm run build`

Expected: tests pass, TypeScript exits 0, and Vite build exits 0.

- [ ] **Step 5: Commit the review UX**

```bash
git add frontend/src/pages/prototype/KeyEvidenceReviewScreen.tsx frontend/src/styles-prototype.css frontend/src/tests/KeyEvidenceReviewScreen.test.tsx
git commit -m "feat: make event evidence review actionable"
```

### Task 7: Verify the real event flow and clean fixture contamination

**Files:**
- Modify: `backend/tests/test_event_research_lifecycle.py`
- Modify: `backend/tests/test_event_research_api.py`
- Create: `frontend/e2e/event-research.spec.ts`

- [ ] **Step 1: Write an end-to-end regression for the full decision boundary**

```python
def test_event_cannot_publish_fixture_evidence_but_can_progress_after_valid_evidence(api_client):
    case_id = create_event_with_valid_and_fixture_material(api_client)
    queue = api_client.get(f"/api/v1/event-research/{case_id}/review-queue").json()
    assert queue["summary"]["invalid_source"] == 1
    assert next(item for item in queue["items"] if item["source_status"] == "invalid")["can_accept"] is False
    confirm_accessible_item(api_client, queue)
    assert lifecycle(api_client, case_id)["next_human_action"] != "审核 2 条关键证据"
```

- [ ] **Step 2: Run the backend flow test to verify it fails before integration**

Run: `cd backend && .venv/bin/pytest tests/test_event_research_lifecycle.py tests/test_event_research_api.py -q`

Expected: fixture source appears as reviewable or lifecycle remains blocked.

- [ ] **Step 3: Add browser coverage for reviewer comprehension**

```typescript
test("event review exposes progress, opens a source, and blocks invalid evidence", async ({ page }) => {
  await page.goto("/events/event-tsm/review?client=mock");
  await expect(page.getByText(/关键证据 4 条/)).toBeVisible();
  await expect(page.getByRole("button", { name: "采纳为本因素的正式证据" })).toBeDisabled();
  await page.getByRole("button", { name: "退回并继续找真实来源" }).click();
  await expect(page.getByRole("status")).toContainText("待处理");
});
```

- [ ] **Step 4: Run full verification**

Run: `cd backend && .venv/bin/pytest tests -q`

Run: `cd frontend && npm test && npm run typecheck && npm run build`

Expected: backend suite has zero failures; frontend tests, typecheck, and build all exit 0.

- [ ] **Step 5: Commit regression coverage**

```bash
git add backend/tests/test_event_research_lifecycle.py backend/tests/test_event_research_api.py frontend/e2e/event-research.spec.ts
git commit -m "test: cover reliable event evidence review"
```
