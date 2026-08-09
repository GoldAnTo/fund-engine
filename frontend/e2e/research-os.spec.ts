import { expect, test } from "@playwright/test";

test.describe("Event-first Research OS", () => {
  test("research dispatch keeps priority, human review and system activity visible", async ({ page }) => {
    await page.route("**/api/v1/research-runs/active", async (route) => route.fulfill({ json: {
      items: [{ run_id: "run-alphabet", case_id: "event-alphabet", case_title: "Alphabet 财报超预期后股价下跌", status: "running", stage: "retrieve", updated_at: "2026-08-09T00:00:00Z", processed_count: 3, next_action: "查看本次运行", scope: { trigger: "manual", monitor_version_id: "monitor-1", factor_ids: ["factor-1"], allowed_source_types: ["licensed_provider"], budget: 20 } }], next_cursor: null, has_more: false,
    } }));
    await page.goto("/?client=mock");

    await expect(page.getByRole("heading", { name: "今天，先推进哪一个判断？" })).toBeVisible();
    await expect(page.getByText("当前优先")).toBeVisible();
    await expect(page.getByRole("main").getByText("研究网络")).toBeVisible();
    await expect(page.getByRole("region", { name: "系统正在运行" })).toContainText("系统正在运行 · retrieve");
    await expect(page.getByRole("region", { name: "系统正在运行" })).toContainText("licensed_provider");
    await expect(page.locator(".ros-event-row").first()).toBeVisible();
  });

  test("Case conclusion has one explicit next action on a narrow workbench", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/events/event-tsm?client=mock");

    await expect(page.getByRole("heading", { name: /尚不能下结论/ })).toBeVisible();
    const action = page.getByRole("link", { name: "进入审核" });
    await expect(action).toHaveCount(1);
    await expect(action).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await action.click();
    await expect(page).toHaveURL(/\/events\/event-tsm\/review/);
    await expect(page.getByRole("heading", { name: /条待审核关系/ })).toBeVisible();
  });

  test("event intake keeps the scope confirmation unavailable until the source is read", async ({ page }) => {
    await page.goto("/events/new?client=mock");

    await expect(page.getByRole("heading", { name: "先固定研究范围，再让系统开始工作" })).toBeVisible();
    await expect(page.getByText("先识别事件，才能编辑研究问题与关键因素。")).toBeVisible();

    await page.getByLabel("事件原始输入").fill("公司上调资本开支指引，盘后股价下跌。");
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await expect(page.getByLabel("研究问题")).toBeVisible();
    await expect(page.getByRole("button", { name: "创建事件 Case" })).toBeEnabled();
  });

  test("Case Wiki renders reviewed and candidate relationships as inspectable paths", async ({ page }) => {
    await page.route("**/api/v1/research-cases/event-tsm/graph?research_mode=true", async (route) => route.fulfill({ json: {
      schema_version: "graph/v1", basis: { as_of: "2026-08-08T00:00:00Z", available_at: "2026-08-08T00:00:00Z" }, page: { items: 3, next_cursor: null }, paths: [],
      nodes: [
        { id: "source", kind: "source", label: "冻结公告", properties: { review_state: "reviewed", verbatim_text: "冻结原文：资本开支指引已上调。" } },
        { id: "factor", kind: "factor", label: "资本开支", properties: { review_state: "reviewed" } },
        { id: "proposal", kind: "proposal", label: "AI 候选", properties: { review_state: "machine_generated" } },
      ],
      edges: [
        { id: "edge-reviewed", semantic_kind: "supports", source: "source", target: "factor", review_state: "reviewed", source_refs: [], properties: {} },
        { id: "edge-candidate", semantic_kind: "proposes", source: "factor", target: "proposal", review_state: "machine_generated", source_refs: [], properties: {} },
      ],
    } }));
    await page.goto("/events/event-tsm/wiki?client=mock");

    await expect(page.getByRole("button", { name: "source 冻结公告 已进入 Case 图谱" })).toBeVisible();
    await expect(page.getByRole("button", { name: "proposal AI 候选 AI 候选，未经人工复核" })).toBeVisible();
    await page.getByRole("button", { name: "source 冻结公告 已进入 Case 图谱" }).click();
    await expect(page.getByText("可追溯检查器")).toBeVisible();
    await expect(page.getByText("已审核对象")).toBeVisible();
    await expect(page.getByText("冻结原文：资本开支指引已上调。")).toBeVisible();
  });

  test("immediate replenishment starts from a visible frozen monitor scope", async ({ page }) => {
    await page.route("**/api/v1/research-cases/event-tsm/monitor", async (route) => route.fulfill({ json: {
      monitor: { id: "monitor-v2", version: 2, status: "active", frequency: "weekday_08_30", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], next_verification_event: "下一次财报", budget: 12, changed_by: "human:researcher", change_reason: "补充公司披露", created_at: "2026-08-08T00:00:00Z" },
      latest_run: null, confirmed_factors: [{ id: "factor-1", statement: "资本开支" }],
    } }));
    await page.route("**/api/v1/research-runs/**/events", async (route) => route.fulfill({ json: { items: [{ seq: 1, stage: "scope", status: "recorded", message: "冻结本次范围", details: { trigger: "manual", monitor_version_id: "monitor-v2", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], budget: 12 }, created_at: "2026-08-08T00:00:00Z" }] } }));
    await page.goto("/events/event-tsm/monitor?client=mock");

    const start = page.getByRole("button", { name: "立即补证一次" });
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page.getByRole("complementary", { name: "运行详情" })).toBeVisible();
    await expect(page.getByText("monitor-v2", { exact: true })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "运行详情" }).getByText("company_disclosure", { exact: true })).toBeVisible();
  });

  test("global network keeps reviewed Case relations separate from AI candidates", async ({ page }) => {
    await page.route("**/api/v1/event-research/network", async (route) => route.fulfill({ json: {
      reviewed_relations: [{ id: "relation-1", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, relation_type: "shared_driver", reason: "共同验证资本开支预期差", created_by: "human:researcher", review_state: "reviewed", created_at: "2026-08-09T00:00:00Z" }],
      candidate_relations: [{ id: "relation-2", source_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, target_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, relation_type: "potential_conflict", reason: "候选解释可能冲突", created_by: "ai:relation-proposal", review_state: "machine_generated", created_at: "2026-08-09T00:00:00Z" }],
    } }));
    await page.goto("/network?client=mock");

    await expect(page.getByRole("heading", { name: "跨 Case 研究网络" })).toBeVisible();
    await expect(page.getByText("共同验证资本开支预期差")).toBeVisible();
    await expect(page.getByText("AI 候选，未经人工复核").first()).toBeVisible();
    await page.getByRole("link", { name: "台积电 Case" }).first().click();
    await expect(page).toHaveURL(/\/events\/event-tsm/);
  });

  test("global monitoring shows the frozen scope behind every active run", async ({ page }) => {
    await page.route("**/api/v1/research-runs/active", async (route) => route.fulfill({ json: {
      items: [{ run_id: "run-tsm", case_id: "event-tsm", case_title: "台积电 Case", status: "running", stage: "retrieve", updated_at: "2026-08-09T00:00:00Z", processed_count: 3, next_action: "查看本次运行", scope: { trigger: "schedule", monitor_version_id: "monitor-v2", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], budget: 12 } }], next_cursor: null, has_more: false,
    } }));
    await page.goto("/monitoring?client=mock");

    await expect(page.getByRole("heading", { name: "全局运行与监控" })).toBeVisible();
    await expect(page.getByRole("main")).toContainText("company_disclosure");
    await expect(page.getByRole("main")).toContainText("monitor-v2");
    await page.getByRole("main").getByRole("link", { name: "查看本次运行", exact: true }).click();
    await expect(page).toHaveURL(/\/events\/event-tsm\/monitor/);
  });

  test("market expression does not turn observations or disclosed holdings into causal claims", async ({ page }) => {
    await page.route("**/api/v1/research-cases/event-tsm/market-expression", async (route) => route.fulfill({ json: {
      case_id: "event-tsm", as_of: "2026-08-09", cutoff: "2026-08-09T23:59:59Z",
      claims: [{ id: "claim-1", text: "订单将增长", claim_kind: "research_opinion", asserted_period: null, asserted_by: "某券商", reviewed_by: "human:researcher", review_reason: "已审核", reviewed_at: "2026-08-09T00:00:00Z", source: { source_statement_id: "statement-1", document_title: "研报", source_url: "https://example.test/report", locator: { page: 12 }, available_at: "2026-08-09T00:00:00Z", permission_status: "not_recorded" } }],
      factors: [{ id: "factor-1", report_claim_id: "claim-1", name: "订单转化", expected_direction: "positive", metric_name: "订单金额", allowed_source_types: ["company_disclosure"], verification_window_start: "2026-07-01", verification_window_end: "2026-10-31", support_condition: "订单增长", refutation_condition: "订单下降", next_verification_event: "财报", reviewed_by: "human:researcher", review_reason: "已审核", reviewed_at: "2026-08-09T00:00:00Z", verification: { outcome: "supported", rationale: "披露支持", reviewed_by: "human:researcher", reviewed_at: "2026-08-09T00:00:00Z", source: { source_statement_id: "statement-1", document_title: "研报", source_url: "https://example.test/report", locator: { page: 12 }, available_at: "2026-08-09T00:00:00Z", permission_status: "not_recorded" } } }],
      fundamentals: [], market_observations: [{ id: "observation-1", key_factor_id: "factor-1", stock_id: "stock-1", stock_code: "688000.SH", stock_name: "供应链公司", event_at: "2026-08-01T09:30:00Z", available_at: "2026-08-09T00:00:00Z", window_label: "T0 至 T+5", benchmark: "中证全指", price_source: "licensed_provider", relative_return: 0.034, reviewed_by: "human:researcher", review_reason: "仅观测", reviewed_at: "2026-08-09T00:00:00Z" }],
      fund_exposure: [{ fund_id: "fund-1", fund_code: "000001", fund_name: "示例成长基金", disclosed_exposure: 0.056, positions: [{ stock_id: "stock-1", stock_code: "688000.SH", stock_name: "供应链公司", weight: 0.056, report_period: "2026-06-30", published_at: "2026-07-20T00:00:00Z", acquired_at: "2026-08-09T00:00:00Z", source: "licensed_provider", coverage_status: "not_recorded", freshness_status: "unknown" }] }],
    } }));
    await page.goto("/events/event-tsm/market?client=mock");

    await expect(page.getByRole("heading", { name: "研报主张、后续验证与基金披露分层呈现" })).toBeVisible();
    await expect(page.getByText("得到支持")).toBeVisible();
    await expect(page.getByText("这是市场观测，不自动表述为研报或因素造成。")).toBeVisible();
    await expect(page.getByText(/报告期 2026-06-30/)).toBeVisible();
  });

  test("a Case keeps its own reviewed associations separate from AI candidates", async ({ page }) => {
    await page.route("**/api/v1/event-research/event-tsm/relations", async (route) => route.fulfill({ json: {
      reviewed_relations: [{ id: "relation-1", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, relation_type: "shared_driver", reason: "共同验证资本开支", created_by: "human:researcher", review_state: "reviewed", created_at: "2026-08-09T00:00:00Z" }],
      candidate_relations: [{ id: "relation-2", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-other", title: "候选 Case", lifecycle_status: "researching" }, relation_type: "potential_conflict", reason: "等待人工核对", created_by: "ai:relation-proposal", review_state: "machine_generated", created_at: "2026-08-09T00:00:00Z" }],
    } }));
    await page.goto("/events/event-tsm/relations?client=mock");

    await expect(page.getByRole("heading", { name: "只显示与这个 Case 直接相连的研究" })).toBeVisible();
    await expect(page.getByText("不得自动进入本 Case")).toBeVisible();
    await page.getByRole("link", { name: "Alphabet Case" }).click();
    await expect(page).toHaveURL(/\/events\/event-alphabet/);
  });
});
