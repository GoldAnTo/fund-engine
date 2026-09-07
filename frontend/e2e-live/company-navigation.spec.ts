import { expect, test } from "@playwright/test";

test("company research is reachable from the live workspace at desktop and mobile widths", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "公司研究", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "投资研究导航" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近项目" })).toBeVisible();
  await page.getByRole("link", { name: "研究档案", exact: true }).click();
  await expect(page.getByRole("heading", { name: "公司／行业档案", exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("没有符合条件的冻结档案。");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/research-archive-mobile.png", fullPage: true });
  await page.getByRole("link", { name: "返回公司研究", exact: true }).click();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("navigation", { name: "投资研究导航" }).getByRole("link", { name: "建立研究" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.screenshot({ path: "test-results/company-create-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("navigation", { name: "投资研究导航" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/company-create-mobile.png", fullPage: true });
  await page.getByRole("link", { name: "事件研究", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "研究空间" })).toBeVisible();
});
