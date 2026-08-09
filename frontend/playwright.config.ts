import { defineConfig } from "@playwright/test";

// Do not reuse the common development port: a different worktree may already
// serve an older frontend there, producing false E2E results.
const port = Number(process.env.PW_PORT ?? "5182");
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  webServer: {
    command: `npm run dev:mock -- --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30000,
  },
  use: {
    baseURL,
    // macOS 12 等旧系统无法运行 Playwright 捆绑的 Chromium，
    // 可用 PW_BROWSER_CHANNEL=chrome 回退到系统 Chrome。
    channel: (process.env.PW_BROWSER_CHANNEL as "chrome" | undefined) ?? undefined,
  },
});
