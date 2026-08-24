import { lazy, Suspense, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";

import { InvestmentResearchShell } from "./InvestmentResearchShell";

export { ResearchArchiveLoadErrorBoundary } from "./ResearchArchiveLoadErrorBoundary";

const LegacyResearchRoutes = lazy(() => import("./LegacyResearchRoutes"));
const ResearchHomePage = lazy(() => import("../features/investment-research/ResearchHomePage"));
const NewResearchPage = lazy(() => import("../features/investment-research/NewResearchPage"));
const ResearchWorkbenchPage = lazy(() => import("../features/investment-research/ResearchWorkbenchPage"));

function ProductRoute({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<main className="ir-page" aria-busy="true"><div className="ir-workbench-skeleton"><span /><span /><span /></div></main>}>
      {children}
    </Suspense>
  );
}

export function ResearchOsRoutes() {
  return (
    <Routes>
      <Route path="research" element={<InvestmentResearchShell />}>
        <Route index element={<ProductRoute><ResearchHomePage /></ProductRoute>} />
        <Route path="new" element={<ProductRoute><NewResearchPage /></ProductRoute>} />
        <Route path="projects/:projectId" element={<ProductRoute><ResearchWorkbenchPage /></ProductRoute>} />
      </Route>
      <Route path="*" element={<Suspense fallback={<main className="ros-page" aria-busy="true"><p>正在载入研究系统…</p></main>}><LegacyResearchRoutes /></Suspense>} />
    </Routes>
  );
}
