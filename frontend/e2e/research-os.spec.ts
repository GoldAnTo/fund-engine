import { expect, test } from "@playwright/test";

test.describe("Event-first Research OS", () => {
  test("explicit mock mode renders the Research OS without live API traffic", async ({ page }) => {
    const liveRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/v1/")) liveRequests.push(request.url());
    });

    await page.goto("/events/event-tsm/market?client=mock");

    await expect(page.getByRole("heading", { name: "研报主张、后续验证与基金披露分层呈现" })).toBeVisible();
    expect(liveRequests).toEqual([]);
  });

  test("legacy page addresses cannot reopen the retired prototype UI", async ({ page }) => {
    await page.goto("/workspace?client=mock");

    await expect(page).toHaveURL(/\/events(?:\?client=mock)?$/);
    await expect(page.getByRole("heading", { name: "今天，先推进哪一个判断？" })).toBeVisible();
  });

  test("topbar search opens a matching Case from the current registry", async ({ page }) => {
    await page.goto("/events?client=mock");

    await page.getByLabel("搜索事件、公司、命题或证据").fill("台积");
    const result = page.getByLabel("Case 搜索结果").getByRole("link", { name: /台积电上调 CoWoS 指引后下跌/ });
    await expect(result).toHaveAttribute("href", "/events/event-tsm");
    await result.click();
    await expect(page).toHaveURL(/\/events\/event-tsm(?:\?client=mock)?$/);
  });

  test("research dispatch keeps priority, human review and system activity visible", async ({ page }) => {
    await page.route("**/api/v1/research-runs/active", async (route) => route.fulfill({ json: {
      items: [{ run_id: "run-alphabet", case_id: "event-alphabet", case_title: "Alphabet 财报超预期后股价下跌", status: "running", stage: "retrieve", updated_at: "2026-08-09T00:00:00Z", processed_count: 3, next_action: "查看本次运行", scope: { trigger: "manual", monitor_version_id: "monitor-1", factor_ids: ["factor-1"], allowed_source_types: ["licensed_provider"], budget: 20 } }], next_cursor: null, has_more: false,
    } }));
    await page.goto("/?client=mock");

    await expect(page.getByRole("heading", { name: "今天，先推进哪一个判断？" })).toBeVisible();
    await expect(page.getByText("当前优先")).toBeVisible();
    await expect(page.getByRole("main").getByText("研究网络")).toBeVisible();
    await expect(page.getByRole("region", { name: "系统正在运行" })).toContainText("系统正在运行 · review");
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
    await expect(page.getByRole("button", { name: "建立 Case，进入资料核验" })).toBeEnabled();
  });

  test("new strict Case opens its own intake workbench before any run starts", async ({ page }) => {
    await page.goto("/events/new?client=mock");

    await page.getByLabel("事件原始输入").fill("公司上调资本开支指引，盘后股价下跌。");
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await page.getByRole("button", { name: "建立 Case，进入资料核验" }).click();

    await expect(page).toHaveURL(/\/events\/event-created-1$/);
    await expect(page.getByRole("heading", { name: "公司上调资本开支指引，盘后股价下跌。" })).toBeVisible();
    await expect(page.getByText("无后台运行")).toBeVisible();
    const inspectSource = page.getByRole("link", { name: "核验冻结原文" });
    await expect(inspectSource).toHaveAttribute("href", "/events/event-created-1/documents");
    await inspectSource.click();
    await expect(page).toHaveURL(/\/events\/event-created-1\/documents$/);
    await expect(page.getByRole("heading", { name: "事件原始材料快照" })).toBeVisible();
    await expect(page.getByRole("blockquote").filter({ hasText: "公司上调资本开支指引，盘后股价下跌。" })).toBeVisible();
  });

  test("mock recovery keeps the original and opens its frozen supplement in the same Case", async ({ page }) => {
    await page.goto("/events/event-tsm/documents?client=mock");

    await page.getByRole("button", { name: "从冻结资料提取候选" }).click();
    await expect(page.getByRole("button", { name: "继续补充原 Case" })).toBeVisible();
    await page.getByRole("button", { name: "继续补充原 Case" }).click();
    await page.getByLabel("补充正文").fill("公司在第 3 页明确上调全年资本开支指引。");
    await page.getByLabel("声称页码或位置").fill("第 3 页");
    await page.getByRole("button", { name: "冻结补充正文" }).click();

    await expect(page.getByRole("heading", { name: /补充正文/ })).toBeVisible();
    await expect(page.getByRole("blockquote").filter({ hasText: "公司在第 3 页明确上调全年资本开支指引。" })).toBeVisible();
    await page.getByRole("button", { name: "从冻结资料提取候选" }).click();
    await expect(page.getByText(/创建 1 条待人工审核的原子陈述/)).toBeVisible();
    await page.getByRole("link", { name: "进入证据审核 →" }).click();
    await expect(page.getByText("公司在第 3 页明确上调全年资本开支指引。").last()).toBeVisible();
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

    await expect(page.getByRole("button", { name: "document 冻结公司披露 已进入 Case 图谱" })).toBeVisible();
    await expect(page.getByRole("button", { name: "proposal 自由现金流承压持续 AI 候选，未经人工复核" })).toBeVisible();
    await page.getByRole("button", { name: "document 冻结公司披露 已进入 Case 图谱" }).click();
    await expect(page.getByText("可追溯检查器")).toBeVisible();
    await expect(page.getByText("已审核对象")).toBeVisible();
    await expect(page.getByText("公司上调全年资本开支指引，同时市场关注自由现金流承压。")).toBeVisible();
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
    await expect(page.getByText("monitor-event-tsm-v1", { exact: true })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "运行详情" })).toContainText("company_disclosure");
  });

  test("saving a monitor configuration immediately shows the new effective version", async ({ page }) => {
    const initial = { id: "monitor-v1", version: 1, status: "active", frequency: "weekday_08_30", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], next_verification_event: "下一次财报", budget: 12, changed_by: "human:researcher", change_reason: "初始配置", created_at: "2026-08-09T00:00:00Z" };
    const saved = { ...initial, id: "monitor-v2", version: 2, allowed_source_types: ["company_disclosure", "licensed_provider"], next_verification_event: "下一次财报后补证", change_reason: "补充授权来源" };
    await page.route("**/api/v1/research-cases/event-tsm/monitor", async (route) => route.fulfill({ json: route.request().method() === "PUT" ? saved : { monitor: initial, latest_run: null, confirmed_factors: [{ id: "factor-1", statement: "资本开支" }] } }));
    await page.goto("/events/event-tsm/monitor/config?client=mock");

    await expect(page.getByText("当前生效版本 v1")).toBeVisible();
    await page.getByLabel("授权数据源").check();
    await page.getByLabel("下一验证事件").fill("下一次财报后补证");
    await page.getByLabel("新版本变更原因").fill("补充授权来源");
    await page.getByRole("button", { name: "保存为新监控版本" }).click();

    await expect(page.getByText("当前生效版本 v2")).toBeVisible();
    await expect(page.getByText(/已保存监控版本 v2/)).toBeVisible();
    await expect(page.getByText(/licensed_provider/).last()).toBeVisible();
  });

  test("global network keeps reviewed Case relations separate from AI candidates", async ({ page }) => {
    await page.route("**/api/v1/event-research/network", async (route) => route.fulfill({ json: {
      reviewed_relations: [{ id: "relation-1", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, relation_type: "shared_driver", reason: "共同验证资本开支预期差", created_by: "human:researcher", review_state: "reviewed", created_at: "2026-08-09T00:00:00Z" }],
      candidate_relations: [{ id: "relation-2", source_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, target_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, relation_type: "potential_conflict", reason: "候选解释可能冲突", created_by: "ai:relation-proposal", review_state: "machine_generated", created_at: "2026-08-09T00:00:00Z" }],
    } }));
    await page.goto("/network?client=mock");

    await expect(page.getByRole("heading", { name: "跨 Case 研究网络" })).toBeVisible();
    await expect(page.getByText("均需核验上游资本开支节奏")).toBeVisible();
    await expect(page.getByText("AI 候选，未经人工复核").first()).toBeVisible();
    await page.getByRole("link", { name: "TSM 资本开支与自由现金流验证" }).first().click();
    await expect(page).toHaveURL(/\/events\/event-tsm/);
  });

  test("global monitoring keeps a terminal run and its frozen scope visible", async ({ page }) => {
    await page.route("**/api/v1/research-runs/run-tsm/events", async (route) => route.fulfill({ json: {
      run_id: "run-tsm", items: [{ seq: 1, status: "recorded", stage: "scope", message: "冻结本次范围", details: { allowed_source_types: ["company_disclosure"] }, created_at: "2026-08-09T00:00:00Z" }, { seq: 2, status: "failed", stage: "failed", message: "授权来源返回失败", details: { stop_reason: "task_failed" }, created_at: "2026-08-09T00:04:00Z" }], next_cursor: null, has_more: false,
    } }));
    await page.route("**/api/v1/research-runs", async (route) => route.fulfill({ json: {
      items: [{ run_id: "run-tsm", case_id: "event-tsm", case_title: "台积电 Case", status: "failed", stage: "failed", created_at: "2026-08-09T00:00:00Z", updated_at: "2026-08-09T00:04:00Z", processed_count: 3, stop_reason: "task_failed", next_action: "查看失败原因", scope: { trigger: "schedule", monitor_version_id: "monitor-v2", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], budget: 12 } }], next_cursor: null, has_more: false,
    } }));
    await page.goto("/monitoring?client=mock");

    await expect(page.getByRole("heading", { name: "全局运行与监控" })).toBeVisible();
    await expect(page.getByText(/每 15 秒自动刷新/)).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新运行档案" })).toBeVisible();
    await expect(page.getByRole("main")).toContainText("company_disclosure");
    await expect(page.getByRole("main")).toContainText("monitor-event-tsm-v1");
    await expect(page.getByRole("main")).toContainText("awaiting_review");
    await page.getByRole("main").getByRole("button", { name: "展开本次运行记录" }).click();
    await expect(page.getByRole("complementary", { name: "全局运行记录" })).toContainText("已按许可读取候选资料");
    await page.getByRole("button", { name: "关闭全局运行记录" }).click();
    await page.getByRole("main").getByRole("link", { name: "查看运行详情", exact: true }).click();
    await expect(page).toHaveURL(/\/events\/event-tsm\/monitor/);
  });

  test("market expression does not turn observations or disclosed holdings into causal claims", async ({ page }) => {
    await page.route("**/api/v1/research-cases/event-tsm/market-expression", async (route) => route.fulfill({ json: {
      case_id: "event-tsm", as_of: "2026-08-09", cutoff: "2026-08-09T23:59:59Z",
      claims: [{ id: "claim-1", text: "订单将增长", claim_kind: "research_opinion", asserted_period: null, asserted_by: "某券商", reviewed_by: "human:researcher", review_reason: "已审核", reviewed_at: "2026-08-09T00:00:00Z", source: { source_statement_id: "statement-1", document_title: "研报", source_url: "https://example.test/report", locator: { page: 12 }, available_at: "2026-08-09T00:00:00Z", permission_status: "not_recorded" } }],
      factors: [{ id: "factor-1", thesis_id: "factor-1", report_claim_id: "claim-1", name: "订单转化", expected_direction: "positive", metric_name: "订单金额", allowed_source_types: ["company_disclosure"], verification_window_start: "2026-07-01", verification_window_end: "2026-10-31", support_condition: "订单增长", refutation_condition: "订单下降", next_verification_event: "财报", reviewed_by: "human:researcher", review_reason: "已审核", reviewed_at: "2026-08-09T00:00:00Z", verification: { outcome: "supported", rationale: "披露支持", reviewed_by: "human:researcher", reviewed_at: "2026-08-09T00:00:00Z", source: { source_statement_id: "statement-1", document_title: "研报", source_url: "https://example.test/report", locator: { page: 12 }, available_at: "2026-08-09T00:00:00Z", permission_status: "not_recorded" } } }],
      fundamentals: [], market_observations: [{ id: "observation-1", key_factor_id: "factor-1", stock_id: "stock-1", stock_code: "688000.SH", stock_name: "供应链公司", event_at: "2026-08-01T09:30:00Z", available_at: "2026-08-09T00:00:00Z", window_label: "T0 至 T+5", benchmark: "中证全指", price_source: "licensed_provider", relative_return: 0.034, reviewed_by: "human:researcher", review_reason: "仅观测", reviewed_at: "2026-08-09T00:00:00Z" }],
      fund_exposure: [{ fund_id: "fund-1", fund_code: "000001", fund_name: "示例成长基金", disclosed_exposure: 0.056, positions: [{ stock_id: "stock-1", stock_code: "688000.SH", stock_name: "供应链公司", weight: 0.056, report_period: "2026-06-30", published_at: "2026-07-20T00:00:00Z", acquired_at: "2026-08-09T00:00:00Z", source: "licensed_provider", coverage_status: "not_recorded", freshness_status: "unknown" }] }],
    } }));
    await page.goto("/events/event-tsm/market?client=mock");

    await expect(page.getByRole("heading", { name: "研报主张、后续验证与基金披露分层呈现" })).toBeVisible();
    await expect(page.getByRole("link", { name: "定位到冻结原文" })).toHaveAttribute("href", "/events/event-tsm/documents?document=doc-demo-capex");
    await expect(page.getByRole("button", { name: /尚未到验证时点/ })).toBeVisible();
    await expect(page.getByText("这是市场观测，不自动表述为研报或因素造成。")).toBeVisible();
    await expect(page.getByText(/报告期 2026-06-30/)).toBeVisible();
    await expect(page.getByRole("button", { name: "立即补证此因素" })).toBeEnabled();
    await page.getByRole("button", { name: "立即补证此因素" }).click();
    await expect(page).toHaveURL(/\/events\/event-tsm\/monitor/);
  });

  test("published Case exposes immutable conclusion versions instead of implying an automatic rewrite", async ({ page }) => {
    await page.goto("/events/event-published/history?client=mock");

    await expect(page.getByRole("heading", { name: "结论版本与人工发布边界" })).toBeVisible();
    await expect(page.getByText("AI 草案，未发布")).toBeVisible();
    await expect(page.getByText("人工发布", { exact: true })).toBeVisible();
    await expect(page.getByRole("main")).toContainText("新的材料只能进入待审流程，不能自动改写这里的任何结论。");
  });

  test("published Case starts a successor only from a selected frozen document and stated reason", async ({ page }) => {
    await page.goto("/events/event-published/documents?client=mock");

    await expect(page.getByRole("heading", { name: "以此冻结版本重新核验" })).toBeVisible();
    const start = page.getByRole("button", { name: "以此资料启动重新研究" });
    await expect(start).toBeDisabled();
    await page.getByLabel("重新研究原因").fill("新披露需要核验既有判断");
    await start.click();
    await expect(page.getByText(/已创建后继运行/)).toBeVisible();
    await expect(page.getByText(/此前发布结论未被改写/)).toBeVisible();
  });

  test("published Case records a no-change decision for newly frozen material without a hidden run", async ({ page }) => {
    await page.goto("/events/event-published/documents?client=mock");

    await page.getByLabel("新增材料正文").fill("新增研报重复既有订单判断，未给出新指标。");
    await page.getByRole("radio", { name: /记录为不改变当前判断/ }).check();
    await page.getByLabel("新材料决定理由").fill("没有新增可核验指标或反证条件。");
    await page.getByRole("button", { name: "冻结材料并记录不改变判断" }).click();
    await expect(page.getByText(/记录“不改变当前判断”的人工决定/)).toBeVisible();
    await expect(page.getByText(/已创建后继运行/)).toHaveCount(0);
  });

  test("a Case keeps its own reviewed associations separate from AI candidates", async ({ page }) => {
    await page.route("**/api/v1/event-research/event-tsm/relations", async (route) => route.fulfill({ json: {
      reviewed_relations: [{ id: "relation-1", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, relation_type: "shared_driver", reason: "共同验证资本开支", created_by: "human:researcher", review_state: "reviewed", created_at: "2026-08-09T00:00:00Z" }],
      candidate_relations: [{ id: "relation-2", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-other", title: "候选 Case", lifecycle_status: "researching" }, relation_type: "potential_conflict", reason: "等待人工核对", created_by: "ai:relation-proposal", review_state: "machine_generated", created_at: "2026-08-09T00:00:00Z" }],
    } }));
    await page.goto("/events/event-tsm/relations?client=mock");

    await expect(page.getByRole("heading", { name: "只显示与这个 Case 直接相连的研究" })).toBeVisible();
    await expect(page.getByText("不得自动进入本 Case")).toBeVisible();
    await page.getByRole("link", { name: "AI 服务器订单验证" }).first().click();
    await expect(page).toHaveURL(/\/events\/event-ai-server/);
  });
});
