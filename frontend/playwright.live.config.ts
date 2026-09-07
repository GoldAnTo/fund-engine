import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-live",
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5188",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...(process.env.PW_BROWSER_CHANNEL ? { channel: process.env.PW_BROWSER_CHANNEL } : {}),
  },
  webServer: [
    {
      command: "../backend/.venv/bin/python ../backend/scripts/serve_isolated_research_ui.py --port 5187 --preparation-worker --run-controls-fixture",
      url: "http://127.0.0.1:5187/openapi.json",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5188 --strictPort",
      url: "http://127.0.0.1:5188",
      env: { VITE_API_BASE: "http://127.0.0.1:5187", RESEARCH_BEARER_TOKEN: "browser-integration-token" },
      reuseExistingServer: false,
    },
  ],
});
