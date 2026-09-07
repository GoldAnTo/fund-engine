import { expect, test } from "@playwright/test";
for (const route of ["/research/new", "/underwriting/research"]) {
  for (const width of [1280, 390]) {
    test(`company and archive keyboard skip (${route}, ${width}px)`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(route);
      const skip = page.getByRole("link", { name: "跳到研究内容", exact: true });
      await expect(skip).toHaveCSS("opacity", "0");
      await page.keyboard.press("Tab");
      await expect(skip).toBeFocused();
      await expect(skip).toHaveCSS("opacity", "1");
      await expect(skip).toBeInViewport();
      await page.keyboard.press("Enter");
      await expect(page.locator(route.startsWith("/research") ? "#company-content" : "#archive-content")).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(page.getByRole("main").getByRole(route.startsWith("/research") ? "textbox" : "searchbox").first()).toBeFocused();
      await expect(skip).toHaveCSS("opacity", "0");
    });
  }
}
for (const width of [1280, 390]) {
  test(`skip link is painted only on focus and transfers keyboard focus (${width}px)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    const skip = page.getByRole("link", { name: "跳到研究内容", exact: true });
    await expect(skip).toHaveCSS("opacity", "0");
    await page.keyboard.press("Tab");
    await expect(skip).toBeFocused();
    await expect(skip).toHaveCSS("opacity", "1");
    await expect(skip).toBeInViewport();
    await page.keyboard.press("Enter");
    await expect(page.locator("#workspace")).toBeFocused();
    await expect(skip).toHaveCSS("opacity", "0");
    await page.getByRole("button", { name: "新建研究", exact: true }).click();
    await page.getByLabel("创建人", { exact: true }).fill("键盘核验");
    // Scrolled full-page screenshots previously painted top:-50 fixed links.
    await expect(skip).toHaveCSS("opacity", "0");
    await page.screenshot({ path: `test-results/skip-link-${width}.png`, fullPage: true });
  });
}
