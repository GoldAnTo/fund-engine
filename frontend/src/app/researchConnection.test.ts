import { describe, expect, it } from "vitest";

import { describeResearchConnection } from "./researchConnection";

describe("describeResearchConnection", () => {
  it("tells a local researcher exactly which missing variables prevent protected Case reads", () => {
    expect(
      describeResearchConnection({
        isDevelopment: true,
        apiUrl: "",
        backendUrl: "",
        bearerToken: "",
      }),
    ).toBe(
      "本地真实数据连接尚未配置：设置 VITE_BACKEND_URL 或 VITE_RESEARCH_API_URL，并设置 RESEARCH_BEARER_TOKEN 后重启前端。",
    );
  });

  it("does not show a setup hint once the development connection has all required inputs", () => {
    expect(
      describeResearchConnection({
        isDevelopment: true,
        apiUrl: "http://127.0.0.1:8000/api/v1",
        backendUrl: "",
        bearerToken: "local-demo-token",
      }),
    ).toBeNull();
  });

  it("accepts a token held by the local Vite proxy without exposing it to browser code", () => {
    expect(
      describeResearchConnection({
        isDevelopment: true,
        apiUrl: "",
        backendUrl: "http://127.0.0.1:8000",
        bearerToken: "",
        proxyBearerConfigured: true,
      }),
    ).toBeNull();
  });
});
