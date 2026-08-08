import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import type { ReportWikiGraph } from "../domain/eventResearch";
import { ReportResearchScreen } from "../pages/prototype/ReportResearchScreen";

const graph: ReportWikiGraph = {
  researchCaseId: "report-1", scopeVersion: 1, documentId: "doc-1",
  scope: { version: 1, documentId: "doc-1", visibilityCutoffAt: "2026-08-08T00:00:00Z", researchQuestion: "上调资本开支会否压低供应商的现金流预期？", factorSelection: ["现金流"], evidencePlan: [], selectedClaimIds: ["claim-1"], selectedRelationIds: ["relation-1"] },
  nodes: [
    { id: "claim:1", kind: "report_claim", label: "全年资本开支上调", status: "report_claim", sourceLocator: "https://source.test/report", scopeVersion: 1 },
    { id: "evidence:1", kind: "evidence", label: "尚无独立经营数据可验证", status: "candidate", sourceLocator: "valuation_snapshot:private-id", scopeVersion: 1 },
    { id: "evidence:2", kind: "evidence", label: "研报原文页码", status: "report_claim", sourceLocator: '{"page":3,"paragraph":2}', scopeVersion: 1 },
    { id: "market:1d", kind: "market_window", label: "发布后 1 个交易日，证据不足", status: "market_observation", sourceLocator: null, scopeVersion: 1 },
    { id: "market:5d", kind: "market_window", label: "发布后 5 个交易日，尚待验证", status: "market_observation", sourceLocator: null, scopeVersion: 1 },
    { id: "company:unlisted", kind: "company", label: "未上市供应商", status: "report_claim", sourceLocator: '{"page":3,"paragraph":2}', scopeVersion: 1, assetMapping: { companyKind: "unlisted_transmission", aShareCodes: [] } },
    { id: "company:listed", kind: "company", label: "示例 A 股公司", status: "verified", sourceLocator: null, scopeVersion: 1, assetMapping: { companyKind: "listed_a_share", aShareCodes: ["600000.SH"] } },
    { id: "fund:1", kind: "fund", label: "示例中国基金，披露过期", status: "candidate", sourceLocator: null, scopeVersion: 1, assetMapping: { fundCoverage: "stale", computable: false } },
  ],
  edges: [
    { id: "edge:support", sourceId: "claim:1", targetId: "evidence:1", kind: "independent_evidence", status: "verified", relationId: "relation-1", sourceLocator: "valuation_snapshot:private-id", scopeVersion: 1 },
    { id: "edge:confounder", sourceId: "claim:1", targetId: "evidence:1", kind: "confounder:earnings", status: "rejected", relationId: "relation-1", sourceLocator: "valuation_snapshot:private-id", scopeVersion: 1 },
  ],
  factors: [{ claimId: "claim-1", relationId: "relation-1", statement: "资本开支上调压低自由现金流预期", classification: "evidence_gap", components: { report_claim: true, confounder_assessed: false }, explanation: "缺少独立证据与混杂因素核对" }],
};

function renderScreen() {
  return render(<MemoryRouter initialEntries={["/reports/report-1"]}><Routes><Route path="/reports/:caseId" element={<ReportResearchScreen />} /></Routes></MemoryRouter>);
}

describe("ReportResearchScreen", () => {
  let adapter: MockResearchAdapter;
  beforeEach(() => {
    adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getReportWikiGraph").mockResolvedValue(graph);
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("puts the current judgment first and states the evidence gap with 1D/5D windows", async () => {
    renderScreen();
    expect(await screen.findByRole("heading", { name: "当前判断" })).toBeVisible();
    expect(screen.getByText("研报主张")).toBeVisible();
    expect(screen.getByText("发布后 1 个交易日，证据不足")).toBeVisible();
    expect(screen.getByText("发布后 5 个交易日，尚待验证")).toBeVisible();
    expect(screen.getAllByText(/缺少独立证据与混杂因素核对/).length).toBeGreaterThan(0);
    expect(screen.getByText("示例中国基金，披露过期")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看安全来源" })).toHaveAttribute("href", "https://source.test/report");
    expect(screen.getByText("已记录的市场/行业数据快照")).toBeVisible();
    expect(screen.queryByText("valuation_snapshot:private-id")).not.toBeInTheDocument();
    expect(screen.getByText("原文定位：第 3 页 · 第 2 段")).toBeVisible();
    expect(screen.getByText("传导节点，不计算股票或基金暴露")).toBeVisible();
    expect(screen.getByText("A 股映射：600000.SH")).toBeVisible();
    expect(screen.getByText("基金覆盖已过期，不可计算基金暴露")).toBeVisible();
    expect(screen.getByText("存在重大替代解释")).toBeVisible();
  });

  it("gives loading and retryable API failures a deliberate state", async () => {
    vi.spyOn(adapter, "getReportWikiGraph").mockRejectedValueOnce(new Error("服务暂不可用"));
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("button", { name: "重新加载研究" })).toBeVisible();
  });
});
