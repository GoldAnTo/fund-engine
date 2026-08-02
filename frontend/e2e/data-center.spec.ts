import { test, expect } from "@playwright/test";

test.describe("Data center research-ops section", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/data");
  });

  test("data center renders the research-ops section with an accessible label", async ({ page }) => {
    await expect(page.getByTestId("data-center-screen")).toBeVisible();
    const section = page.getByTestId("research-ops-section");
    await expect(section).toBeVisible();
    await expect(section).toHaveAttribute("aria-label", "研究效能指标");
  });

  test("research-ops section shows the three KPI subsections", async ({ page }) => {
    const section = page.getByTestId("research-ops-section");
    await expect(section.getByRole("heading", { name: "审核吞吐" })).toBeVisible();
    await expect(section.getByRole("heading", { name: "人机一致率" })).toBeVisible();
    await expect(section.getByRole("heading", { name: "判断时滞（天）" })).toBeVisible();
  });

  test("throughput metrics render numeric ledger-derived values", async ({ page }) => {
    const section = page.getByTestId("research-ops-section");
    // 兼容 mock fixture 与真实后端两种模式：只断言结构与数字形态，不断言具体值
    for (const label of ["待审链路", "待审评估", "近 7 天链路复核", "近 7 天评估复核"]) {
      await expect(section.getByText(label, { exact: true })).toBeVisible();
    }
    const pendingLinks = section.getByText("待审链路", { exact: true }).locator("..").getByRole("strong");
    await expect(pendingLinks).toHaveText(/^\d+$/);
    const pendingAssessments = section
      .getByText("待审评估", { exact: true })
      .locator("..")
      .getByRole("strong");
    await expect(pendingAssessments).toHaveText(/^\d+$/);
  });

  test("agreement metrics show a rate or an honest empty state", async ({ page }) => {
    const section = page.getByTestId("research-ops-section");
    await expect(section.locator("p", { hasText: "评估级：" })).toHaveText(
      /评估级：\s*(\d+%|—（暂无复核数据）)/,
    );
    await expect(section.locator("p", { hasText: "链路级：" })).toHaveText(
      /链路级：\s*(\d+%|—（暂无链路复核数据）)/,
    );
  });

  test("latency metrics render average and peak values", async ({ page }) => {
    const section = page.getByTestId("research-ops-section");
    await expect(section.getByText("证据 → AI 判断")).toBeVisible();
    await expect(section.getByText("AI 判断 → 人工复核")).toBeVisible();
    const value = section.getByText("证据 → AI 判断").locator("..").locator("dd");
    await expect(value).toHaveText(/^(均 \d+(\.\d+)? \/ 峰 \d+(\.\d+)?|—（暂无评估）)$/);
  });

  test("research-ops section does not cause horizontal overflow", async ({ page }) => {
    await expect(page.getByTestId("research-ops-section")).toBeVisible();
    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflows).toBe(false);
  });
});
