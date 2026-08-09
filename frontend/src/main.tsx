import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ResearchOsRoutes } from "./app/routes";
import { setResearchClient } from "./data/researchClient";
import { MockResearchAdapter } from "./data/mockResearchAdapter";

// 测试钩子：?client=mock 强制使用内存 mock 适配器。
// 写入路径的 e2e 用例借此保证任何人工决策都不会落到真实后端；
// 正常访问不带该参数时行为不变。
if (
  new URLSearchParams(window.location.search).get("client") === "mock" ||
  import.meta.env.VITE_RESEARCH_CLIENT === "mock"
) {
  setResearchClient(new MockResearchAdapter());
}
import "./styles/research-os.css";
import "./styles/research-os-overrides.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ResearchOsRoutes />
    </BrowserRouter>
  </React.StrictMode>,
);
