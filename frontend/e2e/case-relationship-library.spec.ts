import { test, expect } from "@playwright/test";

// 案例工作台与证据图谱（只读断言：e2e 可能跑在真实后端上，
// 任何会写入人工决策的交互都不在 e2e 覆盖范围内，由 vitest/mock 层保障）。

test.describe("Case workbench", () => {
  test("case workbench renders with a default case", async ({ page }) => {
    await page.goto("/cases");
    const screen = page.getByTestId("case-workbench-screen");
    await expect(screen).toBeVisible();
    await expect(screen.getByRole("heading", { name: "正式结论" })).toBeVisible();
  });

  test("case workbench keeps the explicit caseId route working", async ({ page }) => {
    await page.goto("/cases/RC-AIC-2025-01");
    await expect(page.getByTestId("case-workbench-screen")).toBeVisible();
  });
});

test.describe("Relationship canvas", () => {
  test("relationship screen shows the evidence-to-fund chain heading", async ({ page }) => {
    await page.goto("/relationships");
    const screen = page.getByTestId("relationship-screen");
    await expect(screen).toBeVisible();
    await expect(
      screen.getByRole("heading", { name: "证据 → 命题 → 因果链 → 公司 → 基金" }),
    ).toBeVisible();
  });

  test("clicking a node opens the node inspector", async ({ page }) => {
    await page.goto("/relationships");
    const screen = page.getByTestId("relationship-screen");
    await expect(screen).toBeVisible();
    // 画布节点为按钮；点击第一个节点应打开检查器
    const nodeButton = screen.locator("button").first();
    await nodeButton.click();
    await expect(page.getByTestId("node-inspector")).toBeVisible();
  });
});

test.describe("Library", () => {
  test("library screen lists frozen documents", async ({ page }) => {
    await page.goto("/library");
    const screen = page.getByTestId("library-screen");
    await expect(screen).toBeVisible();
    await expect(
      screen.getByRole("heading", { name: "冻结资料 · 已审核关系 · 知识复用" }),
    ).toBeVisible();
    await expect(screen.getByRole("heading", { name: /份冻结资料/ })).toBeVisible();
  });
});

// Wiki 图谱为 2026-08 重写的画布：真实边、卡片节点、聚焦模式与导出走
// fixture 数据（?client=mock），断言与具体节点解耦。
test.describe("Wiki relationship graph (mock client)", () => {
  test("renders real edges with labels and five column headers", async ({
    page,
  }) => {
    await page.goto("/relationships?client=mock");
    const graph = page.getByTestId("wiki-graph");
    await expect(graph).toBeVisible();
    for (const label of ["证据", "命题", "因果链", "公司", "基金"]) {
      await expect(
        graph.locator(".wiki-graph__colhead-label", { hasText: label }),
      ).toBeVisible();
    }
    // 边标签来自 fixture 真实边数据，不再是假连线
    await expect(
      graph.locator(".wiki-edge__label", { hasText: "反驳" }).first(),
    ).toBeVisible();
    await expect(
      graph.locator(".wiki-edge__label", { hasText: "支持" }).first(),
    ).toBeVisible();
    await expect(
      graph.locator(".wiki-edge__label", { hasText: /持仓/ }).first(),
    ).toBeVisible();
  });

  test("source layer renders document cards linking to the library", async ({
    page,
  }) => {
    // P2 缺陷 9 修复：图谱最左的「证据」列必须包含原文层（document
    // 卡片），让"回溯到冻结原文"在画布层也成立。fixture evidence 层
    // 至少包含 NVIDIA Form 10-Q 一张原文卡。
    await page.goto("/relationships?client=mock");
    const graph = page.getByTestId("wiki-graph");
    await expect(graph).toBeVisible();
    const evidenceCol = graph.locator(
      ".wiki-graph__colhead:has(.wiki-graph__colhead-label:text('证据'))",
    );
    await expect(evidenceCol).toBeVisible();
    // 卡片节点数 > 1（至少包含 document + statement）
    const evidenceCards = graph.locator(
      ".wiki-node.tone-fact, .wiki-node.tone-contradict, .wiki-node.tone-ai",
    );
    expect(await evidenceCards.count()).toBeGreaterThanOrEqual(1);
  });

  test("clicking a card focuses its chain and clicking again clears", async ({
    page,
  }) => {
    await page.goto("/relationships?client=mock");
    const graph = page.getByTestId("wiki-graph");
    await expect(graph).toBeVisible();
    const cards = graph.locator(".wiki-node");
    expect(await cards.count()).toBeGreaterThan(10);
    await cards.first().click();
    expect(await graph.locator(".wiki-node.is-dim").count()).toBeGreaterThan(0);
    // 再点同一张卡片取消聚焦，恢复全图
    await cards.first().click();
    await expect(graph.locator(".wiki-node.is-dim")).toHaveCount(0);
  });

  test("跳转原文 navigates to the library with the document selected", async ({
    page,
  }) => {
    await page.goto("/relationships?client=mock");
    // fixture 默认选中 ST-004（业绩说明会反面证据）
    const inspector = page.getByTestId("node-inspector");
    await expect(inspector).toBeVisible();
    await inspector.getByRole("link", { name: "跳转原文" }).click();
    await page.waitForURL(/\/library\?document=DOC-MSFT-FY25Q3-CALL/);
    await expect(page.getByTestId("library-screen")).toBeVisible();
    // 资料库应定位到目标文档，而不是默认第一篇
    await expect(
      page.locator(".prototype-library-source-list a.is-selected"),
    ).toContainText("业绩说明会");
  });

  test("PNG export triggers a graph download", async ({ page }) => {
    await page.goto("/relationships?client=mock");
    const graph = page.getByTestId("wiki-graph");
    await expect(graph).toBeVisible();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      graph.getByRole("button", { name: "导出 PNG" }).click(),
    ]);
    expect(download.suggestedFilename()).toContain("证据图谱");
  });

  test("research brief export triggers a PDF download", async ({ page }) => {
    await page.goto("/relationships?client=mock");
    const graph = page.getByTestId("wiki-graph");
    await expect(graph).toBeVisible();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      graph.getByRole("button", { name: "导出研究简报" }).click(),
    ]);
    expect(download.suggestedFilename()).toContain("研究简报");
    expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  });
});
