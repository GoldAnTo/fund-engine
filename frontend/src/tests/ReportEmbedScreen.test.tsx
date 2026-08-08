import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { BrowserRouter, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { ReportEmbedScreen } from "../pages/prototype/ReportWikiGraphScreen";
import type { ReportEmbedWikiGraph } from "../domain/eventResearch";

describe("ReportEmbedScreen", () => {
  let adapter: MockResearchAdapter;
  beforeEach(() => { adapter = new MockResearchAdapter(); setResearchClient(adapter); });
  afterEach(() => resetResearchClient());

  it("only uses a hash token and renders a read-only redacted graph", async () => {
    vi.spyOn(adapter, "getReportEmbedWiki").mockResolvedValue({ nodes: [{ id: "n1", kind: "company", label: "公司", status: "verified" }], edges: [], factors: [{ classification: "key", components: {}, explanation: "已验证" }] });
    const regularGraph = vi.spyOn(adapter, "getReportWikiGraph");
    window.history.replaceState(null, "", "/embed/reports/report-1/wiki#token=hash-only-token");
    render(<StrictMode><MemoryRouter initialEntries={["/embed/reports/report-1/wiki#token=hash-only-token"]}><Routes><Route path="/embed/reports/:caseId/wiki" element={<ReportEmbedScreen embedClient={{ getReportEmbedWiki: (caseId, token) => adapter.getReportEmbedWiki!(caseId, token) }} />} /></Routes></MemoryRouter></StrictMode>);
    expect(await screen.findByText("已验证")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(adapter.getReportEmbedWiki).toHaveBeenCalledTimes(2);
    expect(adapter.getReportEmbedWiki).toHaveBeenLastCalledWith("report-1", "hash-only-token");
    expect(regularGraph).not.toHaveBeenCalled();
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

  it("keeps a late prior-case response from replacing the active case", async () => {
    let resolveFirst: ((value: ReportEmbedWikiGraph) => void) | undefined;
    let resolveSecond: ((value: ReportEmbedWikiGraph) => void) | undefined;
    const embedClient = {
      getReportEmbedWiki: vi.fn((caseId: string): Promise<ReportEmbedWikiGraph> => new Promise<ReportEmbedWikiGraph>((resolve) => {
        if (caseId === "report-1") resolveFirst = resolve;
        else resolveSecond = resolve;
      })),
    };
    window.history.replaceState(null, "", "/embed/reports/report-1/wiki#token=stable-token");
    render(<BrowserRouter><Routes><Route path="/embed/reports/:caseId/wiki" element={<ReportEmbedScreen embedClient={embedClient} />} /></Routes></BrowserRouter>);
    expect(embedClient.getReportEmbedWiki).toHaveBeenCalledWith("report-1", "stable-token");
    await act(async () => {
      window.history.pushState(null, "", "/embed/reports/report-2/wiki");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(embedClient.getReportEmbedWiki).toHaveBeenLastCalledWith("report-2", "stable-token"));
    await act(async () => resolveSecond?.({ nodes: [], edges: [], factors: [{ classification: "key", components: {}, explanation: "新案例" }] }));
    expect(await screen.findByText("新案例")).toBeVisible();
    await act(async () => resolveFirst?.({ nodes: [], edges: [], factors: [{ classification: "key", components: {}, explanation: "旧案例" }] }));
    expect(screen.queryByText("旧案例")).not.toBeInTheDocument();
  });
});
