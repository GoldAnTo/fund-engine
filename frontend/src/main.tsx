import React from "react";
import ReactDOM from "react-dom/client";
import { ResearchWorkbenchPage } from "./pages/ResearchWorkbenchPage";

const params = new URLSearchParams(window.location.search);
const caseId = params.get("case") ?? "ai-compute";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ResearchWorkbenchPage caseId={caseId} />
  </React.StrictMode>
);
