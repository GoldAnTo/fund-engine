import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 30000,
  },
  use: {
    baseURL: "http://localhost:5173",
    // macOS 12 等旧系统无法运行 Playwright 捆绑的 Chromium，
    // 可用 PW_BROWSER_CHANNEL=chrome 回退到系统 Chrome。
    channel: (process.env.PW_BROWSER_CHANNEL as "chrome" | undefined) ?? undefined,
  },
});