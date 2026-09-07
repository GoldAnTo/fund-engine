/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiBase = env.VITE_API_BASE ?? "http://localhost:8000";
  const bearerToken = env.RESEARCH_BEARER_TOKEN?.trim();
  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      port: 5173,
      host: "127.0.0.1",
      proxy: {
        "/api": {
          target: apiBase,
          changeOrigin: true,
          // Hosting credentials stay in the local proxy, never VITE_* client env.
          ...(bearerToken ? { headers: { Authorization: `Bearer ${bearerToken}` } } : {}),
          rewrite: (p: string) => p.replace(/^\/api/, "/api"),
        },
      },
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./src/tests/setup.ts"],
      css: true,
      exclude: ["**/node_modules/**", "**/e2e/**", "**/dist/**"],
      include: ["src/**/*.test.{ts,tsx}"],
    },
  };
});
