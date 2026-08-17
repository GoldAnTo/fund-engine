import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import type { AutomaticResearchView } from "../domain/automaticResearch";
import {
  AUTOMATIC_RESEARCH_PROCESS_COLORS,
  AutomaticResearchPage,
} from "../features/events/AutomaticResearchPage";

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

function SwitchablePage() {
  const navigate = useNavigate();
  return (
    <>
      <AutomaticResearchPage />
      <button
        type="button"
        onClick={() => navigate("/events/case-beta/automatic-research")}
      >
        切换 Case
      </button>
    </>
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

  it("isolates the mock failure scenario per Case", async () => {
    const failedAdapter = new MockResearchAdapter({ automaticResearchScenario: "failed" });
    const first = await failedAdapter.startAutomaticResearch("第一个失败 Case");
    const second = await failedAdapter.startAutomaticResearch("第二个失败 Case");

    await expect(failedAdapter.getAutomaticResearch(first.caseId)).resolves.toMatchObject({
      status: "failed",
    });
    await expect(failedAdapter.getAutomaticResearch(second.caseId)).resolves.toMatchObject({
      status: "failed",
    });
  });

  it("gives completed mock stages increasing timestamps", async () => {
    const started = await adapter.startAutomaticResearch("检查时间线");
    const view = await adapter.getAutomaticResearch(started.caseId);
    const ranges = view.stages.map((stage) => ({
      startedAt: Date.parse(stage.startedAt || ""),
      completedAt: Date.parse(stage.completedAt || ""),
    }));

    expect(ranges.every((range) => range.completedAt > range.startedAt)).toBe(true);
    for (let index = 1; index < ranges.length; index += 1) {
      expect(ranges[index].startedAt).toBeGreaterThanOrEqual(ranges[index - 1].completedAt);
    }
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
      expect(stage).toHaveAccessibleName(new RegExp(`${label}阶段，已完成，`));
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
    const liveStage = screen.getByText(/当前阶段：形成结论/);
    expect(liveStage).toHaveAttribute("aria-live", "polite");
    expect(liveStage).toHaveAttribute("aria-atomic", "true");
  });

  it("keeps the machine-result label above WCAG AA contrast", () => {
    expect(contrastRatio(
      AUTOMATIC_RESEARCH_PROCESS_COLORS.machineLabelText,
      AUTOMATIC_RESEARCH_PROCESS_COLORS.machineLabelBackground,
    )).toBeGreaterThanOrEqual(4.5);
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

  it("keeps the last running view through a transient read failure and recovers", async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(activeView("running"))
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce(completedView());
    renderPage();
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });

    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByText("处理中", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    expect(screen.getByRole("status", { name: "进度暂时无法更新" })).toBeVisible();
    const readNow = screen.getByRole("button", { name: "立即重新读取" });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_999); });
    expect(get).toHaveBeenCalledTimes(2);

    fireEvent.click(readNow);
    await act(async () => { await Promise.resolve(); });
    expect(get).toHaveBeenCalledTimes(3);
    expect(screen.getByText("已完成", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "立即重新读取" })).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(get).toHaveBeenCalledTimes(3);
  });

  it("disables immediate reading while the automatic recovery GET is in flight", async () => {
    vi.useFakeTimers();
    let resolveRecovery!: (view: AutomaticResearchView) => void;
    const get = vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(activeView("running"))
      .mockRejectedValueOnce(new Error("temporary"))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRecovery = resolve; }));

    renderPage();
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });

    const readNow = screen.getByRole("button", { name: "立即重新读取" });
    expect(readNow).toBeDisabled();
    fireEvent.click(readNow);
    expect(get).toHaveBeenCalledTimes(3);

    await act(async () => { resolveRecovery(completedView()); await Promise.resolve(); });
    expect(screen.getByText("已完成", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
  });

  it("does not overlap polling requests while a progress read is pending", async () => {
    vi.useFakeTimers();
    let resolveSecond!: (view: AutomaticResearchView) => void;
    const get = vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(activeView("running"))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }))
      .mockResolvedValueOnce(completedView());

    renderPage();
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(get).toHaveBeenCalledTimes(2);

    await act(async () => { resolveSecond(activeView("running")); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
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

  it("recovers when the first progress read after retry fails transiently", async () => {
    vi.useFakeTimers();
    vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(failedView())
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce(completedView({ runId: "run-retried" }));
    vi.spyOn(adapter, "retryAutomaticResearch").mockResolvedValue({
      caseId: "case/alpha",
      runId: "run-retried",
      status: "queued",
    });
    renderPage();
    await act(async () => { await Promise.resolve(); });

    fireEvent.click(screen.getByRole("button", { name: "重新运行" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("已排队", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    expect(screen.getByRole("status", { name: "进度暂时无法更新" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "重新运行" })).not.toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(screen.getByText("已完成", { selector: ".automatic-research-process__overall-status" })).toBeVisible();
    expect(adapter.getAutomaticResearch).toHaveBeenCalledTimes(3);
  });

  it("ignores a pending retry after unmount", async () => {
    let resolveRetry!: (value: { caseId: string; runId: string; status: "queued" }) => void;
    vi.spyOn(adapter, "getAutomaticResearch").mockResolvedValue(failedView());
    vi.spyOn(adapter, "retryAutomaticResearch").mockImplementation(
      () => new Promise((resolve) => { resolveRetry = resolve; }),
    );
    const user = userEvent.setup();
    const rendered = renderPage();
    await user.click(await screen.findByRole("button", { name: "重新运行" }));
    rendered.unmount();

    await act(async () => {
      resolveRetry({ caseId: "case/alpha", runId: "run-late", status: "queued" });
      await Promise.resolve();
    });
    expect(adapter.getAutomaticResearch).toHaveBeenCalledTimes(1);
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

  it("re-reads an initially unavailable automatic research from its empty error page", async () => {
    vi.spyOn(adapter, "getAutomaticResearch")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(completedView());
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取这项自动研究");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("heading", { name: "英伟达新产品供应链影响" })).toBeVisible();
    expect(adapter.getAutomaticResearch).toHaveBeenCalledTimes(2);
  });

  it("re-reads an invalid stage projection instead of leaving a dead-end error", async () => {
    vi.spyOn(adapter, "getAutomaticResearch")
      .mockResolvedValueOnce(completedView({ stages: completedView().stages.slice(0, 4) }))
      .mockResolvedValueOnce(completedView());
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("自动研究过程记录不完整");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("heading", { name: "英伟达新产品供应链影响" })).toBeVisible();
  });

  it("rejects an incomplete stage projection instead of showing a partial process", async () => {
    vi.spyOn(adapter, "getAutomaticResearch").mockResolvedValue(completedView({
      stages: completedView().stages.slice(0, 4),
    }));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "自动研究过程记录不完整",
    );
    expect(screen.queryByRole("listitem", { name: /阶段/ })).not.toBeInTheDocument();
  });

  it("resets on Case switch and ignores the previous Case response", async () => {
    let resolveFirst!: (view: AutomaticResearchView) => void;
    vi.spyOn(adapter, "getAutomaticResearch").mockImplementation((caseId) => {
      if (caseId === "case/alpha") {
        return new Promise((resolve) => { resolveFirst = resolve; });
      }
      return Promise.resolve(completedView({ caseId, title: "第二个 Case" }));
    });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/case%2Falpha/automatic-research"]}>
        <Routes>
          <Route path="/events/:caseId/automatic-research" element={<SwitchablePage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "切换 Case" }));
    await act(async () => { resolveFirst(completedView({ title: "旧 Case" })); await Promise.resolve(); });
    expect(await screen.findByRole("heading", { name: "第二个 Case" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "旧 Case" })).not.toBeInTheDocument();
  });

  it("keeps only the current StrictMode request result", async () => {
    let resolveFirst!: (view: AutomaticResearchView) => void;
    const get = vi.spyOn(adapter, "getAutomaticResearch")
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(completedView({ title: "当前结果" }));
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/events/case%2Falpha/automatic-research"]}>
          <Routes>
            <Route path="/events/:caseId/automatic-research" element={<AutomaticResearchPage />} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );

    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await act(async () => { resolveFirst(completedView({ title: "过期结果" })); await Promise.resolve(); });
    expect(await screen.findByRole("heading", { name: "当前结果" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "过期结果" })).not.toBeInTheDocument();
  });
});

type Oklch = { lightness: number; chroma: number; hue: number };

function relativeLuminance({ lightness, chroma, hue }: Oklch): number {
  const radians = hue * Math.PI / 180;
  const a = chroma * Math.cos(radians);
  const b = chroma * Math.sin(radians);
  const l = (lightness + .3963377774 * a + .2158037573 * b) ** 3;
  const m = (lightness - .1055613458 * a - .0638541728 * b) ** 3;
  const s = (lightness - .0894841775 * a - 1.291485548 * b) ** 3;
  const red = Math.min(1, Math.max(0, 4.0767416621 * l - 3.3077115913 * m + .2309699292 * s));
  const green = Math.min(1, Math.max(0, -1.2684380046 * l + 2.6097574011 * m - .3413193965 * s));
  const blue = Math.min(1, Math.max(0, -.0041960863 * l - .7034186147 * m + 1.707614701 * s));
  return .2126 * red + .7152 * green + .0722 * blue;
}

function contrastRatio(first: Oklch, second: Oklch): number {
  const [lighter, darker] = [relativeLuminance(first), relativeLuminance(second)]
    .sort((left, right) => right - left);
  return (lighter + .05) / (darker + .05);
}
