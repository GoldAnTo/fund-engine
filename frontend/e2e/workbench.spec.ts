import { test, expect } from "@playwright/test";

test("workbench page loads and renders root content", async ({ page }) => {
  await page.goto("/?case=ai-compute");
  // 前端可启动即可；后端若未运行会显示加载失败，root 仍非空
  await expect(page.locator("#root")).not.toBeEmpty();
});
