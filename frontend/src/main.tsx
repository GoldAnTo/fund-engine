import React from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Route,
  Routes,
} from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { PrototypeShell } from "./components/PrototypeShell";
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
import "./styles.css";
import "./styles-prototype.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Prototype shell is the default: 9 prototype-aligned screens own the canonical URLs. */}
        <Route element={<PrototypeShell />}>
          <Route index element={<OverviewScreen />} />
          <Route path="workspace" element={<OverviewScreen />} />
          <Route path="new-research" element={<NewResearchScreen />} />
          <Route path="plan" element={<ResearchPlanScreen />} />
          <Route path="cases" element={<CaseWorkbenchScreen />} />
          <Route path="cases/:caseId" element={<CaseWorkbenchScreen />} />
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