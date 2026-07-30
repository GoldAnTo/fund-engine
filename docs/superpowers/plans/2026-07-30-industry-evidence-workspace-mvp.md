# Industry Evidence Workspace MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one auditable AI-compute research case where frozen source material can be traced through AI thesis assessment to company, stock, valuation, fund holding disclosure, and fund exposure.

**Architecture:** PostgreSQL is the append-only evidence ledger and only write-model truth. A FastAPI service writes versioned documents, statements, evidence links, assessments, reviews, securities, and holdings; a graph projection is rebuilt from ledger records. A React workbench reads a focused query API and shows one connected evidence-to-fund path with raw-source drill-down.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest, Docling, Neo4j 5, React, TypeScript, Vite, Cytoscape.js, Playwright.

---

## Delivery boundary

This is the first of three implementation plans. It delivers the full vertical slice for one AI-compute ResearchCase. It deliberately does not implement automated public-web collection, arbitrary multi-hop graph exploration, multiple themes, market-data vendor failover, or investment recommendations.

The current directory is not a Git repository. Before implementation, initialize a repository or move this plan into the intended repository. The commit checkpoints below become mandatory once version control exists.

## File structure

```text
backend/
  pyproject.toml                         # Python dependencies and tool configuration
  alembic.ini
  alembic/versions/0001_evidence_ledger.py
  app/
    main.py                               # FastAPI application
    db.py                                 # database engine and unit-of-work dependency
    models/
      ledger.py                           # SQLAlchemy ledger tables and append-only constraints
      read_models.py                      # API response types only
    repositories/
      documents.py                        # document/version/span persistence
      research.py                         # cases, theses, links, assessments, reviews
      instruments.py                      # company, stock, fund, holdings, valuation
    services/
      ingest.py                           # document freeze and span/statement admission
      assessment.py                       # snapshot creation and AI-result admission
      exposure.py                         # dated theme-exposure calculation
      projection.py                       # idempotent Neo4j projection messages
    api/
      cases.py                            # ResearchCase and workbench read/write endpoints
      documents.py                        # immutable document/source-span endpoints
      reviews.py                          # human review endpoints
    scripts/
      seed_ai_compute_case.py             # fixed vertical-slice fixture import
      rebuild_graph_projection.py         # rebuild from ledger only
  scripts/
    verify_ai_compute_slice.py            # release-gate command
  tests/
    conftest.py
    test_health.py
    test_documents.py
    test_evidence_links.py
    test_time_travel.py
    test_assessments.py
    test_exposure.py
    test_projection.py
    test_workbench_api.py
    test_release_gate.py
frontend/
  package.json
  src/
    api.ts                                # typed HTTP client
    types.ts                              # read-model types
    pages/ResearchWorkbenchPage.tsx       # focused workbench page
    components/EvidenceGraph.tsx          # Cytoscape graph and edge styles
    components/EvidenceDrawer.tsx         # span, reason, scope, review drill-down
    components/AssessmentHeader.tsx       # provisional/review state label
    components/ExposurePanel.tsx          # stock, valuation, fund-disclosure detail
    tests/ResearchWorkbenchPage.test.tsx
  e2e/workbench.spec.ts
docker-compose.yml                        # PostgreSQL and Neo4j for local development
docs/evaluation/
  ai-compute-gold-set.md                  # real frozen source IDs and human labels
  ai-compute-failure-cases.md             # retained failed/ambiguous examples
```

## Data contracts fixed by this plan

The following names must not be changed casually in later tasks:

```python
AssessmentStatus = Literal["supported", "contradicted", "insufficient_evidence"]
EvidenceRole = Literal["supports", "contradicts", "contextualizes"]
SourceStatementKind = Literal[
    "disclosed_fact", "management_attribution", "forecast", "research_opinion"
]
ReviewOutcome = Literal["confirmed", "modified", "rejected"]
ReviewState = Literal["machine_generated", "reviewed", "rejected"]
```

Every ledger entity has a UUID primary key, `created_at`, `created_by`, and an immutable version identity. Immutable means no ORM update/delete path is exposed; corrections append a successor record with `supersedes_id` where applicable.

### Task 1: Bootstrap the reproducible local stack

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/app/db.py`
- Create: `backend/tests/conftest.py`
- Create: `docker-compose.yml`
- Create: `frontend/package.json`

- [ ] **Step 1: Write the health-check test**

```python
def test_health_returns_service_identity(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "industry-evidence-workspace", "status": "ok"}
```

- [ ] **Step 2: Run the test to confirm the baseline fails**

Run: `cd backend && pytest tests -q`

Expected: FAIL because `app.main` does not exist.

- [ ] **Step 3: Add the minimal FastAPI application**

```python
# backend/app/main.py
from fastapi import FastAPI

app = FastAPI(title="Industry Evidence Workspace")

@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}
```

- [ ] **Step 4: Add local services**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: evidence
      POSTGRES_USER: evidence
      POSTGRES_PASSWORD: evidence
    ports: ["5432:5432"]
  neo4j:
    image: neo4j:5
    environment:
      NEO4J_AUTH: neo4j/evidence-graph
    ports: ["7474:7474", "7687:7687"]
```

- [ ] **Step 5: Run the test and startup checks**

Run: `cd backend && pytest tests/test_health.py -q && docker compose up -d postgres neo4j`

Expected: health test passes; both containers report running.

- [ ] **Step 6: Commit checkpoint**

Run after Git exists: `git add backend docker-compose.yml frontend/package.json && git commit -m "chore: bootstrap evidence workspace"`

### Task 2: Create the immutable document, version, and span ledger

**Files:**
- Create: `backend/alembic/versions/0001_evidence_ledger.py`
- Create: `backend/app/models/ledger.py`
- Create: `backend/app/repositories/documents.py`
- Create: `backend/app/services/ingest.py`
- Create: `backend/tests/test_documents.py`

- [ ] **Step 1: Write failing immutability tests**

```python
def test_same_content_hash_reuses_document_version(document_service):
    first = document_service.freeze(raw=b"page one", source_url="https://example.test/a")
    second = document_service.freeze(raw=b"page one", source_url="https://example.test/a")
    assert second.id == first.id

def test_changed_bytes_append_new_document_version(document_service):
    first = document_service.freeze(raw=b"v1", source_url="https://example.test/a")
    second = document_service.freeze(raw=b"v2", source_url="https://example.test/a")
    assert second.id != first.id
    assert second.supersedes_id == first.id

def test_source_span_is_not_mutable(session, span):
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(SourceSpan).where(SourceSpan.id == span.id).values(text="changed"))
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd backend && pytest tests/test_documents.py -q`

Expected: FAIL because ledger models and services do not exist.

- [ ] **Step 3: Implement the minimum schema and append-only guard**

```python
class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None]
    available_at: Mapped[datetime] = mapped_column(nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(nullable=False)
    parser_version: Mapped[str] = mapped_column(nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id"))

class SourceSpan(Base):
    __tablename__ = "source_spans"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"), nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
```

Create PostgreSQL triggers in the Alembic migration that raise on `UPDATE` and `DELETE` for `document_versions` and `source_spans`; allow only `INSERT`.

- [ ] **Step 4: Implement deterministic content freeze**

```python
def freeze(self, raw: bytes, source_url: str, published_at: datetime | None = None) -> DocumentVersion:
    digest = sha256(raw).hexdigest()
    existing = self.repo.by_hash(digest)
    if existing:
        return existing
    prior = self.repo.latest_for_source(source_url)
    return self.repo.insert_version(
        content_sha256=digest, source_url=source_url, published_at=published_at,
        available_at=utcnow(), acquired_at=utcnow(), parser_version="docling-v1",
        supersedes_id=prior.id if prior and prior.content_sha256 != digest else None,
    )
```

- [ ] **Step 5: Run focused tests and a migration smoke test**

Run: `cd backend && alembic upgrade head && pytest tests/test_documents.py -q`

Expected: all document tests pass; direct SQL update and delete are rejected.

- [ ] **Step 6: Commit checkpoint**

Run after Git exists: `git add backend && git commit -m "feat: add immutable document ledger"`

### Task 3: Add source statements and evidence links with scope and time

**Files:**
- Modify: `backend/app/models/ledger.py`
- Create: `backend/app/repositories/research.py`
- Create: `backend/tests/test_evidence_links.py`

- [ ] **Step 1: Write failing classification and link tests**

```python
def test_research_opinion_is_not_stored_as_disclosed_fact(research_service, span):
    statement = research_service.add_statement(span.id, "预计需求增长", kind="research_opinion")
    assert statement.kind == "research_opinion"

def test_evidence_link_requires_reason_scope_and_available_time(research_service, thesis, statement):
    with pytest.raises(ValidationError):
        research_service.link_evidence(thesis.id, statement.id, role="supports", reason="", scope={})

def test_evidence_link_is_machine_generated_until_reviewed(research_service, thesis, statement):
    link = research_service.link_evidence(thesis.id, statement.id, role="supports", reason="orders rose", scope={"segment": "DC"})
    assert link.creator_type == "ai"
    assert link.review_state == "machine_generated"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && pytest tests/test_evidence_links.py -q`

Expected: FAIL because statements and links are absent.

- [ ] **Step 3: Implement the data contract**

```python
class SourceStatement(Base):
    source_span_id: Mapped[UUID] = mapped_column(ForeignKey("source_spans.id"), nullable=False)
    kind: Mapped[str] = mapped_column(nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    observed_period: Mapped[Date | None]

class EvidenceLink(Base):
    thesis_id: Mapped[UUID] = mapped_column(ForeignKey("theses.id"), nullable=False)
    source_statement_id: Mapped[UUID] = mapped_column(ForeignKey("source_statements.id"), nullable=False)
    role: Mapped[str] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    available_at: Mapped[datetime] = mapped_column(nullable=False)
    creator_type: Mapped[str] = mapped_column(nullable=False, default="ai")
    review_state: Mapped[str] = mapped_column(nullable=False, default="machine_generated")
```

Validate `kind`, `role`, non-empty reason, non-empty scope and `available_at >= document_version.available_at` in the service layer. Apply append-only triggers to both tables.

- [ ] **Step 4: Run the tests**

Run: `cd backend && alembic upgrade head && pytest tests/test_evidence_links.py -q`

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

Run after Git exists: `git add backend && git commit -m "feat: record source statements and evidence links"`

### Task 4: Model research cases, theses, causal steps, snapshots, assessments, and reviews

**Files:**
- Modify: `backend/app/models/ledger.py`
- Create: `backend/app/services/assessment.py`
- Create: `backend/tests/test_assessments.py`
- Create: `backend/tests/test_time_travel.py`

- [ ] **Step 1: Write failing snapshot and non-overwrite tests**

```python
def test_snapshot_excludes_source_not_available_at_cutoff(assessment_service, future_link, thesis):
    snapshot = assessment_service.freeze_snapshot(thesis.id, cutoff=datetime(2026, 7, 1, tzinfo=UTC))
    assert future_link.id not in snapshot.evidence_link_ids

def test_human_review_does_not_change_ai_assessment(assessment_service, ai_assessment):
    review = assessment_service.review(ai_assessment.id, outcome="modified", conclusion="insufficient_evidence", reason="scope mismatch")
    assert assessment_service.get(ai_assessment.id).conclusion == "supported"
    assert review.ai_assessment_id == ai_assessment.id
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && pytest tests/test_assessments.py tests/test_time_travel.py -q`

Expected: FAIL because snapshots and reviews do not exist.

- [ ] **Step 3: Implement immutable assessment records**

```python
def freeze_snapshot(self, thesis_id: UUID, cutoff: datetime) -> EvidenceSnapshot:
    links = self.repo.visible_links(thesis_id=thesis_id, cutoff=cutoff)
    return self.repo.insert_snapshot(thesis_id=thesis_id, cutoff=cutoff, evidence_link_ids=[str(link.id) for link in links])

def create_ai_assessment(self, snapshot_id: UUID, conclusion: AssessmentStatus, rationale: str, gaps: list[str]) -> AIAssessment:
    if conclusion not in {"supported", "contradicted", "insufficient_evidence"}:
        raise ValidationError("invalid conclusion")
    return self.repo.insert_ai_assessment(snapshot_id, conclusion, rationale, gaps, displayed_as_provisional=True)

def review(self, assessment_id: UUID, outcome: ReviewOutcome, conclusion: AssessmentStatus | None, reason: str) -> ReviewDecision:
    return self.repo.insert_review(assessment_id, outcome, conclusion, reason)
```

The service must never calculate or persist `ready_for_review`, `maturity_score`, or an automatic review trigger.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_assessments.py tests/test_time_travel.py -q`

Expected: PASS, including cutoff exclusion and immutable original AI result.

- [ ] **Step 5: Commit checkpoint**

Run after Git exists: `git add backend && git commit -m "feat: add versioned thesis assessments and reviews"`

### Task 5: Add dated company, stock, valuation, fund, and holding exposure data

**Files:**
- Create: `backend/app/repositories/instruments.py`
- Create: `backend/app/services/exposure.py`
- Create: `backend/tests/test_exposure.py`
- Modify: `backend/app/models/ledger.py`

- [ ] **Step 1: Write failing disclosure-time tests**

```python
def test_theme_exposure_uses_holding_disclosure_not_latest_portfolio(exposure_service, fund, mapped_stock):
    exposure = exposure_service.for_fund(fund.id, as_of=date(2026, 6, 30))
    assert exposure.theme_weight == Decimal("0.082")
    assert exposure.report_period == date(2026, 3, 31)

def test_future_holding_disclosure_is_hidden_from_historical_as_of(exposure_service, future_disclosure, fund):
    exposure = exposure_service.for_fund(fund.id, as_of=date(2026, 6, 30))
    assert future_disclosure.id not in {row.disclosure_id for row in exposure.rows}
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd backend && pytest tests/test_exposure.py -q`

Expected: FAIL because instruments and disclosures do not exist.

- [ ] **Step 3: Implement the dated exposure query**

```python
def for_fund(self, fund_id: UUID, as_of: date) -> FundExposure:
    disclosures = self.repo.disclosures_visible_on_or_before(fund_id, as_of)
    latest_by_stock = choose_latest_disclosure_per_stock(disclosures)
    rows = [row for row in latest_by_stock if self.repo.stock_has_theme_role(row.stock_id, as_of)]
    return FundExposure(
        fund_id=fund_id,
        as_of=as_of,
        theme_weight=sum((row.weight for row in rows), Decimal("0")),
        rows=rows,
    )
```

Store valuation as `ValuationSnapshot(stock_id, as_of_date, metric_name, metric_value, source, definition)`; never put mutable PE/PB/ROE columns directly on Stock.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_exposure.py -q`

Expected: PASS, including future-disclosure exclusion.

- [ ] **Step 5: Commit checkpoint**

Run after Git exists: `git add backend && git commit -m "feat: add dated instrument and fund exposure ledger"`

### Task 6: Build focused workbench read APIs and graph projection

**Files:**
- Create: `backend/app/services/projection.py`
- Create: `backend/app/api/cases.py`
- Create: `backend/app/scripts/rebuild_graph_projection.py`
- Create: `backend/tests/test_projection.py`
- Create: `backend/tests/test_workbench_api.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing API and rebuild tests**

```python
def test_workbench_marks_ai_assessment_as_unreviewed(client, ai_case):
    payload = client.get(f"/api/research-cases/{ai_case.id}/workbench").json()
    assert payload["assessment"]["provisional"] is True
    assert payload["assessment"]["status"] == "supported"
    assert payload["graph"]["edges"][0]["kind"] in {"evidence", "causal", "theme_role", "holding"}

def test_graph_projection_can_be_rebuilt_from_ledger_only(projector, ledger_fixture):
    projector.clear_projection()
    projector.rebuild_all()
    assert projector.node_count("EvidenceLink") == ledger_fixture.evidence_link_count
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd backend && pytest tests/test_projection.py tests/test_workbench_api.py -q`

Expected: FAIL because endpoint and projector do not exist.

- [ ] **Step 3: Implement the read contract**

```python
@router.get("/api/research-cases/{case_id}/workbench")
def workbench(case_id: UUID, cutoff: datetime | None = None) -> WorkbenchResponse:
    return service.load_workbench(case_id=case_id, cutoff=cutoff)
```

`WorkbenchResponse` includes exactly: case metadata, focus thesis, provisional AI assessment, latest human review if present, major gap, graph nodes/typed edges, evidence drawer records, stock valuation snapshots, and fund holding-disclosure rows. Do not add recommendation fields.

Projection writes must use `MERGE` keyed by ledger UUID and may only read ledger data. The rebuild script first drops only the application's labelled nodes/edges, then repopulates them from PostgreSQL; it must not delete arbitrary Neo4j data.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_projection.py tests/test_workbench_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit checkpoint**

Run after Git exists: `git add backend && git commit -m "feat: expose workbench read model and graph projection"`

### Task 7: Build the connected evidence-to-fund workbench UI

**Files:**
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/pages/ResearchWorkbenchPage.tsx`
- Create: `frontend/src/components/AssessmentHeader.tsx`
- Create: `frontend/src/components/EvidenceGraph.tsx`
- Create: `frontend/src/components/EvidenceDrawer.tsx`
- Create: `frontend/src/components/ExposurePanel.tsx`
- Create: `frontend/src/tests/ResearchWorkbenchPage.test.tsx`
- Create: `frontend/e2e/workbench.spec.ts`

- [ ] **Step 1: Write failing render and interaction tests**

```tsx
it("labels an unreviewed AI assessment as provisional", async () => {
  render(<ResearchWorkbenchPage caseId="ai-compute" />)
  expect(await screen.findByText("AI 临时判断，未经人工复核")).toBeVisible()
})

it("opens the exact source span when an evidence edge is selected", async () => {
  render(<ResearchWorkbenchPage caseId="ai-compute" />)
  await userEvent.click(await screen.findByRole("button", { name: "查看证据：CapEx 披露" }))
  expect(screen.getByText("财报第 32 页，表格第 4 行")).toBeVisible()
})
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd frontend && npm test -- ResearchWorkbenchPage.test.tsx`

Expected: FAIL because the page is absent.

- [ ] **Step 3: Implement the visual semantics**

```ts
const edgeStyleByKind = {
  evidence: { lineColor: "#2e7a48", lineStyle: "solid" },
  causal: { lineColor: "#6f7cff", lineStyle: "dashed" },
  theme_role: { lineColor: "#9a6a12", lineStyle: "solid" },
  holding: { lineColor: "#9a6a12", lineStyle: "dotted" },
} as const
```

The header displays one current result, one main gap, the cutoff, and the review state. The graph is one connected canvas. Clicking an edge opens an inline side drawer with reason, role, scope, period, source statement, source span and review state. Clicking Stock/Fund nodes opens valuation or dated holding rows. The page must not use a card grid as the primary relationship view.

- [ ] **Step 4: Add one end-to-end browser assertion**

```ts
test("user can trace an AI conclusion to a fund holding disclosure", async ({ page }) => {
  await page.goto("/research-cases/ai-compute")
  await page.getByText("AI 临时判断，未经人工复核").click()
  await page.getByRole("button", { name: "查看证据：CapEx 披露" }).click()
  await page.getByText("财报第 32 页，表格第 4 行").click()
  await page.getByRole("button", { name: "查看关联基金" }).click()
  await expect(page.getByText("报告期")).toBeVisible()
  await expect(page.getByText("披露日")).toBeVisible()
})
```

- [ ] **Step 5: Run UI verification**

Run: `cd frontend && npm test -- ResearchWorkbenchPage.test.tsx && npx playwright test e2e/workbench.spec.ts`

Expected: unit and browser tests pass.

- [ ] **Step 6: Commit checkpoint**

Run after Git exists: `git add frontend && git commit -m "feat: add connected evidence workbench"`

### Task 8: Seed the real AI-compute slice and create a frozen evaluation package

**Files:**
- Create: `backend/app/scripts/seed_ai_compute_case.py`
- Create: `docs/evaluation/ai-compute-gold-set.md`
- Create: `docs/evaluation/ai-compute-failure-cases.md`
- Create: `backend/tests/test_seed_ai_compute_case.py`

- [ ] **Step 1: Write the fixture acceptance test**

```python
def test_ai_compute_seed_has_required_auditable_minimum(session):
    assert count(session, ResearchCase) == 1
    assert count(session, Thesis) == 3
    assert count(session, DocumentVersion) >= 6
    assert count(session, SourceSpan) >= 30
    assert count(session, Company) == 3
    assert count(session, Fund) == 2
    assert all(disclosure.report_period and disclosure.published_at for disclosure in session.scalars(select(HoldingDisclosure)))
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd backend && pytest tests/test_seed_ai_compute_case.py -q`

Expected: FAIL because no fixed slice exists.

- [ ] **Step 3: Add only verified, frozen material**

The seed script must accept a local manifest of approved materials, fetch nothing from the web, hash each file, parse it, and fail if any required source span cannot be located. Add source identifiers, cutoff dates, direct/indirect evidence roles, and all known scope mismatches to the gold set. Add at least these retained failure cases: future publication leakage, total-company metric used for a business-line thesis, contradictory research opinion, missing direct transmission evidence, and stale fund disclosure.

- [ ] **Step 4: Run seed and test it**

Run: `cd backend && python -m app.scripts.seed_ai_compute_case --reset-test-db && pytest tests/test_seed_ai_compute_case.py -q`

Expected: seed completes without network access; test passes.

- [ ] **Step 5: Commit checkpoint**

Run after Git exists: `git add backend docs/evaluation && git commit -m "test: add frozen ai compute evidence slice"`

### Task 9: Execute release-gate verification

**Files:**
- Create: `backend/scripts/verify_ai_compute_slice.py`
- Create: `docs/evaluation/ai-compute-release-gate.md`

- [ ] **Step 1: Write the failing release gate test**

```python
def test_release_gate_rejects_missing_source_span(release_gate, seeded_database):
    seeded_database.delete_one_source_span()
    result = release_gate.run()
    assert result.passed is False
    assert "untraceable_assessment" in result.failures
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd backend && pytest tests/test_release_gate.py -q`

Expected: FAIL because the gate does not exist.

- [ ] **Step 3: Implement explicit gate checks**

```python
CHECKS = {
    "document_versions_present": check_document_versions,
    "assessment_source_spans_complete": check_assessment_traceability,
    "holding_disclosures_dated": check_holding_dates,
    "future_material_excluded": check_cutoff_visibility,
    "ai_human_boundary_visible": check_assessment_review_boundary,
    "projection_rebuilds": check_projection_rebuild,
}
```

Each check returns `{name, passed, evidence, failures}`. The script exits non-zero when any check fails and writes one JSON result under `docs/evaluation/runs/` without overwriting prior results.

- [ ] **Step 4: Run the full release gate**

Run: `cd backend && python scripts/verify_ai_compute_slice.py && pytest -q`

Expected: JSON result reports every check passed; test suite passes.

- [ ] **Step 5: Run frontend build and E2E**

Run: `cd frontend && npm run typecheck && npm run build && npx playwright test`

Expected: typecheck, production build and browser tests pass.

- [ ] **Step 6: Commit checkpoint**

Run after Git exists: `git add backend docs/evaluation frontend && git commit -m "test: add ai compute release gate"`

## Plan self-review

### Spec coverage

- Evidence ledger, immutable versions, SourceSpan and dual time are Tasks 2–4.
- Explicit evidence/causal semantics and no LLM-confidence promotion are Tasks 3–4.
- Dated stock/fund penetration is Task 5.
- Graph as rebuildable projection is Task 6.
- One connected, drill-down workbench and provisional AI marker are Task 7.
- AI-compute fixed slice, gold data, failures and replay-oriented acceptance are Tasks 8–9.
- No automatic maturity or review trigger is fixed in Task 4 and excluded from APIs.

### Placeholder and type consistency scan

The plan contains no unresolved placeholder steps. All status, role, source-kind and review-outcome literals are defined once under “Data contracts fixed by this plan” and are reused unchanged. Every service-facing task specifies a test command and expected result.

### Deferred plans

After this MVP passes the release gate, create separate plans for: multi-theme ingestion/scheduling, production authorization and source governance, richer causal-evidence review UI, and portfolio/recommendation integration. None are prerequisites for this vertical slice.
