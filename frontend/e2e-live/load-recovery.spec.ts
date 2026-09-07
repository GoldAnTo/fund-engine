import { expect, test } from "@playwright/test";

for (const target of [
  { module: "CompanyResearchApp", path: "/research/new", heading: "选择研究公司" },
  { module: "ResearchArchiveApp", path: "/underwriting/research", heading: "公司／行业档案" },
]) {
  for (const width of [1280, 390]) {
    test(`failed ${target.module} reloads its deep link with keyboard (${width}px)`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      const modulePath = `**/src/app/${target.module}.tsx*`;
      await page.route(modulePath, route => route.abort("failed"));
      await page.goto(`${target.path}?recovery-check=1`);
      await expect(page.getByRole("alert")).toHaveText("页面暂时无法显示。请检查网络后重新加载。");
      await expect(page.getByRole("main")).toBeFocused();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.keyboard.press("Tab");
      const retry = page.getByRole("button", { name: "重新加载当前页面" });
      await expect(retry).toBeFocused();
      await expect(retry).toBeInViewport();
      await page.screenshot({ path: `test-results/load-failure-${target.module}-${width}.png`, fullPage: true });
      await page.unroute(modulePath);
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { name: target.heading, exact: true })).toBeVisible();
      expect(new URL(page.url()).pathname).toBe(target.path);
      expect(new URL(page.url()).search).toBe("?recovery-check=1");
    });
  }
}
