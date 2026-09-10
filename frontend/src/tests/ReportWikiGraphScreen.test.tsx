import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import type { ReportWikiGraph } from "../domain/eventResearch";
import { ReportWikiGraphScreen } from "../pages/prototype/ReportWikiGraphScreen";

const graph: ReportWikiGraph = {
  researchCaseId: "report-1", scopeVersion: 1, documentId: "doc-1",
  scope: { version: 1, documentId: "doc-1", visibilityCutoffAt: "2026-08-08T00:00:00Z", researchQuestion: "供应链影响？", factorSelection: [], evidencePlan: [], selectedClaimIds: ["claim-1"], selectedRelationIds: ["relation-1", "relation-2"], changedBy: "report-research-system", changeSummary: "初始研报研究范围", createdAt: "2026-08-08T00:00:00Z" },
  nodes: [
    { id: "claim:1", kind: "report_claim", label: "研报主张", status: "report_claim", sourceLocator: '{"page":3,"paragraph":1}', scopeVersion: 1 },
    { id: "company:1", kind: "company", label: "示例公司", status: "verified", sourceLocator: null, scopeVersion: 1 },
    { id: "evidence:1", kind: "evidence", label: "独立证据", status: "verified", sourceLocator: "industry_index_snapshot:private", scopeVersion: 1 },
    { id: "evidence:2", kind: "evidence", label: "经营证据二", status: "candidate", sourceLocator: null, scopeVersion: 1 },
    { id: "evidence:3", kind: "evidence", label: "经营证据三", status: "candidate", sourceLocator: null, scopeVersion: 1 },
    { id: "evidence:4", kind: "evidence", label: "经营证据四", status: "candidate", sourceLocator: null, scopeVersion: 1 },
    { id: "market:1", kind: "market_window", label: "发布后 1D：下跌", status: "market_observation", sourceLocator: "valuation_snapshot:private", scopeVersion: 1 },
    { id: "fund:1", kind: "fund", label: "示例基金", status: "candidate", sourceLocator: null, scopeVersion: 1 },
  ],
  edges: [{ id: "edge:1", sourceId: "claim:1", targetId: "company:1", kind: "影响", status: "verified", relationId: "relation-1", sourceLocator: '{"page":3,"paragraph":1}', scopeVersion: 1 }],
  factors: [{ claimId: "claim-1", relationId: "relation-1", statement: "当前选中路径", classification: "key", components: { confounder_assessed: true }, explanation: "路径已核验" }, { claimId: "claim-1", relationId: "relation-2", statement: "替代路径", classification: "alternative", components: {}, explanation: "需要对照" }],
};

function renderScreen() {
  return render(<MemoryRouter initialEntries={["/reports/report-1/wiki"]}><Routes><Route path="/reports/:caseId/wiki" element={<ReportWikiGraphScreen />} /></Routes></MemoryRouter>);
}

describe("ReportWikiGraphScreen", () => {
  let adapter: MockResearchAdapter;
  beforeEach(() => {
    adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getReportWikiGraph").mockResolvedValue(graph);
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("defaults to a selected path and keeps a keyboard-accessible structured alternative", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "研报关系图谱" })).toBeVisible();
    expect(screen.getByRole("button", { name: "当前路径" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "结构化路径" })).toBeVisible();
    expect(screen.getByRole("button", { name: /研报主张.*示例公司/ })).toBeVisible();
    expect(screen.getByText(/图谱用于定位路径，不替代来源与证据判断/)).toBeVisible();
  });

  it("can show all scoped relations and reveals a safe source locator after a path click", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByRole("heading", { name: "研报关系图谱" });
    await user.click(screen.getByRole("button", { name: "所有关系" }));
    expect(adapter.getReportWikiGraph).toHaveBeenLastCalledWith("report-1", undefined);
    await user.click(screen.getByRole("button", { name: /研报主张.*示例公司/ }));
    expect(screen.getByRole("status")).toHaveTextContent("原文定位：第 3 页 · 第 1 段");
    expect(screen.queryByText("industry_index_snapshot:private")).not.toBeInTheDocument();
  });

  it("keeps every node selectable in a dynamically sized SVG", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "研报关系图谱" });
    const svg = screen.getByRole("group", { name: /当前研究范围的关系图谱/ });
    expect(svg.getAttribute("viewBox")).toMatch(/0 0 820 (?:3|4|5|6|7|8|9)\d{2}/);
    expect(screen.queryByRole("button", { name: "查看 示例基金 的来源定位或状态" })).not.toBeInTheDocument();
  });

  it("disables current-path mode when the selected scope has no relation path", async () => {
    const user = userEvent.setup();
    vi.spyOn(adapter, "getReportWikiGraph").mockResolvedValue({ ...graph, factors: [{ ...graph.factors[0], relationId: null }] });
    renderScreen();
    const button = await screen.findByRole("button", { name: "当前路径" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/当前范围没有可选关系路径/)).toBeVisible();
    await user.click(button);
    expect(adapter.getReportWikiGraph).toHaveBeenCalledTimes(1);
  });

  it("retries the initial base-to-selected sequence when selected-path loading fails", async () => {
    const user = userEvent.setup();
    const initial = vi.spyOn(adapter, "getReportWikiGraph")
      .mockResolvedValueOnce(graph)
      .mockRejectedValueOnce(new Error("选中路径暂不可用"))
      .mockResolvedValueOnce(graph)
      .mockResolvedValueOnce(graph);
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("选中路径暂不可用");
    await user.click(screen.getByRole("button", { name: "重新加载图谱" }));
    expect(await screen.findByRole("heading", { name: "研报关系图谱" })).toBeVisible();
    expect(initial.mock.calls.slice(-2)).toEqual([["report-1", undefined], ["report-1", { relationId: "relation-1" }]]);
  });
});
