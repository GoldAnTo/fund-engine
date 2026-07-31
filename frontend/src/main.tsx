import React from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { WorkspaceOverviewPage } from "./pages/WorkspaceOverviewPage";
import { ResearchCaseDossierPage } from "./pages/ResearchCaseDossierPage";
import { RelationshipCanvasPage } from "./pages/RelationshipCanvasPage";
import { DocumentLibraryPage } from "./pages/DocumentLibraryPage";
import { ReviewWorkbenchPage } from "./pages/ReviewWorkbenchPage";
import { ResearchWorkbenchPage } from "./pages/ResearchWorkbenchPage";
import { NotImplementedPage } from "./pages/NotImplementedPage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<WorkspaceOverviewPage />} />
          <Route path="cases" element={<ResearchCaseDossierPage />} />
          <Route path="cases/:caseId" element={<ResearchCaseDossierPage />} />
          <Route path="relationships" element={<RelationshipCanvasPage />} />
          <Route
            path="relationships/:caseId"
            element={<RelationshipCanvasPage />}
          />
          <Route path="documents" element={<DocumentLibraryPage />} />
          <Route path="review" element={<ReviewWorkbenchPage />} />
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
            path="data"
            element={
              <NotImplementedPage
                title="数据中心"
                hint="聚合数据指标库正在搭建。"
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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);

// Legacy workbench keeps working inside the new shell for transitional traffic.
function LegacyWorkbenchRoute() {
  const params = new URLSearchParams(window.location.search);
  return <ResearchWorkbenchPage caseId={params.get("case") ?? "ai-compute"} />;
}