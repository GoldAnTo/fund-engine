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
        { id: "source", kind: "source", label: "冻结公告", properties: { review_state: "reviewed" } },
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
    await expect(page.getByRole("button", { name: "proposal AI 候选 AI 候选，未进入结论" })).toBeVisible();
    await expect(page.getByLabel("Case Wiki 关系")).toContainText("已审核关系");
    await expect(page.getByLabel("Case Wiki 关系")).toContainText("AI 候选，未经复核");
    await page.getByRole("button", { name: "隐藏 AI 候选 1" }).click();
    await expect(page.getByLabel("Case Wiki 关系")).not.toContainText("AI 候选，未经复核");
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
});
