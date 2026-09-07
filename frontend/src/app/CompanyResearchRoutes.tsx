import { Route, Routes } from "react-router-dom";
import { InvestmentResearchShell } from "./InvestmentResearchShell";
import NewResearchPage from "../features/investment-research/NewResearchPage";
import ResearchHomePage from "../features/investment-research/ResearchHomePage";
import ResearchWorkbenchPage from "../features/investment-research/ResearchWorkbenchPage";

export function CompanyResearchRoutes() {
  return <Routes>
    <Route element={<InvestmentResearchShell />}>
      <Route path="/research" element={<ResearchHomePage />} />
      <Route path="/research/new" element={<NewResearchPage />} />
      <Route path="/research/projects/:projectId" element={<ResearchWorkbenchPage />} />
      <Route path="*" element={<p role="alert">未找到公司研究页面。<a href="/research">返回研究目录</a></p>} />
    </Route>
  </Routes>;
}
