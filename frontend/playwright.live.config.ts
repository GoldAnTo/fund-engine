import { defineConfig, devices } from "@playwright/test";


function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for live browser acceptance`);
  return value;
}

const baseURL = required("LIVE_BASE_URL").replace(/\/$/, "");
required("LIVE_CONTROL_URL");
required("LIVE_CONTROL_TOKEN");
const workflowTimeout = Number(process.env.LIVE_WORKFLOW_TIMEOUT_MS ?? "600000");
if (!Number.isFinite(workflowTimeout) || workflowTimeout < 30_000) {
  throw new Error("LIVE_WORKFLOW_TIMEOUT_MS must be at least 30000");
}

export default defineConfig({
  testDir: "./e2e-live",
  outputDir: "./test-results/live",
  timeout: workflowTimeout,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  forbidOnly: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report/live", open: "never" }]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL,
    channel: (process.env.PW_BROWSER_CHANNEL as "chrome" | undefined) ?? undefined,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
