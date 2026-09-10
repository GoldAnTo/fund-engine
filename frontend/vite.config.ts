/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { localGatewayProxy } from "./server/localGatewayProxy";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // The browser client always uses the same-origin `/api/v1` path. This is
  // only the local development proxy target for the isolated Gateway server.
  const apiBase = env.GATEWAY_API_BASE ?? env.VITE_API_BASE ?? "http://127.0.0.1:8018";
  return {
    plugins: [react(), localGatewayProxy({ target: apiBase, token: env.GATEWAY_PROXY_TOKEN })],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      port: 5173,
      host: "127.0.0.1",
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./src/tests/setup.ts"],
      css: true,
      exclude: ["**/node_modules/**", "**/e2e/**", "**/dist/**"],
      include: ["src/tests/**/*.test.{ts,tsx}"],
    },
  };
});
