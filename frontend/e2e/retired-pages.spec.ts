import { expect, test } from "@playwright/test";

test("retired research entry points do not expose old pages or start research requests", async ({ page }) => {
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) {
      apiRequests.push(request.url());
    }
  });

  for (const route of [
    "/",
    "/?client=mock",
    "/?caseId=previous-case",
    "/research",
    "/research/new",
    "/research/projects/previous-project/overview",
    "/events/previous-case/market",
    "/events/previous-case/stocks/previous-stock",
    "/events/previous-case/funds/previous-fund",
    "/underwriting/research",
    "/underwriting/research/previous-object/v1",
  ]) {
    await page.goto(route);
    await expect(page).toHaveTitle("Fund Engine");
    await expect(page.getByRole("main")).toHaveText("前端页面已清理，新原型待设计。");
    await expect(page.locator("script[type=module][src*=src], nav, form, button, input")).toHaveCount(0);
  }

  expect(apiRequests).toEqual([]);
});
