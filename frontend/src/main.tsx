import React from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { PrototypeShell } from "./components/PrototypeShell";
import { setResearchClient } from "./data/researchClient";
import { MockResearchAdapter } from "./data/mockResearchAdapter";

// 测试钩子：?client=mock 强制使用内存 mock 适配器。
// 写入路径的 e2e 用例借此保证任何人工决策都不会落到真实后端；
// 正常访问不带该参数时行为不变。
if (new URLSearchParams(window.location.search).get("client") === "mock") {
  setResearchClient(new MockResearchAdapter());
}
import { ResearchCaseDossierPage } from "./pages/ResearchCaseDossierPage";
import { RelationshipCanvasPage } from "./pages/RelationshipCanvasPage";
import { DocumentLibraryPage } from "./pages/DocumentLibraryPage";
import { ReviewWorkbenchPage } from "./pages/ReviewWorkbenchPage";
import { ResearchWorkbenchPage } from "./pages/ResearchWorkbenchPage";
import { NotImplementedPage } from "./pages/NotImplementedPage";
import { OverviewScreen } from "./pages/prototype/OverviewScreen";
import { NewResearchScreen } from "./pages/prototype/NewResearchScreen";
import { ResearchPlanScreen } from "./pages/prototype/ResearchPlanScreen";
import { CaseWorkbenchScreen } from "./pages/prototype/CaseWorkbenchScreen";
import { RelationshipCanvasScreen } from "./pages/prototype/RelationshipCanvasScreen";
import { ReviewWorkbenchScreen } from "./pages/prototype/ReviewWorkbenchScreen";
import { LibraryScreen } from "./pages/prototype/LibraryScreen";
import { DataCenterScreen } from "./pages/prototype/DataCenterScreen";
import { VersionsScreen } from "./pages/prototype/VersionsScreen";
import { ThemeIndexScreen } from "./pages/prototype/ThemeIndexScreen";
import { ThemeWorkbenchScreen } from "./pages/prototype/ThemeWorkbenchScreen";
import "./styles.css";
import "./styles-prototype.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Prototype shell — Theme 一等公民下的工作流：
            主题列表 → 主题详情（认知假设 + 证据 + 穿透） → 子流程。 */}
        <Route element={<PrototypeShell />}>
          <Route index element={<ThemeIndexScreen />} />
          <Route path="themes" element={<ThemeIndexScreen />} />
          <Route path="themes/:themeId" element={<ThemeWorkbenchScreen />} />
          <Route path="workspace" element={<OverviewScreen />} />
          <Route path="new-research" element={<NewResearchScreen />} />
          <Route path="plan" element={<ResearchPlanScreen />} />
          <Route
            path="relationships"
            element={<RelationshipCanvasScreen />}
          />
          <Route
            path="relationships/:caseId"
            element={<RelationshipCanvasScreen />}
          />
          <Route path="review" element={<ReviewWorkbenchScreen />} />
          <Route path="library" element={<LibraryScreen />} />
          <Route path="data" element={<DataCenterScreen />} />
          <Route path="versions" element={<VersionsScreen />} />
          {/* 兼容旧版研究案例工作台 */}
          <Route path="cases" element={<CaseWorkbenchScreen />} />
          <Route path="cases/:caseId" element={<CaseWorkbenchScreen />} />
          {/* 二级研究对象入口（建设中） */}
          <Route
            path="companies"
            element={
              <NotImplementedPage
                title="公司研究"
                hint="公司层面证据关系与个股深度跟踪正在搭建。"
              />
            }
          />
          <Route
            path="topics"
            element={
              <NotImplementedPage
                title="主题研究"
                hint="跨行业主题（如 AI 算力、新能源、半导体）的横切研究空间建设中。"
              />
            }
          />
          {/* 未匹配路由兜底：旧链接/书签（如 /cases/:id/graph）不再渲染
              空白页，回到主题入口。React Router 按特异性排序，* 优先级
              最低，不会吞掉下方 legacy 路由。 */}
          <Route path="*" element={<Navigate to="/themes" replace />} />
        </Route>

        {/* Legacy app shell — keeps old entry points working for transitional traffic. */}
        <Route element={<AppShell />}>
          <Route
            path="legacy/dossier/:caseId"
            element={<LegacyDossierRoute />}
          />
          <Route
            path="legacy/graph/:caseId"
            element={<LegacyGraphRoute />}
          />
          <Route path="legacy/documents" element={<DocumentLibraryPage />} />
          <Route path="legacy/review" element={<ReviewWorkbenchPage />} />
          <Route
            path="macro"
            element={
              <NotImplementedPage
                title="宏观与政策"
                hint="宏观周期、利率与政策事件的研究追踪模块正在搭建。"
              />
            }
          />
          <Route
            path="monitor"
            element={
              <NotImplementedPage
                title="监测中心"
                hint="实时事件监测面板正在搭建。"
              />
            }
          />
          <Route
            path="workbench/:caseId"
            element={<LegacyWorkbenchRoute />}
          />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);

// Legacy workbench keeps working inside the new shell for transitional traffic.
function LegacyWorkbenchRoute() {
  const params = new URLSearchParams(window.location.search);
  return <ResearchWorkbenchPage caseId={params.get("case") ?? "ai-compute"} />;
}

// Old research-case dossier lives behind /legacy/dossier/:caseId.
function LegacyDossierRoute() {
  return <ResearchCaseDossierPage />;
}

// Old relationship canvas lives behind /legacy/graph/:caseId.
function LegacyGraphRoute() {
  return <RelationshipCanvasPage />;
}