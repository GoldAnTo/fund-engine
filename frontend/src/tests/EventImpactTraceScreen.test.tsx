import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import type { EventImpactTrace, EventWorkbench } from "../domain/eventResearch";
import { EventImpactTraceScreen } from "../pages/prototype/EventImpactTraceScreen";

function workbench(): EventWorkbench {
  return {
    event: { id: "event-1", eventTitle: "资本开支后的订单传导", companyName: "示例公司", ticker: "600001", eventAt: null, status: "researching", statusSummary: "自动研究中", nextHumanAction: null, updatedAt: "2026-08-08T00:00:00Z" },
    lifecycle: { status: "researching", activeRunId: "run-1", currentRound: 2, summary: "正在核验传导证据", currentGap: "关系来源仍需人工核验", nextHumanAction: null },
    conclusion: { state: "ai_draft", text: "当前材料只支持审慎观察订单传导，尚不能给出投资结论。", confidence: "low", citations: [] },
    factors: [], evidence: [], progress: { verified: 2, pending: 1, invalidSource: 0, currentGap: "关系来源仍需人工核验" }, scope: { version: 2, factors: [], unmappedEvidenceCount: 0 }, nextAction: { kind: "wait", label: "系统继续处理" },
  };
}

function trace(): EventImpactTrace {
  return {
    scopeVersion: 2, asOf: "2026-08-08", progress: { hypotheses: 2, relations: 2 }, alternatives: [],
    factors: [{
      hypothesisId: "h1", statement: "订单向供应链传导", rank: 1, classification: "candidate", scoreComponents: { event: 1, company: 0 }, explanation: "缺少可采纳的关系来源。",
      relations: [
        { relationId: "r-listed", companyId: "c1", companyName: "已上市供应商", companyType: "listed", relationKind: "supplier", direction: "benefits", mechanism: "订单增加", status: "candidate", effectiveStatus: "candidate", sourceStatementId: null, review: null, stocks: [{ stockId: "s1", code: "600001", name: "已上市供应商", market: "SSE" }], observations: [{ kind: "event", status: "verified", sourceStatementId: "statement-1", valuationSnapshotId: null, summary: "事件已确认", asOfDate: "2026-08-08" }], fundExposure: [{ fundId: "f1", fundCode: "000001", fundName: "示例中国基金", reportPeriod: "2026-06-30", publishedAt: "2026-08-01T00:00:00+00:00", source: "https://example.com/disclosure", coverageRatio: 1, coverageStatus: "complete", computable: true, exposure: "0.10" }] },
        { relationId: "r-private", companyId: "c2", companyName: "未上市零部件商", companyType: "unlisted_supplier", relationKind: "supplier", direction: "benefits", mechanism: "供给紧张", status: "candidate", effectiveStatus: "candidate", sourceStatementId: null, review: null, stocks: [], observations: [], fundExposure: [{ fundId: "f2", fundCode: "000002", fundName: "无效链接基金", reportPeriod: "2026-06-30", publishedAt: "2026-08-01T00:00:00+00:00", source: "javascript:alert(1)", coverageRatio: 0.5, coverageStatus: "partial", computable: false, exposure: null }] },
      ],
      funds: [
        { fundId: "f1", fundCode: "000001", fundName: "示例中国基金", reportPeriod: "2026-06-30", publishedAt: "2026-08-01T00:00:00+00:00", source: "https://example.com/disclosure", coverageRatio: 1, coverageStatus: "complete", computable: true, exposure: "0.10" },
        { fundId: "f2", fundCode: "000002", fundName: "无效链接基金", reportPeriod: "2026-06-30", publishedAt: "2026-08-01T00:00:00+00:00", source: "javascript:alert(1)", coverageRatio: 0.5, coverageStatus: "partial", computable: false, exposure: null },
      ],
    }],
  };
}

function renderScreen() {
  return render(<MemoryRouter initialEntries={["/events/event-1/impact"]}><Routes><Route path="/events/:caseId/impact" element={<EventImpactTraceScreen />} /></Routes></MemoryRouter>);
}

describe("EventImpactTraceScreen", () => {
  let adapter: MockResearchAdapter;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getEventWorkbench").mockResolvedValue(workbench());
    vi.spyOn(adapter, "getEventImpactTrace").mockResolvedValue(trace());
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("keeps the conclusion first, then makes transmission, evidence, and fund boundaries inspectable", async () => {
    renderScreen();

    expect(await screen.findByRole("heading", { name: "当前判断" })).toBeVisible();
    expect(screen.getByText("当前材料只支持审慎观察订单传导，尚不能给出投资结论。")).toBeVisible();
    expect(screen.getByRole("heading", { name: "传导关系" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "证据与边界" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "基金披露覆盖" })).toBeVisible();
    expect(screen.getAllByText("未上市零部件商")).toHaveLength(2);
    expect(screen.getByText("未上市主体不映射股票或基金敞口。")).toBeVisible();
    const safeSource = screen.getByRole("link", { name: "查看披露来源" });
    expect(safeSource).toHaveAttribute("href", "https://example.com/disclosure");
    expect(safeSource).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "javascript:alert(1)" })).not.toBeInTheDocument();
  });

  it("uses an accessible review dialog and only records an explicit reviewer decision", async () => {
    const user = userEvent.setup();
    const review = vi.spyOn(adapter, "reviewEventImpactRelation").mockResolvedValue({ reviewId: "review-1", relationId: "r-listed", outcome: "accepted" });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "审核此关系" }));
    expect(screen.getByRole("dialog", { name: "审核公司传导关系" })).toBeVisible();
    await user.selectOptions(screen.getByLabelText("审核结果"), "accepted");
    await user.type(screen.getByLabelText("审核说明"), "已核对披露边界");
    await user.type(screen.getByLabelText("审核人"), "研究员");
    await user.click(screen.getByRole("button", { name: "提交审核" }));

    await waitFor(() => expect(review).toHaveBeenCalledWith({ relationId: "r-listed", outcome: "accepted", reason: "已核对披露边界", reviewer: "研究员" }));
    expect(screen.queryByRole("dialog", { name: "审核公司传导关系" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("已记录审核结果");
  });
});
