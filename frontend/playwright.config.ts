import { defineConfig } from "@playwright/test";

const port = Number(process.env.PW_PORT ?? "5173");
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  webServer: {
    command: `npm run dev:mock -- --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 30000,
  },
  use: {
    baseURL,
    // macOS 12 等旧系统无法运行 Playwright 捆绑的 Chromium，
    // 可用 PW_BROWSER_CHANNEL=chrome 回退到系统 Chrome。
    channel: (process.env.PW_BROWSER_CHANNEL as "chrome" | undefined) ?? undefined,
  },
});
