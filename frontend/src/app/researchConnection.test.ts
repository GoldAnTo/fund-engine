import { describe, expect, it } from "vitest";

import { describeResearchConnection, researchClientMode } from "./researchConnection";

describe("describeResearchConnection", () => {
  it("tells a local researcher exactly which missing variables prevent protected Case reads", () => {
    expect(
      describeResearchConnection({
        isDevelopment: true,
        apiUrl: "",
        backendUrl: "",
        oidcAuthority: "",
        oidcClientId: "",
      }),
    ).toBe(
      "本地真实数据连接尚未配置：设置 VITE_BACKEND_URL 或 VITE_RESEARCH_API_URL，并设置 VITE_OIDC_AUTHORITY 与 VITE_OIDC_CLIENT_ID 后重启前端。",
    );
  });

  it("does not show a setup hint once the development connection has all required inputs", () => {
    expect(
      describeResearchConnection({
        isDevelopment: true,
        apiUrl: "http://127.0.0.1:8000/api/v1",
        backendUrl: "",
        oidcAuthority: "http://localhost:8080/realms/research",
        oidcClientId: "research-web",
      }),
    ).toBeNull();
  });

  it("requires both OIDC authority and client id", () => {
    expect(
      describeResearchConnection({
        isDevelopment: true,
        apiUrl: "",
        backendUrl: "http://127.0.0.1:8000",
        oidcAuthority: "http://localhost:8080/realms/research",
        oidcClientId: "",
      }),
    ).toBe(
      "本地真实数据连接缺少登录配置：设置 VITE_OIDC_AUTHORITY 与 VITE_OIDC_CLIENT_ID 后重启前端。",
    );
  });

  it("exposes an unambiguous client mode for live browser acceptance", () => {
    expect(researchClientMode(false)).toBe("http");
    expect(researchClientMode(true)).toBe("mock");
  });
});
