import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "./AppShell";
import { CaseConclusionHistoryPage, CaseConclusionPage, CaseDocumentsPage, CaseEvidencePage, CaseMarketPage, CaseMonitorPage, CaseProtocolPage, CaseRelationsPage, CaseReviewPage, CaseWikiPage, MonitorConfigPage } from "../features/case/CasePages";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { EventDeskPage } from "../features/events/EventDeskPage";
import { GlobalMonitoringPage } from "../features/events/GlobalMonitoringPage";
import { ResearchNetworkPage } from "../features/events/ResearchNetworkPage";

export function ResearchOsRoutes() {
  return <Routes><Route element={<AppShell />}>
    <Route index element={<Navigate to="/events" replace />} />
    <Route path="events" element={<EventDeskPage />} />
    <Route path="network" element={<ResearchNetworkPage />} />
    <Route path="monitoring" element={<GlobalMonitoringPage />} />
    <Route path="events/new" element={<EventCreatePage />} />
    <Route path="events/:caseId" element={<CaseConclusionPage />} />
    <Route path="events/:caseId/history" element={<CaseConclusionHistoryPage />} />
    <Route path="events/:caseId/evidence" element={<CaseEvidencePage />} />
    <Route path="events/:caseId/documents" element={<CaseDocumentsPage />} />
    <Route path="events/:caseId/review" element={<CaseReviewPage />} />
    <Route path="events/:caseId/wiki" element={<CaseWikiPage />} />
    <Route path="events/:caseId/protocol" element={<CaseProtocolPage />} />
    <Route path="events/:caseId/market" element={<CaseMarketPage />} />
    <Route path="events/:caseId/monitor" element={<CaseMonitorPage />} />
    <Route path="events/:caseId/monitor/config" element={<MonitorConfigPage />} />
    <Route path="events/:caseId/relations" element={<CaseRelationsPage />} />
    <Route path="*" element={<Navigate to="/events" replace />} />
  </Route></Routes>;
}
