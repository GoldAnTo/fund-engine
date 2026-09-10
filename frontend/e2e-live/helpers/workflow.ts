import { expect, type Page, type TestInfo } from "@playwright/test";

import { authenticatedApi } from "../fixtures/auth";


export function observeNoMockResponses(page: Page) {
  const checks: Array<Promise<void>> = [];
  const violations: string[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/")) return;
    const contentType = response.headers()["content-type"] ?? "";
    if (!contentType.includes("application/json")) return;
    checks.push(response.text().then((body) => {
      const normalized = body.toLocaleLowerCase();
      if (normalized.includes('"mock') || normalized.includes("mock-")) {
        violations.push(`${response.status()} ${url.pathname}`);
      }
    }).catch(() => undefined));
  });
  return {
    async assertClean() {
      await Promise.all(checks);
      expect(violations, "live API responses must not contain Mock markers").toEqual([]);
      const loadedMockResources = await page.evaluate(() =>
        performance.getEntriesByType("resource")
          .map((entry) => entry.name)
          .filter((url) => /mock/i.test(url)),
      );
      expect(
        loadedMockResources,
        "HTTP acceptance must not load a dormant Mock client chunk",
      ).toEqual([]);
    },
  };
}

export async function assertLiveRuntimeReady(page: Page, testInfo?: TestInfo): Promise<void> {
  await expect(page.locator("html")).toHaveAttribute("data-research-client-mode", "http");
  expect(new URL(page.url()).searchParams.get("client")).not.toBe("mock");
  const runtime = await authenticatedApi<{
    status: string;
    database: { status: string };
    migration: { status: string; current: string[]; expected: string[] };
    providers: Array<{ service: string; status: string }>;
    services: Array<{ name: string; status: string }>;
  }>(page, "/api/v1/runtime-status");
  expect(runtime.status, runtime.text).toBe(200);
  expect(runtime.body.status).toBe("healthy");
  expect(runtime.body.database.status).toBe("healthy");
  expect(runtime.body.migration.status).toBe("healthy");
  expect(runtime.body.migration.current).toEqual(runtime.body.migration.expected);
  expect(runtime.body.providers.every((item) => item.status === "configured")).toBe(true);
  expect(runtime.body.services.every((item) => item.status === "healthy")).toBe(true);
  if (testInfo) {
    await testInfo.attach("runtime-readiness.json", {
      body: JSON.stringify({
        status: runtime.body.status,
        database: runtime.body.database.status,
        migration: runtime.body.migration,
        providers: runtime.body.providers.map((item) => ({
          service: item.service,
          status: item.status,
        })),
        services: runtime.body.services.map((item) => ({
          name: item.name,
          status: item.status,
        })),
        browserProject: testInfo.project.name,
      }, null, 2),
      contentType: "application/json",
    });
  }
}

export function caseIdFromUrl(page: Page): string {
  const match = new URL(page.url()).pathname.match(/^\/events\/([0-9a-f-]{36})$/i);
  if (!match) throw new Error(`current URL does not contain a Case UUID: ${page.url()}`);
  return match[1];
}
