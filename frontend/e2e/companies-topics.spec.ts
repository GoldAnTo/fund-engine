import { test, expect } from "@playwright/test";

// 公司研究（/companies · 设计图 10）与主题研究（/topics · 设计图 9）
// 三栏布局：左 280 目录 + 中 内容 + 右 320 固定证据检查器。
// e2e 套件只读断言；写入闭环在 vitest/mock 层覆盖。
// 路由兼容：/companies/:id 与 /topics/:tag 自动重定向到主路由 + ?id / ?tag。

test.describe("Company research (设计图 10)", () => {
  test("company list shows the directory, header meta and main area", async ({
    page,
  }) => {
    await page.goto("/companies");
    const screen = page.getByTestId("company-list-screen");
    await expect(screen).toBeVisible();
    // 左栏：标题 + 搜索框 + 至少 1 行公司
    const dir = page.getByTestId("company-list-directory");
    await expect(dir).toBeVisible();
    await expect(
      page.getByTestId("company-list-filter-input"),
    ).toBeVisible();
    const rows = page.locator(".company-list__dir-item");
    expect(await rows.count()).toBeGreaterThan(0);
    // 主区：5 段（角色 / 关联命题 / 估值 / 持仓 / 路径）
    await expect(page.getByTestId("company-list-roles")).toBeVisible();
    await expect(page.getByTestId("company-list-theses")).toBeVisible();
    await expect(page.getByTestId("company-list-valuations")).toBeVisible();
    await expect(page.getByTestId("company-list-holders")).toBeVisible();
    await expect(page.getByTestId("company-list-path")).toBeVisible();
    // 右栏：固定证据检查器
    await expect(page.getByTestId("company-list-inspector-card")).toBeVisible();
  });

  test("clicking a company in the left directory updates the main area", async ({
    page,
  }) => {
    await page.goto("/companies");
    // 默认选中 co-nvda；点击第二行（如有）切换
    const firstRow = page.locator(".company-list__dir-item").first();
    const secondRow = page.locator(".company-list__dir-item").nth(1);
    if (await secondRow.count() > 0) {
      await secondRow.locator("button").click();
    } else {
      await firstRow.locator("button").click();
    }
    // URL 包含 ?id=
    await expect(page).toHaveURL(/\/companies\?id=/);
    // 页面 title 反映公司
    const title = await page.locator("h1").first().textContent();
    expect(title).toBeTruthy();
  });

  test("company list filter narrows the directory", async ({ page }) => {
    await page.goto("/companies");
    const filter = page.getByTestId("company-list-filter-input");
    await expect(filter).toBeVisible();
    await filter.fill("zzzz-no-such-company");
    // 空态
    await expect(page.getByText("未匹配到公司。")).toBeVisible();
  });

  test("pinned inspector card shows the current thesis + source span", async ({
    page,
  }) => {
    await page.goto("/companies?id=co-nvda");
    const card = page.getByTestId("company-list-inspector-card");
    await expect(card).toBeVisible();
    // 标题、id、摘录都渲染
    await expect(page.getByTestId("company-inspector-title")).toBeVisible();
    await expect(page.getByTestId("company-inspector-excerpt")).toBeVisible();
  });

  test("/companies/:id legacy URL redirects to /companies?id=...", async ({
    page,
  }) => {
    await page.goto("/companies/co-nvda");
    await expect(page).toHaveURL(/\/companies\?id=co-nvda/);
    await expect(page.getByTestId("company-list-screen")).toBeVisible();
  });
});

test.describe("Topic research / 横切主题 (设计图 9)", () => {
  test("topic list shows the directory, banner, main area and fixed inspector", async ({
    page,
  }) => {
    await page.goto("/topics");
    const screen = page.getByTestId("topic-list-screen");
    await expect(screen).toBeVisible();
    // 顶部警告条
    await expect(page.getByTestId("topic-list-banner")).toBeVisible();
    // 左栏目录
    const dir = page.getByTestId("topic-list-directory");
    await expect(dir).toBeVisible();
    const rows = page.locator(".topic-list__dir-item");
    expect(await rows.count()).toBeGreaterThan(0);
    // 主区四段
    await expect(page.getByTestId("topic-list-cases")).toBeVisible();
    await expect(page.getByTestId("topic-list-roles")).toBeVisible();
    await expect(page.getByTestId("topic-list-exposure")).toBeVisible();
    await expect(page.getByTestId("topic-list-path")).toBeVisible();
    // 右栏固定证据检查器
    await expect(page.getByTestId("topic-list-inspector-card")).toBeVisible();
  });

  test("clicking a topic in the left directory updates the main area", async ({
    page,
  }) => {
    await page.goto("/topics");
    const firstRow = page.locator(".topic-list__dir-item").first();
    await firstRow.locator("button").click();
    await expect(page).toHaveURL(/\/topics\?tag=/);
  });

  test("topic path chain renders 5 nodes for the pinned topic", async ({
    page,
  }) => {
    await page.goto("/topics");
    const chain = page.getByTestId("topic-list-path-chain");
    await expect(chain).toBeVisible();
    const nodes = page.locator('[data-testid^="topic-path-node-"]');
    expect(await nodes.count()).toBe(5);
  });

  test("pinned inspector card shows the current thesis + source excerpt", async ({
    page,
  }) => {
    await page.goto("/topics?tag=AI 算力基础设施");
    const card = page.getByTestId("topic-list-inspector-card");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("topic-inspector-title")).toBeVisible();
    await expect(page.getByTestId("topic-inspector-excerpt")).toBeVisible();
  });

  test("/topics/:tag legacy URL redirects to /topics?tag=...", async ({
    page,
  }) => {
    await page.goto("/topics/AI 算力基础设施");
    await expect(page).toHaveURL(/\/topics\?tag=/);
    await expect(page.getByTestId("topic-list-screen")).toBeVisible();
  });
});
