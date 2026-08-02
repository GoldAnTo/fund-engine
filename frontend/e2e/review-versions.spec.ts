import { test, expect } from "@playwright/test";

// 审核中心与快照版本。注意：不触发任何写入人工决策的按钮，
// 因为 e2e 可能连接真实后端；决策写入路径由 vitest/mock 层覆盖。

test.describe("Review workbench", () => {
  test("review screen renders the queue or an honest empty state", async ({ page }) => {
    await page.goto("/review");
    const screen = page.getByTestId("review-screen");
    await expect(screen).toBeVisible();
    await expect(screen.getByRole("heading", { name: "关系审核" })).toBeVisible();
    // 有待审项时显示队列，无待审项时显示空态，二者必居其一
    await expect(
      screen.locator('[data-testid="review-empty"], h2:has-text("项等待")').first(),
    ).toBeVisible();
  });

  test("review screen never offers a batch approve button", async ({ page }) => {
    await page.goto("/review");
    await expect(page.getByTestId("review-screen")).toBeVisible();
    await expect(page.locator("button", { hasText: /一键|批量/ })).toHaveCount(0);
  });

  test("non-empty queue shows frozen source and human-only decision panels", async ({ page }) => {
    await page.goto("/review");
    const screen = page.getByTestId("review-screen");
    await expect(screen).toBeVisible();
    const hasQueue = await screen.locator('h2:has-text("项等待")').count();
    test.skip(hasQueue === 0, "当前无待审项，跳过面板结构断言");
    await expect(
      screen.getByRole("heading", { name: "已冻结来源 · AI 提议 · 不变记录" }),
    ).toBeVisible();
    await expect(screen.getByRole("heading", { name: "只有人可写入" })).toBeVisible();
  });
});

test.describe("Snapshot versions", () => {
  test("versions screen renders the snapshot comparison header", async ({ page }) => {
    await page.goto("/versions");
    const screen = page.getByTestId("versions-screen");
    await expect(screen).toBeVisible();
    await expect(screen.getByRole("heading", { name: "快照版本比较" })).toBeVisible();
  });
});
