import { test, expect } from "@playwright/test";

test.describe("App shell + four main pages", () => {
  test("workspace overview loads the research summary and three side rails", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "研究总览" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "任务队列" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "证据变化" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "活动" })).toBeVisible();
  });

  test("case dossier renders the causal chain and supports/contradicts groups", async ({ page }) => {
    await page.goto("/cases/ai-compute");
    await expect(page.getByTestId("causal-chain")).toBeVisible();
    await expect(page.getByRole("heading", { name: "支持" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "反证" })).toBeVisible();
  });

  test("case dossier switches tabs and updates aria-selected", async ({ page }) => {
    await page.goto("/cases/ai-compute");
    const risksTab = page.getByRole("button", { name: "风险与假设" });
    await risksTab.click();
    await expect(risksTab).toHaveAttribute("aria-selected", "true");
  });

  test("case dossier focuses a causal step and dims unrelated evidence", async ({ page }) => {
    await page.goto("/cases/ai-compute");
    const stepBtn = page.getByRole("button", { name: /政策与准入/ });
    await stepBtn.click();
    await expect(page.locator(".evidence-row.is-related, .evidence-row.is-dimmed").first()).toBeVisible();
  });

  test("relationship canvas shows five groups from evidence to fund", async ({ page }) => {
    await page.goto("/relationships/ai-compute");
    await expect(page.getByTestId("relationship-canvas")).toBeVisible();
    for (const label of ["证据", "命题", "因果链", "公司", "基金"]) {
      await expect(page.getByRole("button", { name: label }).first()).toBeVisible();
    }
  });

  test("relationship canvas legend toggles filter visibility", async ({ page }) => {
    await page.goto("/relationships/ai-compute");
    const fundLegend = page.getByTestId("legend-fund");
    await expect(fundLegend).toHaveAttribute("aria-pressed", "true");
    await fundLegend.click();
    await expect(fundLegend).toHaveAttribute("aria-pressed", "false");
  });

  test("document library lists documents and selects the first row", async ({ page }) => {
    await page.goto("/documents");
    await expect(page.getByTestId("library-table")).toBeVisible();
    await expect(page.getByTestId("library-inspector")).toBeVisible();
    await page.getByTestId("library-row-doc-1").click();
  });

  test("document library empty-state copy is reachable via search", async ({ page }) => {
    await page.goto("/documents");
    await page.getByTestId("library-search").fill("不存在的资料");
    await expect(page.getByTestId("library-empty")).toBeVisible();
  });

  test("review workbench exposes the three-column layout", async ({ page }) => {
    await page.goto("/review");
    await expect(page.getByTestId("review-workbench")).toBeVisible();
    await expect(page.getByText("待审核项")).toBeVisible();
    await expect(page.getByText("冻结原文")).toBeVisible();
    await expect(page.getByText("人工决定")).toBeVisible();
  });

  test("review workbench records a decision after confirm", async ({ page }) => {
    await page.goto("/review");
    await page.getByTestId("review-confirm").click();
    await expect(page.locator(".review-decision__history li").first()).toBeVisible();
  });

  test("review workbench never offers a batch approve button", async ({ page }) => {
    await page.goto("/review");
    await expect(page.locator("button", { hasText: /一键|批量/ })).toHaveCount(0);
  });

  test("historical cutoff triggers the cutoff flag and historical banner", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("cutoff-input").fill("2024-05-01");
    await expect(page.getByTestId("cutoff-flag")).toContainText("2024-05-01");
    await page.goto("/cases/ai-compute?cutoff=2024-05-01");
    await expect(page.getByTestId("banner-historical")).toBeVisible();
  });

  test("global search opens with cmd+k and lists grouped results", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Meta+K");
    const input = page.getByLabel("搜索");
    await input.fill("算力");
    await expect(page.getByText("案例")).toBeVisible();
    await expect(page.getByText("AI 算力链")).toBeVisible();
  });
});