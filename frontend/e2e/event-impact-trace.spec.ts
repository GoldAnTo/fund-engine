import { expect, test } from "@playwright/test";

test.describe("event impact trace", () => {
  test("opens from the conclusion workbench and preserves the four-layer boundary at 390px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/events/event-tsm?client=mock");

    await page.getByRole("link", { name: "查看影响传导与披露边界" }).click();
    await expect(page).toHaveURL(/\/events\/event-tsm\/impact/);
    await expect(page.getByRole("heading", { name: "当前判断" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "传导关系" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "证据与边界" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "基金披露覆盖" })).toBeVisible();
    await expect(page.getByText("未上市主体不映射股票或基金敞口。")).toBeVisible();
    await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);
  });

  test("keeps disclosure links safe and explicit", async ({ page }) => {
    await page.goto("/events/event-tsm/impact?client=mock");

    const disclosure = page.getByRole("link", { name: "查看披露来源" }).first();
    await expect(disclosure).toHaveAttribute("target", "_blank");
    await expect(disclosure).toHaveAttribute("rel", "noopener noreferrer");
  });
});
