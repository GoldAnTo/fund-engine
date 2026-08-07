import React from "react";
import ReactDOM from "react-dom/client";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import { LegacyEventRedirect } from "./components/LegacyEventRedirect";
import { PrototypeShell } from "./components/PrototypeShell";
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
import { LibraryScreen } from "./pages/prototype/LibraryScreen";
import { VersionsScreen } from "./pages/prototype/VersionsScreen";
import { EventResearchCreateScreen } from "./pages/prototype/EventResearchCreateScreen";
import { EventResearchListScreen } from "./pages/prototype/EventResearchListScreen";
import { EventResearchWorkbenchScreen } from "./pages/prototype/EventResearchWorkbenchScreen";
import { KeyEvidenceReviewScreen } from "./pages/prototype/KeyEvidenceReviewScreen";
import { EventConclusionReviewScreen } from "./pages/prototype/EventConclusionReviewScreen";
import { EventEvidenceLibraryScreen } from "./pages/prototype/EventEvidenceLibraryScreen";
import { EventMonitoringScreen } from "./pages/prototype/EventMonitoringScreen";
import { EventResearchBasisScreen } from "./pages/prototype/EventResearchBasisScreen";
import "./styles.css";
import "./styles-prototype.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<PrototypeShell />}>
          <Route index element={<Navigate to="/events" replace />} />
          <Route path="events" element={<EventResearchListScreen />} />
          <Route path="events/new" element={<EventResearchCreateScreen />} />
          <Route path="events/:caseId" element={<EventResearchWorkbenchScreen />} />
          <Route path="events/:caseId/review" element={<KeyEvidenceReviewScreen />} />
          <Route path="events/:caseId/conclusion" element={<EventConclusionReviewScreen />} />
          <Route path="events/:caseId/basis" element={<EventResearchBasisScreen />} />
          <Route path="library" element={<EventEvidenceLibraryScreen />} />
          <Route path="versions" element={<EventMonitoringScreen />} />
          <Route path="themes/*" element={<LegacyEventRedirect />} />
          <Route path="workspace" element={<LegacyEventRedirect />} />
          <Route path="auto-research/*" element={<LegacyEventRedirect />} />
          <Route path="new-research" element={<LegacyEventRedirect />} />
          <Route path="plan" element={<LegacyEventRedirect />} />
          <Route path="relationships" element={<LegacyEventRedirect />} />
          <Route path="relationships/:caseId" element={<LegacyEventRedirect />} />
          <Route path="conclusion/*" element={<LegacyEventRedirect />} />
          <Route path="review" element={<LegacyEventRedirect />} />
          <Route path="legacy/library" element={<LibraryScreen />} />
          <Route path="data" element={<LegacyEventRedirect />} />
          <Route path="legacy/versions" element={<VersionsScreen />} />
          <Route path="cases" element={<LegacyEventRedirect />} />
          <Route path="cases/:caseId" element={<LegacyEventRedirect />} />
          <Route path="companies/*" element={<LegacyEventRedirect />} />
          <Route path="topics/*" element={<LegacyEventRedirect />} />
          <Route path="*" element={<Navigate to="/events" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
