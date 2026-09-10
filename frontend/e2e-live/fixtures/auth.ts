import { expect, test as base, type Page, type TestInfo } from "@playwright/test";


export type LiveIdentity = "alice" | "bob";

function credentials(identity: LiveIdentity): { username: string; password: string } {
  const prefix = identity === "alice" ? "LIVE_KEYCLOAK" : "LIVE_SECOND_KEYCLOAK";
  const username = process.env[`${prefix}_USER`]?.trim();
  const password = process.env[`${prefix}_PASSWORD`]?.trim();
  if (!username || !password) {
    throw new Error(`${prefix}_USER and ${prefix}_PASSWORD are required`);
  }
  return { username, password };
}

export async function loginThroughKeycloak(
  page: Page,
  identity: LiveIdentity,
  testInfo?: TestInfo,
): Promise<ResearchSession> {
  const { username, password } = credentials(identity);
  await page.goto("/events");
  await expect(page.locator("html")).toHaveAttribute("data-research-client-mode", "http");
  await page.getByRole("button", { name: "登录研究系统" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(password);
  await page.locator("#kc-login").click();
  await page.waitForURL((url) => url.origin === new URL(process.env.LIVE_BASE_URL!).origin);
  await expect(page.getByLabel("研究工作台导航")).toBeVisible();
  const response = await authenticatedApi<ResearchSession>(page, "/api/v1/research-session");
  expect(response.status).toBe(200);
  expect(response.body.tenant_id).toBe("local-acceptance");
  expect(response.body.display_name.toLocaleLowerCase()).toContain(identity);
  if (testInfo) {
    await testInfo.attach(`${identity}-identity-proof.json`, {
      body: JSON.stringify({
        userId: response.body.user_id,
        displayName: response.body.display_name,
        tenantId: response.body.tenant_id,
        roles: response.body.roles,
      }, null, 2),
      contentType: "application/json",
    });
  }
  return response.body;
}

export type ApiResult<T> = { status: number; body: T; text: string };

export async function authenticatedApi<T>(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<ApiResult<T>> {
  return page.evaluate(async ({ requestPath, requestInit }) => {
    const key = Object.keys(window.sessionStorage).find((item) => item.startsWith("oidc.user:"));
    if (!key) throw new Error("real OIDC session is missing from browser session storage");
    const stored = JSON.parse(window.sessionStorage.getItem(key) ?? "null") as {
      access_token?: string;
    } | null;
    if (!stored?.access_token) throw new Error("real OIDC access token is unavailable");
    const response = await fetch(requestPath, {
      method: requestInit.method ?? "GET",
      credentials: "include",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${stored.access_token}`,
        ...(requestInit.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(requestInit.body === undefined ? {} : { body: JSON.stringify(requestInit.body) }),
    });
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    return { status: response.status, body, text };
  }, { requestPath: path, requestInit: init }) as Promise<ApiResult<T>>;
}

type ResearchSession = {
  user_id: string;
  display_name: string;
  tenant_id: string;
  roles: string[];
};

export const test = base.extend<{ alicePage: Page; aliceSession: ResearchSession }>({
  alicePage: async ({ page }, use, testInfo) => {
    await loginThroughKeycloak(page, "alice", testInfo);
    await use(page);
  },
  aliceSession: async ({ alicePage }, use) => {
    const response = await authenticatedApi<ResearchSession>(
      alicePage,
      "/api/v1/research-session",
    );
    expect(response.status).toBe(200);
    await use(response.body);
  },
});

export { expect };
