import { BrowserRouter, Route, Routes } from "react-router-dom";
import { UnderwritingArchiveShell } from "./UnderwritingArchiveShell";
import ResearchArchivePage from "../features/underwriting/ResearchArchivePage";
import "../styles/company-research.css";
import "../styles/research-archive.css";

export default function ResearchArchiveApp() {
  return <BrowserRouter><Routes><Route element={<UnderwritingArchiveShell />}>
    <Route path="/underwriting/research" element={<ResearchArchivePage />} />
    <Route path="/underwriting/research/:objectId/:versionKind" element={<ResearchArchivePage />} />
    <Route path="*" element={<p role="alert">未找到档案页面。<a href="/underwriting/research">返回目录</a></p>} />
  </Route></Routes></BrowserRouter>;
}
