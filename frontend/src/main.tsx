import React from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useParams,
} from "react-router-dom";
import { PrototypeShell } from "./components/PrototypeShell";
import { setResearchClient } from "./data/researchClient";
import { MockResearchAdapter } from "./data/mockResearchAdapter";

// 测试钩子：?client=mock 强制使用内存 mock 适配器。
// 写入路径的 e2e 用例借此保证任何人工决策都不会落到真实后端；
// 正常访问不带该参数时行为不变。
if (new URLSearchParams(window.location.search).get("client") === "mock") {
  setResearchClient(new MockResearchAdapter());
}
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
import { ConclusionScreen } from "./pages/prototype/ConclusionScreen";
import { CompanyListPage } from "./pages/prototype/CompanyListPage";
import { AutoResearchRunsScreen } from "./pages/prototype/AutoResearchRunsScreen";
import { TopicListPage } from "./pages/prototype/TopicListPage";
import "./styles.css";
import "./styles-prototype.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<PrototypeShell />}>
          <Route index element={<Navigate to="/workspace" replace />} />
          <Route path="themes" element={<ThemeIndexScreen />} />
          <Route path="themes/:themeId" element={<ThemeWorkbenchScreen />} />
          <Route path="workspace" element={<OverviewScreen />} />
          <Route path="auto-research/runs" element={<AutoResearchRunsScreen />} />
          <Route path="auto-research/runs/:runId" element={<AutoResearchRunsScreen />} />
          <Route path="new-research" element={<NewResearchScreen />} />
          <Route path="plan" element={<ResearchPlanScreen />} />
          <Route
            path="relationships"
            element={<RelationshipCanvasScreen />}
          />
          <Route path="relationships/:caseId" element={<RelationshipCanvasScreen />} />
          <Route path="conclusion" element={<ConclusionScreen />} />
          <Route path="conclusion/:caseId" element={<ConclusionScreen />} />
          <Route path="review" element={<ReviewWorkbenchScreen />} />
          <Route path="library" element={<LibraryScreen />} />
          <Route path="data" element={<DataCenterScreen />} />
          <Route path="versions" element={<VersionsScreen />} />
          {/* 兼容旧版研究案例工作台 */}
          <Route path="cases" element={<CaseWorkbenchScreen />} />
          <Route path="cases/:caseId" element={<CaseWorkbenchScreen />} />
          {/* 二级研究对象入口：读模型已上线，无写路径（命令 API 在后端提供）。
              子路由 /companies/:id 与 /topics/:tag 复用同一个三栏页
              （设计图 9/10 视觉，左栏目录 + 中主区 + 右固定证据检查器），
              用 query string 决定选中；URL 兼容旧链接。 */}
          <Route path="companies" element={<CompanyListPage />} />
          <Route
            path="companies/:companyId"
            element={<CompanyDossierRedirect />}
          />
          <Route path="topics" element={<TopicListPage />} />
          <Route
            path="topics/:tag"
            element={<TopicViewRedirect />}
          />
          <Route path="*" element={<Navigate to="/themes" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);

// /companies/:id → /companies?id=...（设计图 10 视觉在 /companies 主路由）
function CompanyDossierRedirect() {
  const params = useParams<{ companyId?: string }>();
  const id = params.companyId ?? "";
  return (
    <Navigate
      to={`/companies${id ? `?id=${encodeURIComponent(id)}` : ""}`}
      replace
    />
  );
}

// /topics/:tag → /topics?tag=...（设计图 9 视觉在 /topics 主路由）
function TopicViewRedirect() {
  const params = useParams<{ tag?: string }>();
  const tag = params.tag ?? "";
  return (
    <Navigate
      to={`/topics${tag ? `?tag=${encodeURIComponent(tag)}` : ""}`}
      replace
    />
  );
}