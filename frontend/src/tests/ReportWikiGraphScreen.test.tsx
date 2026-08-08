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
  scope: { version: 1, documentId: "doc-1", visibilityCutoffAt: "2026-08-08T00:00:00Z", researchQuestion: "供应链影响？", factorSelection: [], evidencePlan: [], selectedClaimIds: ["claim-1"], selectedRelationIds: ["relation-1", "relation-2"] },
  nodes: [{ id: "claim:1", kind: "report_claim", label: "研报主张", status: "report_claim", sourceLocator: "第 3 页", scopeVersion: 1 }, { id: "company:1", kind: "company", label: "示例公司", status: "verified", sourceLocator: null, scopeVersion: 1 }],
  edges: [{ id: "edge:1", sourceId: "claim:1", targetId: "company:1", kind: "影响", status: "verified", relationId: "relation-1", sourceLocator: "第 3 页", scopeVersion: 1 }],
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
    expect(screen.getByRole("status")).toHaveTextContent("第 3 页");
  });
});
