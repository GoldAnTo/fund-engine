import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
import { CaseConclusionPage, CaseMarketPage, CaseMonitorPage, CaseReviewPage, CaseWikiPage, MonitorConfigPage } from "../features/case/CasePages";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { EventDeskPage } from "../features/events/EventDeskPage";

export function ResearchOsRoutes() { return <Routes><Route element={<AppShell />}><Route index element={<Navigate to="/events" replace />} /><Route path="events" element={<EventDeskPage />} /><Route path="events/new" element={<EventCreatePage />} /><Route path="events/:caseId" element={<CaseConclusionPage />} /><Route path="events/:caseId/review" element={<CaseReviewPage />} /><Route path="events/:caseId/wiki" element={<CaseWikiPage />} /><Route path="events/:caseId/market" element={<CaseMarketPage />} /><Route path="events/:caseId/monitor" element={<CaseMonitorPage />} /><Route path="events/:caseId/monitor/config" element={<MonitorConfigPage />} /><Route path="*" element={<Navigate to="/events" replace />} /></Route></Routes>; }
