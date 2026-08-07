# Event Research Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each news or market event an independent, automatically advancing research case whose evidence, key factors, human decisions, and formal conclusion remain traceable and never leak into another event.

**Architecture:** Add an append-only event brief to the existing immutable research ledger and a small operational lifecycle projection for the current natural-language status. A new event API performs AI-assisted extraction, human-editable creation, and automatic durable-run enqueueing; the existing `AutoResearchService` remains the worker engine, but is hidden behind the lifecycle. Replace the prototype-era navigation and multi-page manual flow with an event list, one case workbench, contextual review, and a conclusion draft/publish loop.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, existing append-only ledger and durable worker, React 18, TypeScript, React Router, Vitest, pytest, existing HTTP/mock adapters.

---

## File structure and ownership

| Area | Files | Responsibility |
| --- | --- | --- |
| Immutable event facts | `backend/app/models/event_research.py`, `backend/alembic/versions/0013_event_research_lifecycle.py` | Persist raw event input, AI extraction, human-confirmed event framing, and research-factor drafts without mutating `ResearchCase`. |
| Operational status | `backend/app/models/operational.py`, `backend/app/repositories/event_research.py` | Store the replaceable current lifecycle projection, active run, current round, and next human decision. |
| Event API | `backend/app/schemas/v1/event_research.py`, `backend/app/api/v1/event_research.py`, `backend/app/api/v1/router.py` | Extract event fields, create and auto-start a case, list independent events, return a focused workbench view, and accept contextual human actions. |
| Lifecycle/domain services | `backend/app/services/event_extraction.py`, `backend/app/services/event_research.py`, `backend/app/services/auto_research.py` | Separate reliable extraction, case creation, lifecycle summaries, automatic continuation, and existing collection work. |
| Backend tests | `backend/tests/test_event_research_api.py`, `backend/tests/test_event_extraction.py`, `backend/tests/test_event_research_lifecycle.py` | Lock down non-fabrication, case isolation, automatic startup, multi-round continuation, review/publish gates, and event switching. |
| Frontend domain/adapters | `frontend/src/domain/eventResearch.ts`, `frontend/src/data/eventResearchAdapter.ts`, `frontend/src/data/httpResearchAdapter.ts`, `frontend/src/data/mockResearchAdapter.ts`, `frontend/src/data/researchClient.ts` | Give screens one clean event-workbench contract, with live HTTP and deterministic mock behavior. |
| Primary screens | `frontend/src/pages/prototype/EventResearchListScreen.tsx`, `EventResearchCreateScreen.tsx`, `EventResearchWorkbenchScreen.tsx`, `KeyEvidenceReviewScreen.tsx` | Implement the approved list, creation, automatic-research, evidence-review, and conclusion-confirmation states. |
| Shell/routing | `frontend/src/components/PrototypeShell.tsx`, `frontend/src/main.tsx`, `frontend/src/styles-prototype.css` | Restrict primary navigation to the approved five destinations and keep technical/legacy pages as deep links only. |
| Frontend tests | `frontend/src/tests/EventResearch*.test.tsx`, `frontend/src/tests/eventResearchAdapter.test.ts` | Cover editable extraction, case switching, status transitions, review actions, draft publication, and accessibility semantics. |

## Surface migration matrix

| Existing surface | Target surface | Delivery task |
| --- | --- | --- |
| `OverviewScreen` | `EventResearchListScreen` | Task 7 |
| `NewResearchScreen` | `EventResearchCreateScreen` | Task 7 |
| `CaseWorkbenchScreen` + `ConclusionScreen` | `EventResearchWorkbenchScreen` | Task 8 |
| `ReviewWorkbenchScreen` | `KeyEvidenceReviewScreen` and `/events?attention=1` | Tasks 6 and 8 |
| `LibraryScreen` | Event-contextual source library | Task 10 |
| `VersionsScreen` | Event monitoring and conclusion-change history | Task 10 |
| `RelationshipCanvasScreen` | Collapsed `研究依据` graph/list drill-down | Task 10 |
| `CompanyListPage` + `DataCenterScreen` | Event-contextual object/data drill-down | Task 10 |
| `ThemeIndexScreen` + `ThemeWorkbenchScreen` + `TopicListPage` | Event archive and cross-event observation | Task 10 |
| `AutoResearchRunsScreen` + `ResearchPlanScreen` | Diagnostic links within research basis | Task 9 |

## Task 1: Persist immutable event framing and operational lifecycle state

**Files:**
- Create: `backend/app/models/event_research.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/operational.py`
- Create: `backend/alembic/versions/0013_event_research_lifecycle.py`
- Create: `backend/tests/test_event_research_lifecycle.py`

- [x] **Step 1: Write the failing persistence tests**

```python
def test_event_brief_is_case_scoped_and_append_only(session):
    first, second = make_case(session), make_case(session)
    first_brief = EventResearchBrief(
        research_case_id=first.id,
        raw_input="Alphabet 上调资本开支后盘后下跌",
        source_url="https://example.com/news",
        event_title="Alphabet 财报后下跌",
        company_name="Alphabet",
        ticker="GOOGL",
        event_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        market_reaction="盘后下跌 4%",
        extraction_state="human_confirmed",
        created_at=datetime.now(timezone.utc),
    )
    session.add(first_brief)
    session.commit()

    assert briefs_for_case(session, first.id) == [first_brief]
    assert briefs_for_case(session, second.id) == []
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(EventResearchBrief).values(event_title="changed"))
```

Add tests for one lifecycle row per case, `active_run_id` targeting the correct case, and lifecycle status transitions limited to `extracting`, `researching`, `awaiting_key_review`, `continuing`, `awaiting_scope`, `draft_ready`, `published`, and `exhausted`.

- [x] **Step 2: Run the new tests and verify they fail**

Run: `.venv/bin/pytest backend/tests/test_event_research_lifecycle.py -q`

Expected: FAIL because `EventResearchBrief`, `EventResearchFactorDraft`, and `EventResearchLifecycle` do not exist.

- [x] **Step 3: Add the event ledger tables and lifecycle projection**

```python
# backend/app/models/event_research.py
class EventResearchBrief(Base):
    __tablename__ = "event_research_briefs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), nullable=False)
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    event_title: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(String(32))
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    market_reaction: Mapped[str | None] = mapped_column(Text)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class EventResearchLifecycle(Base):
    __tablename__ = "event_research_lifecycles"
    research_case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_cases.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_runs.id"))
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_summary: Mapped[str] = mapped_column(Text, nullable=False)
    current_gap: Mapped[str | None] = mapped_column(Text)
    next_human_action: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Add the three immutable event tables to `IMMUTABLE_TABLES`; keep only `EventResearchLifecycle` mutable. Write Alembic upgrade/downgrade that creates the event tables, factor table, lifecycle projection, and their foreign keys without altering existing cases.

- [x] **Step 4: Run persistence tests**

Run: `.venv/bin/pytest backend/tests/test_event_research_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/alembic/versions/0013_event_research_lifecycle.py backend/tests/test_event_research_lifecycle.py
git commit -m "feat: add event research lifecycle ledger"
```

## Task 2: Add non-fabricating AI event extraction and editable create/auto-start commands

**Files:**
- Create: `backend/app/schemas/v1/event_research.py`
- Create: `backend/app/services/event_extraction.py`
- Create: `backend/app/services/event_research.py`
- Create: `backend/app/api/v1/event_research.py`
- Modify: `backend/app/api/v1/router.py`
- Create: `backend/tests/test_event_extraction.py`
- Create: `backend/tests/test_event_research_api.py`

- [x] **Step 1: Write failing API tests**

```python
def test_extract_event_keeps_unknown_fields_null_and_marks_confirmation(cmd_client, fake_event_llm):
    response = cmd_client.post("/api/v1/event-research/extract", json={
        "raw_input": "公司宣布新指引，盘后下跌。",
        "source_url": "https://example.com/brief",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["company_name"] is None
    assert body["ticker"] is None
    assert body["confirmation_required"] is True
    assert body["research_question"]
    assert len(body["candidate_factors"]) in {3, 4, 5}

def test_create_event_case_enqueues_research_without_manual_run_button(cmd_client):
    response = cmd_client.post("/api/v1/event-research", json=valid_confirmed_event())
    assert response.status_code == 201
    body = response.json()
    assert body["lifecycle"]["status"] == "researching"
    assert body["lifecycle"]["active_run_id"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest backend/tests/test_event_extraction.py backend/tests/test_event_research_api.py -q`

Expected: FAIL with 404 routes and missing service classes.

- [x] **Step 3: Implement extraction and create commands**

```python
# The service accepts parsed JSON only after validating every populated field
# against the supplied raw input.  Unknown is represented by None, never a
# plausible-looking invented value.
@dataclass(frozen=True)
class EventExtraction:
    event_title: str | None
    company_name: str | None
    ticker: str | None
    event_at: datetime | None
    market_reaction: str | None
    summary: str | None
    research_question: str
    candidate_factors: tuple[str, ...]

def create_event_research(payload: CreateEventResearchRequest) -> EventResearchCreatedDTO:
    case = research_service.add_case(
        title=payload.event_title,
        industry_topic="事件研究",
        created_by=payload.created_by,
        research_object=payload.company_name or payload.event_title,
        phenomenon=payload.market_reaction,
        core_question=payload.research_question,
        evidence_cutoff=payload.evidence_cutoff,
    )
    persist_event_brief(case.id, payload)
    for factor in payload.candidate_factors:
        research_service.add_thesis(case.id, statement=factor, created_by=payload.created_by, creator_type="ai", review_state="draft")
    run = AutoResearchService(session).start(case.id, max_rounds=3, budget=100)
    lifecycle_repo.upsert_researching(case.id, run.id, "正在建立第一轮证据检索")
    session.commit()
    return created_dto(case, run)
```

Require `raw_input`, `event_title`, `research_question`, `created_by`, and 3–5 selected factors at creation. Treat company, ticker, event time, market reaction, and URL as editable optional values. Return the case id plus a natural-language lifecycle object; do not return run internals as the primary payload.

- [x] **Step 4: Run API tests**

Run: `.venv/bin/pytest backend/tests/test_event_extraction.py backend/tests/test_event_research_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1 backend/app/schemas/v1 backend/app/services/event_extraction.py backend/app/services/event_research.py backend/tests/test_event_extraction.py backend/tests/test_event_research_api.py
git commit -m "feat: create event research with AI extraction"
```

## Task 3: Project automatic multi-round research into human-readable case states

**Files:**
- Modify: `backend/app/services/auto_research.py`
- Create: `backend/app/repositories/event_research.py`
- Modify: `backend/app/api/v1/auto_research.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/api/v1/event_research.py`
- Modify: `backend/tests/test_event_research_lifecycle.py`
- Modify: `backend/tests/test_auto_research_api.py`

- [x] **Step 1: Write failing continuation and isolation tests**

```python
def test_no_key_evidence_starts_the_next_bounded_round_and_preserves_prior_links(session):
    case = create_event_case(session)
    first = AutoResearchService(session).start(case.id, max_rounds=1, budget=10)
    first_links = reviewed_links_for_case(session, case.id)
    finish_run_without_decisive_evidence(session, first)

    lifecycle = EventResearchLifecycleRepository(session).get(case.id)
    assert lifecycle.status == "continuing"
    assert lifecycle.current_round == 2
    assert lifecycle.active_run_id != first.id
    assert reviewed_links_for_case(session, case.id) == first_links

def test_event_workbench_never_surfaces_another_case_run_or_evidence(cmd_client):
    first, second = create_event_case_pair(cmd_client)
    first_view = cmd_client.get(f"/api/v1/event-research/{first}/workbench").json()
    assert all(item["case_id"] == first for item in first_view["evidence"])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest backend/tests/test_event_research_lifecycle.py backend/tests/test_auto_research_api.py -q`

Expected: FAIL because completed runs do not update the lifecycle or enqueue a successor run.

- [x] **Step 3: Update lifecycle after each worker terminal decision**

```python
def refresh_event_lifecycle(case_id: uuid.UUID, run: ResearchRun) -> None:
    review_count = review_queue.count_key_items(case_id)
    if review_count:
        lifecycle_repo.set(case_id, status="awaiting_key_review", summary=f"已筛出 {review_count} 条关键证据，等待审核", next_human_action=f"审核 {review_count} 条关键证据")
    elif run.stop_reason in {"max_rounds_reached", "no_new_evidence"} and can_expand_search(case_id):
        successor = start_successor_run(case_id, previous_run=run)
        lifecycle_repo.set(case_id, status="continuing", active_run_id=successor.id, summary=successor_summary(case_id), current_round=run.round + 1)
    elif run.stop_reason == "budget_exhausted":
        lifecycle_repo.set(case_id, status="exhausted", summary="已覆盖允许来源，尚未找到足以形成结论的关键材料", current_gap=largest_gap(case_id), next_human_action="补充来源或调整研究范围")
    elif has_publishable_assessment(case_id):
        lifecycle_repo.set(case_id, status="draft_ready", summary="证据门槛已满足，可审核 AI 结论草案", next_human_action="审核结论草案")
```

Invoke this projection updater from worker completion and after review decisions. Bound continuation with an explicit per-case policy (`max_automatic_cycles=3`) and do not start a successor while a key-evidence review is pending. The public workbench API exposes `status`, `summary`, `current_gap`, `next_human_action`, and `round_label`; detailed jobs, JSON, budget, and task ids remain only in the existing diagnostic deep link.

- [x] **Step 4: Run lifecycle and existing auto-research tests**

Run: `.venv/bin/pytest backend/tests/test_event_research_lifecycle.py backend/tests/test_auto_research_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/auto_research.py backend/app/repositories/event_research.py backend/app/api/v1 backend/app/schemas/v1 backend/tests/test_event_research_lifecycle.py backend/tests/test_auto_research_api.py
git commit -m "feat: continue event research automatically"
```

## Task 4: Provide event list, workbench, key-evidence, and conclusion-draft read models

**Files:**
- Modify: `backend/app/queries/conclusion.py`
- Create: `backend/app/queries/event_research.py`
- Modify: `backend/app/schemas/v1/event_research.py`
- Modify: `backend/app/api/v1/event_research.py`
- Modify: `backend/app/api/v1/commands/reviews.py`
- Modify: `backend/tests/test_event_research_api.py`
- Modify: `backend/tests/test_conclusion_read_api_v1.py`

- [ ] **Step 1: Write failing view-model tests**

```python
def test_event_list_orders_independent_events_by_last_update(cmd_client):
    response = cmd_client.get("/api/v1/event-research?status=researching")
    assert response.status_code == 200
    assert response.json()["items"][0] == {
        "case_id": ANY,
        "event_title": "Alphabet 财报超预期后股价下跌",
        "ticker": "GOOGL",
        "lifecycle_status": "researching",
        "status_summary": ANY,
        "next_human_action": None,
        "updated_at": ANY,
    }

def test_conclusion_draft_only_uses_reviewed_links_and_review_publishes(cmd_client):
    workbench = cmd_client.get(f"/api/v1/event-research/{case_id}/workbench").json()
    assert workbench["conclusion"]["state"] == "draft_ready"
    assert all(item["review_state"] == "reviewed" for item in workbench["conclusion"]["citations"])
    assert cmd_client.post(f"/api/v1/event-research/{case_id}/conclusion/review", json=publish_payload()).status_code == 201
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest backend/tests/test_event_research_api.py backend/tests/test_conclusion_read_api_v1.py -q`

Expected: FAIL because event list/workbench DTOs and conclusion review route do not exist.

- [ ] **Step 3: Assemble purpose-specific read models and contextual commands**

```python
class EventWorkbenchDTO(V1Model):
    event: EventSummaryDTO
    lifecycle: LifecycleDTO
    conclusion: ConclusionDraftDTO
    factors: list[FactorEvidenceDTO]
    evidence_gate: EvidenceGateDTO
    next_action: NextActionDTO
    evidence: list[KeyEvidenceDTO]

@router.post("/{case_id}/conclusion/review", status_code=201)
def review_event_conclusion(case_id: uuid.UUID, payload: EventConclusionReviewRequest, db: Session = Depends(get_db)):
    assessment = EventResearchQueries(db).latest_draft(case_id)
    require_reviewed_citations(assessment)
    decision = AssessmentService(ResearchRepository(db)).review(
        assessment.id, outcome=payload.outcome, conclusion=payload.conclusion,
        reason=payload.reason, reviewer=payload.reviewer,
    )
    EventResearchLifecycleRepository(db).refresh(case_id)
    commit_or_rollback(db)
    return EventConclusionReviewResponse.from_decision(decision)
```

Limit factors to the five most material by reviewed support/contradiction and current gap. Expose evidence role, source title, original excerpt, locator, available time, and review state for each key item. Use existing append-only `EvidenceReview` and `ReviewDecision` writes; do not introduce mutable evidence or conclusion rows.

- [ ] **Step 4: Run read-model tests**

Run: `.venv/bin/pytest backend/tests/test_event_research_api.py backend/tests/test_conclusion_read_api_v1.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/queries/event_research.py backend/app/queries/conclusion.py backend/app/api/v1/event_research.py backend/app/api/v1/commands/reviews.py backend/app/schemas/v1/event_research.py backend/tests/test_event_research_api.py backend/tests/test_conclusion_read_api_v1.py
git commit -m "feat: expose event research workbench"
```

## Task 5: Define the frontend event-research contract and adapters

**Files:**
- Create: `frontend/src/domain/eventResearch.ts`
- Create: `frontend/src/data/eventResearchAdapter.ts`
- Modify: `frontend/src/data/httpResearchAdapter.ts`
- Modify: `frontend/src/data/mockResearchAdapter.ts`
- Modify: `frontend/src/data/researchClient.ts`
- Modify: `frontend/src/domain/prototypeTypes.ts`
- Create: `frontend/src/tests/eventResearchAdapter.test.ts`

- [ ] **Step 1: Write failing adapter tests**

```ts
it("maps an extracted event without inventing optional fields", async () => {
  server.use(extractEvent({ company_name: null, ticker: null }));
  await expect(eventResearchClient.extract({ rawInput: "盘后下跌", sourceUrl: "" })).resolves.toMatchObject({
    companyName: null,
    ticker: null,
    confirmationRequired: true,
  });
});

it("returns separate case rows and an actionable lifecycle", async () => {
  await expect(eventResearchClient.list()).resolves.toEqual(expect.arrayContaining([
    expect.objectContaining({ id: "case-a", status: "researching" }),
    expect.objectContaining({ id: "case-b", status: "awaiting_key_review" }),
  ]));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- eventResearchAdapter.test.ts`

Expected: FAIL because `eventResearchClient` and its types do not exist.

- [ ] **Step 3: Implement a dedicated frontend contract**

```ts
export type EventLifecycleStatus =
  | "extracting" | "researching" | "awaiting_key_review" | "continuing"
  | "awaiting_scope" | "draft_ready" | "published" | "exhausted";

export interface EventWorkbench {
  event: EventResearchListItem;
  lifecycle: { status: EventLifecycleStatus; summary: string; roundLabel: string; currentGap: string | null };
  conclusion: { state: "cannot_conclude" | "ai_draft" | "published"; text: string; citations: EvidenceCitation[] };
  factors: EventFactor[];
  evidenceGate: EvidenceGate;
  nextAction: { kind: "wait" | "review_evidence" | "review_conclusion" | "supply_scope"; label: string; count?: number };
}
```

Keep this contract separate from the large legacy `prototypeTypes.ts`; extend the shared `ResearchClient` only with an `eventResearch` facade. HTTP maps snake case to camel case once; mock data must include at least automatic first round, multi-round continuation, evidence review, draft-ready, published, and exhausted states.

- [ ] **Step 4: Run adapter tests**

Run: `npm test -- eventResearchAdapter.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/domain/eventResearch.ts frontend/src/data/eventResearchAdapter.ts frontend/src/data/httpResearchAdapter.ts frontend/src/data/mockResearchAdapter.ts frontend/src/data/researchClient.ts frontend/src/domain/prototypeTypes.ts frontend/src/tests/eventResearchAdapter.test.ts
git commit -m "feat: add event research frontend contract"
```

## Task 6: Replace primary navigation and routes without losing traceability deep links

**Files:**
- Modify: `frontend/src/components/PrototypeShell.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/styles-prototype.css`
- Create: `frontend/src/tests/EventResearchRouting.test.tsx`

- [ ] **Step 1: Write failing route/navigation tests**

```tsx
it("shows only event-oriented primary navigation", () => {
  renderWithAppShell(<Outlet />, { initialEntries: ["/events"] });
  expect(screen.getByRole("link", { name: "事件研究" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "自动研究运行" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "研究计划" })).not.toBeInTheDocument();
});

it("redirects the application root and unknown URLs to the event list", () => {
  render(<AppAt path="/unknown" />);
  expect(screen.getByRole("heading", { name: "事件研究" })).toBeVisible();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- EventResearchRouting.test.tsx`

Expected: FAIL because old theme-oriented navigation is still rendered.

- [ ] **Step 3: Implement the approved information architecture**

```ts
const NAV_ITEMS = [
  { to: "/events", label: "事件研究", icon: "▤" },
  { to: "/library", label: "资料库", icon: "▦" },
  { to: "/events?attention=1", label: "待我处理", icon: "✓" },
  { to: "/versions", label: "监测与更新", icon: "↻" },
] as const;

// /auto-research, /plan, /review, /relationships, /data, /themes, and
// /companies remain routable only as context/deep-link surfaces. They are
// removed from both desktop and mobile primary navigation.
```

Route `/`, `/workspace`, and the fallback to `/events`. Route `/events/new`, `/events/:caseId`, and `/events/:caseId/review` to the new screens. Keep old URLs behind explicit compatibility routes so saved deep links continue to resolve while no new user path points to them. Replace the bottom shortcut tiles with no more than the same five destinations; do not present diagnostic pages as a primary choice.

- [ ] **Step 4: Run routing tests**

Run: `npm test -- EventResearchRouting.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PrototypeShell.tsx frontend/src/main.tsx frontend/src/styles-prototype.css frontend/src/tests/EventResearchRouting.test.tsx
git commit -m "feat: make event research the primary navigation"
```

## Task 7: Build event creation and independent event-list switching

**Files:**
- Create: `frontend/src/pages/prototype/EventResearchCreateScreen.tsx`
- Create: `frontend/src/pages/prototype/EventResearchListScreen.tsx`
- Modify: `frontend/src/styles-prototype.css`
- Create: `frontend/src/tests/EventResearchCreateScreen.test.tsx`
- Create: `frontend/src/tests/EventResearchListScreen.test.tsx`

- [ ] **Step 1: Write failing screen tests**

```tsx
it("extracts pasted information, allows edits, then creates and opens automatic research", async () => {
  renderEventCreate();
  await userEvent.type(screen.getByLabelText("新闻或研究信息"), newsText);
  await userEvent.click(screen.getByRole("button", { name: "AI 提取关键信息" }));
  await userEvent.clear(screen.getByLabelText("研究问题"));
  await userEvent.type(screen.getByLabelText("研究问题"), "资本开支是否是盘后下跌的主要因素？");
  await userEvent.click(screen.getByRole("button", { name: "创建并开始自动研究" }));
  expect(await screen.findByText("系统正在自动研究")).toBeVisible();
});

it("switches rows without carrying another event's conclusion", async () => {
  renderEventList();
  await userEvent.click(screen.getByRole("link", { name: /台积电上调 CoWoS/ }));
  expect(await screen.findByText("需审核 2 条关键证据")).toBeVisible();
  expect(screen.queryByText("Alphabet 财报超预期后股价下跌的结论")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- EventResearchCreateScreen.test.tsx EventResearchListScreen.test.tsx`

Expected: FAIL because the screens do not exist.

- [ ] **Step 3: Implement creation and list screens**

Implement the exact approved flow: one large raw-input field and optional source URL; one extraction action; AI-labelled editable fields with unknown values blank; editable question; 3–5 selectable candidate factors; one primary create action. On success navigate directly to `/events/:caseId`, never to `/plan` or `/auto-research/runs`.

Implement the list as a readable table/list, not equal-size dashboard cards. Each row shows title, company/ticker, event time, last update, lifecycle summary, and the one human action when present. Add filters for company/ticker, conclusion state, research progress, attention required, and archival tag. Use semantic links/buttons and keyboard focus visible states.

- [ ] **Step 4: Run screen tests**

Run: `npm test -- EventResearchCreateScreen.test.tsx EventResearchListScreen.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/prototype/EventResearchCreateScreen.tsx frontend/src/pages/prototype/EventResearchListScreen.tsx frontend/src/styles-prototype.css frontend/src/tests/EventResearchCreateScreen.test.tsx frontend/src/tests/EventResearchListScreen.test.tsx
git commit -m "feat: add AI-assisted event research creation"
```

## Task 8: Build the conclusion-first workbench, contextual evidence review, and publication loop

**Files:**
- Create: `frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx`
- Create: `frontend/src/pages/prototype/KeyEvidenceReviewScreen.tsx`
- Modify: `frontend/src/pages/prototype/ConclusionScreen.tsx`
- Modify: `frontend/src/styles-prototype.css`
- Create: `frontend/src/tests/EventResearchWorkbenchScreen.test.tsx`
- Create: `frontend/src/tests/KeyEvidenceReviewScreen.test.tsx`

- [ ] **Step 1: Write failing workbench-state tests**

```tsx
it.each([
  ["researching", "正在核验资本开支是否足以解释盘后跌幅", "系统继续处理"],
  ["continuing", "继续研究中 · 第 2 轮", "系统继续处理"],
  ["awaiting_key_review", "需审核 2 条关键证据", "审核 2 条关键证据"],
  ["draft_ready", "AI 结论草案", "审核结论草案"],
  ["published", "已确认正式结论", "查看本次结论变化"],
])("renders a single next action for %s", async (status, summary, action) => {
  renderWorkbench(status);
  expect(await screen.findByText(summary)).toBeVisible();
  expect(screen.getByRole("button", { name: action })).toBeVisible();
  expect(screen.getAllByRole("button", { name: /审核|生成|继续/ })).toHaveLength(1);
});

it("requires confirmation before publishing an AI draft", async () => {
  renderWorkbench("draft_ready");
  await userEvent.click(screen.getByRole("button", { name: "审核结论草案" }));
  await userEvent.click(screen.getByRole("button", { name: "确认并发布正式结论" }));
  expect(eventResearchClient.reviewConclusion).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ outcome: "confirmed" }));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- EventResearchWorkbenchScreen.test.tsx KeyEvidenceReviewScreen.test.tsx`

Expected: FAIL because the conclusion-first screens do not exist.

- [ ] **Step 3: Implement the workbench and review flow**

Keep the above-the-fold order invariant across every state:

1. Current conclusion, explicitly `尚不能下结论`, `AI 结论草案`, or `已确认正式结论`.
2. At most five competing factors ordered by material effect, each with reviewed support, reviewed contradiction, quality/boundary, and largest gap.
3. Evidence gate, separated into reviewed support, reviewed contradiction, pending candidates, and the one required gap.
4. Exactly one next action selected from lifecycle state.
5. A collapsed `研究依据` disclosure containing sources, original text, runs, graph, and versions.

The review screen must show one key evidence item at a time: original excerpt and locator, source/published/available time, factor effect, AI rationale, and buttons `采纳`, `不采纳`, `需要更多材料`. Show scope and reason fields only when the reviewer modifies the AI relation or publishes a conclusion. Reuse the existing append-only review endpoints, then refresh the workbench state.

- [ ] **Step 4: Run workbench tests**

Run: `npm test -- EventResearchWorkbenchScreen.test.tsx KeyEvidenceReviewScreen.test.tsx ConclusionScreen.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/prototype/EventResearchWorkbenchScreen.tsx frontend/src/pages/prototype/KeyEvidenceReviewScreen.tsx frontend/src/pages/prototype/ConclusionScreen.tsx frontend/src/styles-prototype.css frontend/src/tests/EventResearchWorkbenchScreen.test.tsx frontend/src/tests/KeyEvidenceReviewScreen.test.tsx
git commit -m "feat: add conclusion-first event workbench"
```

## Task 9: Remove obsolete primary-path screens and verify the full event loop

**Files:**
- Modify: `frontend/src/pages/prototype/AutoResearchRunsScreen.tsx`
- Modify: `frontend/src/pages/prototype/ResearchPlanScreen.tsx`
- Modify: `frontend/src/pages/prototype/ReviewWorkbenchScreen.tsx`
- Modify: `frontend/src/pages/prototype/ThemeIndexScreen.tsx`
- Modify: `frontend/src/pages/prototype/ThemeWorkbenchScreen.tsx`
- Delete: `frontend/src/pages/prototype/CompanyDossierPage.tsx`
- Delete: `frontend/src/pages/prototype/TopicViewPage.tsx`
- Delete after test migration: `frontend/src/pages/DocumentLibraryPage.tsx`, `frontend/src/pages/NotImplementedPage.tsx`, `frontend/src/pages/ReviewWorkbenchPage.tsx`, `frontend/src/pages/WorkspaceOverviewPage.tsx`, `frontend/src/pages/RelationshipCanvasPage.tsx`, `frontend/src/pages/ResearchCaseDossierPage.tsx`, `frontend/src/pages/ResearchWorkbenchPage.tsx` and their exclusive tests
- Modify: `docs/superpowers/specs/2026-08-07-event-driven-conclusion-workbench-design.md`
- Create: `frontend/src/tests/EventResearchFlow.e2e.tsx`

- [ ] **Step 1: Write the end-to-end component test**

```tsx
it("goes from pasted event to automatic research, key review, and published conclusion", async () => {
  render(<AppAt path="/events/new?client=mock" />);
  await createAlphabetEvent();
  expect(await screen.findByText("系统正在自动研究")).toBeVisible();
  await advanceMockLifecycleTo("awaiting_key_review");
  await userEvent.click(screen.getByRole("button", { name: "审核 2 条关键证据" }));
  await reviewAllKeyEvidence();
  expect(await screen.findByText("AI 结论草案")).toBeVisible();
  await publishDraft();
  expect(await screen.findByText("已确认正式结论")).toBeVisible();
});
```

- [ ] **Step 2: Run the flow test and verify it fails**

Run: `npm test -- EventResearchFlow.e2e.tsx`

Expected: FAIL until the new routes and lifecycle actions are connected.

- [ ] **Step 3: Demote old screens and clean unreachable code**

Keep `AutoResearchRunsScreen`, `ResearchPlanScreen`, `RelationshipCanvasScreen`, `LibraryScreen`, and `VersionsScreen` only as workbench evidence-basis deep links. Replace direct calls to the old theme/case flow with contextual links back to `/events/:caseId`. Remove the unreferenced company/topic prototype pages and the obsolete non-routed `frontend/src/pages/` layer only after `rg` confirms no production import remains and their tests are migrated or removed. Update the design document’s prototype acceptance section to list the five shipped states: new input, automatic research, multi-round continuation, key review, and formal conclusion.

- [ ] **Step 4: Run complete verification**

Run:

```bash
cd backend && ../.venv/bin/pytest -q
cd ../frontend && npm run typecheck && npm run build && npm test
```

Expected: backend `580+ passed`, frontend typecheck/build success, and all migrated Vitest tests passing. Then run the repository release gate:

```bash
docs/evaluation/reproduce.sh
```

Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add -A frontend backend docs/superpowers/specs/2026-08-07-event-driven-conclusion-workbench-design.md
git commit -m "refactor: retire legacy research navigation"
```

## Task 10: Rework every retained support page around an event context

**Files:**
- Create: `frontend/src/pages/prototype/EventEvidenceLibraryScreen.tsx`
- Create: `frontend/src/pages/prototype/EventMonitoringScreen.tsx`
- Create: `frontend/src/pages/prototype/EventResearchBasisScreen.tsx`
- Modify: `frontend/src/pages/prototype/LibraryScreen.tsx`
- Modify: `frontend/src/pages/prototype/VersionsScreen.tsx`
- Modify: `frontend/src/pages/prototype/RelationshipCanvasScreen.tsx`
- Modify: `frontend/src/pages/prototype/CompanyListPage.tsx`
- Modify: `frontend/src/pages/prototype/DataCenterScreen.tsx`
- Modify: `frontend/src/pages/prototype/ThemeIndexScreen.tsx`
- Modify: `frontend/src/pages/prototype/ThemeWorkbenchScreen.tsx`
- Modify: `frontend/src/pages/prototype/TopicListPage.tsx`
- Modify: `frontend/src/domain/eventResearch.ts`
- Modify: `frontend/src/data/eventResearchAdapter.ts`
- Modify: `frontend/src/styles-prototype.css`
- Create: `frontend/src/tests/EventEvidenceLibraryScreen.test.tsx`
- Create: `frontend/src/tests/EventMonitoringScreen.test.tsx`
- Create: `frontend/src/tests/EventResearchBasisScreen.test.tsx`
- Create: `frontend/src/tests/SupportSurfaceContext.test.tsx`

- [ ] **Step 1: Write failing event-context tests for all retained support pages**

```tsx
it("shows each evidence row with its event, factor, source, and review state", async () => {
  renderAt("/events/case-a/evidence");
  expect(await screen.findByText("Alphabet 财报超预期后股价下跌")).toBeVisible();
  expect(screen.getByText("资本开支 / 自由现金流担忧")).toBeVisible();
  expect(screen.getByRole("link", { name: "返回结论工作台" })).toHaveAttribute("href", "/events/case-a");
});

it("explains whether a monitored material changed the event conclusion", async () => {
  renderAt("/events/case-a/monitoring");
  expect(await screen.findByRole("heading", { name: "是什么改变了结论" })).toBeVisible();
  expect(screen.getByText("目前无需重新研究")).toBeVisible();
  expect(screen.getByText("资本开支 / 自由现金流担忧")).toBeVisible();
});

it.each(["/relationships/case-a", "/companies?caseId=case-a", "/data?caseId=case-a"]) (
  "keeps the event breadcrumb and links back to its workbench for %s",
  async (route) => {
    renderAt(route);
    expect(await screen.findByLabelText("所属事件")).toHaveTextContent("Alphabet 财报超预期后股价下跌");
    expect(screen.getByRole("link", { name: "返回结论工作台" })).toBeVisible();
  },
);
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- EventEvidenceLibraryScreen.test.tsx EventMonitoringScreen.test.tsx EventResearchBasisScreen.test.tsx SupportSurfaceContext.test.tsx`

Expected: FAIL because retained pages have no mandatory event context or return link.

- [ ] **Step 3: Implement the evidence library and research-basis drill-down**

Add an `EventResearchBasis` adapter response containing the event title, current conclusion, focused factor, sources, source excerpts, review state, and contextual deep links. Build `/events/:caseId/evidence` as the primary source page: filters are `全部来源`, `已审核`, `待审核`, and `反证`; every result names the event and the factor it affects; the selected original excerpt shows frozen locator, publisher, publication time, available time, evidence quality, and the human/AI decision boundary.

Replace the standalone graph entry with a collapsed `研究依据` section in the workbench. Its explicit links can open graph, company/valuation, or run diagnostics, but each destination must receive `caseId` and show the same event context strip. Provide a structured factor-to-evidence list alongside any graph so the relationship visual is never the only way to understand evidence.

- [ ] **Step 4: Implement monitoring and conclusion-change history**

Use the existing append-only snapshots, assessments, reviews, and activity events to form an event-specific monitoring response. The page order is: `是什么改变了结论` timeline; current monitoring judgement by factor; conclusion versions with a readable before/after summary; watch conditions that name documents, data revisions, and abnormal market movement. A material update calls the event lifecycle continuation endpoint; it changes the event to `researching` and creates a durable successor run before showing any human action.

```ts
export interface EventMonitoringView {
  event: Pick<EventResearchListItem, "id" | "title" | "ticker">;
  conclusion: { state: "published" | "ai_draft" | "cannot_conclude"; latestVersion: string };
  changes: Array<{ id: string; occurredAt: string; sourceLabel: string; factor: string; effect: "supports" | "weakens" | "no_change"; summary: string }>;
  currentJudgement: Array<{ factor: string; state: string; rationale: string }>;
  reResearch: { required: boolean; reason: string | null };
  watchConditions: Array<{ label: string; condition: string }>;
}
```

- [ ] **Step 5: Convert old object, data, and theme pages to subordinate context**

Keep company and data pages only as event-contextual drill-downs. They must explain which event factor selected the company/metric, show source/as-of dates, and provide the return link. Convert theme pages to an archive/observation index of event rows grouped by tag; remove every theme-level conclusion, research-plan action, and direct `AI 提议` action. If a user enters an old theme URL without a case id, show an event archive list and require selection of an event before presenting any evidence or company detail.

- [ ] **Step 6: Run support-surface tests**

Run: `npm test -- EventEvidenceLibraryScreen.test.tsx EventMonitoringScreen.test.tsx EventResearchBasisScreen.test.tsx SupportSurfaceContext.test.tsx`

Expected: PASS.

- [ ] **Step 7: Verify accessible page structure and remove residual standalone copy**

Run:

```bash
npx impeccable --json frontend/src/pages/prototype frontend/src/components
rg -n "主题驱动|主题级结论|启动自动研究|运行列表|研究计划预览|示例 · 非目标范围" frontend/src/pages/prototype frontend/src/components
```

Expected: detector output contains no newly introduced warnings; the text search returns only explicit legacy deep-link compatibility notices, never user-facing primary page copy. Fix each remaining primary-path match before committing.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/prototype frontend/src/domain/eventResearch.ts frontend/src/data/eventResearchAdapter.ts frontend/src/styles-prototype.css frontend/src/tests/EventEvidenceLibraryScreen.test.tsx frontend/src/tests/EventMonitoringScreen.test.tsx frontend/src/tests/EventResearchBasisScreen.test.tsx frontend/src/tests/SupportSurfaceContext.test.tsx
git commit -m "feat: contextualize research support surfaces"
```

## Plan self-review

- **Spec coverage:** Tasks 1–4 implement independent event records, AI extraction, automatic creation, repeated bounded research, evidence isolation, review gates, and formal conclusion. Tasks 5–8 implement the approved event list, creation, automatic progress, event switching, conclusion-first workbench, and contextual human actions. Tasks 9–10 remove legacy primary-path UI and move every retained research page into explicit event context.
- **Completeness:** Every task has exact paths, a failing test, a command, implementation detail, verification, and commit; no step defers implementation work.
- **Type consistency:** `EventLifecycleStatus`, `EventWorkbench`, `EventResearchBrief`, `EventResearchLifecycle`, and the event API are introduced before the screens that consume them. Existing immutable review and assessment writes remain the sole publication mechanism.
