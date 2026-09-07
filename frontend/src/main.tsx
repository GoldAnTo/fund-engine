import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { LiveResearchWorkbench } from "@/workbench/LiveResearchWorkbench";
import { ResearchWorkbench } from "@/workbench/ResearchWorkbench";
import { AppErrorBoundary } from "@/app/AppErrorBoundary";

import "@/styles/global.css";
import "@/styles/app.css";

const EventMarketApp = lazy(() => import("@/app/EventMarketApp"));
const CompanyResearchApp = lazy(() => import("@/app/CompanyResearchApp"));
const ResearchArchiveApp = lazy(() => import("@/app/ResearchArchiveApp"));

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("#root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <AppErrorBoundary>
    {/^\/events\/[^/]+\/(?:market|stocks|funds)(?:\/|$)/.test(window.location.pathname)
      ? <Suspense fallback={<p role="status">正在加载市场研究…</p>}><EventMarketApp /></Suspense>
      : /^\/underwriting\/research(?:\/|$)/.test(window.location.pathname)
      ? <Suspense fallback={<p role="status">正在加载研究档案…</p>}><ResearchArchiveApp /></Suspense>
      : /^\/research(?:\/|$)/.test(window.location.pathname)
      ? <Suspense fallback={<p role="status">正在加载公司研究…</p>}><CompanyResearchApp /></Suspense>
      : new URLSearchParams(window.location.search).get("client") === "mock" ? <ResearchWorkbench /> : <LiveResearchWorkbench />}
    </AppErrorBoundary>
  </StrictMode>,
);
