/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backendUrl = env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
  const proxyBearerToken = env.RESEARCH_BEARER_TOKEN || "";
  return {
    plugins: [react()],
    define: {
      // The browser only learns whether the local proxy can authenticate. The
      // opaque bearer value stays inside the Vite process and proxy header.
      "import.meta.env.VITE_RESEARCH_PROXY_AUTH_CONFIGURED": JSON.stringify(
        Boolean(proxyBearerToken),
      ),
    },
    server: {
      proxy: {
        "/api": {
          target: backendUrl,
          changeOrigin: true,
          headers: proxyBearerToken
            ? { Authorization: `Bearer ${proxyBearerToken}` }
            : undefined,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/tests/setup.ts",
      globals: true,
      exclude: ["**/node_modules/**", "**/dist/**", "**/e2e/**"],
    },
  };
});
