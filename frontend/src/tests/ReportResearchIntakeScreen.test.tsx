import { act, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { ReportResearchIntakeScreen } from "../pages/prototype/ReportResearchIntakeScreen";
import type { ReportResearchIntake } from "../domain/eventResearch";

function renderIntake(caseId = "report-pending") {
  return render(
    <MemoryRouter initialEntries={[`/reports/${caseId}/intake`]}>
      <Routes><Route path="/reports/:caseId/intake" element={<ReportResearchIntakeScreen />} /></Routes>
    </MemoryRouter>,
  );
}

describe("ReportResearchIntakeScreen", () => {
  let adapter: MockResearchAdapter;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("shows exactly one truthful action for pending candidate extraction", async () => {
    vi.spyOn(adapter, "getReportResearchIntake").mockResolvedValue({
      caseId: "report-pending", caseTitle: "AI 服务器产业链", primaryDocument: { id: "doc-1", title: "授权研报", inputKind: "pdf_upload" },
      supplementDocuments: [], supplementArtifacts: [], state: "pending_candidate_extraction", blockingReason: null,
      nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" },
    });
    renderIntake();

    expect(await screen.findByRole("heading", { name: "AI 服务器产业链" })).toBeVisible();
    expect(screen.getByText("完成后可审核关键陈述。")).toBeVisible();
    expect(screen.getAllByRole("button", { name: /开始抽取待审核陈述|补充正文并标注页码|查看已保存快照/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "开始抽取待审核陈述" })).toBeDisabled();
    expect(screen.getByText(/候选抽取适配器尚未接入/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "查看 Case 概览" })).not.toBeInTheDocument();
  });

  it("shows the saved-snapshot branch without offering extraction", async () => {
    vi.spyOn(adapter, "getReportResearchIntake").mockResolvedValue({
      caseId: "report-artifact", caseTitle: "重复内容恢复", primaryDocument: { id: "doc-1", title: "原始 PDF", inputKind: "pdf_upload" },
      supplementDocuments: [], supplementArtifacts: [{ id: "artifact-1", claimedPageReference: "第 3 页" }], state: "artifact_extraction_unavailable",
      blockingReason: "已保存为 Case 内快照，等待受控候选抽取适配器。",
      nextAction: { kind: "view_saved_snapshot", label: "查看已保存快照", unlockMessage: "可确认保存状态，候选抽取仍需受控适配器。" },
    });
    renderIntake("report-artifact");

    expect(await screen.findByRole("button", { name: "查看已保存快照" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /抽取/ })).not.toBeInTheDocument();
    expect(screen.getByText("已保存为 Case 内快照，等待受控候选抽取适配器。")).toBeVisible();
  });

  it("keeps a web-source recovery tied to its true input kind", async () => {
    vi.spyOn(adapter, "getReportResearchIntake").mockResolvedValue({
      caseId: "report-web", caseTitle: "网页研报", primaryDocument: { id: "doc-web", title: "网页正文", inputKind: "web_content" },
      supplementDocuments: [], supplementArtifacts: [], state: "needs_supplement", blockingReason: "正文需要补充定位。",
      nextAction: { kind: "supplement_text", label: "补充正文并标注页码", unlockMessage: "冻结补充快照后可进入候选抽取。" },
    });
    renderIntake("report-web");

    const recovery = await screen.findByRole("link", { name: "补充正文并标注页码" });
    expect(recovery).toHaveAttribute("href", expect.stringContaining("resume_input_kind=web_content"));
  });

  it("clears a prior Case title while the newly selected Case is loading", async () => {
    let resolveSecond: ((value: ReportResearchIntake) => void) | undefined;
    vi.spyOn(adapter, "getReportResearchIntake").mockImplementation((caseId) => {
      if (caseId === "report-first") {
        return Promise.resolve({
          caseId, caseTitle: "旧 Case", primaryDocument: { id: "doc-first", title: "旧研报", inputKind: "pdf_upload" },
          supplementDocuments: [], supplementArtifacts: [], state: "pending_candidate_extraction", blockingReason: null,
          nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" },
        });
      }
      return new Promise<ReportResearchIntake>((resolve) => { resolveSecond = resolve; });
    });
    window.history.replaceState(null, "", "/reports/report-first/intake");
    render(<BrowserRouter><Routes><Route path="/reports/:caseId/intake" element={<ReportResearchIntakeScreen />} /></Routes></BrowserRouter>);
    expect(await screen.findByRole("heading", { name: "旧 Case" })).toBeVisible();

    await act(async () => {
      window.history.pushState(null, "", "/reports/report-second/intake");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(screen.queryByRole("heading", { name: "旧 Case" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "正在读取冻结资料" })).toBeVisible();
    await act(async () => resolveSecond?.({
      caseId: "report-second", caseTitle: "新 Case", primaryDocument: { id: "doc-second", title: "新研报", inputKind: "pdf_upload" },
      supplementDocuments: [], supplementArtifacts: [], state: "pending_candidate_extraction", blockingReason: null,
      nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" },
    }));
    expect(await screen.findByRole("heading", { name: "新 Case" })).toBeVisible();
  });

  it("ignores a late prior-Case intake response", async () => {
    let resolveFirst: ((value: ReportResearchIntake) => void) | undefined;
    let resolveSecond: ((value: ReportResearchIntake) => void) | undefined;
    vi.spyOn(adapter, "getReportResearchIntake").mockImplementation((caseId) => new Promise<ReportResearchIntake>((resolve) => {
      if (caseId === "report-first") resolveFirst = resolve;
      else resolveSecond = resolve;
    }));
    window.history.replaceState(null, "", "/reports/report-first/intake");
    render(<BrowserRouter><Routes><Route path="/reports/:caseId/intake" element={<ReportResearchIntakeScreen />} /></Routes></BrowserRouter>);

    await act(async () => {
      window.history.pushState(null, "", "/reports/report-second/intake");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(adapter.getReportResearchIntake).toHaveBeenLastCalledWith("report-second"));
    await act(async () => resolveSecond?.({
      caseId: "report-second", caseTitle: "新 Case", primaryDocument: { id: "doc-second", title: "新研报", inputKind: "pdf_upload" },
      supplementDocuments: [], supplementArtifacts: [], state: "pending_candidate_extraction", blockingReason: null,
      nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" },
    }));
    expect(await screen.findByRole("heading", { name: "新 Case" })).toBeVisible();
    await act(async () => resolveFirst?.({
      caseId: "report-first", caseTitle: "旧 Case", primaryDocument: { id: "doc-first", title: "旧研报", inputKind: "pdf_upload" },
      supplementDocuments: [], supplementArtifacts: [], state: "pending_candidate_extraction", blockingReason: null,
      nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" },
    }));
    expect(screen.queryByRole("heading", { name: "旧 Case" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "新 Case" })).toBeVisible();
  });

  it("retries the current frozen Case after a recoverable read error", async () => {
    vi.spyOn(adapter, "getReportResearchIntake")
      .mockRejectedValueOnce(new Error("网络暂时不可用"))
      .mockResolvedValueOnce({
        caseId: "report-pending", caseTitle: "恢复后的 Case", primaryDocument: { id: "doc-1", title: "授权研报", inputKind: "pdf_upload" },
        supplementDocuments: [], supplementArtifacts: [], state: "pending_candidate_extraction", blockingReason: null,
        nextAction: { kind: "extract_candidates", label: "开始抽取待审核陈述", unlockMessage: "完成后可审核关键陈述。" },
      });
    renderIntake();

    expect(await screen.findByRole("alert")).toHaveTextContent("网络暂时不可用");
    await act(async () => screen.getByRole("button", { name: "重新读取" }).click());
    expect(await screen.findByRole("heading", { name: "恢复后的 Case" })).toBeVisible();
    expect(adapter.getReportResearchIntake).toHaveBeenCalledTimes(2);
  });
});
