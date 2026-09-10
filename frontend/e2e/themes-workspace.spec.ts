import { test, expect } from "@playwright/test";

// 主题工作流：主题列表 → 主题工作台（命题树 + 证据 + 穿透）。

test.describe("Theme workflow", () => {
  test("theme index lists theme cards linking to the workbench", async ({ page }) => {
    await page.goto("/themes");
    await expect(page.getByTestId("theme-index-screen")).toBeVisible();
    const firstCard = page.locator(".theme-card").first();
    await expect(firstCard).toBeVisible();
    await firstCard.click();
    await expect(page).toHaveURL(/\/themes\/.+/);
    await expect(page.getByTestId("theme-workbench-screen")).toBeVisible();
  });

  test("theme workbench exposes thesis tree, evidence and penetration sections", async ({ page }) => {
    await page.goto("/themes");
    await page.locator(".theme-card").first().click();
    const screen = page.getByTestId("theme-workbench-screen");
    await expect(screen.getByRole("heading", { name: "命题树" })).toBeVisible();
    await expect(screen.getByRole("heading", { name: /条证据/ })).toBeVisible();
    await expect(screen.getByRole("heading", { name: /关联标的 · 估值/ })).toBeVisible();
  });

  test("theme index reaches the new-research flow", async ({ page }) => {
    await page.goto("/themes");
    await page.getByRole("link", { name: /新建研究/ }).first().click();
    await expect(page).toHaveURL(/\/new-research/);
    await expect(page.getByTestId("new-research-screen")).toBeVisible();
  });
});

test.describe("Workspace overview", () => {
  test("overview renders judgment, change and research-log sections", async ({ page }) => {
    await page.goto("/workspace");
    const screen = page.getByTestId("overview-screen");
    await expect(screen).toBeVisible();
    await expect(screen.getByRole("heading", { name: "关键判断" })).toBeVisible();
    await expect(screen.getByRole("heading", { name: "变化总览" })).toBeVisible();
    await expect(screen.getByRole("heading", { name: "研究主线" })).toBeVisible();
    await expect(screen.getByRole("heading", { name: "研究日志" })).toBeVisible();
  });
});
