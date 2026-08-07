import { expect, test } from "@playwright/test";

// Event-review source admission is enforced on the API, and this browser
// regression keeps the corresponding reviewer contract honest: invalid
// fixtures remain visible for audit but cannot be accepted as formal evidence.
test.describe("Event research evidence review", () => {
  test("keeps the conclusion, factors, and sole next action usable on a narrow workbench", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/events/event-tsm?client=mock");

    const conclusion = page.locator(".event-conclusion");
    const factors = page.locator(".event-factors");
    const action = page.locator(".event-next-action .prototype-button.primary");
    await expect(conclusion).toBeVisible();
    await expect(factors).toBeVisible();
    await expect(action).toHaveCount(1);
    await expect(action).toBeVisible();

    const conclusionBox = await conclusion.boundingBox();
    const factorsBox = await factors.boundingBox();
    expect(conclusionBox?.y).toBeLessThan(factorsBox?.y ?? Number.POSITIVE_INFINITY);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await action.click();
    await expect(page).toHaveURL(/\/events\/event-tsm\/review/);
  });

  test("shows review progress, a traceable original-source link, and return feedback", async ({ page }) => {
    await page.goto("/events/event-tsm/review?client=mock");

    await expect(page.getByText("总数 3")).toBeVisible();
    await expect(page.getByText("待审核 1")).toBeVisible();
    const rawSource = page.getByRole("link", {
      name: "https://investor.tsmc.com/english/quarterly-results/2026/q2",
    });
    await expect(rawSource).toHaveAttribute(
      "href",
      "https://investor.tsmc.com/english/quarterly-results/2026/q2",
    );
    await expect(rawSource).toHaveAttribute("target", "_blank");
    await expect(rawSource).toHaveAttribute("rel", "noopener noreferrer");

    await page.getByRole("button", { name: "退回并继续找真实来源" }).click();
    await expect(page.getByRole("status")).toContainText(
      "已退回“资本开支 / 自由现金流担忧”的材料并继续找真实来源",
    );
  });

  test("an invalid fixture source remains auditable but cannot be accepted", async ({ page }) => {
    await page.goto("/events/event-tsm/review?client=mock");

    await expect(
      page.getByRole("heading", { name: "核验原文，再决定是否计入因素" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "未验证测试来源" }).click();

    await expect(page.getByText("无效来源").last()).toBeVisible();
    await expect(
      page.getByText("来源不可采纳：测试域名不能作为正式证据来源"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "采纳为本因素的正式证据" }),
    ).toBeDisabled();
  });
});
