# Cambricon 2025 Profitability Case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one real, offline-reproducible Cambricon 2025 profitability-inflection case to the existing immutable research flow and display it through the existing theme, case, review, and conclusion pages.

**Architecture:** Store the verified Juyuan result and CNINFO annual-report table as small immutable application fixtures, normalize exact monetary values with `Decimal`, and seed only existing ledger entities. A separate live-refresh command appends new provider material as machine-generated pending evidence; it never mutates the seed snapshot or assessment. Existing FastAPI read models and React screens remain unchanged unless a failing integration test proves a narrow compatibility defect.

**Tech Stack:** Python 3.11, SQLAlchemy 2, FastAPI/TestClient, Pydantic, pytest, existing Gildata MCP client, React 18, TypeScript/Vitest, SQLite for deterministic acceptance.

---

## File map

Create:

- `backend/app/fixtures/cambricon_profitability_case/juyuan_finquery_2026-08-03.json` — frozen normalized provider observation plus original response text and provenance.
- `backend/app/fixtures/cambricon_profitability_case/cninfo_2025_annual_report_page_10.txt` — minimal official quarterly-table excerpt with metadata and exact yuan values.
- `backend/app/scripts/cambricon_profitability_data.py` — fixture loading, schema validation, `Decimal` derivation, and reconciliation; no database access.
- `backend/app/scripts/seed_cambricon_profitability_case.py` — idempotent orchestration into existing immutable ledger services/repositories and CLI.
- `backend/app/scripts/refresh_cambricon_profitability_case.py` — injected-client live Juyuan refresh that only appends pending evidence.
- `backend/tests/test_cambricon_profitability_data.py` — exact arithmetic and fixture provenance tests.
- `backend/tests/test_seed_cambricon_profitability_case.py` — ledger mapping, traceability, initial review boundary, and idempotence tests.
- `backend/tests/test_refresh_cambricon_profitability_case.py` — fake-provider refresh, deduplication, failure isolation, and snapshot immutability tests.
- `backend/tests/test_cambricon_profitability_case_e2e.py` — real v1 HTTP read/review flow on a seeded temporary database.

Modify only if a failing test proves it necessary:

- `backend/app/queries/conclusion.py` — narrow case-independent read-model correction.
- `frontend/src/data/httpResearchAdapter.ts` — narrow contract mapping correction.
- `frontend/src/pages/prototype/ConclusionScreen.tsx` — narrow rendering correction; no layout redesign.

Do not modify:

- database migrations or immutable entity definitions;
- frontend routes, prototype fixture, or mock adapter;
- the existing `seed_ai_compute_case.py` fixture, which contains a different research case.

### Task 1: Freeze and validate the real financial observations

**Files:**
- Create: `backend/app/fixtures/cambricon_profitability_case/juyuan_finquery_2026-08-03.json`
- Create: `backend/app/fixtures/cambricon_profitability_case/cninfo_2025_annual_report_page_10.txt`
- Create: `backend/app/scripts/cambricon_profitability_data.py`
- Test: `backend/tests/test_cambricon_profitability_data.py`

- [ ] **Step 1: Write failing exact-arithmetic and provenance tests**

```python
from decimal import Decimal

from app.scripts.cambricon_profitability_data import load_case_data


def test_case_data_reconciles_quarters_to_annual_totals():
    data = load_case_data()
    assert data.single_quarter_parent_profit == {
        "2025Q1": Decimal("355465241.04"),
        "2025Q2": Decimal("682617327.53"),
        "2025Q3": Decimal("566563175.54"),
        "2025Q4": Decimal("454582794.56"),
    }
    assert sum(data.single_quarter_parent_profit.values()) == Decimal(
        "2059228538.67"
    )
    assert sum(data.single_quarter_adjusted_profit.values()) == Decimal(
        "1769934157.68"
    )
    assert sum(data.single_quarter_operating_cash_flow.values()) == Decimal(
        "-498398137.01"
    )


def test_case_data_supports_five_consecutive_positive_quarters():
    data = load_case_data()
    series = [data.parent_profit_2024_q4, *data.single_quarter_parent_profit.values()]
    assert len(series) == 5
    assert all(value > 0 for value in series)


def test_case_data_keeps_two_independent_sources_and_locators():
    data = load_case_data()
    assert data.juyuan.tool == "FinQuery"
    assert data.juyuan.fetched_at == "2026-08-03T00:00:00+08:00"
    assert data.annual_report.source_url.startswith("https://dataclouds.cninfo.com.cn/")
    assert data.annual_report.page == 10
    assert data.juyuan.raw_response.strip()
    assert data.annual_report.verbatim_text.strip()
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_cambricon_profitability_data.py -q
```

Expected: collection fails with `ModuleNotFoundError: app.scripts.cambricon_profitability_data`.

- [ ] **Step 3: Add the two minimal frozen source files**

The JSON must use exact yuan strings and this stable shape:

```json
{
  "provider": "gildata-juyuan",
  "tool": "FinQuery",
  "fetched_at": "2026-08-03T00:00:00+08:00",
  "queries": [
    "寒武纪 688256 2025年各报告期归属于母公司股东的净利润",
    "寒武纪 688256 2025年各报告期扣除非经常性损益后的归母净利润"
  ],
  "cumulative_parent_profit_yuan": {
    "2025Q1": "355465241.04",
    "2025H1": "1038082568.57",
    "2025Q1-Q3": "1604645744.11",
    "2025FY": "2059228538.67"
  },
  "cumulative_adjusted_profit_yuan": {
    "2025Q1": "275962803.95",
    "2025H1": "912566847.07",
    "2025Q1-Q3": "1418887977.30",
    "2025FY": "1769934157.68"
  }
}
```

Add `raw_response` as the exact string returned by `GildataMCPClient.call_tool`; obtain it with the two fixed queries above and preserve it byte-for-byte. The fixture review must compare the stored string with the captured return value before commit.

The CNINFO excerpt must contain parseable metadata followed by the page-10 values:

```text
# TITLE: 中科寒武纪科技股份有限公司2025年年度报告（第10页节选）
# SOURCE_URL: https://dataclouds.cninfo.com.cn/shgonggao/hsomarket/2026/20260312/05ca784762a7401b9ed371d917e436dc.PDF
# PUBLISHED_AT: 2026-03-12T00:00:00+08:00
# PAGE: 10
[ROW 营业收入] 1111398926.80 | 1769244544.29 | 1726780892.57 | 1889771835.02
[ROW 归属于上市公司股东的净利润] 355465241.04 | 682617327.53 | 566563175.54 | 454582794.56
[ROW 扣除非经常性损益后的归母净利润] 275962803.95 | 636604043.12 | 506321130.23 | 351046180.38
[ROW 经营活动产生的现金流量净额] -1399358712.85 | 2310509034.58 | -940455133.44 | -469093325.30
```

Do not insert a reconstructed provider response under `raw_response`. If the exact response text is unavailable, run the fixed FinQuery once and capture it; if the provider is unavailable, stop this step rather than labeling normalized data as raw.

- [ ] **Step 4: Implement the pure loader and reconciliation**

Use frozen dataclasses and exact subtraction:

```python
@dataclass(frozen=True)
class CaseData:
    juyuan: JuyuanObservation
    annual_report: AnnualReportObservation
    parent_profit_2024_q4: Decimal
    single_quarter_parent_profit: dict[str, Decimal]
    single_quarter_adjusted_profit: dict[str, Decimal]
    single_quarter_operating_cash_flow: dict[str, Decimal]


def _derive_quarters(cumulative: dict[str, Decimal]) -> dict[str, Decimal]:
    return {
        "2025Q1": cumulative["2025Q1"],
        "2025Q2": cumulative["2025H1"] - cumulative["2025Q1"],
        "2025Q3": cumulative["2025Q1-Q3"] - cumulative["2025H1"],
        "2025Q4": cumulative["2025FY"] - cumulative["2025Q1-Q3"],
    }


def load_case_data() -> CaseData:
    fixture_dir = Path(__file__).resolve().parents[1] / "fixtures" / "cambricon_profitability_case"
    payload = json.loads((fixture_dir / "juyuan_finquery_2026-08-03.json").read_text("utf-8"))
    required = {"provider", "tool", "fetched_at", "queries", "raw_response"}
    missing = required - payload.keys()
    if missing or not payload["raw_response"].strip():
        raise ValueError(f"invalid Juyuan fixture; missing={sorted(missing)}")
    parent = {key: Decimal(value) for key, value in payload["cumulative_parent_profit_yuan"].items()}
    adjusted = {key: Decimal(value) for key, value in payload["cumulative_adjusted_profit_yuan"].items()}
    report = _parse_annual_report(
        (fixture_dir / "cninfo_2025_annual_report_page_10.txt").read_text("utf-8")
    )
    derived_parent = _derive_quarters(parent)
    derived_adjusted = _derive_quarters(adjusted)
    if derived_parent != report.parent_profit or derived_adjusted != report.adjusted_profit:
        raise ValueError("Juyuan quarterly derivation does not reconcile to CNINFO page 10")
    return CaseData(
        juyuan=_juyuan_observation(payload),
        annual_report=report,
        parent_profit_2024_q4=Decimal("272152952.65"),
        single_quarter_parent_profit=derived_parent,
        single_quarter_adjusted_profit=derived_adjusted,
        single_quarter_operating_cash_flow=report.operating_cash_flow,
    )
```

Implement `_parse_annual_report` with one anchored regular expression per `[ROW ...]` line, require exactly four `Decimal` values per row, and reject extra/missing periods. Implement `_juyuan_observation` as direct validated dataclass construction. No network or database imports are allowed in this module.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/test_cambricon_profitability_data.py -q`

Expected: `3 passed`.

- [ ] **Step 6: Commit the verified data slice**

```bash
git add backend/app/fixtures/cambricon_profitability_case \
  backend/app/scripts/cambricon_profitability_data.py \
  backend/tests/test_cambricon_profitability_data.py
git commit -m "feat: freeze Cambricon profitability observations"
```

### Task 2: Seed the case through the existing immutable ledger

**Files:**
- Create: `backend/app/scripts/seed_cambricon_profitability_case.py`
- Test: `backend/tests/test_seed_cambricon_profitability_case.py`

- [ ] **Step 1: Write failing ledger and idempotence tests**

```python
from sqlalchemy import func, select

from app.models.ledger import (
    AIAssessment, DocumentVersion, EvidenceLink, EvidenceReview,
    EvidenceSnapshot, ResearchCase, ReviewDecision, SourceSpan,
    SourceStatement, Thesis,
)
from app.scripts.seed_cambricon_profitability_case import CASE_TITLE, seed


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_seed_builds_traceable_unreviewed_case(session):
    result = seed(session)
    case = session.scalar(select(ResearchCase).where(ResearchCase.id == result.case_id))
    assert case.title == CASE_TITLE
    assert _count(session, Thesis) == 1
    assert _count(session, DocumentVersion) == 2
    assert _count(session, SourceSpan) >= 6
    assert _count(session, SourceStatement) >= 6
    assert _count(session, EvidenceLink) >= 6
    assert _count(session, EvidenceReview) == 0
    assert _count(session, ReviewDecision) == 0
    assessment = session.scalar(select(AIAssessment))
    assert assessment.conclusion == "supported"
    assert assessment.displayed_as_provisional is True


def test_seed_is_idempotent_when_complete(session):
    first = seed(session)
    before = {model: _count(session, model) for model in (
        ResearchCase, Thesis, DocumentVersion, SourceSpan,
        SourceStatement, EvidenceLink, EvidenceSnapshot, AIAssessment,
    )}
    second = seed(session)
    after = {model: _count(session, model) for model in before}
    assert second.case_id == first.case_id
    assert after == before
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/test_seed_cambricon_profitability_case.py -q`

Expected: collection fails because the seed module does not exist.

- [ ] **Step 3: Implement a narrow seed manifest and completeness check**

Define:

```python
CASE_TITLE = "寒武纪 2025 年盈利拐点"
CUTOFF = datetime(2026, 8, 3, tzinfo=timezone.utc)
CREATED_BY = "codex-case-draft"

@dataclass(frozen=True)
class SeedResult:
    case_id: uuid.UUID
    thesis_id: uuid.UUID
    assessment_id: uuid.UUID
    created: bool

def _existing_complete_case(session: Session) -> SeedResult | None:
    case = session.scalar(select(ResearchCase).where(ResearchCase.title == CASE_TITLE))
    if case is None:
        return None
    theses = list(session.scalars(select(Thesis).where(Thesis.research_case_id == case.id)))
    if len(theses) != 1:
        raise RuntimeError("existing Cambricon case is partial: expected one thesis")
    assessment = session.scalar(
        select(AIAssessment)
        .join(EvidenceSnapshot, EvidenceSnapshot.id == AIAssessment.snapshot_id)
        .where(EvidenceSnapshot.thesis_id == theses[0].id)
    )
    if assessment is None:
        raise RuntimeError("existing Cambricon case is partial: assessment missing")
    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    if snapshot is None or not snapshot.evidence_link_ids:
        raise RuntimeError("existing Cambricon case is partial: snapshot empty")
    for link_id in snapshot.evidence_link_ids:
        link = session.get(EvidenceLink, uuid.UUID(link_id))
        statement = session.get(SourceStatement, link.source_statement_id) if link else None
        span = session.get(SourceSpan, statement.source_span_id) if statement else None
        document = session.get(DocumentVersion, span.document_version_id) if span else None
        if document is None:
            raise RuntimeError("existing Cambricon case is partial: traceability broken")
    return SeedResult(case.id, theses[0].id, assessment.id, False)
```

Use `DocumentRepository.insert_version` for the two historical fixtures so `published_at` and the actual acquisition time remain distinct. Use `DocumentService.add_span`, `ResearchService.add_statement/link_evidence/add_case/add_thesis`, `AssessmentService.freeze_snapshot/create_ai_assessment`, `ThemeService.apply_theme_tags`, and `InstrumentRepository` for the optional company/stock/role records.

- [ ] **Step 4: Implement the exact thesis, evidence roles, and scope**

The seed manifest must contain one thesis:

```python
THESIS = {
    "title": "会计利润口径的盈利拐点已经出现",
    "statement": (
        "寒武纪自2024Q4至2025Q4连续五个季度单季度归母净利润为正，"
        "且2025年归母净利润与扣非归母净利润均为正"
    ),
    "support_condition": "连续五季度单季度归母净利润为正且2025全年归母、扣非均为正",
    "falsification_condition": "任一季度归母净利润不为正，或2025全年归母/扣非任一不为正",
    "next_verification_event": "复核2026年季度利润与经营现金流，判断拐点可持续性",
}
```

Create supports for the five-quarter series and annual parent/adjusted profit. Create contextualizes links for negative annual operating cash flow and for the explicit non-causal boundary. Set scope fields such as:

```python
{
    "company": "中科寒武纪科技股份有限公司",
    "stock_code": "688256.SH",
    "metric": "归属于母公司股东的净利润",
    "period": "2024Q4-2025Q4",
    "unit": "CNY",
    "derivation": "single-quarter values cross-checked against annual-report page 10",
}
```

Create the assessment rationale with this exact boundary:

```text
冻结数据支持会计利润口径的盈利拐点：2024Q4至2025Q4连续五个季度归母净利润为正，2025全年归母与扣非归母净利润均为正。该判断不证明国产算力需求是唯一原因，也不证明盈利可持续；2025全年经营现金流净额为负，需要后续验证回款与现金转化。
```

Set gaps to `['需求到利润的可审计传导证据不足', '盈利持续性仍需后续季度与经营现金流验证']`. Do not create `EvidenceReview` or `ReviewDecision`.

- [ ] **Step 5: Run seed tests and verify GREEN**

Run: `cd backend && .venv/bin/pytest tests/test_seed_cambricon_profitability_case.py -q`

Expected: all tests pass.

- [ ] **Step 6: Run existing immutable-ledger regression tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_seed_ai_compute_case.py \
  tests/test_time_travel.py tests/test_release_gate.py -q
```

Expected: all tests pass; existing warnings may remain unchanged.

- [ ] **Step 7: Commit the seed**

```bash
git add backend/app/scripts/seed_cambricon_profitability_case.py \
  backend/tests/test_seed_cambricon_profitability_case.py
git commit -m "feat: seed reproducible Cambricon research case"
```

### Task 3: Append live Juyuan refreshes without changing the frozen conclusion

**Files:**
- Create: `backend/app/scripts/refresh_cambricon_profitability_case.py`
- Test: `backend/tests/test_refresh_cambricon_profitability_case.py`

- [ ] **Step 1: Write failing refresh-boundary tests with an injected fake client**

```python
class FakeClient:
    def __init__(self, responses): self.responses = iter(responses)
    def call_tool(self, name, arguments, timeout=60):
        assert name == "FinQuery"
        return next(self.responses)
    def close(self): pass


def test_refresh_appends_pending_links_without_changing_snapshot(session):
    seeded = seed(session)
    before = session.get(AIAssessment, seeded.assessment_id)
    snapshot = session.get(EvidenceSnapshot, before.snapshot_id)
    member_ids = list(snapshot.evidence_link_ids)

    result = refresh(session, seeded.case_id, client=FakeClient(SUCCESS_RESPONSES))

    session.refresh(snapshot)
    assert snapshot.evidence_link_ids == member_ids
    assert result.pending_links > 0
    assert all(link.review_state == "machine_generated" for link in result.links)
    assert session.scalar(select(func.count()).select_from(ReviewDecision)) == 0


def test_refresh_failure_rolls_back_and_preserves_seed(session):
    seeded = seed(session)
    counts = ledger_counts(session)
    with pytest.raises(GildataMCPError):
        refresh(session, seeded.case_id, client=FailingClient())
    session.rollback()
    assert ledger_counts(session) == counts
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd backend && .venv/bin/pytest tests/test_refresh_cambricon_profitability_case.py -q`

Expected: collection fails because the refresh module does not exist.

- [ ] **Step 3: Implement response capture, validation, deduplication, and append-only writes**

Expose this interface:

```python
@dataclass(frozen=True)
class RefreshResult:
    document_ids: list[uuid.UUID]
    links: list[EvidenceLink]
    created_documents: int
    duplicate_documents: int
    pending_links: int

def refresh(
    session: Session,
    case_id: uuid.UUID,
    *,
    client: GildataMCPClient,
) -> RefreshResult:
    case = ResearchRepository(session).get_case(case_id)
    if case is None or case.title != CASE_TITLE:
        raise ValueError("Cambricon profitability case not found")
    thesis = ResearchRepository(session).theses_for_case(case_id)[0]
    raw_results = [client.call_tool("FinQuery", {"query": query}) for query in QUERIES]
    parsed_results = [parse_content(raw) for raw in raw_results]
    if any(not rows for rows in parsed_results):
        raise GildataMCPError("FinQuery returned no usable profitability rows")
    document_ids, links = _append_pending_observations(
        session=session,
        thesis=thesis,
        queries=QUERIES,
        raw_results=raw_results,
        parsed_results=parsed_results,
    )
    return RefreshResult(
        document_ids=document_ids,
        links=links,
        created_documents=len(document_ids),
        duplicate_documents=len(raw_results) - len(document_ids),
        pending_links=len(links),
    )
```

Use exactly the two queries stored in the frozen fixture. Parse the inner payload with `app.datasources.gildata.adapters.parse_content`. Implement `_append_pending_observations` to validate every parsed row before writing, freeze each complete response text as bytes using source URL `gildata://FinQuery/688256/profitability`, skip a response when its content hash already exists, add one locator-bearing span and disclosed-fact statement per normalized period/metric, then call `ResearchService.link_evidence` with the same thesis scope used by the seed. Never call `freeze_snapshot`, `create_ai_assessment`, or `review`.

- [ ] **Step 4: Add the CLI boundary**

```python
def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True, type=uuid.UUID)
    args = parser.parse_args()
    with GildataMCPClient.from_env() as client, SessionLocal() as session:
        result = refresh(session, args.case_id, client=client)
        session.commit()
    print(json.dumps({
        "case_id": str(args.case_id),
        "created_documents": result.created_documents,
        "duplicate_documents": result.duplicate_documents,
        "pending_links": result.pending_links,
        "review_url": "http://localhost:5173/review",
    }, ensure_ascii=False))
    return 0
```

Catch `GildataMCPError`, roll back, print a token-free error to stderr, and return nonzero. Never print request URLs because the provider token is carried in the URL query string.

- [ ] **Step 5: Run refresh tests and existing provider tests**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_refresh_cambricon_profitability_case.py \
  tests/test_gildata_client.py -q
```

Expected: all tests pass and no credential value appears in captured output.

- [ ] **Step 6: Commit the refresh command**

```bash
git add backend/app/scripts/refresh_cambricon_profitability_case.py \
  backend/tests/test_refresh_cambricon_profitability_case.py
git commit -m "feat: append pending Juyuan refresh evidence"
```

### Task 4: Prove the existing HTTP research and review flow end-to-end

**Files:**
- Create: `backend/tests/test_cambricon_profitability_case_e2e.py`
- Modify only if RED proves necessary: `backend/app/queries/conclusion.py`

- [ ] **Step 1: Write the failing real-HTTP flow test**

Use the test session override already defined in `backend/tests/conftest.py`. Seed through the Python function, then assert:

```python
def test_cambricon_case_runs_through_existing_http_flow(api_client, session):
    seeded = seed(session)
    session.commit()

    cases = api_client.get("/api/v1/research-cases").json()["items"]
    assert any(row["id"] == str(seeded.case_id) for row in cases)

    theme_index = api_client.get("/api/v1/themes").json()["items"]
    assert any(row["tag"] == "算力国产化" for row in theme_index)

    conclusion_url = f"/api/v1/research-cases/{seeded.case_id}/conclusion"
    before = api_client.get(conclusion_url).json()
    assert before["header"]["conclusion_status"] == "supported"
    assert before["header"]["ai_provisional"] is True
    assert before["header"]["reviewer"] is None
    rendered = str(before)
    assert "355465241.04" in rendered
    assert "经营现金流" in rendered
    assert "可持续" in rendered

    queue = api_client.get(
        "/api/v1/review-queue", params={"case_id": str(seeded.case_id)}
    ).json()
    for item in queue["items"]:
        response = api_client.post(
            f"/api/v1/evidence-links/{item['link_id']}/reviews",
            json={
                "outcome": "confirmed",
                "relation": item["ai_role"],
                "factor_role": "盈利拐点事实或范围限制",
                "scope_boundary": "寒武纪会计利润口径，2024Q4至2025Q4",
                "reason": "逐项核对聚源冻结结果与年报第10页后确认",
                "reviewer": "e2e-human-reviewer",
            },
        )
        assert response.status_code == 201

    reviewed = api_client.post(
        f"/api/v1/assessments/{seeded.assessment_id}/reviews",
        json={
            "outcome": "confirmed",
            "conclusion": "supported",
            "reason": "证据关系已逐项确认，维持狭义盈利拐点判断",
            "reviewer": "e2e-human-reviewer",
        },
    )
    assert reviewed.status_code == 201
    after = api_client.get(conclusion_url).json()
    assert after["header"]["review_state"] == "confirmed"
    assert after["header"]["reviewer"] == "e2e-human-reviewer"
```

The test must use the checked-in `ReviewQueueResponse` names shown above (`link_id` and `ai_role`); do not bypass routes with direct inserts.

- [ ] **Step 2: Run the E2E test and inspect the first real defect**

Run: `cd backend && .venv/bin/pytest tests/test_cambricon_profitability_case_e2e.py -q -x`

Expected before implementation completion: FAIL at the first contract/content mismatch, with seed and API stack otherwise running.

- [ ] **Step 3: Make only the minimal case-independent read correction if required**

Allowed examples are deterministic ordering, correct reviewed relation projection, or preserving exact textual amounts. Do not add `if case.title == ...`, case IDs, new DTO fields, or presentation-specific database queries. If the test already passes, make no production change in this step.

- [ ] **Step 4: Run the focused API and historical-read suite**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_cambricon_profitability_case_e2e.py \
  tests/test_conclusion_read_api_v1.py tests/test_case_read_api_v1.py \
  tests/test_review_commands_api.py tests/test_time_travel.py \
  tests/test_release_gate.py -q
```

Expected: all tests pass; warnings must match the existing baseline.

- [ ] **Step 5: Commit the end-to-end acceptance**

```bash
git add backend/tests/test_cambricon_profitability_case_e2e.py
git add backend/app/queries/conclusion.py  # only when genuinely modified
git commit -m "test: verify Cambricon case through research flow"
```

### Task 5: Run the real application and verify the existing pages

**Files:**
- No planned frontend production changes.
- Modify only if a failing browser assertion proves necessary: `frontend/src/data/httpResearchAdapter.ts`, `frontend/src/pages/prototype/ConclusionScreen.tsx`

- [ ] **Step 1: Create and seed a disposable real SQLite database**

Run:

```bash
cd backend
CASE_DB="$(mktemp -d)/cambricon-case.db"
DATABASE_URL="sqlite:///$CASE_DB" .venv/bin/python -m app.scripts.seed_cambricon_profitability_case
```

Expected: JSON output contains `created: true`, a UUID `case_id`, and `http://localhost:5173/conclusion/<uuid>`.

- [ ] **Step 2: Start backend and frontend against real adapters**

Run backend in one terminal:

```bash
cd backend
DATABASE_URL="sqlite:///$CASE_DB" .venv/bin/uvicorn app.main:app --port 8000
```

Run frontend in another terminal:

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Do not add `?client=mock`.

- [ ] **Step 3: Verify the current navigation in a real browser**

Open `/themes`, select “寒武纪 2025 年盈利拐点”, then use the existing links to inspect the theme workbench, case workbench, review center, conclusion page, and a source downlink. Record screenshots outside git under `.superpowers/verification/cambricon-profitability/`.

Required visible assertions:

- case title and “持续验证/未经人工复核” state;
- the 2024Q4–2025Q4 positive-profit sequence;
- 2025 full-year parent and adjusted profit;
- negative operating cash flow scope warning;
- demand-causality and sustainability gaps;
- Juyuan and CNINFO sources with locator/hash information.

- [ ] **Step 4: Add a frontend test only if the existing page drops required ledger text**

If browser inspection proves the adapter or screen discards required content, first add a failing assertion to `frontend/src/tests/HttpResearchAdapter.test.ts` or `frontend/src/tests/ConclusionScreen.test.tsx`, run that single test RED, make the smallest case-independent mapping/rendering change, then rerun it GREEN. Do not add case-specific UI branches or a new page.

- [ ] **Step 5: Run frontend verification**

Run:

```bash
cd frontend
npm test -- --run src/tests/ConclusionScreen.test.tsx src/tests/HttpResearchAdapter.test.ts
npm run build
```

Expected: tests and TypeScript/Vite build pass.

- [ ] **Step 6: Run all automated tests**

Run:

```bash
cd backend
.venv/bin/pytest -q
cd ../frontend
npm test
npm run build
```

Expected: all suites pass. Report pre-existing skips/warnings separately from new failures.

- [ ] **Step 7: Optionally exercise the live refresh once**

Run only after the offline flow passes:

```bash
cd backend
DATABASE_URL="sqlite:///$CASE_DB" \
  .venv/bin/python -m app.scripts.refresh_cambricon_profitability_case \
  --case-id <seed-output-uuid>
```

Expected when the provider is available: pending link count is nonzero, `/review` shows the new items, and `/conclusion/<uuid>` retains its previous snapshot and judgment. Expected when quota/provider fails: nonzero CLI exit with a token-free diagnostic; the frozen case remains usable.

- [ ] **Step 8: Commit any proven frontend compatibility fix**

Skip this commit when no frontend production file changed. Otherwise:

```bash
git add frontend/src/data/httpResearchAdapter.ts \
  frontend/src/pages/prototype/ConclusionScreen.tsx \
  frontend/src/tests/HttpResearchAdapter.test.ts \
  frontend/src/tests/ConclusionScreen.test.tsx
git commit -m "fix: preserve case evidence in conclusion view"
```

### Task 6: Final audit and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-08-03-cambricon-profitability-case.md` only to check completed boxes during execution.

- [ ] **Step 1: Verify scope and working tree**

Run:

```bash
git status --short
git diff main...HEAD --stat
git diff main...HEAD -- frontend/src/main.tsx backend/alembic
```

Expected: no route or migration diff; only the approved fixtures, scripts, tests, design/plan docs, and any test-proven narrow compatibility fix.

- [ ] **Step 2: Verify no secrets or fake sources entered git**

Run:

```bash
git grep -nE 'GILDATA_TOKEN=|JUYUAN_API_TOKEN=|INVESTODAY_API_KEY=' main...HEAD -- . ':!*.md'
git grep -n 'example\.test' main...HEAD -- backend/app/fixtures/cambricon_profitability_case
```

Expected: no matches. The committed provider response contains data only, never request URLs with token query parameters.

- [ ] **Step 3: Review every changed line against the confirmed design**

Confirm explicitly that:

- no artificial human review exists in the seed;
- no causal or sustainability claim is promoted beyond evidence;
- exact values reconcile and remain traceable;
- refresh cannot mutate the frozen snapshot or assessment;
- the real page works without mock mode.

- [ ] **Step 4: Commit plan checkbox updates if they are retained**

```bash
git add docs/superpowers/plans/2026-08-03-cambricon-profitability-case.md
git commit -m "docs: record Cambricon case implementation verification"
```

- [ ] **Step 5: Request independent standards and quality review**

Use the repository code-review workflow on `main...HEAD`. Address only findings within this case scope, rerun the affected focused tests, then rerun the final full verification before claiming completion.
