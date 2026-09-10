import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ResearchOsRoutes } from "./app/routes";
import { setResearchOsApi } from "./app/researchOsApi";
import { setResearchClient } from "./data/researchClient";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { completeSilentOidcCallbackIfPresent } from "./auth/oidc";
import { researchClientMode } from "./app/researchConnection";

// 测试钩子：?client=mock 强制使用内存 mock 适配器。
// 写入路径的 e2e 用例借此保证任何人工决策都不会落到真实后端；
// 正常访问不带该参数时行为不变。
import "./styles/research-os.css";
import "./styles/research-os-overrides.css";
import "./styles/event-workflow.css";

async function bootstrap() {
  if (await completeSilentOidcCallbackIfPresent()) return;
  const mockRequested =
    new URLSearchParams(window.location.search).get("client") === "mock" ||
    import.meta.env.VITE_RESEARCH_CLIENT === "mock";
  document.documentElement.dataset.researchClientMode = researchClientMode(mockRequested);
  if (mockRequested && !import.meta.env.DEV) {
    ReactDOM.createRoot(document.getElementById("root")!).render(
      <main className="ros-page"><div className="ros-empty"><strong>离线演示仅在开发环境可用</strong><p>生产环境不会打包或加载旧原型 fixture，也不会将 mock 请求回退到真实研究账本。</p></div></main>,
    );
    return;
  }
  if (mockRequested) {
    // Keep the retired prototype adapter available to the local Vite server
    // only. A variable + vite-ignore import deliberately prevents it (and its
    // historical fixture) from becoming a production build chunk.
    const localMockAdapterModule = "./data/" + "mockResearchAdapter";
    const { MockResearchAdapter } = await import(/* @vite-ignore */ localMockAdapterModule);
    const { MockResearchOsApi } = await import("./data/mockResearchOsApi");
    const mockAdapter = new MockResearchAdapter();
    setResearchClient(mockAdapter);
    setResearchOsApi(new MockResearchOsApi(mockAdapter));
  }
  const routes = <BrowserRouter><ResearchOsRoutes /></BrowserRouter>;
  ReactDOM.createRoot(document.getElementById("root")!).render(
    mockRequested
      ? <React.StrictMode>{routes}</React.StrictMode>
      : <AuthProvider><RequireAuth><React.StrictMode>{routes}</React.StrictMode></RequireAuth></AuthProvider>,
  );
}

void bootstrap();
