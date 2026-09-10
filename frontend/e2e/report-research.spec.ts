import { expect, test } from "@playwright/test";

test.describe("report-first research workspace", () => {
  test("keeps the judgment, evidence boundary and market windows legible at desktop and 390px", async ({ page }) => {
    await page.goto("/reports/report-mock?client=mock");
    await expect(page.getByRole("heading", { name: "当前判断" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "研报主张" })).toBeVisible();
    await expect(page.getByText("发布后 1 个交易日，证据不足")).toBeVisible();
    await expect(page.getByText("发布后 5 个交易日，尚待验证")).toBeVisible();
    await expect(page).toHaveScreenshot("report-workspace-desktop.png", { fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("heading", { name: "当前判断" })).toBeVisible();
    await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);
    await expect(page).toHaveScreenshot("report-workspace-390.png", { fullPage: true });
  });

  test("uses a selected path by default, offers structured navigation, and keeps the embed read-only", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/reports/report-mock/wiki?client=mock");
    await expect(page.getByRole("button", { name: "当前路径" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("heading", { name: "结构化路径" })).toBeVisible();
    await expect(page.getByRole("group", { name: /当前研究范围的关系图谱/ })).toBeHidden();
    await page.getByRole("button", { name: "所有关系" }).click();
    await expect(page.getByRole("button", { name: "所有关系" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);

    await page.goto("/embed/reports/report-mock/wiki?client=mock#token=read-only-token");
    await expect(page.getByRole("alert")).toHaveText(/无法显示嵌入图谱(嵌入API未配置为独立来源|嵌入访问未获授权)/);
    await expect(page.getByRole("button")).toHaveCount(0);
    await expect(page.getByText("read-only-token")).toHaveCount(0);
  });

  test("creates a report, appends a successor scope, then opens the historical scope", async ({ page }) => {
    await page.goto("/reports/new?client=mock");
    await page.getByLabel("研报标题").fill("端到端研报");
    await page.getByRole("textbox", { name: "研报正文" }).fill("研报观点：供应商甲是目标公司的供应商。");
    await page.getByRole("button", { name: "创建并开始自动研究" }).click();
    await expect(page).toHaveURL(/\/reports\/report-pasted_text-mock$/);
    await expect(page.getByRole("heading", { name: "研究范围" })).toBeVisible();
    await page.getByRole("button", { name: "创建新的研究范围" }).click();
    await page.getByLabel("研究问题").fill("只研究供应商路径的市场验证。");
    await page.getByRole("button", { name: "保存为新的研究范围" }).click();
    await expect(page.getByText("当前正在查看范围 v2")).toBeVisible();
    await page.getByLabel("查看研究范围版本").selectOption("1");
    await expect(page.getByText("当前正在查看范围 v1")).toBeVisible();
  });
});
