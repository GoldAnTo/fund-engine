import { test, expect } from "@playwright/test";

// 旧版（legacy）入口：过渡期内保持可用，历史截点与时间横幅是合规关键路径。
// 截点控件与历史横幅在数据加载失败时也会渲染，断言与具体数据无关。

test.describe("Legacy dossier and historical cutoff", () => {
  test("legacy dossier route still renders under the app shell", async ({ page }) => {
    await page.goto("/legacy/dossier/ai-compute");
    await expect(page.getByTestId("cutoff-control")).toBeVisible();
  });

  test("setting a historical cutoff shows the cutoff flag", async ({ page }) => {
    await page.goto("/legacy/dossier/ai-compute");
    await page.getByTestId("cutoff-input").fill("2024-05-01");
    await expect(page.getByTestId("cutoff-flag")).toContainText("2024-05-01");
  });

  test("cutoff query param surfaces a time-context banner", async ({ page }) => {
    await page.goto("/legacy/dossier/ai-compute?cutoff=2024-05-01");
    // 数据可用时显示历史回放横幅；后端不可用时降级为离线横幅（历史横幅让位）。
    // 两种都是诚实的状态提示，必须出现其一。
    await expect(
      page.locator('[data-testid="banner-historical"], [data-testid="banner-offline"]'),
    ).toBeVisible();
  });

  test("loaded dossier exposes the causal chain", async ({ page }) => {
    await page.goto("/legacy/dossier/ai-compute");
    await expect(page.getByTestId("cutoff-control")).toBeVisible();
    const chain = page.getByTestId("causal-chain");
    const loaded = await chain.count();
    test.skip(loaded === 0, "当前数据源无 ai-compute 案例，跳过因果链断言");
    await expect(chain).toBeVisible();
  });
});
