import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import type { EventWorkbench } from "../domain/eventResearch";
import { EventResearchWorkbenchScreen } from "../pages/prototype/EventResearchWorkbenchScreen";

const initialFactors = ["资本开支压力", "盈利预期变化", "估值重定价"];

function workbench(overrides: Partial<EventWorkbench> = {}): EventWorkbench {
  return {
    event: { id: "event-1", eventTitle: "财报后的异常下跌", companyName: "示例公司", ticker: "XYZ", eventAt: null, status: "exhausted", statusSummary: "需要补充研究范围", nextHumanAction: "编辑因素", updatedAt: "2026-08-07T00:00:00Z" },
    lifecycle: { status: "exhausted", activeRunId: null, currentRound: 3, summary: "已核验现有材料，等待补充范围", currentGap: "缺少能区分主要解释的反证", nextHumanAction: "编辑因素" },
    conclusion: { state: "cannot_conclude", text: "当前证据不足以判断主要原因。", citations: [] },
    factors: initialFactors.map((statement, index) => ({ statement, position: index + 1, reviewedSupportCount: index + 1, reviewedContradictionCount: index, currentGap: index === 0 ? "需要更多反证" : null })),
    evidence: [],
    progress: { verified: 3, pending: 2, invalidSource: 1, currentGap: "缺少能区分主要解释的反证" },
    scope: { version: 1, factors: initialFactors, unmappedEvidenceCount: 0 },
    nextAction: { kind: "edit_factors", label: "编辑并继续自动研究" },
    ...overrides,
  };
}

function renderWorkbench(caseId = "event-1") {
  return render(<MemoryRouter initialEntries={[`/events/${caseId}`]}><Routes><Route path="/events/:caseId" element={<EventResearchWorkbenchScreen />} /></Routes></MemoryRouter>);
}

describe("EventResearchWorkbenchScreen", () => {
  let adapter: MockResearchAdapter;
  let currentView: EventWorkbench;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    currentView = workbench();
    vi.spyOn(adapter, "getEventWorkbench").mockImplementation(async () => currentView);
    setResearchClient(adapter);
  });

  afterEach(() => resetResearchClient());

  it("puts an inconclusive judgment, reliable progress, and the sole edit action first", async () => {
    renderWorkbench();

    expect(await screen.findByRole("heading", { name: "暂不能下结论" })).toBeVisible();
    expect(screen.getByText("当前证据不足以判断主要原因。")).toBeVisible();
    expect(screen.getByText("已核验 3")).toBeVisible();
    expect(screen.getByText("待审核 2")).toBeVisible();
    expect(screen.getByText("无效来源不计结论 1")).toBeVisible();
    expect(screen.getByText(/研究中：已核验现有材料/)).toBeVisible();
    expect(screen.getByRole("button", { name: "编辑并继续自动研究" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "查看研究依据" })).not.toBeInTheDocument();
  });

  it("links the evidence-review action when key evidence awaits review", async () => {
    currentView = workbench({
      lifecycle: { ...workbench().lifecycle, status: "awaiting_key_review" },
      nextAction: { kind: "review_evidence", label: "审核 2 条关键证据", count: 2 },
    });
    renderWorkbench();

    expect(await screen.findByRole("link", { name: "审核 2 条关键证据" })).toHaveAttribute("href", "/events/event-1/review");
  });

  it("links the conclusion review action when a draft is ready", async () => {
    currentView = workbench({
      conclusion: { state: "ai_draft", text: "当前证据最支持资本开支压力。", citations: [] },
      lifecycle: { ...workbench().lifecycle, status: "draft_ready" },
      nextAction: { kind: "review_conclusion", label: "审核结论草案" },
    });
    renderWorkbench();

    expect(await screen.findByRole("link", { name: "审核结论草案" })).toHaveAttribute("href", "/events/event-1/conclusion");
  });

  it("adds, reorders, and saves factors before reloading the factor statements", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(adapter, "updateEventResearchScope").mockImplementation(async ({ factors }) => {
      currentView = workbench({
        factors: factors.map((statement, index) => ({ statement, position: index + 1, reviewedSupportCount: 0, reviewedContradictionCount: 0, currentGap: null })),
        scope: { version: 2, factors, unmappedEvidenceCount: 0 },
      });
      return { version: 2, factors, reclassifiedEvidenceCount: 2, unmappedEvidenceCount: 0 };
    });
    renderWorkbench();

    await user.click(await screen.findByRole("button", { name: "编辑并继续自动研究" }));
    await user.click(screen.getByRole("button", { name: "添加因素" }));
    await user.type(screen.getByLabelText("因素 4"), "现金流压力");
    await user.click(screen.getByRole("button", { name: "上移因素 4" }));
    await user.click(screen.getByRole("button", { name: "保存因素并继续自动研究" }));

    await waitFor(() => expect(update).toHaveBeenCalledWith({
      caseId: "event-1",
      factors: ["资本开支压力", "盈利预期变化", "现金流压力", "估值重定价"],
      changedBy: "研究员",
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("已更新因素；已核验证据会保留并重新归类，系统将继续自动研究");
    expect(screen.getByText("3. 现金流压力")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "编辑研究因素" })).not.toBeInTheDocument();
  });

  it("keeps the editor open and displays an error when saving factors fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(adapter, "updateEventResearchScope").mockRejectedValue(new Error("服务暂不可用"));
    renderWorkbench();

    await user.click(await screen.findByRole("button", { name: "编辑并继续自动研究" }));
    await user.click(screen.getByRole("button", { name: "保存因素并继续自动研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败：服务暂不可用");
    expect(screen.getByRole("dialog", { name: "编辑研究因素" })).toBeVisible();
  });

  it("does not offer factor editing after publication and links to the conclusion", async () => {
    currentView = workbench({
      event: { ...workbench().event, id: "event-published", status: "published" },
      conclusion: { state: "published", text: "人工确认的结论。", citations: [] },
      lifecycle: { ...workbench().lifecycle, status: "published" },
      nextAction: { kind: "view_conclusion_change", label: "查看结论变更" },
    });
    renderWorkbench("event-published");

    expect(await screen.findByRole("heading", { name: "已确认正式结论" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "编辑并继续自动研究" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看结论变更" })).toHaveAttribute("href", "/events/event-published/conclusion");
  });
});
