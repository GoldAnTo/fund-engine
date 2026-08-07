import { expect, test } from "@playwright/test";

// Event-review source admission is enforced on the API, and this browser
// regression keeps the corresponding reviewer contract honest: invalid
// fixtures remain visible for audit but cannot be accepted as formal evidence.
test.describe("Event research evidence review", () => {
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
