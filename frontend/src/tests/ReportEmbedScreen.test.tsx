import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { ReportEmbedScreen } from "../pages/prototype/ReportWikiGraphScreen";

describe("ReportEmbedScreen", () => {
  let adapter: MockResearchAdapter;
  beforeEach(() => { adapter = new MockResearchAdapter(); setResearchClient(adapter); });
  afterEach(() => resetResearchClient());

  it("only uses a hash token and renders a read-only redacted graph", async () => {
    vi.spyOn(adapter, "getReportEmbedWiki").mockResolvedValue({ nodes: [{ id: "n1", kind: "company", label: "公司", status: "verified" }], edges: [], factors: [{ classification: "key", components: {}, explanation: "已验证" }] });
    window.history.replaceState(null, "", "/embed/reports/report-1/wiki#token=hash-only-token");
    render(<MemoryRouter initialEntries={["/embed/reports/report-1/wiki#token=hash-only-token"]}><Routes><Route path="/embed/reports/:caseId/wiki" element={<ReportEmbedScreen embedClient={{ getReportEmbedWiki: (caseId, token) => adapter.getReportEmbedWiki!(caseId, token) }} />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("只读研究关系图谱")).toBeVisible();
    expect(adapter.getReportEmbedWiki).toHaveBeenCalledWith("report-1", "hash-only-token");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/report-1|doc-/)).not.toBeInTheDocument();
  });

  it("explains denied access without falling back to regular graph data", async () => {
    vi.spyOn(adapter, "getReportEmbedWiki").mockRejectedValue(new Error("embed access denied"));
    window.history.replaceState(null, "", "/embed/reports/report-1/wiki#token=wrong");
    render(<MemoryRouter initialEntries={["/embed/reports/report-1/wiki#token=wrong"]}><Routes><Route path="/embed/reports/:caseId/wiki" element={<ReportEmbedScreen embedClient={{ getReportEmbedWiki: (caseId, token) => adapter.getReportEmbedWiki!(caseId, token) }} />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法显示嵌入图谱");
  });

  it("does not let a URL mock flag select the regular mock client", async () => {
    const mockRead = vi.spyOn(adapter, "getReportEmbedWiki");
    window.history.replaceState(null, "", "/embed/reports/report-1/wiki?client=mock#token=untrusted-url-token");
    render(<MemoryRouter initialEntries={["/embed/reports/report-1/wiki?client=mock#token=untrusted-url-token"]}><Routes><Route path="/embed/reports/:caseId/wiki" element={<ReportEmbedScreen />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("嵌入API未配置为独立来源");
    expect(mockRead).not.toHaveBeenCalled();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});
