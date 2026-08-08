# Report Research and Market Impact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn one uploaded or pasted company research report into an auditable research case, evidence graph, market-impact study, and embeddable read-only Wiki graph.

**Architecture:** Reuse the immutable `DocumentVersion → SourceSpan → SourceStatement` ledger as the report source of truth. Append report claims and relation assessments rather than mutating extracted text; compute China A-share 1/5-trading-day observations only from point-in-time ledger data, with explicit confounders and missing-data states. Project only the selected current scope through a graph query and an authenticated read-only embed surface.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic/PostgreSQL, existing ingest and China ledger services, React/TypeScript, Vitest, Playwright.

---

## File structure

- `backend/app/services/report_research.py`: report ingestion orchestration, extraction boundary and research-case creation.
- `backend/app/models/report_research.py`: append-only claims, relations, market windows and graph snapshots.
- `backend/app/services/report_market_impact.py`: trading-day alignment, 1/5-day observations and confounder collection.
- `backend/app/queries/report_wiki.py`: current-scope graph and read-only embed projection.
- `backend/app/api/v1/report_research.py`: upload/paste/create, graph and embed endpoints.
- `frontend/src/pages/prototype/ReportResearchScreen.tsx`: report-first research workspace.
- `frontend/src/pages/prototype/ReportWikiGraphScreen.tsx`: selected-factor graph plus embed-safe presentation.

### Task 1: Freeze report inputs and create report-first research cases

**Files:** Create `backend/app/services/report_research.py`, `backend/tests/test_report_research.py`; modify `backend/app/services/ingest.py`, `backend/app/api/v1/event_research.py` or a new `backend/app/api/v1/report_research.py`.

- [ ] **Step 1: Write failing ingestion tests**

```python
def test_pasted_report_is_frozen_and_creates_case(client):
    response = client.post('/api/v1/report-research', json={
        'input_kind': 'pasted_text', 'title': '服务器产业链更新',
        'publisher': '某券商', 'published_at': '2026-08-01T08:00:00Z',
        'content': '核心观点：订单增长。'
    })
    assert response.status_code == 201
    assert response.json()['document']['kind'] == 'research_report'
```

- [ ] **Step 2: Verify red**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_research.py -k pasted_report`

Expected: route/service is absent.

- [ ] **Step 3: Implement the explicit input contract**

```python
class CreateReportResearchRequest(V1Model):
    input_kind: Literal['pdf_upload', 'pasted_text', 'web_content']
    title: str = Field(min_length=1, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    content: str = Field(min_length=1)

def create_report_research(session: Session, request: CreateReportResearchRequest) -> ReportResearchCreated:
    document, _ = IngestService(session).freeze_text(
        content=request.content, title=request.title, source_kind='research_report',
        published_at=request.published_at,
    )
    return ReportResearchService(session).create_case(document.id, request)
```

PDF upload must use the existing parser adapter to produce text plus page/table locators before `freeze_text`; parser failure persists the original upload and returns a recoverable `needs_text_or_pages` state.

- [ ] **Step 4: Verify green and commit**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_research.py`

Commit: `git commit -am "feat: create report research cases"`

### Task 2: Append report claims and auditable relations

**Files:** Create `backend/app/models/report_research.py`, `backend/alembic/versions/0025_report_research.py`; modify `backend/app/models/__init__.py`, `backend/app/services/report_research.py`; test `backend/tests/test_report_research.py`.

- [ ] **Step 1: Write failing append-only tests**

```python
def test_extracted_claim_keeps_span_and_relation(session, report_case, report_span):
    claim = ReportClaimExtractor(session, FakeExtractor()).extract(report_case.id)[0]
    assert claim.source_span_id == report_span.id
    assert claim.kind == 'report_opinion'
    assert claim.relations[0].status == 'report_claim'
```

- [ ] **Step 2: Verify red**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_research.py -k claim`

- [ ] **Step 3: Implement immutable claim and relation records**

```python
class ReportClaim(Base):
    __tablename__ = 'report_claims'
    id = mapped_column(Uuid, primary_key=True, default=uuid4)
    research_case_id = mapped_column(Uuid, ForeignKey('research_cases.id'), nullable=False)
    source_statement_id = mapped_column(Uuid, ForeignKey('source_statements.id'), nullable=False)
    kind = mapped_column(String(32), nullable=False)  # opinion, forecast, assumption, risk
    statement = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)

class ReportRelation(Base):
    __tablename__ = 'report_relations'
    id = mapped_column(Uuid, primary_key=True, default=uuid4)
    claim_id = mapped_column(Uuid, ForeignKey('report_claims.id'), nullable=False)
    subject_company_id = mapped_column(Uuid, ForeignKey('companies.id'))
    object_company_id = mapped_column(Uuid, ForeignKey('companies.id'))
    relation_kind = mapped_column(String(32), nullable=False)
    mechanism = mapped_column(Text, nullable=False)
    status = mapped_column(String(24), nullable=False, default='report_claim')
```

Migration adds source/case indexes and PostgreSQL update/delete rejection triggers. Extractor emits `SourceStatement` first, then claims for conclusions, forecasts, assumptions, risks and relations. A missing entity remains an unresolved named node, never a fabricated company.

- [ ] **Step 4: Verify green and commit**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_research.py`

Commit: `git commit -am "feat: extract report claims and relations"`

### Task 3: Collect evidence and China market-impact observations

**Files:** Create `backend/app/services/report_market_impact.py`; modify `backend/app/services/report_research.py`, `backend/app/services/china_market_data.py`; test `backend/tests/test_report_market_impact.py`.

- [ ] **Step 1: Write failing point-in-time tests**

```python
def test_report_market_window_uses_next_trading_days(session, listed_claim):
    result = ReportMarketImpactService(session).collect(listed_claim.id)
    assert result.windows == {'1d': 'verified', '5d': 'verified'}

def test_missing_publish_time_skips_market_window(session, undated_claim):
    assert ReportMarketImpactService(session).collect(undated_claim.id).windows == {}
```

- [ ] **Step 2: Verify red**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_market_impact.py`

- [ ] **Step 3: Implement ledger-only collection**

```python
def collect(self, claim_id: UUID) -> MarketImpactResult:
    claim = self._claim(claim_id)
    if claim.document_published_at is None:
        return MarketImpactResult(windows={}, gap='缺少公开时间，未计算市场反应')
    days = self.calendar.next_trading_days(claim.document_published_at.date(), count=5)
    return self._append_windows(claim, days[0], days[4])
```

Append observations for target A-share, peer/index controls, volume/turnover/volatility and valuation only from ledger snapshots. Collect same-window announcements, earnings, policy and news as confounders. Unlisted relations only receive transmission observations; no stock/fund values. Missing snapshots append `insufficient` evidence and a gap.

- [ ] **Step 4: Verify green and commit**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_market_impact.py tests/test_report_research.py`

Commit: `git commit -am "feat: verify report market impact"`

### Task 4: Classify report factors and expose an evidence graph

**Files:** Create `backend/app/queries/report_wiki.py`, `backend/app/schemas/v1/report_research.py`, `backend/app/api/v1/report_research.py`; modify `backend/app/services/report_research.py`; test `backend/tests/test_report_wiki_api.py`.

- [ ] **Step 1: Write failing graph tests**

```python
def test_wiki_graph_returns_current_scope_path_only(client, report_case):
    graph = client.get(f'/api/v1/report-research/{report_case.id}/wiki').json()
    assert graph['nodes'][0]['kind'] == 'report_claim'
    assert all(edge['scope_version'] == graph['scope_version'] for edge in graph['edges'])
```

- [ ] **Step 2: Verify red**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_wiki_api.py`

- [ ] **Step 3: Implement graph projection and factor gate**

```python
class ReportWikiNodeDTO(V1Model):
    id: UUID
    kind: Literal['report_claim', 'company', 'evidence', 'market_window', 'fund']
    label: str
    status: Literal['report_claim', 'verified', 'candidate', 'rejected', 'market_observation']
    source_locator: str | None
```

The query bulk-loads current-scope claims, companies, evidence, observations and fund mappings. Key factors require source-backed report claim, verified company relation, operating/market/peer evidence and confounder assessment; otherwise return alternative or evidence gap. Historical scope nodes are excluded by default but available through a version parameter to authorized users.

- [ ] **Step 4: Verify green and commit**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_wiki_api.py`

Commit: `git commit -am "feat: expose report wiki graph"`

### Task 5: Provide read-only embed access

**Files:** Modify `backend/app/api/v1/report_research.py`, `backend/app/queries/report_wiki.py`; test `backend/tests/test_report_embed_api.py`.

- [ ] **Step 1: Write failing embed tests**

```python
def test_embed_token_is_read_only_and_redacts_private_fields(client, embed_token, report_case):
    response = client.get(f'/api/v1/report-research/{report_case.id}/embed/wiki', headers={'X-Embed-Token': embed_token})
    assert response.status_code == 200
    assert 'reviewer' not in response.text
```

- [ ] **Step 2: Implement endpoint policy**

```python
@router.get('/{case_id}/embed/wiki', response_model=ReportWikiGraphDTO)
def embed_wiki(case_id: UUID, token: str = Header(alias='X-Embed-Token'), db: Session = Depends(get_db)):
    EmbedAccessService(db).require_read_only(token, case_id)
    return ReportWikiQueries(db).graph(case_id, redacted=True)
```

Redacted embeds omit original file URLs, reviewer identities, internal task ids and restricted fund disclosures. No embed endpoint accepts mutation methods.

- [ ] **Step 3: Verify green and commit**

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_report_embed_api.py`

Commit: `git commit -am "feat: add read-only report wiki embeds"`

### Task 6: Build the report-first workspace and Wiki graph UI

**Files:** Create `frontend/src/pages/prototype/ReportResearchScreen.tsx`, `frontend/src/pages/prototype/ReportWikiGraphScreen.tsx`, tests `frontend/src/tests/ReportResearchScreen.test.tsx`, `frontend/src/tests/ReportWikiGraphScreen.test.tsx`, `frontend/e2e/report-research.spec.ts`; modify `frontend/src/main.tsx`, `frontend/src/data/httpResearchAdapter.ts`, `frontend/src/data/mockResearchAdapter.ts`, `frontend/src/domain/eventResearch.ts`, `frontend/src/styles-prototype.css`.

- [ ] **Step 1: Write failing component tests**

```tsx
it('shows report claim, evidence gap and 1d/5d market windows', async () => {
  render(<ReportResearchScreen caseId="report-1" />)
  expect(await screen.findByText('研报主张')).toBeVisible()
  expect(screen.getByText('发布后 1 个交易日')).toBeVisible()
  expect(screen.getByText('证据不足')).toBeVisible()
})
```

- [ ] **Step 2: Implement pages and safe interactions**

The workspace starts with current conclusion and gaps, then report claims, evidence plan and market windows. The graph defaults to the selected factor path and offers a structured list alternative; node click opens source locator or snapshot metadata. The external embed route is read-only. All external source links allow only HTTP(S); use explicit text for missing, stale or partial data.

- [ ] **Step 3: Verify desktop/mobile and commit**

Run: `cd frontend && npm test -- --run src/tests/ReportResearchScreen.test.tsx src/tests/ReportWikiGraphScreen.test.tsx && npm run typecheck && npm run build`

Run: `cd frontend && PW_BROWSER_CHANNEL=chrome PW_PORT=5192 npm run e2e -- e2e/report-research.spec.ts --reporter=line`

Commit: `git commit -am "feat: add report research wiki workspace"`

## Final verification

- [ ] `cd backend && .venv/bin/python -m pytest -q`
- [ ] `cd frontend && npm test && npm run typecheck && npm run build`
- [ ] Run report E2E at 390px and desktop; verify no horizontal overflow and that evidence/source states remain readable.
- [ ] `cd backend && .venv/bin/alembic upgrade head && .venv/bin/alembic current`
- [ ] Verify one pasted report and one PDF fixture: original locator, relationship path, 1d/5d windows, confounder gap, China-only asset mapping and redacted embed response.
