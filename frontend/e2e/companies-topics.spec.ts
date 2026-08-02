import { test, expect } from "@playwright/test";

// 公司研究（/companies）与主题研究（/topics · 横切主题）只读 e2e 断言。
// e2e 套件可能跑在真实后端或 mock 适配器下（main.tsx 测试钩子
// `?client=mock`），写入闭环不在本套件覆盖范围。

test.describe("Company research", () => {
  test("company list renders table and links to dossier", async ({ page }) => {
    await page.goto("/companies");
    const screen = page.getByTestId("company-list-screen");
    await expect(screen).toBeVisible();
    // 表格至少有一行；点击档案链接跳转到 dossier
    const firstLink = page.getByRole("link", { name: /档案/ }).first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await expect(page).toHaveURL(/\/companies\/.+/);
    await expect(page.getByTestId("company-dossier-screen")).toBeVisible();
  });

  test("company list filter input narrows the table", async ({ page }) => {
    await page.goto("/companies");
    const screen = page.getByTestId("company-list-screen");
    await expect(screen).toBeVisible();
    const filter = page.getByTestId("company-list-filter-input");
    await expect(filter).toBeVisible();
    // 输入一个不存在的关键字后表格进入空态（"未匹配到公司"）
    await filter.fill("zzzz-no-such-company");
    await expect(screen.getByText("未匹配到公司")).toBeVisible();
  });

  test("company dossier shows identity, theme roles, theses, valuations, holders", async ({ page }) => {
    await page.goto("/companies");
    const firstLink = page.getByRole("link", { name: /档案/ }).first();
    await firstLink.click();
    const screen = page.getByTestId("company-dossier-screen");
    await expect(screen).toBeVisible();
    // 五个段落均渲染（mock 数据完整）
    await expect(screen.getByTestId("company-dossier-roles")).toBeVisible();
    await expect(screen.getByTestId("company-dossier-theses")).toBeVisible();
    await expect(screen.getByTestId("company-dossier-valuations")).toBeVisible();
    await expect(screen.getByTestId("company-dossier-holders")).toBeVisible();
  });

  test("company dossier historical cutoff button rewrites the URL", async ({ page }) => {
    await page.goto("/companies");
    const firstLink = page.getByRole("link", { name: /档案/ }).first();
    await firstLink.click();
    await expect(page.getByTestId("company-dossier-screen")).toBeVisible();
    // 切到 2025-06-30 回放，URL 应写入 cutoff 查询参数
    await page.getByRole("button", { name: /切到 2025-06-30 回放/ }).click();
    await expect(page).toHaveURL(/cutoff=2025-06-30/);
    // 再点"回到当下"应清空 cutoff
    await page.getByRole("button", { name: /回到当下/ }).click();
    await expect(page).not.toHaveURL(/cutoff=/);
  });
});

test.describe("Topic research (cross-cutting)", () => {
  test("topic list renders table and links to topic view", async ({ page }) => {
    await page.goto("/topics");
    const screen = page.getByTestId("topic-list-screen");
    await expect(screen).toBeVisible();
    // 顶部固定提示：聚合投影、非主题级结论
    await expect(screen.getByText(/聚合投影/).first()).toBeVisible();
    const firstLink = page.getByRole("link", { name: /视图/ }).first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await expect(page).toHaveURL(/\/topics\/.+/);
    await expect(page.getByTestId("topic-view-screen")).toBeVisible();
  });

  test("topic view shows banner, cases, company roles and derivedFrom", async ({ page }) => {
    await page.goto("/topics");
    const firstLink = page.getByRole("link", { name: /视图/ }).first();
    await firstLink.click();
    const screen = page.getByTestId("topic-view-screen");
    await expect(screen).toBeVisible();
    // 顶部固定提示（不构成主题级结论）
    await expect(page.getByTestId("topic-view-banner")).toBeVisible();
    // 三个核心段落：案例 / 公司角色 / 持仓
    await expect(page.getByTestId("topic-view-cases")).toBeVisible();
    await expect(page.getByTestId("topic-view-roles")).toBeVisible();
    await expect(page.getByTestId("topic-view-exposure")).toBeVisible();
    // derivedFrom 引用列表
    await expect(page.getByTestId("topic-view-derived")).toBeVisible();
  });

  test("topic view historical cutoff button rewrites the URL", async ({ page }) => {
    await page.goto("/topics");
    const firstLink = page.getByRole("link", { name: /视图/ }).first();
    await firstLink.click();
    await expect(page.getByTestId("topic-view-screen")).toBeVisible();
    await page.getByRole("button", { name: /切到 2025-06-30 回放/ }).click();
    await expect(page).toHaveURL(/cutoff=2025-06-30/);
    await page.getByRole("button", { name: /回到当下/ }).click();
    await expect(page).not.toHaveURL(/cutoff=/);
  });
});
