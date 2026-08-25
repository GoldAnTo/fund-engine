import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./AppShell";
import { ResearchArchiveLoadErrorBoundary } from "./ResearchArchiveLoadErrorBoundary";
import { UnderwritingArchiveShell } from "./UnderwritingArchiveShell";
import { CaseConclusionHistoryPage, CaseConclusionPage, CaseDocumentsPage, CaseEvidencePage, CaseFundProfilePage, CaseMarketPage, CaseMonitorPage, CaseProtocolPage, CaseRelationsPage, CaseReviewPage, CaseScopePage, CaseStockProfilePage, CaseWikiPage, MonitorConfigPage } from "../features/case/CasePages";
import { AutomaticResearchPage } from "../features/events/AutomaticResearchPage";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { EventDeskPage } from "../features/events/EventDeskPage";
import { GlobalMonitoringPage } from "../features/events/GlobalMonitoringPage";
import { LegacyAdmissionPage } from "../features/events/LegacyAdmissionPage";
import { ResearchNetworkPage } from "../features/events/ResearchNetworkPage";
import { ResearchPreparationPage } from "../features/events/ResearchPreparationPage";

const ResearchArchivePage = lazy(() => import("../features/underwriting/ResearchArchivePage"));

function ResearchArchiveRoute() {
  const location = useLocation();
  return (
    <ResearchArchiveLoadErrorBoundary resetKey={location.pathname}>
      <Suspense fallback={<main className="ros-page" aria-busy="true"><p>正在载入公司／行业档案…</p></main>}>
        <ResearchArchivePage />
      </Suspense>
    </ResearchArchiveLoadErrorBoundary>
  );
}

export default function LegacyResearchRoutes() {
  return (
    <Routes>
      <Route element={<UnderwritingArchiveShell />}>
        <Route path="underwriting/research" element={<ResearchArchiveRoute />} />
        <Route path="underwriting/research/:objectId/:versionKind" element={<ResearchArchiveRoute />} />
      </Route>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/events" replace />} />
        <Route path="events" element={<EventDeskPage />} />
        <Route path="network" element={<ResearchNetworkPage />} />
        <Route path="monitoring" element={<GlobalMonitoringPage />} />
        <Route path="governance/case-admissions" element={<LegacyAdmissionPage />} />
        <Route path="events/new" element={<EventCreatePage />} />
        <Route path="events/:caseId/automatic-research" element={<AutomaticResearchPage />} />
        <Route path="events/:caseId/preparation" element={<ResearchPreparationPage />} />
        <Route path="events/:caseId" element={<CaseConclusionPage />} />
        <Route path="events/:caseId/history" element={<CaseConclusionHistoryPage />} />
        <Route path="events/:caseId/scope" element={<CaseScopePage />} />
        <Route path="events/:caseId/evidence" element={<CaseEvidencePage />} />
        <Route path="events/:caseId/documents" element={<CaseDocumentsPage />} />
        <Route path="events/:caseId/review" element={<CaseReviewPage />} />
        <Route path="events/:caseId/wiki" element={<CaseWikiPage />} />
        <Route path="events/:caseId/protocol" element={<CaseProtocolPage />} />
        <Route path="events/:caseId/market" element={<CaseMarketPage />} />
        <Route path="events/:caseId/stocks/:stockId" element={<CaseStockProfilePage />} />
        <Route path="events/:caseId/funds/:fundId" element={<CaseFundProfilePage />} />
        <Route path="events/:caseId/monitor" element={<CaseMonitorPage />} />
        <Route path="events/:caseId/monitor/config" element={<MonitorConfigPage />} />
        <Route path="events/:caseId/relations" element={<CaseRelationsPage />} />
        <Route path="*" element={<Navigate to="/events" replace />} />
      </Route>
    </Routes>
  );
}
