import { Navigate, Route, Routes } from "react-router-dom";

import { LegacyEventRedirect } from "./components/LegacyEventRedirect";
import { PrototypeShell } from "./components/PrototypeShell";
import { EventConclusionReviewScreen } from "./pages/prototype/EventConclusionReviewScreen";
import { EventEvidenceLibraryScreen } from "./pages/prototype/EventEvidenceLibraryScreen";
import { EventImpactTraceScreen } from "./pages/prototype/EventImpactTraceScreen";
import { EventMonitoringScreen } from "./pages/prototype/EventMonitoringScreen";
import { EventResearchBasisScreen } from "./pages/prototype/EventResearchBasisScreen";
import { EventResearchCreateScreen } from "./pages/prototype/EventResearchCreateScreen";
import { EventResearchListScreen } from "./pages/prototype/EventResearchListScreen";
import { EventResearchWorkbenchScreen } from "./pages/prototype/EventResearchWorkbenchScreen";
import { KeyEvidenceReviewScreen } from "./pages/prototype/KeyEvidenceReviewScreen";
import { LibraryScreen } from "./pages/prototype/LibraryScreen";
import { ReportEmbedScreen, ReportWikiGraphScreen } from "./pages/prototype/ReportWikiGraphScreen";
import { ReportResearchCreateScreen } from "./pages/prototype/ReportResearchCreateScreen";
import { ReportResearchIntakeScreen } from "./pages/prototype/ReportResearchIntakeScreen";
import { ReportResearchScreen } from "./pages/prototype/ReportResearchScreen";
import { VersionsScreen } from "./pages/prototype/VersionsScreen";

export function ApplicationRoutes() {
  return (
    <Routes>
      <Route path="embed/reports/:caseId/wiki" element={<ReportEmbedScreen />} />
      <Route element={<PrototypeShell />}>
        <Route index element={<Navigate to="/reports/new" replace />} />
        <Route path="events" element={<EventResearchListScreen />} />
        <Route path="events/new" element={<EventResearchCreateScreen />} />
        <Route path="events/:caseId" element={<EventResearchWorkbenchScreen />} />
        <Route path="events/:caseId/review" element={<KeyEvidenceReviewScreen />} />
        <Route path="events/:caseId/conclusion" element={<EventConclusionReviewScreen />} />
        <Route path="events/:caseId/impact" element={<EventImpactTraceScreen />} />
        <Route path="events/:caseId/basis" element={<EventResearchBasisScreen />} />
        <Route path="reports/new" element={<ReportResearchCreateScreen />} />
        <Route path="reports/:caseId/intake" element={<ReportResearchIntakeScreen />} />
        <Route path="reports/:caseId" element={<ReportResearchScreen />} />
        <Route path="reports/:caseId/wiki" element={<ReportWikiGraphScreen />} />
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
        <Route path="*" element={<Navigate to="/reports/new" replace />} />
      </Route>
    </Routes>
  );
}
