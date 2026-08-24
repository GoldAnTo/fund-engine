import { Component, lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./AppShell";
import { InvestmentResearchShell } from "./InvestmentResearchShell";
import { UnderwritingArchiveShell } from "./UnderwritingArchiveShell";
import {
  CaseConclusionHistoryPage,
  CaseConclusionPage,
  CaseDocumentsPage,
  CaseEvidencePage,
  CaseFundProfilePage,
  CaseMarketPage,
  CaseMonitorPage,
  CaseProtocolPage,
  CaseRelationsPage,
  CaseReviewPage,
  CaseScopePage,
  CaseStockProfilePage,
  CaseWikiPage,
  MonitorConfigPage,
} from "../features/case/CasePages";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { AutomaticResearchPage } from "../features/events/AutomaticResearchPage";
import { EventDeskPage } from "../features/events/EventDeskPage";
import { ResearchPreparationPage } from "../features/events/ResearchPreparationPage";
import { GlobalMonitoringPage } from "../features/events/GlobalMonitoringPage";
import { ResearchNetworkPage } from "../features/events/ResearchNetworkPage";
import { LegacyAdmissionPage } from "../features/events/LegacyAdmissionPage";

const ResearchArchivePage = lazy(() => import("../features/underwriting/ResearchArchivePage"));
const ResearchHomePage = lazy(() => import("../features/investment-research/ResearchHomePage"));
const NewResearchPage = lazy(() => import("../features/investment-research/NewResearchPage"));
const ResearchWorkbenchPage = lazy(() => import("../features/investment-research/ResearchWorkbenchPage"));

type ResearchArchiveLoadErrorBoundaryProps = {
  children: ReactNode;
  resetKey?: string;
};

type ResearchArchiveLoadErrorBoundaryState = {
  hasError: boolean;
};

export class ResearchArchiveLoadErrorBoundary extends Component<
  ResearchArchiveLoadErrorBoundaryProps,
  ResearchArchiveLoadErrorBoundaryState
> {
  state: ResearchArchiveLoadErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ResearchArchiveLoadErrorBoundaryState {
    return { hasError: true };
  }

  componentDidUpdate(previousProps: ResearchArchiveLoadErrorBoundaryProps) {
    if (this.state.hasError && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="ros-page" role="alert" aria-live="assertive">
          <header className="ros-page-head">
            <div>
              <p className="ros-eyebrow">研究资产 · Research Archive</p>
              <h1>档案暂时无法载入</h1>
              <p>公司／行业档案未能载入。不会以当前或最新研究替代这个版本。</p>
              <p>请刷新页面后重试，或返回档案目录重新打开。</p>
              <a href="/underwriting/research">返回档案目录</a>
            </div>
          </header>
        </main>
      );
    }

    return this.props.children;
  }
}

function ResearchArchiveRoute() {
  const location = useLocation();
  return (
    <ResearchArchiveLoadErrorBoundary resetKey={location.pathname}>
      <Suspense fallback={(
        <main className="ros-page" aria-busy="true">
          <p>正在载入公司／行业档案…</p>
        </main>
      )}>
        <ResearchArchivePage />
      </Suspense>
    </ResearchArchiveLoadErrorBoundary>
  );
}

function InvestmentResearchRoute({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={(
      <main className="ir-page" aria-busy="true">
        <div className="ir-workbench-skeleton"><span /><span /><span /></div>
      </main>
    )}>
      {children}
    </Suspense>
  );
}

export function ResearchOsRoutes() {
  return (
    <Routes>
      <Route path="research" element={<InvestmentResearchShell />}>
        <Route index element={<InvestmentResearchRoute><ResearchHomePage /></InvestmentResearchRoute>} />
        <Route path="new" element={<InvestmentResearchRoute><NewResearchPage /></InvestmentResearchRoute>} />
        <Route
          path="projects/:projectId"
          element={<InvestmentResearchRoute><ResearchWorkbenchPage /></InvestmentResearchRoute>}
        />
      </Route>
      <Route element={<UnderwritingArchiveShell />}>
        <Route path="underwriting/research" element={<ResearchArchiveRoute />} />
        <Route
          path="underwriting/research/:objectId/:versionKind"
          element={<ResearchArchiveRoute />}
        />
      </Route>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/events" replace />} />
        <Route path="events" element={<EventDeskPage />} />
        <Route path="network" element={<ResearchNetworkPage />} />
        <Route path="monitoring" element={<GlobalMonitoringPage />} />
        <Route path="governance/case-admissions" element={<LegacyAdmissionPage />} />
        <Route path="events/new" element={<EventCreatePage />} />
        <Route
          path="events/:caseId/automatic-research"
          element={<AutomaticResearchPage />}
        />
        <Route path="events/:caseId/preparation" element={<ResearchPreparationPage />} />
        <Route path="events/:caseId" element={<CaseConclusionPage />} />
        <Route
          path="events/:caseId/history"
          element={<CaseConclusionHistoryPage />}
        />
        <Route path="events/:caseId/scope" element={<CaseScopePage />} />
        <Route path="events/:caseId/evidence" element={<CaseEvidencePage />} />
        <Route
          path="events/:caseId/documents"
          element={<CaseDocumentsPage />}
        />
        <Route path="events/:caseId/review" element={<CaseReviewPage />} />
        <Route path="events/:caseId/wiki" element={<CaseWikiPage />} />
        <Route path="events/:caseId/protocol" element={<CaseProtocolPage />} />
        <Route path="events/:caseId/market" element={<CaseMarketPage />} />
        <Route
          path="events/:caseId/stocks/:stockId"
          element={<CaseStockProfilePage />}
        />
        <Route
          path="events/:caseId/funds/:fundId"
          element={<CaseFundProfilePage />}
        />
        <Route path="events/:caseId/monitor" element={<CaseMonitorPage />} />
        <Route
          path="events/:caseId/monitor/config"
          element={<MonitorConfigPage />}
        />
        <Route
          path="events/:caseId/relations"
          element={<CaseRelationsPage />}
        />
        <Route path="*" element={<Navigate to="/events" replace />} />
      </Route>
    </Routes>
  );
}
