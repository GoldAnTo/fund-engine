# Live Industrial Foxconn Forecast Case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one real, source-frozen Industrial Foxconn forecast case through a published supported verdict, audited AI-product key-factor verification, stock mapping and historical fund disclosure, then read it in the existing Case UI.

**Architecture:** A narrow runner service owns provider selection, source validation and immutable ledger writes; a CLI only supplies the database and the live Gildata client. It freezes exactly four provider responses: the historical report, company annual report, fund holding table and fund annual report. Existing forecast and market-expression queries remain the only reader paths; the runner creates their reviewed inputs and appends visible run events.

**Tech Stack:** Python 3.11, SQLAlchemy, FastAPI query models, existing Gildata MCP adapters, React/Vitest/Playwright.

---

### Task 1: Select only the verified provider records

**Files:**
- Create: `backend/app/services/industrial_foxconn_forecast_case.py`
- Create: `backend/tests/test_industrial_foxconn_forecast_case.py`

- [ ] **Step 1: Write the failing source-selection test**

```python
def test_loads_only_the_predeclared_report_annual_report_and_fund_position():
    bundle = load_industrial_foxconn_sources(FakeGildataClient())

    assert bundle.report.title == "工业富联(601138)：盈利整体平稳增长 AI业务表现强劲"
    assert bundle.report.published_at == datetime(2024, 3, 25, tzinfo=timezone.utc)
    assert bundle.expected_profit == Decimal("25149000000")
    assert bundle.annual_report.published_at == datetime(2025, 4, 30, tzinfo=timezone.utc)
    assert bundle.actual_profit == Decimal("23216000000")
    assert bundle.fund.code == "515050"
    assert bundle.fund.position_weight == Decimal("0.0533")
    assert bundle.fund.report_period == date(2024, 12, 31)


def test_rejects_a_provider_response_without_the_approved_numeric_prediction():
    with pytest.raises(SourceSelectionError, match="251.49"):
        load_industrial_foxconn_sources(FakeGildataClient(report_text="预测不可解析"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_industrial_foxconn_forecast_case.py -q`

Expected: FAIL because the source selector does not exist.

- [ ] **Step 3: Implement the bounded provider selector**

```python
REPORT_QUERY = "工业富联 2024年 归母净利润 预测 研报"
ANNUAL_REPORT_QUERY = "工业富联 2024年年度报告"
FUND_HOLDINGS_QUERY = (
    "查询基金515050 2024年第4季度公开披露的股票持仓明细，"
    "包括股票代码、股票名称、持仓权重、报告期"
)
FUND_REPORT_QUERY = "华夏中证5G通信主题交易型开放式指数证券投资基金 2024年年度报告"

def load_industrial_foxconn_sources(client: Any) -> IndustrialFoxconnSourceBundle:
    """Read, validate and normalize exactly the approved live source records."""
```

Call `FinancialResearchReport`, `AnnouncementData`, and `FinQuery`; keep each raw `call_tool` response in the returned bundle. Match the report title and publication date exactly; parse `251.49` and `232.16` as CNY values; require the 515050/601138 row and convert percentage `5.33` to decimal `0.0533`. Require the named fund annual report and its 2025-03-31 publication date. Do not infer an absent value from a title or a query string.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_industrial_foxconn_forecast_case.py -q`

Expected: PASS with the selector rejecting any drifted record.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/industrial_foxconn_forecast_case.py backend/tests/test_industrial_foxconn_forecast_case.py
git commit -m "feat: select live industrial foxconn forecast sources"
```

### Task 2: Write the complete immutable Case chain and visible run trail

**Files:**
- Modify: `backend/app/services/industrial_foxconn_forecast_case.py`
- Modify: `backend/tests/test_industrial_foxconn_forecast_case.py`
- Create: `backend/app/scripts/run_industrial_foxconn_forecast_case.py`

- [ ] **Step 1: Write the failing ledger-projection test**

```python
def test_runner_creates_a_published_supported_verdict_and_partial_fund_disclosure(session):
    result = run_industrial_foxconn_case(session, FakeGildataClient(), actor="human:researcher")
    expression = MarketExpressionQueries(session).get(
        case_id=result.case_id, as_of=date(2025, 3, 31), cutoff=result.completed_at
    )
    verdicts = ForecastVerdictQueries(session).history(result.case_id, cutoff=result.completed_at)

    assert verdicts.items[0].outcome == "supported"
    assert verdicts.items[0].target.expected_value == 25149000000
    assert verdicts.items[0].actual.observed_value == 23216000000
    assert expression.factors[0].verification.outcome == "supported"
    assert any(item.metric_name == "云计算收入" for item in expression.factors)
    assert expression.fund_exposure[0].fund_code == "515050"
    assert expression.fund_exposure[0].positions[0].weight == pytest.approx(0.0533)
    assert expression.fund_exposure[0].positions[0].coverage_status == "partial"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_industrial_foxconn_forecast_case.py::test_runner_creates_a_published_supported_verdict_and_partial_fund_disclosure -q`

Expected: FAIL because no runner creates the Case-scoped records.

- [ ] **Step 3: Implement one idempotent runner**

```python
def run_industrial_foxconn_case(
    session: Session, client: Any, *, actor: str
) -> IndustrialFoxconnCaseRun:
    """Freeze selected provider responses and append one auditable Case run."""
```

The runner must:

1. Find or create the Case by exact title `工业富联 2024 年利润预测与 AI 产品验证`, scoped to 2024.
2. Freeze all four raw provider responses with `DocumentService`, attach them to the Case, record Gildata source governance with `allow_ai_processing=True` and `allow_display=True`, and create precise `SourceSpan` locators containing tool, query, returned title, metric and period.
3. Append a reviewed forecast `ReportClaim`, a reviewed profit `KeyFactor`, and separate reviewed report claims/key factors for cloud-computing revenue, AI-server growth and 400G/800G switching. Record reviewed `ClaimVerification` entries for these factor metrics from the company annual-report source.
4. Freeze a `ForecastTargetVersion` with `25149000000 CNY`, `within_tolerance`, `0.10`, `2024-01-01` through `2024-12-31`; freeze an `ActualMetricObservation` of `23216000000 CNY`; evaluate with a cutoff after 2025-04-30; append a `confirmed` human verdict with outcome `supported`.
5. Create or reuse Company/Stock `601138.SH`, append a reviewed instrument binding and fundamental impact tied to the annual-report statement. Create or reuse Fund `515050`, and append one `HoldingDisclosure` with `weight=Decimal("0.0533")`, `report_period=2024-12-31`, `published_at=2025-03-31`, source document and provider record, and `coverage_status="partial"`.
6. Create one `ResearchRun` and append ordered `ResearchRunEvent` rows for `provider_capability`, `freeze_source`, `key_factor_verification`, `forecast_evaluation`, `human_verdict`, and `fund_disclosure`. Each payload names tool/query, frozen document ID, written record ID or explicit coverage boundary. End the run as `succeeded`; do not fabricate a background task.
7. Return existing records on rerun, append a short no-duplicate run trail, and never update an immutable target, observation, candidate, verdict or disclosure.

The CLI must load `backend/.env`, require `GILDATA_TOKEN`, use `DATABASE_URL` or explicit `--database-url`, print JSON with `case_id`, `run_id`, verdict outcome, document IDs, fund coverage and `http://127.0.0.1:5173/events/<case_id>/market`.

- [ ] **Step 4: Run focused tests to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_industrial_foxconn_forecast_case.py tests/test_forecast_verdicts.py tests/test_fund_disclosure_sync_api.py -q`

Expected: PASS; test also asserts a cutoff before 2025-04-30 does not expose the verdict and no exposure label contains realtime or causal language.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/industrial_foxconn_forecast_case.py backend/app/scripts/run_industrial_foxconn_forecast_case.py backend/tests/test_industrial_foxconn_forecast_case.py
git commit -m "feat: run live industrial foxconn forecast case"
```

### Task 3: Make the completed live chain scannable in the market page

**Files:**
- Modify: `frontend/src/features/case/MarketExpressionContent.tsx`
- Modify: `frontend/src/styles/research-os-overrides.css`
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`
- Modify: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: Write the failing presentation test**

```tsx
it("presents a published supported forecast with key factors and historical fund exposure", async () => {
  mockMarketExpression({
    factors: industrialFoxconnFactors,
    fund_exposure: [industrialFoxconnFundExposure],
  });
  mockForecastVerdicts({ items: [industrialFoxconnSupportedVerdict] });
  renderMarketPage();

  expect(await screen.findByText("预测在容差内兑现")).toBeVisible();
  expect(screen.getByText("251.49 亿元")).toBeVisible();
  expect(screen.getByText("232.16 亿元")).toBeVisible();
  expect(screen.getByText("云计算收入")).toBeVisible();
  expect(screen.getByText("5.33%")).toBeVisible();
  expect(screen.getByText("覆盖：部分覆盖")).toBeVisible();
  expect(screen.queryByText("实时仓位")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: FAIL because the verdict card only shows raw numeric CNY and does not make the single-case chain scannable.

- [ ] **Step 3: Implement the compact live-case chain**

In `ForecastVerdictPanel`, add a six-step read-only chain label: 研报预测、冻结基线、后续公告、自动判据、人审 verdict、股票/基金披露. Format large CNY values as `251.49 亿元` while retaining raw CNY in the rule details. Reuse existing source links and factor/holding cards; do not add mock data, new automatic decisions, price causality or investment actions.

- [ ] **Step 4: Run frontend verification**

Run: `npm --prefix frontend run typecheck && npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx && npm --prefix frontend run e2e -- --grep "published supported forecast" && npm --prefix frontend run build`

Expected: PASS. The browser path shows frozen-source links, machine-rule details, human reason, key-factor verifications and `partial` disclosure label.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/case/MarketExpressionContent.tsx frontend/src/styles/research-os-overrides.css frontend/src/tests/ResearchOsPages.test.tsx frontend/e2e/research-os.spec.ts
git commit -m "feat: present live forecast verification chain"
```

### Task 4: Execute and verify the actual provider-backed Case

**Files:**
- Modify: `docs/design/2026-08-08-research-operating-system-baseline.md`
- Create: `docs/evaluation/reports/industrial-foxconn-forecast-case.json`

- [ ] **Step 1: Run the live command against the database served by the local API**

Run: `cd backend && .venv/bin/python -m app.scripts.run_industrial_foxconn_forecast_case --actor human:researcher`

Expected: JSON with a non-empty Case ID, run ID, `supported`, expected `25149000000`, actual `23216000000`, fund `515050`, weight `0.0533` and coverage `partial`.

- [ ] **Step 2: Verify the live API and UI, without mock mode**

Run: `cd backend && .venv/bin/python -m pytest tests/test_verify_live_event_ui.py -q && cd ../frontend && npm run e2e -- --grep "industrial foxconn live case"`

Expected: PASS against `/events/<case_id>/market` with no `?client=mock`; direct document links open only current-Case frozen sources and monitor shows each provider and ledger step.

- [ ] **Step 3: Record the verification report and baseline scope**

Write live command IDs, source titles, content hashes, response counts, timestamps, cutoff, test commands and results to the JSON report. Update baseline design to state that Industrial Foxconn is a verified one-case capability, while batch scoring, price causality and real-time holdings remain out of scope.

- [ ] **Step 4: Commit**

```bash
git add docs/design/2026-08-08-research-operating-system-baseline.md docs/evaluation/reports
git commit -m "test: verify live industrial foxconn forecast case"
```
