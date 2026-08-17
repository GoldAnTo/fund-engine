import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import type { AutomaticResearchView } from "../domain/automaticResearch";
import { AutomaticResearchPage } from "../features/events/AutomaticResearchPage";

function completedView(overrides: Partial<AutomaticResearchView> = {}): AutomaticResearchView {
  return {
    caseId: "case/alpha",
    runId: "run-completed",
    title: "英伟达新产品供应链影响",
    status: "completed",
    stages: [
      { key: "acquire", label: "采集资料", status: "completed", summary: "找到 3 份资料", startedAt: "2026-08-17T01:00:00Z", completedAt: "2026-08-17T01:00:10Z" },
      { key: "parse", label: "解析内容", status: "completed", summary: "提取原文", startedAt: "2026-08-17T01:00:10Z", completedAt: "2026-08-17T01:00:20Z" },
      { key: "admit", label: "证据准入", status: "completed", summary: "自动纳入 2 条", startedAt: "2026-08-17T01:00:20Z", completedAt: "2026-08-17T01:00:30Z" },
      { key: "analyze", label: "分析证据", status: "completed", summary: "比较支持与反证", startedAt: "2026-08-17T01:00:30Z", completedAt: "2026-08-17T01:00:40Z" },
      { key: "conclude", label: "形成结论", status: "completed", summary: "结论已生成", startedAt: "2026-08-17T01:00:40Z", completedAt: "2026-08-17T01:01:05Z" },
    ],
    stats: { sourceCount: 3, admittedEvidenceCount: 2, skippedCount: 1, durationSeconds: 65 },
    recentActivity: ["完成来源采集", "生成自动结论"],
    exceptions: [{ reason: "来源许可不满足", stage: "admit", count: 1 }],
    failureReason: null,
    result: {
      label: "系统生成，未经人工审核",
      humanReviewed: false,
      conclusion: "新增产品可能提高液冷和电源环节需求。",
      keyFindings: ["机柜功率密度上升"],
      counterEvidence: ["部分订单仍受交付周期约束"],
      limitations: ["供应商口径仍需后续财报验证"],
      sources: [
        { title: "产品公告", url: "https://example.com/report", role: "support", reviewState: "automatically_admitted" },
        { title: null, url: null, role: "counter_evidence", reviewState: "automatically_admitted" },
      ],
    },
    ...overrides,
  };
}

function activeView(status: "queued" | "running", runId = `run-${status}`): AutomaticResearchView {
  return completedView({
    runId,
    status,
    stages: completedView().stages.map((stage, index) => ({
      ...stage,
      status: index === 0 ? (status === "running" ? "running" : "pending") : "pending",
      completedAt: null,
    })),
    stats: { sourceCount: 0, admittedEvidenceCount: 0, skippedCount: 0, durationSeconds: 0 },
    result: null,
  });
}

function failedView(): AutomaticResearchView {
  return completedView({
    runId: "run-failed",
    status: "failed",
    stages: completedView().stages.map((stage, index) => ({
      ...stage,
      status: index === 2 ? "failed" : index < 2 ? "completed" : "pending",
      completedAt: index < 2 ? stage.completedAt : null,
    })),
    failureReason: "暂时无法读取一个必要来源。",
    result: null,
  });
}

function renderPage(path = "/events/case%2Falpha/automatic-research") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/events/:caseId/automatic-research" element={<AutomaticResearchPage />} />
        <Route path="*" element={<AutomaticResearchPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AutomaticResearchPage", () => {
  let adapter: MockResearchAdapter;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    setResearchClient(adapter);
  });

  afterEach(() => {
    resetResearchClient();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("keeps a traceable completed automatic result in the default mock", async () => {
    const started = await adapter.startAutomaticResearch("英伟达新产品供应链影响");
    const view = await adapter.getAutomaticResearch(started.caseId);

    expect(view).toMatchObject({
      caseId: started.caseId,
      runId: started.runId,
      title: "英伟达新产品供应链影响",
      status: "completed",
      stats: { skippedCount: 1 },
      result: {
        label: "系统生成，未经人工审核",
        humanReviewed: false,
        sources: [expect.objectContaining({ reviewState: "automatically_admitted" })],
        counterEvidence: [expect.any(String)],
      },
    });
    expect(view.stages).toHaveLength(5);
  });

  it("moves a failed mock research onto a new run when it is retried", async () => {
    const failedAdapter = new MockResearchAdapter({ automaticResearchScenario: "failed" });
    const started = await failedAdapter.startAutomaticResearch("需要重试的研究");
    const failed = await failedAdapter.getAutomaticResearch(started.caseId);

    expect(failed.status).toBe("failed");
    const retried = await failedAdapter.retryAutomaticResearch(started.caseId);
    expect(retried.runId).not.toBe(started.runId);
    await expect(failedAdapter.getAutomaticResearch(started.caseId)).resolves.toMatchObject({
      runId: retried.runId,
      status: "completed",
    });
  });

  it("loads and shows the completed result with five textual stages and trace details", async () => {
    vi.spyOn(adapter, "getAutomaticResearch").mockResolvedValue(completedView());
    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("正在读取自动研究过程");
    expect(await screen.findByRole("heading", { name: "英伟达新产品供应链影响" })).toBeVisible();
    expect(screen.getByText("已完成", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    expect(screen.getByText("系统生成，未经人工审核")).toBeVisible();
    expect(screen.getByText("新增产品可能提高液冷和电源环节需求。")).toBeVisible();

    for (const label of ["采集资料", "解析内容", "证据准入", "分析证据", "形成结论"]) {
      const stage = screen.getByRole("listitem", { name: new RegExp(label) });
      expect(stage).toHaveTextContent("已完成");
      expect(stage).toHaveTextContent(/2026/);
    }
    expect(screen.getAllByRole("listitem", { name: /阶段/ })).toHaveLength(5);
    expect(screen.getByText("3", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("2", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("1", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("1 分 5 秒")).toBeVisible();

    const details = screen.getByText("查看过程").closest("details");
    expect(details).toBeInTheDocument();
    expect(details).toHaveTextContent("完成来源采集");
    expect(details).toHaveTextContent("来源许可不满足");
    expect(details).toHaveTextContent("供应商口径仍需后续财报验证");
    expect(details).toHaveTextContent("产品公告");
    expect(details).toHaveTextContent("未命名来源");
    expect(details).toHaveTextContent("部分订单仍受交付周期约束");
    const sourceLink = screen.getByRole("link", { name: "产品公告" });
    expect(sourceLink).toHaveAttribute("href", "https://example.com/report");
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("rel", expect.stringMatching(/noopener/));
    expect(screen.getByRole("link", { name: "查看 Case 详情" })).toHaveAttribute(
      "href",
      "/events/case%2Falpha",
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/确认研究|授权启动|发布结论|人工流程控制/);
  });

  it("polls active work every two seconds and stops at a terminal state", async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(activeView("queued"))
      .mockResolvedValueOnce(activeView("running"))
      .mockResolvedValueOnce(completedView());

    renderPage();
    await act(async () => { await Promise.resolve(); });
    expect(get).toHaveBeenCalledTimes(1);
    expect(screen.getByText("已排队", { selector: ".automatic-research-process__overall-status" })).toBeVisible();

    await act(async () => { await vi.advanceTimersByTimeAsync(1_999); });
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByText("处理中", { selector: ".automatic-research-process__overall-status" })).toBeVisible();

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(get).toHaveBeenCalledTimes(3);
    expect(screen.getByText("已完成", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(get).toHaveBeenCalledTimes(3);
  });

  it("clears pending polling and ignores a response that arrives after unmount", async () => {
    let resolveSecond!: (view: AutomaticResearchView) => void;
    const get = vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(activeView("running"))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    vi.useFakeTimers();
    const rendered = renderPage();
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(get).toHaveBeenCalledTimes(2);

    rendered.unmount();
    await act(async () => { resolveSecond(completedView()); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("offers one locked retry only for failure, then immediately reads and resumes the new run", async () => {
    let resolveRetry!: (value: { caseId: string; runId: string; status: "queued" }) => void;
    vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(failedView())
      .mockResolvedValueOnce(activeView("running", "run-retried"));
    const retry = vi.spyOn(adapter, "retryAutomaticResearch").mockImplementation(
      () => new Promise((resolve) => { resolveRetry = resolve; }),
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取一个必要来源。");
    const button = screen.getByRole("button", { name: "重新运行" });
    await user.click(button);
    expect(screen.getByRole("button", { name: "正在重新运行…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "正在重新运行…" }));
    expect(retry).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRetry({ caseId: "case/alpha", runId: "run-retried", status: "queued" });
      await Promise.resolve();
    });
    expect(await screen.findByText("处理中", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    expect(adapter.getAutomaticResearch).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "重新运行" })).not.toBeInTheDocument();
  });

  it("announces retry errors accessibly and lets the user try again", async () => {
    vi.spyOn(adapter, "getAutomaticResearch").mockResolvedValue(failedView());
    vi.spyOn(adapter, "retryAutomaticResearch").mockRejectedValue(new Error("offline"));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "重新运行" }));
    expect(await screen.findByRole("alert", { name: "重新运行失败" })).toHaveTextContent(
      "重新运行未能启动，请稍后再试。",
    );
    expect(screen.getByRole("button", { name: "重新运行" })).toBeEnabled();
  });

  it("renders safe accessible loading, missing identity, and load-error states", async () => {
    vi.spyOn(adapter, "getAutomaticResearch").mockRejectedValue(new Error("not found"));
    const failedLoad = renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取这项自动研究");
    failedLoad.unmount();

    renderPage("/automatic-research");
    expect(screen.getByRole("alert")).toHaveTextContent("缺少自动研究标识");
  });
});
