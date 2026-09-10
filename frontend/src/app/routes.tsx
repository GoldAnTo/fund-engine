import { lazy, Suspense, type ReactNode } from "react";
import { Route, Routes } from "react-router-dom";

import { InvestmentResearchShell } from "./InvestmentResearchShell";
import { ProductRouteErrorBoundary } from "./ProductRouteErrorBoundary";

export { ResearchArchiveLoadErrorBoundary } from "./ResearchArchiveLoadErrorBoundary";

const LegacyResearchRoutes = lazy(() => import("./LegacyResearchRoutes"));
const ResearchHomePage = lazy(() => import("../features/investment-research/ResearchHomePage"));
const NewResearchPage = lazy(() => import("../features/investment-research/NewResearchPage"));
const ResearchWorkbenchPage = lazy(() => import("../features/investment-research/ResearchWorkbenchPage"));

function ProductRoute({ children }: { children: ReactNode }) {
  return (
    <ProductRouteErrorBoundary onRetry={() => { window.location.reload(); return false; }}>
      <Suspense fallback={<main className="ir-page" aria-busy="true"><div className="ir-workbench-skeleton"><span /><span /><span /></div></main>}>
        {children}
      </Suspense>
    </ProductRouteErrorBoundary>
  );
}

function ProductNotFound() {
  return (
    <main className="ir-page">
      <div className="ir-empty">
        <h1>研究页面不存在</h1>
        <p>该地址不属于独立投资研究产品，或页面已经移动。</p>
        <a href="/research">返回研究目录</a>
      </div>
    </main>
  );
}

export function ResearchOsRoutes() {
  return (
    <Routes>
      <Route path="research" element={<InvestmentResearchShell />}>
        <Route index element={<ProductRoute><ResearchHomePage /></ProductRoute>} />
        <Route path="new" element={<ProductRoute><NewResearchPage /></ProductRoute>} />
        <Route path="projects/:projectId" element={<ProductRoute><ResearchWorkbenchPage /></ProductRoute>} />
        <Route path="*" element={<ProductNotFound />} />
      </Route>
      <Route path="*" element={<Suspense fallback={<main className="ros-page" aria-busy="true"><p>正在载入研究系统…</p></main>}><LegacyResearchRoutes /></Suspense>} />
    </Routes>
  );
}
