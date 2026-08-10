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

  test("fund disclosure opens its Case-owned frozen source instead of a generic holding page", async ({ page }) => {
    await page.goto("/events/event-tsm/market?client=mock");

    await page
      .getByRole("link", { name: "定位到冻结持仓来源（版本 doc-fund-holdings-2026q2）" })
      .click();

    await expect(page).toHaveURL(/\/events\/event-tsm\/documents\?document=doc-fund-holdings-2026q2/);
    await expect(page.getByRole("heading", { name: "原文资料" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "演示成长基金 2026 年第二季度持仓披露" })).toBeVisible();
    await expect(page.getByText("截至 2026 年 6 月 30 日，台积电占基金资产净值 3.80%。")).toBeVisible();
  });

  test("market expression drills from a reviewed stock to its Case-scoped fund disclosure", async ({ page }) => {
    await page.goto("/events/event-tsm/market?client=mock");

    await page.getByRole("link", { name: "台积电" }).first().click();
    await expect(page.getByRole("heading", { name: "台积电 · 股票研究档案" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "市场观测" })).toBeVisible();
    await page.getByRole("link", { name: "演示成长基金" }).click();
    await expect(page.getByRole("heading", { name: "演示成长基金 · 基金披露档案" })).toBeVisible();
    await expect(page.getByText("命中股票与披露来源")).toBeVisible();
  });

  test("market expression records an explicit instrument link before a fundamental impact", async ({ page }) => {
    await page.goto("/events/event-tsm/market?client=mock");

    await expect(page.getByRole("heading", { name: "关联公司与股票" })).toBeVisible();
    await expect(page.getByText("不会从事件标题或代码自动推断。每一条关联都必须由本 Case 已准入的冻结原文、审核人和理由支持。")).toBeVisible();
    await page.getByRole("button", { name: "关联公司与股票" }).click();
    await page.getByLabel("标的审核理由").fill("冻结原文明确该公司处于本 Case 的传导范围。");
    await page.getByRole("button", { name: "保存已审核标的关联" }).click();
    await expect(page.getByText(/已追加已审核标的关联/)).toBeVisible();

    await page.getByRole("button", { name: "登记基本面传导" }).click();
    await page.getByLabel("传导机制").fill("订单兑现将按履约周期传导为收入增长。");
    await page.getByLabel("传导审核理由").fill("已核对来源、标的绑定和指标口径。");
    await page.getByRole("button", { name: "保存已审核基本面传导" }).click();
    await expect(page.getByText(/已追加已审核基本面传导/)).toBeVisible();

    await page.getByRole("button", { name: "登记市场观测" }).click();
    await expect(page.getByText("市场窗口只记录发生了什么，不表示研报、事件或因素造成价格变化。")).toBeVisible();
    await page.getByLabel("市场观测审核理由").fill("已核对事件时点、资料可得时点、窗口和价格来源。");
    await page.getByRole("button", { name: "保存已审核市场观测" }).click();
    await expect(page.getByText(/已追加已审核市场观测/)).toBeVisible();
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

  test("shows a visible keyboard focus ring for the global research search", async ({ page }) => {
    await page.goto("/events?client=mock");

    const search = page.getByLabel("搜索事件、公司、命题或证据");
    await search.focus();

    await expect(search).toHaveCSS("outline-style", "solid");
  });

  test("stops the active-run pulse when reduced motion is requested", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route("**/api/v1/research-runs/active", async (route) =>
      route.fulfill({
        json: {
          items: [
            {
              run_id: "run-reduced-motion",
              case_id: "event-tsm",
              case_title: "台积电上调 CoWoS 指引后下跌",
              status: "running",
              stage: "retrieve",
              updated_at: "2026-08-09T00:00:00Z",
              processed_count: 3,
              next_action: "查看本次运行",
              scope: {
                trigger: "manual",
                monitor_version_id: "monitor-1",
                factor_ids: ["factor-1"],
                allowed_source_types: ["licensed_provider"],
                budget: 20,
              },
            },
          ],
          next_cursor: null,
          has_more: false,
        },
      }),
    );
    await page.goto("/events?client=mock");

    await expect(page.locator(".ros-run-strip__pulse")).toHaveCSS(
      "animation-name",
      "none",
    );
  });

  test("research dispatch keeps priority, human review and system activity visible", async ({ page }) => {
    await page.route("**/api/v1/research-runs/active", async (route) => route.fulfill({ json: {
      items: [{ run_id: "run-alphabet", case_id: "event-alphabet", case_title: "Alphabet 财报超预期后股价下跌", status: "running", stage: "retrieve", updated_at: "2026-08-09T00:00:00Z", processed_count: 3, next_action: "查看本次运行", scope: { trigger: "manual", monitor_version_id: "monitor-1", factor_ids: ["factor-1"], allowed_source_types: ["licensed_provider"], budget: 20 } }], next_cursor: null, has_more: false,
    } }));
    await page.goto("/?client=mock");

    await expect(page.getByRole("heading", { name: "今天，先推进哪一个判断？" })).toBeVisible();
    await expect(page.getByText("当前优先")).toBeVisible();
    await expect(page.getByRole("main").getByText("研究网络")).toBeVisible();
    await expect(page.getByRole("region", { name: "系统正在运行" })).toContainText("系统正在运行 · 等待审核");
    await expect(page.getByRole("region", { name: "系统正在运行" })).toContainText("授权供应商资料");
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

    const wikiTab = page
      .getByRole("navigation", { name: "Case 页面" })
      .getByRole("link", { name: "Wiki 图谱" });
    await wikiTab.focus();
    await expect(wikiTab).toBeFocused();
    await wikiTab.press("Enter");
    await expect(page).toHaveURL(/\/events\/event-tsm\/wiki/);
    await expect(page.getByRole("heading", { name: "从关系回到冻结原文与审核边界" })).toBeVisible();

    const relationsTab = page
      .getByRole("navigation", { name: "Case 页面" })
      .getByRole("link", { name: "关联研究" });
    await relationsTab.focus();
    await relationsTab.press("Enter");
    await expect(page).toHaveURL(/\/events\/event-tsm\/relations/);
    await expect(page.getByRole("heading", { name: "只显示与这个 Case 直接相连的研究" })).toBeVisible();
  });

  test("event intake keeps the scope confirmation unavailable until the source is read", async ({ page }) => {
    await page.goto("/events/new?client=mock");

    await expect(page.getByRole("heading", { name: "先冻结材料，再决定它属于哪个研究" })).toBeVisible();
    await expect(page.getByText("先识别事件，才能决定它应创建新研究还是归入已有 Case。")).toBeVisible();

    await page.getByLabel("事件原始输入").fill("公司上调资本开支指引，盘后股价下跌。");
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await expect(page.getByLabel("研究问题")).toBeVisible();
    await expect(page.getByRole("button", { name: "建立 Case，进入资料核验" })).toBeEnabled();
  });

  test("licensed-provider intake requires a reproducible record and makes use permissions explicit", async ({ page }) => {
    await page.goto("/events/new?client=mock");

    await page.getByLabel("事件原始输入").fill("供应商研报指出资本开支计划调整。 ");
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await page.getByLabel("来源接入方式").selectOption("licensed_provider");

    await expect(page.getByLabel("供应商名称")).toBeVisible();
    await expect(page.getByLabel("供应商记录 ID")).toBeVisible();
    await expect(page.getByLabel("允许 AI 处理")).not.toBeChecked();
    await expect(page.getByRole("button", { name: "建立 Case，进入资料核验" })).toBeDisabled();

    await page.getByLabel("供应商名称").fill("聚源");
    await page.getByLabel("供应商记录 ID").fill("report-2026-001");
    await page.getByLabel("允许 AI 处理").check();
    await page.getByLabel("允许团队展示").check();
    await expect(page.getByRole("button", { name: "建立 Case，进入资料核验" })).toBeEnabled();
    await page.getByRole("button", { name: "建立 Case，进入资料核验" }).click();
    await page.getByRole("link", { name: "核验冻结原文" }).click();
    await expect(page.getByText("供应商记录")).toBeVisible();
    await expect(page.getByText("聚源 · report-2026-001")).toBeVisible();
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

  test("inbox freezes a material into a chosen active Case without starting a new Case", async ({ page }) => {
    await page.goto("/events/new?client=mock");

    await page.getByLabel("事件原始输入").fill("公司补充说明订单交付节奏，需进入现有 Case 由研究员核验。");
    await page.getByRole("button", { name: "识别事件与研究问题" }).click();
    await page.getByRole("radio", { name: "归入已有 Case" }).check();
    await expect(page.getByLabel("选择目标 Case")).toHaveValue("");
    await expect(page.getByRole("button", { name: "冻结并归入当前 Case" })).toBeDisabled();

    await page.getByLabel("选择目标 Case").selectOption("event-tsm");
    await page.getByRole("button", { name: "冻结并归入当前 Case" }).click();

    await expect(page).toHaveURL(/\/events\/event-tsm\/documents\?document=document-attached-/);
    await expect(page.getByRole("heading", { name: "收件箱新增材料" })).toBeVisible();
    await expect(page.getByRole("blockquote").filter({ hasText: "公司补充说明订单交付节奏" })).toBeVisible();
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
    await expect(page.getByRole("link", { name: "定位到冻结原文" })).toHaveAttribute("href", "/events/event-tsm/documents?document=doc-event-tsm-q2");
    await expect(page.getByLabel("关联审核与时点")).toContainText("审核状态 reviewed");
    await expect(page.getByLabel("关联审核与时点")).toContainText("审核人 human:reviewer");
    await page.getByRole("button", { name: /冻结公司披露.*quoted_by/ }).click();
    await expect(page.getByRole("heading", { name: "关系：冻结公司披露 → 资本开支指引上调" })).toBeVisible();
    await expect(page.getByText("已逐字核对冻结原文与定位。")).toBeVisible();
    await page.getByRole("link", { name: "定位到冻结原文" }).click();
    await expect(page.getByRole("heading", { name: "台积电 2026 年第二季度法说会摘要" })).toBeVisible();
    await page.goBack();
    await page.getByRole("button", { name: "proposal 自由现金流承压持续 AI 候选，未经人工复核" }).click();
    await expect(page.getByRole("link", { name: "审核此候选关系" })).toHaveAttribute("href", "/events/event-tsm/review");
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
    await expect(page.getByRole("complementary", { name: "运行详情" })).toContainText("公司披露");
  });

  test("Case run detail makes exclusions and counts readable instead of hiding them in a worker log", async ({ page }) => {
    await page.goto("/events/event-tsm/monitor?client=mock");

    await page.getByRole("button", { name: "打开运行详情" }).click();
    const drawer = page.getByRole("complementary", { name: "运行详情" });
    await expect(drawer).toContainText("已纳入资料：2");
    await expect(drawer).toContainText("已排除资料：1");
    await expect(drawer).toContainText("排除原因：来源许可不足");
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
    await expect(page.getByText("授权供应商资料").last()).toBeVisible();
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
    await expect(page.getByRole("main")).toContainText("公司披露");
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

  test("market expression registers a reviewed claim only from an admitted frozen source", async ({ page }) => {
    await page.goto("/events/event-tsm/market?client=mock");

    await page.getByRole("button", { name: "选择冻结原文并登记主张" }).click();
    await expect(page.getByLabel("冻结原文陈述")).toBeVisible();
    await expect(page.getByText("公司季度业绩说明", { exact: true })).toBeVisible();
    await expect(page.getByText(/只可选择本 Case 内已准入/)).toBeVisible();
    await page.getByLabel("主张归属").fill("公司管理层");
    await page.getByLabel("主张审核理由").fill("已核对冻结原文、定位与许可范围。");
    await page.getByRole("button", { name: "登记已审核主张" }).click();
    await expect(page.getByText(/已登记已审核主张/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "固定关键因素的验证口径" })).toBeVisible();
  });

  test("market expression records a source-backed human verification instead of inferring it from a run", async ({ page }) => {
    await page.goto("/events/event-tsm/market?client=mock");

    await page.getByRole("button", { name: "登记审核验证" }).click();
    await expect(page.getByLabel("验证来源")).toBeVisible();
    await page.getByLabel("核验说明").fill("冻结披露中的资本开支增长满足已登记支持条件。");
    await page.getByLabel("验证审核理由").fill("已核对原文定位、指标口径和可得时间。");
    await page.getByRole("button", { name: "保存审核验证" }).click();
    await expect(page.getByText(/当前验证：得到支持。冻结披露中的资本开支增长/)).toBeVisible();
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

    await expect(page.getByText("当前已发布结论")).toBeVisible();
    await expect(page.getByText("待冻结的新材料")).toBeVisible();
    await page.getByLabel("新增材料正文").fill("新增研报重复既有订单判断，未给出新指标。");
    await expect(page.getByLabel("已发布结论与新材料对照")).toContainText("新增研报重复既有订单判断，未给出新指标。");
    await page.getByRole("radio", { name: /记录为不改变当前判断/ }).check();
    await page.getByLabel("新材料决定理由").fill("没有新增可核验指标或反证条件。");
    await page.getByRole("button", { name: "冻结材料并记录不改变判断" }).click();
    await expect(page.getByText(/记录“不改变当前判断”的人工决定/)).toBeVisible();
    await expect(page.getByText(/已创建后继运行/)).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "新增待比较材料" })).toBeVisible();
  });

  test("published Case reads an uploaded text snapshot before the human change decision", async ({ page }) => {
    await page.goto("/events/event-published/documents?client=mock");

    await page.getByLabel("新增材料来源接入方式").selectOption("uploaded_file");
    await page.getByLabel("上传新增材料正文文件").setInputFiles({
      name: "published-note.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("公司补充披露订单交付节奏。"),
    });

    await expect(page.getByRole("textbox", { name: "新增材料正文", exact: true })).toHaveValue("公司补充披露订单交付节奏。");
    await expect(page.getByText(/只读取并冻结文本正文快照/)).toBeVisible();
    await expect(page.getByLabel("已发布结论与新材料对照")).toContainText("公司补充披露订单交付节奏。");
  });

  test("freezes an uploaded original into an existing Case and exposes its immutable file record", async ({ page }) => {
    await page.goto("/events/new?client=mock");

    await page.getByLabel("来源接入方式").selectOption("uploaded_file");
    await page.getByLabel("选择上传原件文件").setInputFiles({
      name: "disclosure.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("公司披露本季度订单金额增长。"),
    });
    await expect(page.getByText(/原件待冻结 · disclosure.txt/)).toBeVisible();
    await page.getByLabel("选择原件目标 Case").selectOption("event-tsm");
    await page.getByRole("button", { name: "冻结原件并归入当前 Case" }).click();

    await expect(page.getByRole("heading", { name: "disclosure.txt" })).toBeVisible();
    await expect(page.getByText("文本原件已冻结；解析内容作为定位片段另行展示。")).toBeVisible();
    await expect(page.getByText("原件文件")).toBeVisible();
    await expect(page.getByText(/human:researcher · case_retained/)).toBeVisible();
  });

  test("published Case cannot freeze a provider material without its reproducible record", async ({ page }) => {
    await page.goto("/events/event-published/documents?client=mock");

    await page.getByLabel("新增材料正文").fill("供应商研报补充了一个可能影响结论的指标。");
    await page.getByLabel("新材料决定理由").fill("需要先保留来源许可与供应商记录，再决定是否重新复核。");
    await page.getByLabel("新增材料来源接入方式").selectOption("licensed_provider");

    await expect(page.getByLabel("新增材料供应商名称")).toBeVisible();
    await expect(page.getByLabel("新增材料供应商记录 ID")).toBeVisible();
    await expect(page.getByLabel("新增材料允许 AI 处理")).not.toBeChecked();
    await expect(page.getByRole("button", { name: "冻结材料并纳入重新复核" })).toBeDisabled();

    await page.getByLabel("新增材料供应商名称").fill("聚源");
    await page.getByLabel("新增材料供应商记录 ID").fill("report-2026-002");
    await expect(page.getByRole("button", { name: "冻结材料并纳入重新复核" })).toBeEnabled();
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
