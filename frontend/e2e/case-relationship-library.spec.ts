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
