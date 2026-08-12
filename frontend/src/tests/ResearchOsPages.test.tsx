import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { MockResearchOsApi } from "../data/mockResearchOsApi";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { resetResearchOsApi, setResearchOsApi } from "../app/researchOsApi";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { EventDeskPage } from "../features/events/EventDeskPage";
import { GlobalMonitoringPage } from "../features/events/GlobalMonitoringPage";
import { ResearchNetworkPage } from "../features/events/ResearchNetworkPage";
import {
  CaseConclusionHistoryPage,
  CaseConclusionPage,
  CaseFundProfilePage,
  CaseMarketPage,
  CaseReviewPage,
  CaseRelationsPage,
  CaseScopePage,
  CaseStockProfilePage,
  CaseWikiPage,
} from "../features/case/CasePages";
import {
  CaseDocumentsPage,
  CaseEvidencePage,
  CaseMonitorPage,
  CaseProtocolPage,
  MonitorConfigPage,
} from "../features/case/CasePages";
import { AppShell } from "../app/AppShell";
import { ResearchOsRoutes } from "../app/routes";
import type {
  EventResearchListItem,
  EventWorkbench,
} from "../domain/eventResearch";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const historicalRunSummaries = [
  {
    id: "run-reviewed",
    status: "succeeded",
    stage: "complete",
    round: 1,
    stop_reason: "max_rounds_reached",
    created_at: "2026-08-11T01:00:00Z",
    next_action: "查看已审核结论",
  },
  {
    id: "run-waiting",
    status: "waiting_for_review",
    stage: "stopped",
    round: 1,
    stop_reason: "max_rounds_reached",
    created_at: "2026-08-10T01:00:00Z",
    next_action: "人工审核临时评估",
  },
];

function MonitorLocationProbe() {
  const location = useLocation();
  return <output data-testid="monitor-location">{location.search}</output>;
}

function CaseLocationProbe() {
  const location = useLocation();
  return <output data-testid="case-location">{location.pathname}{location.search}</output>;
}

function renderMonitorPage(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/events/:caseId/monitor"
          element={<><CaseMonitorPage /><MonitorLocationProbe /></>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function monitorRunSummary(id: string, status: string, stage: string) {
  return {
    id,
    status,
    stage,
    round: 1,
    stop_reason: null,
    created_at: "2026-08-09T00:00:00Z",
    next_action: "查看运行详情",
  };
}

describe("Research OS event entry", () => {
  it("groups Case navigation into research stages", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm"]}>
        <Routes>
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const stages = await screen.findByLabelText("事件研究工作区");
    expect(stages).toHaveTextContent("研究结论");
    expect(stages).toHaveTextContent("证据工作台");
    expect(stages).toHaveTextContent("市场与表达");
    expect(stages).toHaveTextContent("监测与运行");
    expect(stages).toHaveTextContent("关系与图谱");
    expect(stages).not.toHaveTextContent("原文资料");
    expect(screen.queryByLabelText("证据工作台页面")).not.toBeInTheDocument();
  });

  it("keeps an original-document deep link inside the evidence workbench", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <Routes>
          <Route path="/events/:caseId/documents" element={<CaseDocumentsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText("证据工作台页面")).toHaveTextContent("命题与证据");
    expect(screen.getByRole("link", { name: /证据工作台/ })).toHaveAttribute("aria-current", "page");
    expect(within(screen.getByLabelText("证据工作台页面")).getByRole("link", { name: "原文资料" })).toHaveAttribute("aria-current", "page");
  });

  it("reveals the remaining research stages from a compact Case menu", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByLabelText("事件研究工作区");
    await user.click(screen.getByText("更多研究内容"));
    const more = screen.getByRole("group", { name: "更多研究内容" });
    expect(more).toHaveTextContent("研究结论");
    expect(more).toHaveTextContent("证据工作台");
    expect(more).toHaveTextContent("监测与运行");
    expect(more).toHaveTextContent("关系与图谱");
    expect(more).not.toHaveTextContent("市场与表达");
  });

  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => {
    resetResearchClient();
    resetResearchOsApi();
    vi.unstubAllGlobals();
  });

  it("puts the next human action ahead of automatic event work", async () => {
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route path="/events" element={<EventDeskPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "今天，先推进哪一个判断？" }),
    ).toBeVisible();
    expect(await screen.findByText("当前优先")).toBeVisible();
    expect(await screen.findByText("研究网络")).toBeVisible();
    expect(
      screen.getAllByRole("link", { name: /Alphabet 财报超预期后股价下跌/ })
        .length,
    ).toBeGreaterThan(0);
  });

  it("does not call active Case work running when the execution worker is unavailable", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "workerStatus").mockResolvedValue({
      status: "unavailable",
      last_seen_at: null,
      mode: null,
      state: null,
    });
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route path="/events" element={<EventDeskPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("执行器未启动；已排队研究不会自动推进")).toBeVisible();
    expect(screen.getByText(/范围和排队记录已保存/)).toBeVisible();
  });

  it("keeps the research dispatch structure visible while Cases are loading", () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listEventResearch").mockReturnValue(new Promise(() => {}));
    const api = new MockResearchOsApi(adapter);
    vi.spyOn(api, "workerStatus").mockReturnValue(new Promise(() => {}));
    setResearchClient(adapter);
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route path="/events" element={<EventDeskPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("研究调度加载中")).toBeVisible();
    expect(screen.getAllByTestId("research-dispatch-skeleton")).toHaveLength(4);
  });

  it("lets a researcher retry the dispatch after its live Case list is unavailable", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listEventResearch").mockRejectedValueOnce(
      new Error("Dispatch unavailable"),
    );
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route path="/events" element={<EventDeskPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "暂时无法读取事件研究",
    );
    await user.click(
      screen.getByRole("button", { name: "重试读取事件研究" }),
    );
    expect(
      await screen.findByRole("heading", { name: "今天，先推进哪一个判断？" }),
    ).toBeVisible();
  });

  it("keeps the global run archive structure visible while monitoring loads", () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "runs").mockReturnValue(new Promise(() => {}));
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/monitoring"]}>
        <Routes>
          <Route path="/monitoring" element={<GlobalMonitoringPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("全局运行档案加载中")).toBeVisible();
    expect(screen.getAllByTestId("global-run-skeleton")).toHaveLength(2);
  });

  it("keeps reviewed and candidate relation lanes visible while the network loads", () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "network").mockReturnValue(new Promise(() => {}));
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/network"]}>
        <Routes>
          <Route path="/network" element={<ResearchNetworkPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("跨 Case 关联加载中")).toBeVisible();
    expect(screen.getAllByTestId("network-relation-skeleton")).toHaveLength(2);
  });

  it("lets a researcher retry the network after its live relation read is unavailable", async () => {
    const user = userEvent.setup();
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "network").mockRejectedValueOnce(
      new Error("Network unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/network"]}>
        <Routes>
          <Route path="/network" element={<ResearchNetworkPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取跨 Case 关联",
    );
    await user.click(
      screen.getByRole("button", { name: "重试读取跨 Case 关联" }),
    );
    expect(
      await screen.findByRole("heading", { name: "跨 Case 研究网络" }),
    ).toBeVisible();
  });

  it("treats the current event as the workbench subject and switches events in place", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/evidence"]}>
        <Routes>
          <Route path="/events/:caseId/evidence" element={<><CaseEvidencePage /><CaseLocationProbe /></>} />
        </Routes>
      </MemoryRouter>,
    );

    const switcher = await screen.findByRole("button", { name: /当前事件研究/ });
    expect(switcher).toHaveTextContent("台积电上调 CoWoS 指引后下跌");
    expect(screen.queryByLabelText("切换 ResearchCase")).not.toBeInTheDocument();
    await user.click(switcher);
    const eventList = screen.getByRole("group", { name: "切换事件研究" });
    expect(within(eventList).getAllByRole("button").length).toBeGreaterThan(1);
    await user.click(within(eventList).getByRole("button", { name: /经营数据披露后的变动/ }));
    expect(screen.getByTestId("case-location")).toHaveTextContent("/events/event-published/evidence");
  });

  it("shows the current event's six-stage progress separately from workspaces", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm"]}>
        <Routes><Route path="/events/:caseId" element={<CaseConclusionPage />} /></Routes>
      </MemoryRouter>,
    );

    const progress = await screen.findByLabelText("当前事件研究进展");
    expect(progress).toHaveTextContent("资料接入");
    expect(progress).toHaveTextContent("定义研究");
    expect(progress).toHaveTextContent("执行补证");
    expect(progress).toHaveTextContent("审核证据");
    expect(progress).toHaveTextContent("形成结论");
    expect(progress).toHaveTextContent("持续跟踪");
    expect(screen.getByLabelText("事件研究工作区")).toHaveTextContent("研究结论");
  });

  it("keeps Case navigation visible while the selected Case is loading", () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getEventWorkbench").mockReturnValue(new Promise(() => {}));
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm"]}>
        <Routes>
          <Route path="/events/:caseId" element={<CaseEvidencePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("Case 工作台加载中")).toBeVisible();
    expect(screen.getAllByTestId("case-workbench-skeleton")).toHaveLength(3);
  });

  it("does not let a stale Case response replace the current route's failure state", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const firstWorkbench = await adapter.getEventWorkbench("event-tsm");
    let resolveFirst!: (value: EventWorkbench) => void;
    const firstRequest = new Promise<EventWorkbench>((resolve) => {
      resolveFirst = resolve;
    });
    vi.spyOn(adapter, "getEventWorkbench").mockImplementation((caseId) =>
      caseId === "event-tsm"
        ? firstRequest
        : Promise.reject(new Error("Case unavailable")),
    );
    setResearchClient(adapter);

    render(
      <MemoryRouter initialEntries={["/events/event-tsm"]}>
        <Routes>
          <Route
            path="/events/:caseId"
            element={
              <>
                <Link to="/events/case-offline">切换到不可读取的 Case</Link>
                <CaseEvidencePage />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      screen.getByRole("link", { name: "切换到不可读取的 Case" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取这个 Case",
    );

    await act(async () => resolveFirst(firstWorkbench));

    expect(screen.getByRole("alert")).toHaveTextContent("无法读取这个 Case");
    expect(
      screen.queryByRole("heading", { name: firstWorkbench.event.eventTitle }),
    ).not.toBeInTheDocument();
  });

  it("makes an unavailable conclusion history explicit instead of leaving a loading message", async () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getEventConclusionHistory").mockRejectedValue(
      new Error("Conclusion history unavailable"),
    );
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/history"]}>
        <Routes>
          <Route
            path="/events/:caseId/history"
            element={<CaseConclusionHistoryPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取不可变结论版本",
    );
  });

  it("makes an unavailable review queue explicit instead of leaving a loading message", async () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getEventReviewQueue").mockRejectedValue(
      new Error("Review queue unavailable"),
    );
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
        <Routes>
          <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取待审核证据",
    );
  });

  it("makes an unavailable scope-history audit explicit instead of showing an empty history", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "scopeHistory").mockRejectedValue(
      new Error("Scope history unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/scope"]}>
        <Routes>
          <Route path="/events/:caseId/scope" element={<CaseScopePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取范围版本历史",
    );
  });

  it("does not call an unreadable monitor history an empty configuration history", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "monitor").mockRejectedValue(
      new Error("Monitor history unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor/config"]}>
        <Routes>
          <Route
            path="/events/:caseId/monitor/config"
            element={<MonitorConfigPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取 CaseMonitor 版本历史",
    );
  });

  it("makes an unreadable Case monitor explicit and lets the researcher retry it", async () => {
    const user = userEvent.setup();
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "monitor").mockRejectedValueOnce(
      new Error("Monitor unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "监控配置暂不可用",
    );
    await user.click(
      screen.getByRole("button", { name: "重新读取监控配置" }),
    );
    expect(
      await screen.findByRole("heading", { name: "用于未来运行的配置" }),
    ).toBeVisible();
  });

  it("makes an unreadable run-event ledger explicit and lets the researcher retry it", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listResearchRuns").mockResolvedValue([
      monitorRunSummary("run-demo-1", "awaiting_review", "review"),
    ]);
    const api = new MockResearchOsApi(adapter);
    setResearchClient(adapter);
    const runEvents = vi.spyOn(api, "runEvents").mockRejectedValue(
      new Error("Run events unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本次运行事件暂不可读取",
    );
    runEvents.mockRestore();
    await user.click(
      screen.getByRole("button", { name: "重新读取本次运行事件" }),
    );
    expect(await screen.findByText("已冻结本次运行范围")).toBeVisible();
  });

  it("does not allow immediate replenishment while researchability is unreadable", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listResearchRuns").mockResolvedValue([
      monitorRunSummary("run-demo-1", "awaiting_review", "review"),
    ]);
    const api = new MockResearchOsApi(adapter);
    setResearchClient(adapter);
    const researchability = vi
      .spyOn(api, "researchability")
      .mockRejectedValue(new Error("Researchability unavailable"));
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "研究协议状态暂不可读取",
    );
    expect(screen.getByRole("button", { name: "立即补证一次" })).toBeDisabled();
    researchability.mockRestore();
    await user.click(
      screen.getByRole("button", { name: "重新读取研究协议状态" }),
    );
    expect(
      await screen.findByRole("button", { name: "立即补证一次" }),
    ).toBeEnabled();
  });

  it("keeps an unavailable Case-scoped stock profile explicit and retryable", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "marketExpression").mockRejectedValue(
      new Error("Market expression unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/stocks/stock-demo"]}>
        <Routes>
          <Route
            path="/events/:caseId/stocks/:stockId"
            element={<CaseStockProfilePage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取当前 Case 的市场表达",
    );
    expect(
      screen.getByRole("button", { name: "重试读取市场表达" }),
    ).toBeEnabled();
  });

  it("lets a researcher retry the market-expression workbench after a live read error", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "marketExpression").mockRejectedValue(
      new Error("Market expression unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取已审核的市场表达",
    );
    expect(
      screen.getByRole("button", { name: "重试读取市场表达" }),
    ).toBeEnabled();
  });

  it("blocks factor replenishment before a missing research protocol reaches the run API", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "researchability").mockResolvedValue({
      status: "blocked",
      reason_codes: ["missing_outcome_binding"],
      effective_binding_id: null,
      next_action: "确认结果指标、范围、基线和时间窗",
    });
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(
        "研究协议尚未通过，不能启动补证。下一步：确认结果指标、范围、基线和时间窗",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "立即补证此因素" })).toBeDisabled();
  });

  it("does not offer a duplicate key-factor replenishment while its prior run is active", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "activeRuns").mockResolvedValue({
      has_more: false,
      items: [
        {
          run_id: "run-existing-factor",
          case_id: "event-tsm",
          case_title: "TSM 资本开支与自由现金流验证",
          status: "queued",
          stage: "planning",
          updated_at: "2026-08-11T03:20:00Z",
          processed_count: 0,
          next_action: "查看本次运行",
          scope: {
            trigger: "factor_manual",
            monitor_version_id: "monitor-event-tsm-v1",
            factor_ids: ["factor-capex"],
            factor_statements: ["资本开支增速是否高于此前指引"],
            allowed_source_types: ["company_disclosure"],
            budget: 20,
          },
        },
      ],
    });
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/该关键因素已有未结束的补证运行/)).toBeVisible();
    expect(screen.getByRole("button", { name: "立即补证此因素" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "查看运行记录" })).toHaveAttribute(
      "href",
      "/events/event-tsm/monitor",
    );
  });

  it("does not present a provider record URI as a clickable source webpage", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    const originalMarketExpression = api.marketExpression.bind(api);
    vi.spyOn(api, "marketExpression").mockImplementation(async (caseId) => {
      const expression = await originalMarketExpression(caseId);
      return {
        ...expression,
        claims: expression.claims.map((claim) => ({
          ...claim,
          source: {
            ...claim.source,
            source_url: "gildata://FinancialResearchReport/fixture-record",
          },
        })),
      };
    });
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "研报主张、后续验证与基金披露分层呈现" });
    expect(screen.queryByRole("link", { name: "查看来源地址" })).not.toBeInTheDocument();
  });

  it("lets a researcher retry an unavailable Case relation read", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "caseRelations").mockRejectedValue(
      new Error("Case relations unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/relations"]}>
        <Routes>
          <Route
            path="/events/:caseId/relations"
            element={<CaseRelationsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取当前 Case 的关联记录",
    );
    expect(
      screen.getByRole("button", { name: "重试读取关联记录" }),
    ).toBeEnabled();
  });

  it("makes an unavailable atomic-claim queue explicit instead of leaving review in a loading state", async () => {
    const user = userEvent.setup();
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "atomicClaims").mockRejectedValueOnce(
      new Error("Atomic claim queue unavailable"),
    );
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
        <Routes>
          <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "原子陈述队列暂不可读",
    );
    expect(
      screen.getByRole("button", { name: "重试读取原子陈述队列" }),
    ).toBeEnabled();
    await user.click(
      screen.getByRole("button", { name: "重试读取原子陈述队列" }),
    );
    expect(
      await screen.findByText("当前 Case 没有待展示的原子陈述候选。"),
    ).toBeVisible();
  });

  it("does not present unreadable baseline sources as an empty protocol selection", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getDocuments").mockRejectedValueOnce(
      new Error("Baseline documents unavailable"),
    );
    setResearchClient(adapter);
    setResearchOsApi(new MockResearchOsApi(adapter));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/protocol"]}>
        <Routes>
          <Route
            path="/events/:caseId/protocol"
            element={<CaseProtocolPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "设定结果指标与验证窗口" }),
    );
    expect(
      await screen.findByText("无法读取当前 Case 的冻结资料，不能据此判断为空。"),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "重试读取冻结资料" }),
    );
    expect(
      await screen.findByRole("option", {
        name: /台积电 2026 年第二季度法说会摘要/,
      }),
    ).toBeVisible();
  });

  it("keeps an unavailable verification-rule history visible and retryable", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    vi.spyOn(api, "caseMechanismProtocol")
      .mockRejectedValueOnce(new Error("Protocol unavailable"))
      .mockRejectedValueOnce(new Error("Rule configuration unavailable"))
      .mockRejectedValueOnce(new Error("Rule history unavailable"));
    setResearchClient(adapter);
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/protocol"]}>
        <Routes>
          <Route
            path="/events/:caseId/protocol"
            element={<CaseProtocolPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("验证规则版本暂不可读，不能将其当作没有历史记录。"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重试读取验证规则版本" }));
    expect(
      await screen.findByRole("heading", { name: "每次调整都可回放" }),
    ).toBeVisible();
  });

  it("does not call an unavailable Case Wiki an empty graph", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "graph").mockRejectedValue(new Error("Wiki unavailable"));
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/wiki"]}>
        <Routes>
          <Route path="/events/:caseId/wiki" element={<CaseWikiPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取 Case Wiki 图谱",
    );
    expect(
      screen.getByRole("button", { name: "重试读取 Case Wiki 图谱" }),
    ).toBeEnabled();
  });

  it("keeps every Case research stage reachable from the stable Case frame", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm"]}>
        <Routes>
          <Route path="/events/:caseId" element={<CaseEvidencePage />} />
        </Routes>
      </MemoryRouter>,
    );

    const navigation = await screen.findByRole("navigation", {
      name: "事件研究工作区",
    });
    expect(within(navigation).getByRole("link", { name: "研究结论" }))
      .toHaveAttribute("href", "/events/event-tsm");
    expect(within(navigation).getByRole("link", { name: /证据工作台/ }))
      .toHaveAttribute("href", "/events/event-tsm/evidence");
    expect(within(navigation).getByRole("link", { name: "市场与表达" }))
      .toHaveAttribute("href", "/events/event-tsm/market");
    expect(within(navigation).getByRole("link", { name: "监测与运行" }))
      .toHaveAttribute("href", "/events/event-tsm/monitor");
    expect(within(navigation).getByRole("link", { name: "关系与图谱" }))
      .toHaveAttribute("href", "/events/event-tsm/wiki");
  });

  it("keeps a small reviewed Case-relation context beside the current conclusion", async () => {
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm"]}>
        <Routes>
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("已审核关联")).toBeVisible();
    expect(await screen.findByRole("link", { name: "AI 服务器订单验证" })).toHaveAttribute(
      "href",
      "/events/event-ai-server",
    );
    expect(screen.getByText(/另有 1 条 AI 候选/)).toBeVisible();
  });

  it("opens Case evidence through its frozen Case document instead of a live source URL", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/evidence"]}>
        <Routes>
          <Route path="/events/:caseId/evidence" element={<CaseEvidencePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("link", { name: "定位到冻结原文" }),
    ).toHaveAttribute(
      "href",
      "/events/event-tsm/documents?document=doc-event-tsm-q2",
    );
    expect(
      screen.queryByRole("link", { name: "打开冻结来源" }),
    ).not.toBeInTheDocument();
  });

  it("does not render source contents when the Case cannot display that material", async () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getEventWorkbench").mockResolvedValue({
      ...(await adapter.getEventWorkbench("event-tsm")),
      evidence: [{
        caseId: "event-tsm",
        factorStatement: "受限来源的研究因素",
        role: "supports",
        reviewState: "reviewed",
        sourceTitle: null,
        sourceUrl: null,
        documentVersionId: "doc-restricted",
        sourceVisibleInCase: false,
        excerpt: "这段受限原文绝不能显示",
        locator: { page: 12 },
        availableAt: "2026-08-08T00:00:00Z",
      }],
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/evidence"]}>
        <Routes>
          <Route path="/events/:caseId/evidence" element={<CaseEvidencePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("受限来源的研究因素")).toBeVisible();
    expect(screen.getByText("当前未获在此 Case 定位原文的许可")).toBeVisible();
    expect(screen.queryByText("这段受限原文绝不能显示")).not.toBeInTheDocument();
    expect(screen.queryByText('{"page":12}')).not.toBeInTheDocument();
  });

  it("turns a Case Wiki source and AI candidate into traceable researcher actions", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/wiki"]}>
        <Routes>
          <Route path="/events/:caseId/wiki" element={<CaseWikiPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("img", { name: "Case Wiki 关系图谱" }),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "图谱节点：冻结公司披露" }),
    );
    expect(
      await screen.findByRole("link", { name: "定位到冻结原文" }),
    ).toHaveAttribute(
      "href",
      "/events/event-tsm/documents?document=doc-event-tsm-q2",
    );

    await user.click(
      screen.getByRole("button", {
        name: "document 冻结公司披露 已进入 Case 图谱",
      }),
    );
    expect(
      await screen.findByRole("link", { name: "定位到冻结原文" }),
    ).toHaveAttribute(
      "href",
      "/events/event-tsm/documents?document=doc-event-tsm-q2",
    );
    expect(screen.getByText("许可")).toBeVisible();
    expect(screen.getByText("已准入")).toBeVisible();
    expect(screen.getByText("定位")).toBeVisible();
    expect(screen.getByText("第 12 页 · 资本开支")).toBeVisible();
    expect(screen.getByText(/审核人 human:reviewer/)).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /冻结公司披露.*quoted_by/ }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "关系：冻结公司披露 → 资本开支指引上调",
      }),
    ).toBeVisible();
    expect(screen.getByText("审核理由")).toBeVisible();
    expect(screen.getByText("已逐字核对冻结原文与定位。")).toBeVisible();
    expect(screen.getByText("可用时点")).toBeVisible();

    await user.click(
      screen.getByRole("button", {
        name: "proposal 自由现金流承压持续 AI 候选，未经人工复核",
      }),
    );
    expect(
      await screen.findByRole("link", { name: "审核此候选关系" }),
    ).toHaveAttribute("href", "/events/event-tsm/review");

    await user.click(
      screen.getByRole("button", {
        name: "case AI 服务器订单验证 已进入 Case 图谱",
      }),
    );
    expect(
      await screen.findByRole("link", { name: "审核关联 Case 候选" }),
    ).toHaveAttribute("href", "/events/event-tsm/relations");
  });

  it("lets a researcher begin registering a reviewed claim from an admitted frozen source", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "登记已审核主张与关键因素" }),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "选择冻结原文并登记主张" }),
    );
    expect(await screen.findByLabelText("冻结原文陈述")).toBeVisible();
    expect(screen.getByText("公司季度业绩说明")).toBeVisible();
    expect(screen.getByText(/只可选择本 Case 内已准入/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("登记主张前还需填写");
    expect(
      screen.getByRole("button", { name: "登记已审核主张" }),
    ).toBeDisabled();
    await user.type(screen.getByLabelText("主张归属"), "公司管理层");
    await user.type(
      screen.getByLabelText("主张审核理由"),
      "已核对冻结原文、定位与许可范围。",
    );
    await user.click(screen.getByRole("button", { name: "登记已审核主张" }));
    expect(await screen.findByText(/已登记已审核主张/)).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "固定关键因素的验证口径" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "登记已审核关键因素" }),
    ).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("登记关键因素前还需填写");
    await user.type(screen.getByLabelText("验证指标"), "订单同比增速");
    await user.type(screen.getByLabelText("验证开始日期"), "2026-07-01");
    await user.type(screen.getByLabelText("验证结束日期"), "2026-09-30");
    await user.type(
      screen.getByLabelText("支持条件"),
      "已准入资料显示订单同比增长。",
    );
    await user.type(
      screen.getByLabelText("反证条件"),
      "已准入资料显示订单同比下降。",
    );
    await user.type(screen.getByLabelText("下一验证事件"), "下一次订单披露");
    await user.type(
      screen.getByLabelText("因素审核理由"),
      "指标、窗口、来源和反证条件均已人工确认。",
    );
    expect(
      screen.getByRole("button", { name: "登记已审核关键因素" }),
    ).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "登记已审核关键因素" }));
    expect(await screen.findByText(/已登记已审核关键因素/)).toBeVisible();
  });

  it("shows a reproducible key-factor candidate parse before any factor is registered", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "从冻结原文解析关键因素" }),
    );
    expect(await screen.findByLabelText("解析来源原文")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "生成关键因素候选" }));

    expect(await screen.findByRole("heading", { name: "关键因素候选解析" })).toBeVisible();
    expect(screen.getByText("规则 key-factor-rules-v1")).toBeVisible();
    expect(screen.getByText("2026 年资本开支预测兑现")).toBeVisible();
    expect(screen.getByText("预计2026年资本开支为200亿元")).toBeVisible();
    expect(screen.getByText(/候选尚未成为正式关键因素/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "带入人工登记" }));
    expect(
      await screen.findByText(/候选已带入登记草稿/),
    ).toBeVisible();
    expect(screen.getByLabelText("关键因素名称")).toHaveValue(
      "2026 年资本开支预测兑现",
    );
    expect(screen.getByLabelText("验证指标")).toHaveValue("资本开支");
  });

  it("lets a researcher resume key-factor registration from an existing reviewed claim", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "选择冻结原文并登记主张" }),
    );
    const claims = await screen.findByLabelText("已审核主张");
    await user.selectOptions(claims, "report-claim-demo");

    expect(
      screen.getByRole("heading", { name: "固定关键因素的验证口径" }),
    ).toBeVisible();
    expect(screen.getByLabelText("关键因素名称")).toHaveValue(
      "公司预计资本开支将高于此前指引。",
    );
  });

  it("makes a reviewed factor's verification an explicit source-backed decision", async () => {
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("button", { name: "登记审核验证" }),
    ).toBeVisible();
    expect(screen.getByText(/不会把运行结果自动写成支持或反证/)).toBeVisible();
  });

  it("keeps the company and stock link explicit before opening fundamental-impact registration", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "关联公司与股票" }),
    ).toBeVisible();
    expect(screen.getByText(/直接受影响 · 审核 human:reviewer/)).toBeVisible();
    expect(screen.queryByText(/directly_affected/)).not.toBeInTheDocument();
    expect(screen.getByText(/不会从事件标题或代码自动推断/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "登记基本面传导" }));
    expect(await screen.findByLabelText("传导标的")).toBeVisible();
  });

  it("requires an approved stock binding before opening a market-observation record", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "登记市场观测" }),
    );
    expect(await screen.findByLabelText("观测标的")).toBeVisible();
    expect(screen.getByLabelText("冻结行情原文")).toBeVisible();
    expect(screen.getByLabelText("盘后处理")).toBeVisible();
    expect(screen.getByText(/市场窗口只记录发生了什么/)).toBeVisible();
  });

  it("shows the frozen source and coverage for a fund disclosure instead of implying a live position", async () => {
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("link", {
        name: "定位到冻结持仓来源（版本 doc-fund-holdings-2026q2）",
      }),
    ).toHaveAttribute(
      "href",
      "/events/event-tsm/documents?document=doc-fund-holdings-2026q2",
    );
    expect(
      screen.getByText(/覆盖：完整 · 时效：报告期仍在有效期/),
    ).toBeVisible();
    expect(screen.getAllByText(/许可：已准入/).slice(-1)[0]).toBeVisible();
  });

  it("lets a researcher configure suggested fund disclosure replenishment and replay the immediate run", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "建议补充的基金披露" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("heading", { name: "历史预测验证" }),
    ).toBeVisible();
    expect(
      screen.getByLabelText("建议基金：演示成长基金（000001）"),
    ).toBeChecked();
    expect(
      screen.queryByLabelText("允许在当前 Case 展示匹配季报来源"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存基金披露配置" })).toBeDisabled();
    expect(screen.getByText("保存前还需：说明配置调整理由")).toBeVisible();
    expect(screen.getByLabelText("目标报告期")).toHaveValue("2026-06-30");
    await user.selectOptions(screen.getByLabelText("补充频率"), "weekly");
    await user.type(screen.getByLabelText("配置调整理由"), "本周补充当前股票相关基金披露");
    expect(screen.getByRole("button", { name: "保存基金披露配置" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "保存基金披露配置" }));
    expect(await screen.findByText(/已保存配置版本/)).toBeVisible();
    const configHistory = (
      await screen.findByRole("heading", { name: "配置版本与调整理由" })
    ).parentElement;
    expect(configHistory).toHaveTextContent("本周补充当前股票相关基金披露");
    expect(configHistory).toHaveTextContent("版本 1 · 每周一 09:00");
    expect(configHistory).toHaveTextContent("目标报告期：2026-06-30");
    const runRefresh = vi.fn();
    window.addEventListener("research-os-run-refresh", runRefresh);
    await user.click(screen.getByRole("button", { name: "立即补充一次" }));
    expect(runRefresh).toHaveBeenCalledTimes(2);
    window.removeEventListener("research-os-run-refresh", runRefresh);
    expect(await screen.findByText("本次补充记录")).toBeVisible();
    expect(await screen.findByText("已完成")).toBeVisible();
    expect(screen.getAllByText(/同基金、同报告期季报/).length).toBeGreaterThan(1);
    expect(screen.getByText("本次数据能力与字段")).toBeVisible();
    expect(screen.getByText(/已使用：FinQuery、AnnouncementData/)).toBeVisible();
    expect(screen.getByText(/未验证能力不会被当作基金持仓/)).toBeVisible();
  });

  it("keeps historical forecast verification as an explicit human publication workflow", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <Routes>
          <Route path="/events/:caseId/market" element={<CaseMarketPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "历史预测验证" });
    await user.click(screen.getByRole("button", { name: "登记历史预测验证" }));
    expect(await screen.findByLabelText("冻结预测值")).toBeVisible();
    expect(screen.getByText(/机器只会生成候选/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("冻结预测前还需填写");
    expect(screen.getByRole("button", { name: "冻结预测目标" })).toBeDisabled();
    await user.type(screen.getByLabelText("冻结预测值"), "100");
    await user.type(screen.getByLabelText("实体标识"), "TSM");
    await user.type(screen.getByLabelText("单位"), "%");
    await user.type(screen.getByLabelText("本阶段审核理由"), "已核对研报表格、原文定位和期间口径。");
    await user.click(screen.getByRole("button", { name: "冻结预测目标" }));
    expect(await screen.findByText(/已冻结预测目标/)).toBeVisible();
    await user.type(screen.getByLabelText("后续实际值"), "80");
    await user.type(screen.getByLabelText("本阶段审核理由"), "年报实际值与预测的实体、单位和期间一致。");
    await user.click(screen.getByRole("button", { name: "冻结后续实际值" }));
    expect(await screen.findByText(/已冻结后续实际值/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "生成机器候选" }));
    expect(await screen.findByText(/机器候选：出现反证/)).toBeVisible();
    await user.selectOptions(screen.getByLabelText("发布方式"), "modified");
    expect(await screen.findByLabelText("修订后的结果")).toBeVisible();
    await user.selectOptions(
      screen.getByLabelText("修订后的结果"),
      "insufficient_evidence",
    );
    await user.type(screen.getByLabelText("人工裁决理由"), "确认实际值未达到冻结预测。 ");
    await user.click(screen.getByRole("button", { name: "发布人工裁决" }));
    expect(await screen.findByText(/已追加人工发布裁决/)).toBeVisible();
    expect(
      await screen.findByText("证据不足", {
        selector: ".ros-forecast-verdict strong",
      }),
    ).toBeVisible();
  });

  it("keeps stock and fund drill-downs inside the Case's reviewed market-expression chain", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/stocks/stock-tsm"]}>
        <Routes>
          <Route
            path="/events/:caseId/stocks/:stockId"
            element={<CaseStockProfilePage />}
          />
          <Route
            path="/events/:caseId/funds/:fundId"
            element={<CaseFundProfilePage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "台积电 · 股票研究档案" }),
    ).toBeVisible();
    expect(screen.getByText("基本面传导")).toBeVisible();
    expect(screen.getByText("市场观测")).toBeVisible();
    expect(screen.getByRole("heading", { name: "基金历史披露" })).toBeVisible();
    await user.click(screen.getByRole("link", { name: "演示成长基金" }));
    expect(
      await screen.findByRole("heading", {
        name: "演示成长基金 · 基金披露档案",
      }),
    ).toBeVisible();
    expect(screen.getByText("命中股票与披露来源")).toBeVisible();
    expect(screen.getByText("披露版本：年报")).toBeVisible();
    expect(screen.getByText("前序披露：季度报告（2026/7/20）")).toBeVisible();
  });

  it("routes a published Case to its immutable conclusion history, not a generic monitor", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-published"]}>
        <Routes>
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("link", { name: "查看结论版本" }),
    ).toHaveAttribute("href", "/events/event-published/history");
  });

  it("routes a scope-blocked Case to a versioned factor editor", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-exhausted"]}>
        <Routes>
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("link", { name: "调整研究范围" }),
    ).toHaveAttribute("href", "/events/event-exhausted/scope");
  });

  it("saves a new immutable research-scope version with three to five factors", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-exhausted/scope"]}>
        <Routes>
          <Route path="/events/:caseId/scope" element={<CaseScopePage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", {
        name: "调整关键因素，创建新的研究范围",
      }),
    ).toBeVisible();
    await user.clear(screen.getByLabelText("本次调整原因"));
    expect(screen.getByRole("button", { name: "保存新的研究范围" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "保存研究范围前还需填写：填写本次调整原因",
    );
    await user.type(screen.getByLabelText("本次调整原因"), "补足当前验证缺口");
    await user.click(screen.getByRole("button", { name: "保存新的研究范围" }));
    expect(await screen.findByText(/已创建范围版本 v2/)).toBeVisible();
  });

  it("shows drafts and human-published conclusions as a replayable version chain", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-published/history"]}>
        <Routes>
          <Route
            path="/events/:caseId/history"
            element={<CaseConclusionHistoryPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "结论版本与人工发布边界" }),
    ).toBeVisible();
    expect(screen.getByText("AI 草案，未发布")).toBeVisible();
    expect(screen.getByText("人工发布")).toBeVisible();
    expect(
      screen.getByText("人工确认：当前材料不足以断定唯一原因。"),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "查看补证运行" })).toHaveAttribute(
      "href",
      "/events/event-published/monitor",
    );
  });

  it("requires an explicit reason before a published Case can start a successor run from frozen material", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-published/documents"]}>
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "以此冻结版本重新核验" }),
    ).toBeVisible();
    const start = screen.getByRole("button", { name: "以此资料启动重新研究" });
    expect(start).toBeDisabled();
    await user.type(
      screen.getByLabelText("重新研究原因"),
      "新增披露可能影响原有判断",
    );
    await user.click(start);
    expect(await screen.findByText(/已创建后继运行/)).toBeVisible();
    expect(screen.getByText(/此前发布结论未被改写/)).toBeVisible();
  });

  it("freezes new published-Case material and records a no-change decision without starting a run", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-published/documents"]}>
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", {
        name: "新材料是否需要改变复核范围？",
      }),
    ).toBeVisible();
    expect(screen.getByText("当前已发布结论")).toBeVisible();
    expect(screen.getByText("待冻结的新材料")).toBeVisible();
    expect(screen.getByLabelText("资料适用地域")).toHaveValue("CN");
    expect(screen.getByLabelText("授权生效日")).toHaveValue("");
    expect(screen.getByLabelText("授权失效日")).toHaveValue("");
    expect(screen.getByLabelText("保留策略")).toHaveValue("case_retained");
    expect(screen.getByLabelText("删除策略")).toHaveValue("not_recorded");
    expect(screen.getByLabelText("下游使用限制")).toHaveValue(
      "仅限当前 Case 研究与人工审核",
    );
    await user.type(
      screen.getByLabelText("新增材料正文"),
      "这份新材料只重复既有判断。 ",
    );
    await user.click(
      screen.getByRole("radio", { name: /记录为不改变当前判断/ }),
    );
    await user.type(
      screen.getByLabelText("新材料决定理由"),
      "没有新增可核验指标或反证。 ",
    );
    await user.click(
      screen.getByRole("button", { name: "冻结材料并记录不改变判断" }),
    );
    expect(
      await screen.findByText(/记录“不改变当前判断”的人工决定/),
    ).toBeVisible();
    expect(screen.queryByText(/已创建后继运行/)).not.toBeInTheDocument();
  });

  it("freezes the configured governance boundary with new published-Case material", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const decide = vi.spyOn(adapter, "decidePublishedMaterial");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-published/documents"]}>
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", {
      name: "新材料是否需要改变复核范围？",
    });
    await user.clear(screen.getByLabelText("资料适用地域"));
    await user.type(screen.getByLabelText("资料适用地域"), "US");
    await user.type(screen.getByLabelText("授权生效日"), "2026-01-01");
    await user.type(screen.getByLabelText("授权失效日"), "2026-12-31");
    await user.clear(screen.getByLabelText("保留策略"));
    await user.type(screen.getByLabelText("保留策略"), "contract_2026");
    await user.clear(screen.getByLabelText("删除策略"));
    await user.type(screen.getByLabelText("删除策略"), "delete_after_2027");
    await user.type(screen.getByLabelText("合同或许可版本"), "juyuan-research-v4");
    await user.clear(screen.getByLabelText("下游使用限制"));
    await user.type(
      screen.getByLabelText("下游使用限制"),
      "仅限投研团队；禁止外部导出",
    );
    await user.type(screen.getByLabelText("新增材料正文"), "新材料正文。");
    await user.click(screen.getByRole("radio", { name: /记录为不改变当前判断/ }));
    await user.type(screen.getByLabelText("新材料决定理由"), "仅作为已冻结的边界验证。");
    await user.click(
      screen.getByRole("button", { name: "冻结材料并记录不改变判断" }),
    );

    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith(
        expect.objectContaining({
          sourceMetadata: expect.objectContaining({
            region: "US",
            effective_from: "2026-01-01",
            effective_until: "2026-12-31",
            retention_policy: "contract_2026",
            deletion_policy: "delete_after_2027",
            contract_version: "juyuan-research-v4",
            downstream_restrictions: ["仅限投研团队", "禁止外部导出"],
          }),
        }),
      ),
    );
    await screen.findByText(/记录“不改变当前判断”的人工决定/);
    await user.click(
      await screen.findByRole("button", { name: /新增待比较材料/ }),
    );
    expect(await screen.findByText("授权有效期")).toBeVisible();
    expect(screen.getByText("2026-01-01 至 2026-12-31")).toBeVisible();
    expect(screen.getByText("合同 / 许可版本")).toBeVisible();
    expect(screen.getByText("juyuan-research-v4")).toBeVisible();
  });

  it("freezes a published-Case text upload as an original before the human decision", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-published/documents"]}>
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", {
      name: "新材料是否需要改变复核范围？",
    });
    await user.selectOptions(
      screen.getByLabelText("新增材料来源接入方式"),
      "uploaded_file",
    );
    await user.upload(
      screen.getByLabelText("上传新增材料原件"),
      new File(["公司补充披露订单交付节奏。"], "published-note.txt", {
        type: "text/plain",
      }),
    );

    await waitFor(() =>
      expect(screen.getByLabelText("新增材料正文")).toHaveValue(
        "公司补充披露订单交付节奏。",
      ),
    );
    expect(screen.getByText(/冻结原始 PDF、TXT、Markdown 或 CSV/)).toBeVisible();
  });

  it("does not invent PDF text before a published-Case original is frozen", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const decide = vi.spyOn(adapter, "decidePublishedUploadedMaterial");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-published/documents"]}>
        <Routes>
          <Route path="/events/:caseId/documents" element={<CaseDocumentsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "新材料是否需要改变复核范围？" });
    await user.selectOptions(screen.getByLabelText("新增材料来源接入方式"), "uploaded_file");
    await user.upload(
      screen.getByLabelText("上传新增材料原件"),
      new File(["%PDF-not-a-real-pdf"], "late-report.pdf", { type: "application/pdf" }),
    );
    expect(screen.getByText(/PDF 原件将由服务端冻结并解析/)).toBeVisible();
    expect(screen.getByLabelText("新增材料正文")).toHaveValue("");
    expect(screen.getByLabelText("已发布结论与新材料对照")).toHaveTextContent("late-report.pdf");
    expect(screen.getByLabelText("已发布结论与新材料对照")).toHaveTextContent("本页不伪造正文预览");

    await user.type(screen.getByPlaceholderText("https://…"), "https://provider.example/report/42");
    await user.type(screen.getByLabelText("新材料决定理由"), "解析结果不改变当前人工判断。");
    await user.click(screen.getByRole("button", { name: "冻结材料并纳入重新复核" }));
    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith(expect.objectContaining({
        file: expect.objectContaining({ name: "late-report.pdf", type: "application/pdf" }),
        sourceMetadata: expect.objectContaining({
          retrieval_reference: "https://provider.example/report/42",
        }),
      })),
    );
    expect(await screen.findByText(/解析尚未完成；未创建后继运行/)).toBeVisible();
  });

  it("keeps the research question and three factors editable before a Case is created", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<EventCreatePage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.type(
      screen.getByLabelText("事件原始输入"),
      "Alphabet 公布财报后上调资本开支指引，盘后股价下跌。",
    );
    await user.click(
      screen.getByRole("button", { name: "识别事件与研究问题" }),
    );

    expect(await screen.findByLabelText("研究问题")).toBeVisible();
    expect(screen.getAllByLabelText(/关键因素/)).toHaveLength(3);
    expect(
      screen.getByRole("button", { name: "建立 Case，进入资料核验" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("checkbox", { name: /启用严格研究协议/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/新建 Case 默认采用严格研究协议/)).toBeVisible();
  });

  it("still creates a new Case when the existing-Case list cannot be read", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listEventResearch").mockRejectedValue(
      new Error("Case list unavailable"),
    );
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<EventCreatePage />} />
          <Route path="/events/:caseId" element={<p>新 Case 已建立</p>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法读取可归入 Case 清单",
    );
    await user.type(
      screen.getByLabelText("事件原始输入"),
      "公司更新指引后，需新建可验证的研究。",
    );
    await user.click(
      screen.getByRole("button", { name: "识别事件与研究问题" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "建立 Case，进入资料核验" }),
    );

    expect(await screen.findByText("新 Case 已建立")).toBeVisible();
  });

  it("lets a frozen inbox material be assigned to an existing non-published Case", async () => {
    const user = userEvent.setup();
    setResearchClient(new MockResearchAdapter());
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<EventCreatePage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.type(
      screen.getByLabelText("事件原始输入"),
      "公司补充订单交付节奏，需归入已有研究。",
    );
    await user.click(screen.getByRole("button", { name: "识别事件与研究问题" }));

    expect(await screen.findByText("决定材料归属")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: "归入已有 Case" }));
    expect(screen.getByLabelText("选择目标 Case")).toHaveValue("");
    expect(screen.getByRole("button", { name: "冻结并归入当前 Case" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("选择目标 Case"), "event-tsm");
    expect(screen.getByRole("button", { name: "冻结并归入当前 Case" })).toBeEnabled();
    expect(screen.getByText(/已发布 Case 必须走变化比较/)).toBeVisible();
  });

  it("selects an uploaded text original and explains that it will be frozen separately", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<EventCreatePage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.selectOptions(
      screen.getByLabelText("来源接入方式"),
      "uploaded_file",
    );
    const file = new File(["公司更新指引，盘后股价下跌。"], "event-note.txt", {
      type: "text/plain",
    });
    await user.upload(screen.getByLabelText("选择上传原件文件"), file);

    await waitFor(() =>
      expect(screen.getByLabelText("事件原始输入")).toHaveValue(
        "公司更新指引，盘后股价下跌。",
      ),
    );
    expect(screen.getByText(/原件与解析片段分别冻结/)).toBeVisible();
    expect(screen.getByText(/原件待冻结 · event-note.txt/)).toBeVisible();
  });

  it("makes the frozen source authority explicit before event creation", async () => {
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<EventCreatePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("来源权威性")).toHaveValue("unknown");
    expect(
      screen.getByRole("option", { name: "公司或发行人一手披露" }),
    ).toBeVisible();
    expect(screen.getByText(/权威性为 未知，待核验/)).toBeVisible();
    expect(screen.getByText(/不会直接把二手转述写成已披露事实/)).toBeVisible();
  });

  it("keeps a public web page as a URL-bound, unverified frozen snapshot", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes>
          <Route path="/events/new" element={<EventCreatePage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.type(
      screen.getByLabelText("事件原始输入"),
      "公司发布公告后，市场开始重新评估订单节奏。",
    );
    await user.selectOptions(screen.getByLabelText("来源接入方式"), "public_url");

    expect(screen.getByLabelText("公开网页链接（必填）")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "允许 AI 处理" })).not.toBeChecked();
    expect(screen.getByText(/系统只冻结你提交的正文快照，不会抓取网页/)).toBeVisible();
    expect(screen.getByRole("button", { name: "识别事件与研究问题" })).toBeDisabled();

    await user.type(
      screen.getByLabelText("公开网页链接（必填）"),
      "https://www.szse.cn/disclosure/listed/notice/index.html",
    );
    expect(screen.getByRole("button", { name: "识别事件与研究问题" })).toBeEnabled();
  });

  it("lets a researcher request review-gated extraction from a frozen Case document", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "原文资料" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: "从冻结资料提取候选" }),
    ).toBeVisible();
    expect(screen.getByText(/只会创建待人工审核的原子陈述/)).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "从冻结资料提取候选" }),
    );
    expect(await screen.findByText(/离线原型未运行 LLM 抽取/)).toBeVisible();
    expect(screen.getByText(/未产生可研究陈述/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "继续补充原 Case" }),
    ).toBeVisible();
  });

  it("opens the requested frozen document and marks the span used for review", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          "/events/event-tsm/documents?document=doc-event-tsm-q2&span=sp-tsm-capex",
        ]}
      >
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/已定位到审核候选对应的冻结原文片段/),
    ).toBeVisible();
    expect(
      screen
        .getByText("公司上调全年资本开支指引，同时市场关注自由现金流承压。")
        .closest("article"),
    ).toHaveClass("is-focused");
  });

  it("keeps a parse-failed source in the Case and makes recovery target explicit before accepting text", async () => {
    setResearchClient(new MockResearchAdapter({ scenario: "parse_failed" }));
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <Routes>
          <Route
            path="/events/:caseId/documents"
            element={<CaseDocumentsPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/解析失败；保留资料记录/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "继续补充原 Case" }),
    ).toBeVisible();
    expect(screen.getByText(/不会改写原件，也不会自动启动研究/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "继续补充原 Case" }));
    expect(await screen.findByLabelText("补充正文")).toBeVisible();
    expect(screen.getByRole("button", { name: "取消恢复" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "放弃恢复并新建资料" }),
    ).toBeVisible();
  });

  it.each([
    {
      button: "确认采纳",
      reason: "原文来自冻结的一手公司披露，支持当前因素。",
      notice: "证据已采纳，系统已生成待复核的结论草案。",
      stage: "形成结论",
      lifecycle: "结论草案待复核",
      nextTask: "审核结论草案",
      verified: 1,
      pending: 0,
    },
    {
      button: "要求补充证据",
      reason: "还需要同一期间的反证和实际经营数据。",
      notice: "补证要求已记录，系统将按冻结范围继续处理。",
      stage: "执行补证",
      lifecycle: "系统补证中",
      nextTask: "系统补证中",
      verified: 0,
      pending: 0,
    },
    {
      button: "驳回候选",
      reason: "当前候选无法支持已冻结的研究因素。",
      notice: "候选已驳回，请调整研究范围或补充来源。",
      stage: "形成结论",
      lifecycle: "当前范围已穷尽",
      nextTask: "编辑并继续自动研究",
      verified: 0,
      pending: 0,
    },
  ])("returns $button to the refreshed event workbench", async ({
    button,
    reason,
    notice,
    stage,
    lifecycle,
    nextTask,
    verified,
    pending,
  }) => {
    const user = userEvent.setup();
    const workflowRefresh = vi.fn();
    window.addEventListener("research-os-workflow-refresh", workflowRefresh);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review?client=mock"]}>
        <Routes>
          <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
          <Route
            path="/events/:caseId"
            element={<><CaseConclusionPage /><CaseLocationProbe /></>}
          />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: /条待审核关系/ });
    expect(screen.getByRole("button", { name: button })).toBeDisabled();
    await user.type(screen.getByLabelText("审核理由"), reason);
    await user.click(screen.getByRole("button", { name: button }));

    expect(await screen.findByText(notice)).toHaveAttribute("role", "status");
    expect(screen.getByTestId("case-location")).toHaveTextContent(
      "/events/event-tsm?client=mock",
    );
    expect(workflowRefresh).toHaveBeenCalledTimes(1);
    expect(
      within(screen.getByRole("list", { name: "当前事件研究进展" }))
        .getByText(stage)
        .closest("li"),
    ).toHaveAttribute("aria-current", "step");
    expect(screen.getByText(`已审核证据 ${verified}`)).toBeVisible();
    expect(screen.getByText(`待审核 ${pending}`)).toBeVisible();
    expect(screen.getAllByText(lifecycle).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: nextTask })).toBeVisible();

    window.removeEventListener("research-os-workflow-refresh", workflowRefresh);
  });

  it("keeps the review reason and stays on the review page when submission fails", async () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "reviewProposal").mockRejectedValueOnce(
      new Error("review unavailable"),
    );
    setResearchClient(adapter);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review?client=mock"]}>
        <Routes>
          <Route
            path="/events/:caseId/review"
            element={<><CaseReviewPage /><CaseLocationProbe /></>}
          />
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: /条待审核关系/ });
    const reason = screen.getByLabelText("审核理由");
    await user.type(reason, "原文口径仍需人工确认。");
    await user.click(screen.getByRole("button", { name: "确认采纳" }));

    expect(
      await screen.findByText("提交审核决定失败；候选未被自动采纳。请刷新后重试。"),
    ).toBeVisible();
    expect(reason).toHaveValue("原文口径仍需人工确认。");
    expect(screen.getByTestId("case-location")).toHaveTextContent(
      "/events/event-tsm/review?client=mock",
    );
  });

  it("refreshes global workflow state without redirecting when review succeeds after leaving the page", async () => {
    const adapter = new MockResearchAdapter();
    const pendingReview = deferred<void>();
    const reviewProposal = vi
      .spyOn(adapter, "reviewProposal")
      .mockReturnValueOnce(pendingReview.promise);
    setResearchClient(adapter);
    const workflowRefresh = vi.fn();
    window.addEventListener("research-os-workflow-refresh", workflowRefresh);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review?client=mock"]}>
        <Routes>
          <Route
            path="/events/:caseId/review"
            element={
              <>
                <CaseReviewPage />
                <Link to="/events?client=mock">离开审核页</Link>
              </>
            }
          />
          <Route
            path="/events"
            element={<><p>已离开审核页</p><CaseLocationProbe /></>}
          />
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: /条待审核关系/ });
    await user.type(screen.getByLabelText("审核理由"), "原文已经人工核验。");
    await user.click(screen.getByRole("button", { name: "确认采纳" }));
    await waitFor(() => expect(reviewProposal).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("link", { name: "离开审核页" }));
    expect(await screen.findByText("已离开审核页")).toBeVisible();

    await act(async () => {
      pendingReview.resolve();
      await pendingReview.promise;
    });

    expect(screen.getByText("已离开审核页")).toBeVisible();
    expect(screen.getByTestId("case-location")).toHaveTextContent(
      "/events?client=mock",
    );
    expect(workflowRefresh).toHaveBeenCalledTimes(1);
    window.removeEventListener("research-os-workflow-refresh", workflowRefresh);
  });

  it("ignores an invalid workflow notice route state", async () => {
    render(
      <MemoryRouter
        initialEntries={[{
          pathname: "/events/event-tsm",
          state: { workflowNotice: 42 },
        }]}
      >
        <Routes>
          <Route path="/events/:caseId" element={<CaseConclusionPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", {
        name: "尚不能下结论：系统正在核验不同解释及其反证。",
      }),
    ).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not expose a dead original-source action when an extracted claim has no web source URL", async () => {
    const user = userEvent.setup();
    const atomicClaim = {
      id: "atomic-1",
      source_span_id: "sp-tsm-capex",
      document_version_id: "doc-event-tsm-q2",
      document_source_url: "event://pasted-news",
      locator: { page: 2, paragraph: 3 },
      quote: "订单同比增长20%",
      quote_start: 14,
      quote_end: 23,
      quote_sha256: "a".repeat(64),
      normalized_text: "公司披露订单同比增长 20%",
      claim_type: "disclosed_fact",
      assertion_actor: "公司",
      authority_level: "primary_disclosure",
      structured_fields: { run_ref: "extract:run-1" },
      validation_result: { quote_continuous: true },
      created_at: "2026-08-09T00:00:00Z",
      review_state: "awaiting_review",
      review_history: [],
      published_source_statement: null,
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
          if (String(input).includes("/atomic-claims/atomic-1/reviews")) {
            return Promise.resolve(
              new Response(
                JSON.stringify({
                  id: "review-1",
                  outcome: "confirmed",
                  reviewer: "human:researcher",
                  reason: "原文和定位已复核",
                  published_source_statement: {
                    id: "statement-1",
                    normalized_text: atomicClaim.normalized_text,
                    kind: "disclosed_fact",
                    observed_period: null,
                    created_at: "2026-08-09T00:02:00Z",
                  },
                  created_at: "2026-08-09T00:02:00Z",
                }),
                {
                  status: 201,
                  headers: { "content-type": "application/json" },
                },
              ),
            );
          }
          return Promise.resolve(
            new Response(JSON.stringify({ items: [atomicClaim] }), {
              status: 200,
              headers: { "content-type": "application/json" },
            }),
          );
        }),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
        <Routes>
          <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("原子陈述审核")).toBeVisible();
    expect(screen.getByText("订单同比增长20%")).toBeVisible();
    expect(screen.getByText(/extract:run-1/)).toBeVisible();
    expect(screen.getByRole("link", { name: "定位到冻结原文" })).toHaveAttribute(
      "href",
      "/events/event-tsm/documents?document=doc-event-tsm-q2&span=sp-tsm-capex",
    );
    expect(
      screen.queryByRole("link", { name: "打开原始链接" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("原始链接未记录")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "在此页核对原文" }));
    expect(await screen.findByText("在此页核对的冻结原文")).toBeVisible();
    expect(
      screen.getByText(
        "公司上调全年资本开支指引，同时市场关注自由现金流承压。",
      ),
    ).toBeVisible();
    await user.type(
      screen.getByLabelText("原子陈述审核理由"),
      "原文和定位已复核",
    );
    await user.click(screen.getByRole("button", { name: "确认并发布" }));
    expect(await screen.findByText(/已发布为正式陈述/)).toBeVisible();
  });

  it("keeps non-admissible source candidates visible with their blocking reason", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
        <Routes>
          <Route path="/events/:caseId/review" element={<CaseReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("资料受限，不能采纳")).toBeVisible();
    expect(
      screen.getByText("来源由用户粘贴解析，尚未完成内容验证"),
    ).toBeVisible();
    expect(screen.getByText("测试域名不能作为正式证据来源")).toBeVisible();
  });

  it("keeps a frozen active-run scope visible above every workbench route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            items: [
              {
                run_id: "run-1",
                case_id: "event-tsm",
                case_title: "台积电上调 CoWoS 指引后下跌",
                status: "running",
                stage: "retrieve",
                updated_at: "2026-08-09T00:00:00Z",
                processed_count: 3,
                next_action: "查看本次运行",
                scope: {
                  trigger: "manual",
                  monitor_version_id: "monitor-1",
                  factor_ids: ["factor-1"],
                  allowed_source_types: ["company_disclosure"],
                  budget: 12,
                },
              },
            ],
            next_cursor: null,
            has_more: false,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("台积电上调 CoWoS 指引后下跌");
    expect(strip).toHaveTextContent("公司披露");
    expect(strip).toHaveTextContent("已处理 3");
  });

  it("does not describe queued work as advancing when the shared worker is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.endsWith("/research-runs/worker-status")
          ? { status: "unavailable", last_seen_at: null, mode: null, state: null }
          : url.endsWith("/research-runs/active")
            ? {
                items: [{
                  run_id: "run-queued",
                  case_id: "event-tsm",
                  case_title: "等待执行器的 Case",
                  status: "queued",
                  stage: "scope",
                  updated_at: "2026-08-09T00:00:00Z",
                  processed_count: 0,
                  next_action: "查看本次运行",
                  scope: { allowed_source_types: ["company_disclosure"] },
                }],
                next_cursor: null,
                has_more: false,
              }
            : { items: [], next_cursor: null, has_more: false };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "已排队但执行器未启动" });
    expect(strip).toHaveTextContent("排队中 · 执行器未启动");
    expect(strip).toHaveTextContent("已冻结，尚未执行");
  });

  it("keeps an active fund-disclosure replenishment visible outside the market page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.endsWith("/fund-disclosure-sync-runs/active")
          ? {
              items: [{
                run_id: "fund-run-1",
                case_id: "event-tsm",
                case_title: "台积电上调 CoWoS 指引后下跌",
                trigger: "scheduled",
                status: "started",
                stage: "query_holdings",
                message: "开始查询指定基金的历史股票持仓披露",
                fund_codes: ["515050"],
                stock_codes: ["601138.SH"],
                updated_at: "2026-08-11T01:00:00Z",
              }],
            }
          : { items: [], next_cursor: null, has_more: false };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("基金披露补充 · 定时任务");
    expect(strip).toHaveTextContent("515050");
    expect(strip).toHaveTextContent("开始查询指定基金的历史股票持仓披露");
    expect(screen.getByRole("link", { name: "查看基金披露运行 →" })).toHaveAttribute(
      "href",
      "/events/event-tsm/market",
    );
  });

  it("labels a review-blocked run as waiting instead of claiming it is still executing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              String(input).endsWith("/research-runs/active")
                ? {
                    items: [{
                      run_id: "run-waiting",
                      case_id: "event-tsm",
                      case_title: "等待审核的 Case",
                      status: "waiting_for_review",
                      stage: "candidate",
                      updated_at: "2026-08-09T00:00:00Z",
                      processed_count: 2,
                      next_action: "审核候选证据",
                      scope: { allowed_source_types: ["company_disclosure"] },
                    }],
                    next_cursor: null,
                    has_more: false,
                  }
                : { run_id: "run-waiting", items: [], next_cursor: null, has_more: false },
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("等待人工审核 · 提出候选");
    expect(strip).not.toHaveTextContent("系统正在运行 ·");
  });

  it("refreshes the global run strip and event review count after workflow writes", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const listEventResearch = vi.spyOn(adapter, "listEventResearch");
    setResearchClient(adapter);
    setResearchOsApi(api);

    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("等待人工审核 · 等待审核");
    expect(await screen.findByRole("link", { name: "待我审核 2" })).toBeVisible();
    const initialEventReads = listEventResearch.mock.calls.length;

    await act(async () => {
      await adapter.reviewProposal("proposal-event-tsm", {
        outcome: "confirmed",
        reason: "原始披露足以支持该因素。",
        reviewer_id: "human:researcher",
        expected_version: 1,
      });
      window.dispatchEvent(new Event("research-os-workflow-refresh"));
    });

    await waitFor(() => {
      expect(listEventResearch).toHaveBeenCalledTimes(initialEventReads + 1);
      expect(
        screen.queryByText("等待人工审核 · 等待审核"),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "待我审核 2" })).toBeVisible();

    await act(async () => {
      await adapter.publishEventConclusion({
        caseId: "event-tsm",
        text: "资本开支上调构成当前市场担忧的重要可验证因素。",
        reviewer: "human:researcher",
      });
      window.dispatchEvent(new Event("research-os-workflow-refresh"));
    });

    expect(await screen.findByRole("link", { name: "待我审核 1" })).toBeVisible();
    expect(
      screen.queryByText("等待人工审核 · 等待审核"),
    ).not.toBeInTheDocument();
  });

  it("refreshes the AppShell run strip from the real evidence-review producer", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const activeRuns = vi.spyOn(api, "activeRuns");
    setResearchClient(adapter);
    setResearchOsApi(api);
    const user = userEvent.setup();

    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/events/event-tsm/review?client=mock"]}>
          <ResearchOsRoutes />
        </MemoryRouter>
      </StrictMode>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("等待人工审核 · 等待审核");
    const initialActiveRunReads = activeRuns.mock.calls.length;
    await screen.findByRole("heading", { name: /条待审核关系/ });
    await user.type(
      screen.getByLabelText("审核理由"),
      "冻结原文和定位已经人工核验。",
    );
    await user.click(screen.getByRole("button", { name: "确认采纳" }));

    expect(
      await screen.findByText("证据已采纳，系统已生成待复核的结论草案。"),
    ).toHaveAttribute("role", "status");
    await waitFor(() => {
      expect(activeRuns.mock.calls.length).toBeGreaterThan(initialActiveRunReads);
      expect(
        screen.queryByText("等待人工审核 · 等待审核"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("region", { name: "系统正在运行" }),
      ).not.toBeInTheDocument();
    });
  });

  it("refreshes run events when workflow state changes without changing the active run ID", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const runEvents = vi.spyOn(api, "runEvents");
    setResearchClient(adapter);
    setResearchOsApi(api);

    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    await waitFor(() => {
      expect(strip).toHaveTextContent("候选证据等待人工审核，未写入结论");
    });
    const initialRunEventReads = runEvents.mock.calls.length;

    await act(async () => {
      await adapter.reviewProposal("proposal-event-tsm", {
        outcome: "needs_more_evidence",
        reason: "需要补充资本开支与自由现金流的季度桥接数据。",
        reviewer_id: "human:researcher",
        expected_version: 1,
      });
      window.dispatchEvent(new Event("research-os-workflow-refresh"));
    });

    await waitFor(() => {
      expect(runEvents).toHaveBeenCalledTimes(initialRunEventReads + 1);
      expect(strip).toHaveTextContent("运行中 · 采集资料");
      expect(strip).toHaveTextContent("审核要求已记录，系统继续补证");
    });
  });

  it("keeps the latest workflow event-list response when an older poll resolves last", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const oldRequest = deferred<EventResearchListItem[]>();
    const freshEvents: EventResearchListItem[] = [{
      id: "event-fresh",
      eventTitle: "最新审核任务",
      companyName: null,
      ticker: null,
      eventAt: null,
      status: "draft_ready",
      statusSummary: "最新状态",
      nextHumanAction: "审核最新结论",
      updatedAt: "2026-08-12T10:00:00Z",
    }];
    const staleEvents: EventResearchListItem[] = [
      ...freshEvents,
      {
        ...freshEvents[0],
        id: "event-stale",
        eventTitle: "过期审核任务",
      },
    ];
    const listEventResearch = vi
      .spyOn(adapter, "listEventResearch")
      .mockImplementationOnce(() => oldRequest.promise)
      .mockResolvedValue(freshEvents);
    setResearchClient(adapter);
    setResearchOsApi(api);

    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(listEventResearch).toHaveBeenCalledTimes(1));
    act(() => {
      window.dispatchEvent(new Event("research-os-workflow-refresh"));
    });
    expect(await screen.findByRole("link", { name: "待我审核 1" })).toBeVisible();

    await act(async () => {
      oldRequest.resolve(staleEvents);
      await oldRequest.promise;
    });

    expect(screen.getByRole("link", { name: "待我审核 1" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "待我审核 2" })).not.toBeInTheDocument();
  });

  it("keeps the latest active-run response when an older poll resolves last", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const oldRequest = deferred<Awaited<ReturnType<typeof api.activeRuns>>>();
    const run = (caseTitle: string, status: string, stage: string) => ({
      has_more: false,
      items: [{
        run_id: "run-race",
        case_id: "event-tsm",
        case_title: caseTitle,
        status,
        stage,
        updated_at: "2026-08-12T10:00:00Z",
        processed_count: 1,
        next_action: "查看运行详情",
        scope: {
          trigger: "schedule",
          allowed_source_types: ["company_disclosure"],
        },
      }],
    });
    const activeRuns = vi
      .spyOn(api, "activeRuns")
      .mockImplementationOnce(() => oldRequest.promise)
      .mockResolvedValue(run("最新运行状态", "running", "retrieve"));
    vi.spyOn(api, "runEvents").mockResolvedValue({
      run_id: "run-race",
      has_more: false,
      items: [],
    });
    setResearchClient(adapter);
    setResearchOsApi(api);

    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(activeRuns).toHaveBeenCalledTimes(1));
    act(() => {
      window.dispatchEvent(new Event("research-os-workflow-refresh"));
    });
    expect(await screen.findByText(/最新运行状态/)).toBeVisible();

    await act(async () => {
      oldRequest.resolve(run("过期运行状态", "awaiting_review", "review"));
      await oldRequest.promise;
    });

    expect(screen.getByText(/最新运行状态/)).toBeVisible();
    expect(screen.queryByText(/过期运行状态/)).not.toBeInTheDocument();
  });

  it("removes stale active-run strips when their live status can no longer be confirmed", async () => {
    vi.useFakeTimers();
    let activeRunRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/research-runs/active")) {
          activeRunRequests += 1;
          if (activeRunRequests > 1)
            return Promise.resolve(new Response("unavailable", { status: 503 }));
          return Promise.resolve(
            new Response(
              JSON.stringify({
                items: [
                  {
                    run_id: "run-stale",
                    case_id: "event-tsm",
                    case_title: "不应被当成当前运行的 Case",
                    status: "running",
                    stage: "retrieve",
                    updated_at: "2026-08-09T00:00:00Z",
                    processed_count: 3,
                    next_action: "查看本次运行",
                    scope: { allowed_source_types: ["company_disclosure"] },
                  },
                ],
                next_cursor: null,
                has_more: false,
              }),
              { status: 200, headers: { "content-type": "application/json" } },
            ),
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify({ items: [], next_cursor: null, has_more: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await act(async () => {});
    expect(screen.getByRole("region", { name: "系统正在运行" })).toHaveTextContent(
      "不应被当成当前运行的 Case",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(screen.getByRole("region", { name: "运行状态不可用" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "重新读取统一运行记录" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("region", { name: "系统正在运行" }),
    ).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("lets a researcher retry the unified run status without waiting for the next poll", async () => {
    let activeRunRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/research-runs/active")) {
          activeRunRequests += 1;
          if (activeRunRequests === 1) {
            return Promise.resolve(new Response("unavailable", { status: 503 }));
          }
        }
        return Promise.resolve(
          new Response(
            JSON.stringify({ items: [], next_cursor: null, has_more: false }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        );
      }),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "重新读取统一运行记录" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "运行状态不可用" }),
      ).not.toBeInTheDocument(),
    );
    expect(activeRunRequests).toBeGreaterThanOrEqual(2);
  });

  it("keeps every concurrent active run visible and individually expandable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.endsWith("/research-runs/active")
          ? {
              items: [
                {
                  run_id: "run-a",
                  case_id: "event-a",
                  case_title: "订单验证 Case",
                  status: "running",
                  stage: "retrieve",
                  updated_at: "2026-08-09T00:00:00Z",
                  processed_count: 3,
                  next_action: "查看本次运行",
                  scope: { allowed_source_types: ["company_disclosure"] },
                },
                {
                  run_id: "run-b",
                  case_id: "event-b",
                  case_title: "毛利率验证 Case",
                  status: "running",
                  stage: "verify",
                  updated_at: "2026-08-09T00:00:00Z",
                  processed_count: 7,
                  next_action: "审核候选证据",
                  scope: { allowed_source_types: ["licensed_provider"] },
                },
              ],
              next_cursor: null,
              has_more: false,
            }
          : url.endsWith("/research-runs/run-a/events")
            ? {
                run_id: "run-a",
                items: [
                  {
                    seq: 3,
                    stage: "retrieve",
                    status: "completed",
                    message: "已按许可范围读取公司披露",
                    details: {},
                    created_at: "2026-08-09T00:00:00Z",
                  },
                ],
                next_cursor: null,
                has_more: false,
              }
            : url.endsWith("/research-runs/run-b/events")
              ? {
                  run_id: "run-b",
                  items: [
                    {
                      seq: 7,
                      stage: "verify",
                      status: "running",
                      message: "正在核验毛利率验证指标",
                      details: {},
                      created_at: "2026-08-09T00:00:00Z",
                    },
                  ],
                  next_cursor: null,
                  has_more: false,
                }
              : { items: [], next_cursor: null, has_more: false };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strips = await screen.findAllByRole("region", {
      name: "系统正在运行",
    });
    expect(strips).toHaveLength(2);
    expect(strips[0]).toHaveTextContent("订单验证 Case");
    expect(strips[1]).toHaveTextContent("毛利率验证 Case");
    await waitFor(() => {
      expect(strips[0]).toHaveTextContent(
        "最近记录 · 采集资料 · 已按许可范围读取公司披露",
      );
      expect(strips[1]).toHaveTextContent(
        "最近记录 · 验证关键因素 · 正在核验毛利率验证指标",
      );
    });
    expect(screen.getAllByRole("button", { name: "展开运行详情" })).toHaveLength(2);
  });

  it("makes the latest recorded active-run step visible without opening a drawer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              String(input).endsWith("/research-runs/active")
                ? {
                    items: [
                      {
                        run_id: "run-1",
                        case_id: "event-tsm",
                        case_title: "台积电 Case",
                        status: "running",
                        stage: "parse",
                        updated_at: "2026-08-09T00:00:00Z",
                        processed_count: 3,
                        next_action: "查看本次运行",
                        scope: {
                          trigger: "manual",
                          monitor_version_id: "monitor-1",
                          factor_ids: ["factor-1"],
                          allowed_source_types: ["company_disclosure"],
                          budget: 12,
                        },
                      },
                    ],
                    next_cursor: null,
                    has_more: false,
                  }
                : {
                    run_id: "run-1",
                    items: [
                      {
                        seq: 4,
                        stage: "parse",
                        status: "completed",
                        message: "已冻结 2 份可核验材料",
                        details: { frozen_documents: 2 },
                        created_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                    next_cursor: null,
                    has_more: false,
                  },
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    await waitFor(() =>
      expect(strip).toHaveTextContent(
        "最近记录 · 解析原文 · 已冻结 2 份可核验材料",
      ),
    );
  });

  it("searches the current Case registry and makes the matched Case directly navigable", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ items: [], next_cursor: null, has_more: false }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
            <Route path="/events/:caseId" element={<p>Case 详情</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await user.type(
      screen.getByLabelText("搜索事件、公司、命题或证据"),
      "台积",
    );
    const result = await screen.findByRole("link", {
      name: /台积电上调 CoWoS 指引后下跌/,
    });
    expect(result).toHaveAttribute("href", "/events/event-tsm");
    await user.click(result);
    expect(await screen.findByText("Case 详情")).toBeVisible();
  });

  it("surfaces a reviewed global thesis result instead of limiting the topbar to the current Case registry", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "search").mockResolvedValue([
      {
        group: "命题",
        id: "thesis-capex",
        title: "客户资本开支转化为订单",
        hint: "已审核命题 · 可回到当前 Case",
        navigate_to: "/events/event-tsm/evidence",
      },
    ]);
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
            <Route
              path="/events/:caseId/evidence"
              element={<p>命题与证据详情</p>}
            />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await user.type(
      screen.getByLabelText("搜索事件、公司、命题或证据"),
      "资本开支",
    );
    expect(
      await screen.findByText("全局已准入研究资产找到 1 条匹配"),
    ).toBeVisible();
    const result = await screen.findByRole("link", {
      name: /客户资本开支转化为订单/,
    });
    expect(result).toHaveAttribute("href", "/events/event-tsm/evidence");
    await user.click(result);
    expect(await screen.findByText("命题与证据详情")).toBeVisible();
  });

  it("expands the immutable active-run event chain without leaving the current page", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              String(input).endsWith("/research-runs/active")
                ? {
                    items: [
                      {
                        run_id: "run-1",
                        case_id: "event-tsm",
                        case_title: "台积电 Case",
                        status: "running",
                        stage: "retrieve",
                        updated_at: "2026-08-09T00:00:00Z",
                        processed_count: 3,
                        next_action: "查看本次运行",
                        scope: {
                          trigger: "manual",
                          monitor_version_id: "monitor-1",
                          factor_ids: ["factor-1"],
                          allowed_source_types: ["company_disclosure"],
                          budget: 12,
                          frequency: "weekday_08_30",
                          next_verification_event: "2026Q1 财报披露",
                          configured_by: "human:lin",
                          configuration_change_reason: "以晨间披露核验订单指引",
                        },
                      },
                    ],
                    next_cursor: null,
                    has_more: false,
                  }
                : {
                    run_id: "run-1",
                    items: [
                      {
                        seq: 1,
                        stage: "scope",
                        status: "recorded",
                        message: "冻结本次范围",
                        details: {
                          allowed_source_types: ["company_disclosure"],
                        },
                        created_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                    next_cursor: null,
                    has_more: false,
                  },
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "展开运行详情" }),
    );
    const drawer = await screen.findByRole("complementary", {
      name: "全局运行详情",
    });
    expect(drawer).toHaveTextContent("公司披露");
    expect(drawer).toHaveTextContent("冻结本次范围");
    expect(drawer).toHaveTextContent("工作日 08:30");
    expect(drawer).toHaveTextContent("2026Q1 财报披露");
    expect(drawer).toHaveTextContent("human:lin");
    expect(drawer).toHaveTextContent("以晨间披露核验订单指引");
    expect(screen.getByText("工作台内容")).toBeVisible();
  });

  it("makes an unreadable active-run drawer explicit and retries the same frozen run", async () => {
    const user = userEvent.setup();
    let eventLedgerAvailable = false;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const isActiveRuns = String(input).endsWith("/research-runs/active");
        if (!isActiveRuns && !eventLedgerAvailable) {
          return Promise.resolve(new Response("temporarily unavailable", { status: 503 }));
        }
        const body = isActiveRuns
          ? {
              items: [
                {
                  run_id: "run-drawer-retry",
                  case_id: "event-tsm",
                  case_title: "台积电 Case",
                  status: "running",
                  stage: "retrieve",
                  updated_at: "2026-08-09T00:00:00Z",
                  processed_count: 3,
                  next_action: "查看本次运行",
                  scope: {
                    trigger: "manual",
                    monitor_version_id: "monitor-1",
                    factor_ids: ["factor-1"],
                    allowed_source_types: ["company_disclosure"],
                    budget: 12,
                    frequency: "weekday_08_30",
                    next_verification_event: "2026Q1 财报披露",
                    configured_by: "human:lin",
                    configuration_change_reason: "以晨间披露核验订单指引",
                  },
                },
              ],
              next_cursor: null,
              has_more: false,
            }
          : {
              run_id: "run-drawer-retry",
              items: [
                {
                  seq: 1,
                  stage: "scope",
                  status: "recorded",
                  message: "已冻结范围",
                  details: { allowed_source_types: ["company_disclosure"] },
                  created_at: "2026-08-09T00:00:00Z",
                },
              ],
              next_cursor: null,
              has_more: false,
            };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/events" element={<p>工作台内容</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "展开运行详情" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本次运行事件暂不可读取",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "不会以当前配置补写历史记录",
    );

    eventLedgerAvailable = true;
    await user.click(
      screen.getByRole("button", { name: "重新读取本次运行事件" }),
    );
    expect(
      await screen.findByRole("complementary", { name: "全局运行详情" }),
    ).toHaveTextContent("已冻结范围");
  });

  it("keeps Case evidence separate from the current conclusion and exposes its frozen locator", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/evidence"]}>
        <Routes>
          <Route
            path="/events/:caseId/evidence"
            element={<CaseEvidencePage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", {
        name: "每一条关系都保留原文、时点与审核边界",
      }),
    ).toBeVisible();
    expect(screen.getAllByText("命题与证据").length).toBeGreaterThan(0);
    expect(screen.getByText("精确定位")).toBeVisible();
    expect(
      screen
        .getAllByRole("link", { name: "原文资料" })
        .some(
          (link) => link.getAttribute("href") === "/events/event-tsm/documents",
        ),
    ).toBe(true);
  });

  it("shows the researchability gate as an explicit Case workflow, never a hidden worker state", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/researchability")
          ? {
              status: "blocked",
              reason_codes: ["missing_outcome_binding"],
              effective_binding_id: null,
              next_action: "确认结果指标、范围、基线和时间窗",
            }
          : url.endsWith("/metric-definitions")
            ? [
                {
                  id: "metric-revenue",
                  metric_id: "revenue",
                  version: 1,
                  display_name: "营业收入",
                  entity_scope: "company",
                  unit: "CNY m",
                  role_eligibility: ["outcome"],
                  approved_by: "human:methodology",
                  reason: "测试用已审核指标",
                  created_at: "2026-08-09T00:00:00Z",
                },
              ]
          : url.endsWith("/mechanism-templates")
            ? [
                {
                  id: "template-1",
                  template_key: "overseas_ai_capex_to_china_hardware",
                  version: 1,
                  display_name: "海外 AI CapEx 到中国硬件",
                  industry_scope: "ai_hardware",
                  approved_by: "human",
                  reason: "已审核路径",
                  created_at: "2026-08-09T00:00:00Z",
                  nodes: [],
                  edges: [],
                },
              ]
            : url.includes("/mechanism-protocol")
              ? { selection: null, template: null, rules: [] }
              : [];
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/protocol"]}>
        <Routes>
          <Route
            path="/events/:caseId/protocol"
            element={<CaseProtocolPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "研究协议与可研究性门槛" }),
    ).toBeVisible();
    expect(screen.getByText("确认结果指标、范围、基线和时间窗")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "设定结果指标与验证窗口" }),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "设定结果指标与验证窗口" }),
    );
    expect(
      screen.getByRole("status"),
    ).toHaveTextContent("保存前还需填写");
    expect(screen.getByRole("status")).toHaveTextContent("填写公司 ID");
    expect(
      screen.getByRole("button", { name: "登记为待审核结果绑定" }),
    ).toBeDisabled();
    expect(
      await screen.findByRole("button", { name: "选择此模板" }),
    ).toBeVisible();
    expect(
      screen.getByText(/系统不会把市场表现自动写成机制成立/),
    ).toBeVisible();
  });

  it("makes a pending outcome binding review actionable before approval", async () => {
    const api = new MockResearchOsApi(new MockResearchAdapter());
    vi.spyOn(api, "researchability").mockResolvedValue({
      status: "blocked",
      reason_codes: ["binding_not_approved"],
      effective_binding_id: "binding-pending",
      next_action: "审核并固定结果绑定",
    });
    setResearchOsApi(api);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/protocol"]}>
        <Routes>
          <Route
            path="/events/:caseId/protocol"
            element={<CaseProtocolPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    const button = await screen.findByRole("button", {
      name: "审核并固定结果绑定",
    });
    expect(screen.getByLabelText("结果绑定审核理由")).toBeVisible();
    expect(button).toBeDisabled();
    expect(screen.getByText(/审核结果绑定前还需填写/)).toHaveTextContent(
      "审核结果绑定前还需填写：填写审核理由",
    );
  });

  it("blocks immediate replenishment in the UI with the exact missing protocol work", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/researchability")
          ? {
              status: "blocked",
              reason_codes: ["missing_outcome_binding"],
              effective_binding_id: null,
              next_action: "确认结果指标、范围、基线和时间窗",
            }
          : {
              monitor: {
                id: "monitor-1",
                version: 1,
                status: "active",
                frequency: "weekday_08_30",
                factor_ids: ["event-tsm-factor-1"],
                allowed_source_types: ["company_disclosure"],
                next_verification_event: "下一次财报",
                budget: 10,
                changed_by: "human",
                change_reason: "test",
                created_at: "2026-08-09T00:00:00Z",
              },
              latest_run: null,
              confirmed_factors: [
                { id: "event-tsm-factor-1", statement: "资本开支指引" },
              ],
            };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("研究协议尚未通过，不能启动补证。"),
    ).toBeVisible();
    expect(screen.getByText(/尚未固定结果指标/)).toBeVisible();
    expect(screen.getByText("下次定时检查")).toBeVisible();
    expect(screen.getByRole("button", { name: "立即补证一次" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "补齐研究协议" })).toHaveAttribute(
      "href",
      "/events/event-tsm/protocol",
    );
  });

  it("tells the Case researcher why a queued run cannot move when no worker is live", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.endsWith("/research-runs/worker-status")
          ? { status: "unavailable", last_seen_at: null, mode: null, state: null }
          : url.includes("/researchability")
            ? {
                status: "ready",
                reason_codes: [],
                effective_binding_id: "binding-ready",
                next_action: "可开始补证",
              }
            : {
                monitor: {
                  id: "monitor-1",
                  version: 1,
                  status: "active",
                  frequency: "weekday_08_30",
                  factor_ids: ["event-tsm-factor-1"],
                  allowed_source_types: ["company_disclosure"],
                  next_verification_event: "下一次财报",
                  budget: 10,
                  changed_by: "human",
                  change_reason: "test",
                  created_at: "2026-08-09T00:00:00Z",
                },
                latest_run: {
                  id: "run-queued",
                  status: "queued",
                  stage: "scope",
                  updated_at: "2026-08-09T00:00:00Z",
                },
                confirmed_factors: [
                  { id: "event-tsm-factor-1", statement: "资本开支指引" },
                ],
              };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("执行器未启动；已排队的研究不会自动推进。"),
    ).toBeVisible();
  });

  it("checks protocol readiness only for the factors selected by the active monitor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/researchability")
          ? url.endsWith("/theses/selected-factor/researchability")
            ? {
                status: "ready",
                reason_codes: [],
                effective_binding_id: "binding-selected",
                next_action: "可开始补证",
              }
            : {
                status: "blocked",
                reason_codes: ["missing_outcome_binding"],
                effective_binding_id: null,
                next_action: "确认结果指标、范围、基线和时间窗",
              }
          : {
              monitor: {
                id: "monitor-selected",
                version: 1,
                status: "active",
                frequency: "weekday_08_30",
                factor_ids: ["selected-factor"],
                allowed_source_types: ["company_disclosure"],
                next_verification_event: "下一次财报",
                budget: 10,
                changed_by: "human",
                change_reason: "只跟踪已就绪因素",
                created_at: "2026-08-09T00:00:00Z",
              },
              latest_run: null,
              confirmed_factors: [
                { id: "selected-factor", statement: "已就绪因素" },
                { id: "unselected-factor", statement: "未纳入本次监控的因素" },
              ],
            };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "立即补证一次" }),
      ).toBeEnabled(),
    );
    expect(
      screen.queryByText("研究协议尚未通过，不能启动补证。"),
    ).not.toBeInTheDocument();
  });

  it("starts immediate replenishment through the frozen-monitor endpoint", async () => {
    const user = userEvent.setup();
    let monitorRunCreated = false;
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listResearchRuns").mockImplementation(async () =>
      monitorRunCreated
        ? [monitorRunSummary("run-monitor-v3", "running", "retrieve")]
        : [],
    );
    setResearchClient(adapter);
    const monitor = {
      id: "monitor-v3",
      version: 3,
      status: "paused",
      frequency: "weekday_08_30",
      factor_ids: ["event-tsm-factor-1"],
      allowed_source_types: ["uploaded_file"],
      next_verification_event: "下一次财报",
      budget: 7,
      changed_by: "human",
      change_reason: "人工补证",
      created_at: "2026-08-09T00:00:00Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = url.includes("/researchability")
        ? {
            status: "ready",
            reason_codes: [],
            effective_binding_id: "binding-1",
            next_action: "可开始补证",
          }
        : url.endsWith("/monitor/runs")
          ? (monitorRunCreated = true, {
              id: "run-monitor-v3",
              case_id: "event-tsm",
              status: "queued",
              stage: "queued",
              round: 0,
              max_rounds: 3,
              budget: 7,
              budget_used: 0,
              stop_reason: null,
              scope_thesis_ids: ["event-tsm-factor-1"],
              progress: {},
              evidence: {},
              by_thesis: {},
              gaps: [],
              gap_tasks: [],
              failed_tasks: [],
              assessments: [],
              pending_proposals: [],
              review_tasks: [],
              next_action: "查看运行详情",
              tasks: [],
            })
          : url.endsWith("/research-runs/run-monitor-v3/events")
            ? {
                run_id: "run-monitor-v3",
                has_more: false,
                items: [
                  {
                    seq: 1,
                    stage: "scope",
                    status: "completed",
                    message: "已冻结本次运行范围",
                    details: {
                      trigger: "manual",
                      monitor_version_id: "monitor-v3",
                      factor_ids: ["event-tsm-factor-1"],
                      factor_statements: ["资本开支指引"],
                      allowed_source_types: ["uploaded_file"],
                      budget: 7,
                    },
                    created_at: "2026-08-09T00:00:00Z",
                  },
                  {
                    seq: 2,
                    stage: "retrieve",
                    status: "completed",
                    message: "已按许可检查候选资料",
                    details: {
                      accepted: 2,
                      excluded: 1,
                      exclusion_reason: "来源许可不足",
                    },
                    created_at: "2026-08-09T00:01:00Z",
                  },
                ],
              }
            : {
                monitor,
                latest_run: monitorRunCreated
                  ? {
                      id: "run-monitor-v3",
                      status: "running",
                      stage: "retrieve",
                      updated_at: "2026-08-09T00:02:00Z",
                    }
                  : null,
                confirmed_factors: [
                  { id: "event-tsm-factor-1", statement: "资本开支指引" },
                ],
              };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "立即补证一次" }),
    );

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/research-cases/event-tsm/monitor/runs",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() =>
      expect(
        screen.getAllByRole("heading", { name: "采集资料 · 运行中" }),
      ).toHaveLength(2),
    );
    expect(
      await screen.findByRole("complementary", { name: "运行详情" }),
    ).toHaveTextContent("monitor-v3");
    expect(
      screen.getByRole("complementary", { name: "运行详情" }),
    ).toHaveTextContent("上传原件");
    expect(
      screen.getByRole("complementary", { name: "运行详情" }),
    ).toHaveTextContent("7");
    expect(
      screen.getByRole("complementary", { name: "运行详情" }),
    ).toHaveTextContent("排除原因：来源许可不足");
  });

  it("requires a recorded reason before a researcher stops an in-progress run", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listResearchRuns").mockResolvedValue([
      monitorRunSummary("run-live", "running", "retrieve"),
    ]);
    setResearchClient(adapter);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = url.includes("/researchability")
        ? {
            status: "ready",
            reason_codes: [],
            effective_binding_id: "binding-1",
            next_action: "可开始补证",
          }
        : url.endsWith("/research-runs/run-live/events")
          ? {
              run_id: "run-live",
              has_more: false,
              items: [
                {
                  seq: 1,
                  stage: "scope",
                  status: "completed",
                  message: "已冻结本次范围",
                  details: {
                    trigger: "manual",
                    monitor_version_id: "monitor-v1",
                    allowed_source_types: ["company_disclosure"],
                    budget: 10,
                  },
                  created_at: "2026-08-09T00:00:00Z",
                },
                {
                  seq: 2,
                  stage: "retrieve",
                  status: "running",
                  message: "正在读取许可来源",
                  details: {},
                  created_at: "2026-08-09T00:01:00Z",
                },
              ],
            }
          : {
              monitor: {
                id: "monitor-v1",
                version: 1,
                status: "active",
                frequency: "weekday_08_30",
                factor_ids: ["event-tsm-factor-1"],
                allowed_source_types: ["company_disclosure"],
                next_verification_event: "下一次财报",
                budget: 10,
                changed_by: "human",
                change_reason: "test",
                created_at: "2026-08-09T00:00:00Z",
              },
              latest_run: {
                id: "run-live",
                status: "running",
                stage: "retrieve",
                updated_at: "2026-08-09T00:01:00Z",
              },
              confirmed_factors: [
                { id: "event-tsm-factor-1", statement: "资本开支指引" },
              ],
            };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("工作日 08:30 · 中国标准时间"),
    ).toBeVisible();
    expect(screen.queryByText("weekday_08_30")).not.toBeInTheDocument();
    await user.click(
      await screen.findByRole("button", { name: "打开运行详情" }),
    );
    expect(
      await screen.findByRole("button", { name: "停止本次运行" }),
    ).toBeDisabled();
    await user.type(
      screen.getByLabelText("停止原因"),
      "资料源授权异常，停止后重新配置",
    );
    await user.click(screen.getByRole("button", { name: "停止本次运行" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/research-runs/run-live/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  async function setupHistoricalRunReplay(options: {
    latestRunId?: string;
    rejectDetailFor?: string;
    rejectHistory?: boolean;
  } = {}) {
    const adapter = new MockResearchAdapter();
    const baseRun = await adapter.getResearchRun("run-aic-001");
    const latestRunId = options.latestRunId ?? "run-reviewed";
    vi.spyOn(adapter, "listResearchRuns").mockImplementation(async () => {
      if (options.rejectHistory) throw new Error("run history unavailable");
      return historicalRunSummaries;
    });
    vi.spyOn(adapter, "getResearchRun").mockImplementation(async (runId) => {
      if (runId === options.rejectDetailFor) {
        throw new Error("run detail unavailable");
      }
      const summary = historicalRunSummaries.find((item) => item.id === runId);
      if (!summary) throw new Error("run missing");
      return {
        ...baseRun,
        ...summary,
        updated_at: undefined,
        case_id: "event-tsm",
        pending_assessments: [],
        pending_proposals: [],
        review_tasks: [],
        gap_tasks: [],
        failed_tasks: [],
      };
    });
    setResearchClient(adapter);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const runId = url.match(/\/research-runs\/([^/]+)\/events$/)?.[1];
        const body = url.endsWith("/research-runs/worker-status")
          ? { status: "healthy", last_seen_at: "2026-08-11T01:00:00Z", mode: "manual", state: "idle" }
          : url.includes("/researchability")
            ? { status: "ready", reason_codes: [], effective_binding_id: "binding-1", next_action: "可开始补证" }
            : runId
              ? {
                  run_id: runId,
                  has_more: false,
                  items: [{
                    seq: 7,
                    stage: "complete",
                    status: runId === "run-reviewed" ? "succeeded" : "waiting_for_review",
                    message: runId === "run-reviewed"
                      ? "人工已完成临时 AI 评估审核；本次运行没有剩余待审项。"
                      : "等待人工审核临时 AI 评估。",
                    details: {},
                    created_at: "2026-08-11T01:00:00Z",
                  }],
                }
              : {
                  monitor: {
                    id: "monitor-v1",
                    version: 1,
                    status: "active",
                    frequency: "weekday_08_30",
                    factor_ids: ["event-tsm-factor-1"],
                    allowed_source_types: ["company_disclosure"],
                    next_verification_event: "下一次财报",
                    budget: 10,
                    changed_by: "human:researcher",
                    change_reason: "验收历史运行回放",
                    created_at: "2026-08-10T01:00:00Z",
                  },
                  latest_run: {
                    id: latestRunId,
                    status: latestRunId === "run-reviewed" ? "succeeded" : "waiting_for_review",
                    stage: latestRunId === "run-reviewed" ? "complete" : "stopped",
                    updated_at: "2026-08-11T01:00:00Z",
                  },
                  confirmed_factors: [{ id: "event-tsm-factor-1", statement: "资本开支指引" }],
                  next_scheduled_at: null,
                };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
  }

  it("replays the exact historical run named by the monitor URL", async () => {
    await setupHistoricalRunReplay();
    renderMonitorPage("/events/event-tsm/monitor?run=run-reviewed");

    expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
    expect(
      screen.getAllByText("人工已完成临时 AI 评估审核；本次运行没有剩余待审项。"),
    ).toHaveLength(2);
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed");
  });

  it("uses the historical run creation time when its detail has no update time", async () => {
    await setupHistoricalRunReplay({ latestRunId: "run-waiting" });
    renderMonitorPage("/events/event-tsm/monitor?run=run-reviewed");

    expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
    expect(screen.queryByText("更新于 undefined")).not.toBeInTheDocument();
    expect(screen.getByText(/更新于 2026-08-11T01:00:00Z/)).toBeVisible();
  });

  it("writes the latest run into an empty monitor URL and keeps it after remount", async () => {
    await setupHistoricalRunReplay();
    const first = renderMonitorPage("/events/event-tsm/monitor");

    await waitFor(() =>
      expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed"),
    );
    first.unmount();
    renderMonitorPage("/events/event-tsm/monitor?run=run-reviewed");
    expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
  });

  it("selects a history row by changing the URL and opening that run detail", async () => {
    const user = userEvent.setup();
    await setupHistoricalRunReplay();
    renderMonitorPage("/events/event-tsm/monitor?run=run-reviewed");

    await user.click(
      await screen.findByRole("button", { name: "等待人工审核 · 运行已停止 · run-waiting" }),
    );
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-waiting");
    expect(await screen.findByText("ResearchRun · run-waiting")).toBeVisible();
  });

  it("does not replace an invalid or unreadable run URL with the latest run", async () => {
    const user = userEvent.setup();
    await setupHistoricalRunReplay();
    renderMonitorPage("/events/event-tsm/monitor?run=run-missing");

    const missingAlert = await screen.findByRole("alert");
    expect(missingAlert).toHaveTextContent("无法回放此运行");
    expect(missingAlert).toHaveTextContent("run-missing");
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-missing");
    expect(screen.queryByText("ResearchRun · run-reviewed")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回最新运行" }));
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed");
    expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
  });

  it("keeps a detail-read failure on the requested run URL", async () => {
    await setupHistoricalRunReplay({ rejectDetailFor: "run-reviewed" });
    renderMonitorPage("/events/event-tsm/monitor?run=run-reviewed");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("无法回放此运行");
    expect(alert).toHaveTextContent("run-reviewed");
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed");
    expect(screen.queryByText("ResearchRun · run-waiting")).not.toBeInTheDocument();
  });

  it("keeps a URL-selected run detail visible when the history list cannot load", async () => {
    await setupHistoricalRunReplay({ rejectHistory: true });
    renderMonitorPage("/events/event-tsm/monitor?run=run-reviewed");

    expect(await screen.findByText("运行历史暂不可读取")).toBeVisible();
    expect(await screen.findByText("ResearchRun · run-reviewed")).toBeVisible();
    expect(screen.getByTestId("monitor-location")).toHaveTextContent("?run=run-reviewed");
  });

  it("reviews a provisional assessment from the run that produced it", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const baseRun = await adapter.getResearchRun("run-aic-001");
    vi.spyOn(adapter, "listResearchRuns").mockResolvedValue([
      monitorRunSummary("run-live", "waiting_for_review", "stopped"),
    ]);
    let reviewed = false;
    const reviewAssessment = vi.spyOn(adapter, "reviewAssessment").mockImplementation(async () => {
      reviewed = true;
      return {
        id: "review-1",
        outcome: "confirmed",
        reviewer: "human:researcher",
        createdAt: "2026-08-11T00:00:00Z",
      };
    });
    vi.spyOn(adapter, "getResearchRun").mockImplementation(async (runId) => {
      if (runId === "run-live") {
        return {
          ...baseRun,
          id: "run-live",
          case_id: "event-tsm",
          status: reviewed ? "succeeded" : "waiting_for_review",
          stage: reviewed ? "complete" : "stopped",
          round: 1,
          stop_reason: "max_rounds_reached",
          pending_assessments: reviewed ? [] : [{
            assessment_id: "assessment-1",
            conclusion: "insufficient_evidence",
            rationale: "缺少历史预测值",
            gaps: ["补充预测基线"],
            task_id: "task-1",
            task_status: "open",
          }],
          pending_proposals: [],
          review_tasks: [],
          gap_tasks: [],
          failed_tasks: [],
          next_action: reviewed ? "继续执行" : "人工审核临时判断",
        };
      }
      return {
        ...baseRun,
        id: "run-older",
        case_id: "event-tsm",
        status: "waiting_for_review",
        stage: "stopped",
        pending_assessments: [],
      };
    });
    setResearchClient(adapter);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = url.includes("/researchability")
        ? {
            status: "ready",
            reason_codes: [],
            effective_binding_id: "binding-1",
            next_action: "可开始补证",
          }
        : url.includes("/research-runs/") && url.endsWith("/events")
          ? { run_id: reviewed ? "run-older" : "run-live", has_more: false, items: [] }
          : {
                monitor: {
                  id: "monitor-v1",
                  version: 1,
                  status: "active",
                  frequency: "weekday_08_30",
                  factor_ids: ["event-tsm-factor-1"],
                  allowed_source_types: ["company_disclosure"],
                  next_verification_event: "下一次财报",
                  budget: 10,
                  changed_by: "human",
                  change_reason: "test",
                  created_at: "2026-08-09T00:00:00Z",
                },
                latest_run: {
                  id: reviewed ? "run-older" : "run-live",
                  status: "waiting_for_review",
                  stage: "stopped",
                  updated_at: "2026-08-09T00:01:00Z",
                },
                confirmed_factors: [
                  { id: "event-tsm-factor-1", statement: "资本开支指引" },
                ],
              };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: init?.method === "POST" ? 201 : 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor"]}>
        <Routes>
          <Route path="/events/:caseId/monitor" element={<CaseMonitorPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "打开运行详情" }),
    );
    expect(await screen.findByText("临时 AI 评估待审核")).toBeVisible();
    expect(screen.getByRole("heading", { name: "insufficient_evidence" })).toBeVisible();
    expect(screen.getByText(/补充预测基线/)).toBeVisible();
    const confirm = screen.getByRole("button", { name: "确认临时评估" });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText("临时评估审核理由"), "人工确认资料不足");
    await user.click(confirm);

    await waitFor(() =>
      expect(reviewAssessment).toHaveBeenCalledWith("assessment-1", {
        outcome: "confirmed",
        conclusion: "insufficient_evidence",
        reason: "人工确认资料不足",
        reviewer: "human:researcher",
      }),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/research-runs/run-live/events",
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(screen.getByText("ResearchRun · run-live")).toBeVisible(),
    );
    expect(screen.queryByText("临时 AI 评估待审核")).not.toBeInTheDocument();
  });

  it("keeps the newly saved monitor version and scope visible in the configuration form", async () => {
    const user = userEvent.setup();
    const initial = {
      monitor: {
        id: "monitor-v1",
        version: 1,
        status: "active",
        frequency: "weekday_08_30",
        factor_ids: ["event-tsm-factor-1"],
        allowed_source_types: ["company_disclosure"],
        next_verification_event: "下一次财报",
        budget: 10,
        changed_by: "human",
        change_reason: "初始配置",
        created_at: "2026-08-09T00:00:00Z",
      },
      latest_run: null,
      confirmed_factors: [
        { id: "event-tsm-factor-1", statement: "资本开支指引" },
      ],
    };
    const saved = {
      ...initial.monitor,
      id: "monitor-v2",
      version: 2,
      allowed_source_types: ["company_disclosure", "licensed_provider"],
      next_verification_event: "下一次财报后补证",
      change_reason: "补充授权来源",
    };
    let current = { ...initial, history: [initial.monitor] };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const isSave =
        url.endsWith("/research-cases/event-tsm/monitor") &&
        init?.method === "PUT";
      if (isSave) {
        current = { ...initial, monitor: saved, history: [saved, initial.monitor] };
      }
      const payload = isSave ? saved : current;
      return Promise.resolve(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/monitor/config"]}>
        <Routes>
          <Route
            path="/events/:caseId/monitor/config"
            element={<MonitorConfigPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("当前生效版本 v1")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(
      "变更定时任务前还需填写：填写变更原因",
    );
    await user.click(screen.getByLabelText("授权数据源"));
    await user.clear(screen.getByLabelText("下一验证事件"));
    expect(screen.getByText(/保存监控版本前还需填写/)).toHaveTextContent(
      "保存监控版本前还需填写：填写下一验证事件",
    );
    expect(
      screen.getByRole("button", { name: "保存为新监控版本" }),
    ).toBeDisabled();
    await user.type(screen.getByLabelText("下一验证事件"), "下一次财报后补证");
    await user.clear(screen.getByLabelText("新版本变更原因"));
    await user.type(screen.getByLabelText("新版本变更原因"), "补充授权来源");
    await user.click(screen.getByRole("button", { name: "保存为新监控版本" }));

    expect(await screen.findByText("当前生效版本 v2")).toBeVisible();
    expect(screen.getByText(/已保存监控版本 v2/)).toBeVisible();
    expect(await screen.findByText("v2 · 已启用")).toBeVisible();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/research-cases/event-tsm/monitor",
        expect.objectContaining({ method: "PUT" }),
      ),
    );
  });

  it("edits a rule from the current Case version and never posts to a global template edge", async () => {
    const user = userEvent.setup();
    const template = {
      id: "template-1",
      template_key: "overseas_ai_capex_to_china_hardware",
      version: 1,
      display_name: "海外 AI CapEx 到中国硬件",
      industry_scope: "ai_hardware",
      approved_by: "human",
      reason: "已审核路径",
      created_at: "2026-08-09T00:00:00Z",
      nodes: [
        {
          id: "node-a",
          node_key: "customer_capex",
          display_name: "客户 CapEx",
          role: "required_for_attribution",
        },
        {
          id: "node-b",
          node_key: "architecture",
          display_name: "目标架构",
          role: "required_for_outcome",
        },
      ],
      edges: [
        {
          id: "edge-1",
          edge_key: "capex_to_architecture",
          source_node_id: "node-a",
          target_node_id: "node-b",
        },
      ],
    };
    const rule = {
      id: "rule-1",
      research_case_id: "event-tsm",
      mechanism_edge_id: "edge-1",
      metric_definition_id: "metric-1",
      expected_direction: "increase",
      support_predicate: "原始支持条件",
      contradiction_predicate: "原始反证条件",
      allowed_source_roles: ["primary_disclosure"],
      observed_period_start: "2026-01-01",
      observed_period_end: "2026-03-31",
      available_at_deadline: "2026-05-31",
      next_verification_event: "一季报",
      supersedes_id: null,
      reviewer: "human:reviewer",
      reason: "原始登记原因",
      created_at: "2026-08-09T00:00:00Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes("/researchability")
        ? {
            status: "blocked",
            reason_codes: ["missing_verification_rule"],
            effective_binding_id: null,
            next_action: "补齐机制边规则",
          }
        : url.endsWith("/mechanism-templates")
          ? [template]
          : url.includes("/mechanism-protocol")
            ? {
                selection: {
                  id: "selection-1",
                  research_case_id: "event-tsm",
                  template_version_id: "template-1",
                  supersedes_id: null,
                  reviewer: "human",
                  reason: "适用",
                  created_at: "2026-08-09T00:00:00Z",
                },
                template,
                rules: [rule],
              }
            : url.endsWith("/metric-definitions")
              ? [
                  {
                    id: "metric-1",
                    metric_id: "capex",
                    version: 1,
                    display_name: "客户 CapEx",
                    entity_scope: "company",
                    unit: "yuan",
                    role_eligibility: ["driver"],
                    approved_by: "human",
                    reason: "指标",
                    created_at: "2026-08-09T00:00:00Z",
                  },
                ]
              : rule;
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/protocol"]}>
        <Routes>
          <Route
            path="/events/:caseId/protocol"
            element={<CaseProtocolPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/当前 Case 独有/)).toBeVisible();
    expect(screen.getByLabelText("支持条件")).toHaveValue("原始支持条件");
    await user.clear(screen.getByLabelText("支持条件"));
    expect(screen.getByRole("status")).toHaveTextContent("保存前还需填写：支持条件");
    expect(
      screen.getByRole("button", { name: "保存为新的验证规则版本" }),
    ).toBeDisabled();
    await user.type(screen.getByLabelText("支持条件"), "调整后的支持条件");
    await user.click(
      screen.getByRole("button", { name: "保存为新的验证规则版本" }),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/research-cases/event-tsm/mechanism-edges/edge-1/verification-rules",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("opens a Case-scoped frozen source snapshot with its exact locator", async () => {
    const user = userEvent.setup();
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "原文资料" }),
    ).toBeVisible();
    await user.click(
      await screen.findByRole("button", {
        name: /台积电 2026 年第二季度法说会摘要/,
      }),
    );
    expect(
      await screen.findByText("内容快照（当前 V1 未提供原件文件）"),
    ).toBeVisible();
    expect(screen.getByText("来源已准入")).toBeVisible();
    expect(screen.getByText("公司披露")).toBeVisible();
    expect(screen.queryByText("company_disclosure")).not.toBeInTheDocument();
    expect(screen.getByText("权威性尚未记录")).toBeVisible();
    expect(screen.getByText(/解析完成 · 解析器 docling-v1.2.3/)).toBeVisible();
    expect(screen.getByText("按 Case 保留 · 删除规则未记录")).toBeVisible();
    expect(
      screen.getByText("AI 允许 · 展示 允许 · 导出 禁止 · API 禁止"),
    ).toBeVisible();
    expect(screen.getByText("仅限当前 Case 研究与人工审核")).toBeVisible();
    expect(screen.getByRole("link", { name: "打开来源链接" })).toHaveAttribute(
      "href",
      "https://investor.tsmc.com/english/quarterly-results/2026/q2",
    );
    expect(screen.getByText(/资本开支指引/)).toBeVisible();
    expect(screen.getByText('{"page":12,"section":"资本开支"}')).toBeVisible();
    await user.click(
      screen.getAllByRole("button", { name: "将此段纳入待审候选" })[0],
    );
    await user.clear(screen.getByLabelText("候选表述"));
    await user.type(
      screen.getByLabelText("候选表述"),
      "管理层上调全年资本开支指引。",
    );
    await user.click(screen.getByRole("button", { name: "创建待审候选" }));
    expect(await screen.findByText("已创建待审候选，尚未写入正式结论。"))
      .toBeVisible();
    expect(screen.getByRole("link", { name: "前往审核此候选" })).toHaveAttribute(
      "href",
      "/events/event-tsm/review",
    );
  });

  it("makes a contract-restricted frozen snapshot auditable but not actionable", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const detail = await adapter.getDocumentDetail("doc-event-tsm-q2", "event-tsm");
    vi.spyOn(adapter, "getDocumentDetail").mockResolvedValue({
      ...detail,
      document: {
        ...detail.document,
        source_contract: {
          ...detail.document.source_contract!,
          status: "restricted",
          effective_until: "2026-08-09T00:00:00Z",
        },
      },
    });
    setResearchClient(adapter);
    setResearchOsApi(new MockResearchOsApi(adapter));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", {
        name: /台积电 2026 年第二季度法说会摘要/,
      }),
    );

    expect(await screen.findByText("来源当前受限")).toBeVisible();
    expect(
      screen.getByText(
        "来源合同当前受限：此快照仅用于审计回放，不能继续提取或提出候选。",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "从冻结资料提取候选" }),
    ).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: "将此段纳入待审候选" })[0],
    ).toBeDisabled();
  });

  it("does not render text or locators when the source contract forbids display", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const detail = await adapter.getDocumentDetail("doc-event-tsm-q2", "event-tsm");
    vi.spyOn(adapter, "getDocumentDetail").mockResolvedValue({
      ...detail,
      document: {
        ...detail.document,
        source_url: null,
        source_contract: {
          ...detail.document.source_contract!,
          permissions: {
            ...detail.document.source_contract!.permissions,
            display: false,
          },
          status: "restricted",
        },
      },
    });
    setResearchClient(adapter);
    setResearchOsApi(new MockResearchOsApi(adapter));
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", {
        name: /台积电 2026 年第二季度法说会摘要/,
      }),
    );

    expect(
      await screen.findByText(
        "来源合同禁止展示：系统仅保留审核元数据，不显示正文、定位或引用。",
      ),
    ).toBeVisible();
    expect(screen.getByText("来源定位").nextElementSibling).toHaveTextContent("未记录");
    expect(screen.queryByText(/资本开支指引/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "将此段纳入待审候选" }),
    ).not.toBeInTheDocument();
  });

  it("renders reviewed Case relations separately from AI candidates in the global network", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              String(input).endsWith("/event-research/network")
                ? {
                    reviewed_relations: [
                      {
                        id: "relation-1",
                        source_case: {
                          case_id: "event-tsm",
                          title: "台积电 Case",
                          lifecycle_status: "researching",
                        },
                        target_case: {
                          case_id: "event-alphabet",
                          title: "Alphabet Case",
                          lifecycle_status: "published",
                        },
                        relation_type: "shared_driver",
                        reason: "共同验证资本开支预期差",
                        created_by: "human:researcher",
                        review_state: "reviewed",
                        created_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                    candidate_relations: [
                      {
                        id: "relation-2",
                        source_case: {
                          case_id: "event-alphabet",
                          title: "Alphabet Case",
                          lifecycle_status: "published",
                        },
                        target_case: {
                          case_id: "event-tsm",
                          title: "台积电 Case",
                          lifecycle_status: "researching",
                        },
                        relation_type: "potential_conflict",
                        reason: "候选解释可能冲突",
                        created_by: "ai:relation-proposal",
                        review_state: "machine_generated",
                        created_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                  }
                : { items: [], next_cursor: null, has_more: false },
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/network"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "跨 Case 研究网络" }),
    ).toBeVisible();
    expect(screen.getByText("共同验证资本开支预期差")).toBeVisible();
    expect(screen.getAllByText("AI 候选，未经人工复核").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByRole("link", { name: "审核此关联候选" })).toHaveAttribute(
      "href",
      "/events/event-alphabet/relations",
    );
    expect(screen.getByText(/不继承证据、结论或审核状态/)).toBeVisible();
  });

  it("keeps terminal runs globally visible with their frozen scope and stop reason", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const payload = String(input).endsWith("/research-runs/run-1/events")
          ? {
              run_id: "run-1",
              items: [
                {
                  seq: 1,
                  status: "recorded",
                  stage: "scope",
                  message: "冻结本次范围",
                  details: { allowed_source_types: ["company_disclosure"] },
                  created_at: "2026-08-09T00:00:00Z",
                },
                {
                  seq: 2,
                  status: "failed",
                  stage: "failed",
                  message: "授权来源返回失败",
                  details: { stop_reason: "task_failed" },
                  created_at: "2026-08-09T00:04:00Z",
                },
              ],
              next_cursor: null,
              has_more: false,
            }
          : {
              items: [
                {
                  run_id: "run-1",
                  case_id: "event-tsm",
                  case_title: "台积电 Case",
                  status: "failed",
                  stage: "failed",
                  created_at: "2026-08-09T00:00:00Z",
                  updated_at: "2026-08-09T00:04:00Z",
                  processed_count: 3,
                  stop_reason: "task_failed",
                  next_action: "查看失败原因",
                  scope: {
                    trigger: "schedule",
                    monitor_version_id: "monitor-v2",
                    factor_ids: ["factor-1"],
                    allowed_source_types: ["company_disclosure"],
                    budget: 12,
                    frequency: "weekday_08_30",
                    next_verification_event: "2026Q1 财报披露",
                    configured_by: "human:lin",
                    configuration_change_reason: "以晨间披露核验订单指引",
                  },
                },
              ],
              next_cursor: null,
              has_more: false,
            };
        return Promise.resolve(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/monitoring"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "全局运行与监控" }),
    ).toBeVisible();
    expect(screen.getByText("台积电 Case")).toBeVisible();
    expect(screen.getByText("公司披露")).toBeVisible();
    expect(screen.getByText("任务执行失败")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看失败原因" })).toHaveAttribute(
      "href",
      "/events/event-tsm/monitor",
    );
    await user.click(screen.getByRole("button", { name: "展开本次运行记录" }));
    const drawer = await screen.findByRole("complementary", {
      name: "全局运行记录",
    });
    expect(drawer).toHaveTextContent("授权来源返回失败");
    expect(drawer).toHaveTextContent("工作日 08:30");
    expect(drawer).toHaveTextContent("2026Q1 财报披露");
    expect(drawer).toHaveTextContent("human:lin");
    expect(drawer).toHaveTextContent("以晨间披露核验订单指引");
  });

  it("makes an unreadable global run ledger explicit and lets the researcher retry it", async () => {
    const user = userEvent.setup();
    let eventLedgerAvailable = false;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const isEventLedger = String(input).endsWith(
          "/research-runs/run-global-retry/events",
        );
        if (isEventLedger && !eventLedgerAvailable) {
          return Promise.resolve(new Response("temporarily unavailable", { status: 503 }));
        }
        const payload = isEventLedger
          ? {
              run_id: "run-global-retry",
              items: [
                {
                  seq: 1,
                  status: "recorded",
                  stage: "scope",
                  message: "已冻结范围",
                  details: { allowed_source_types: ["company_disclosure"] },
                  created_at: "2026-08-09T00:00:00Z",
                },
              ],
              next_cursor: null,
              has_more: false,
            }
          : {
              items: [
                {
                  run_id: "run-global-retry",
                  case_id: "event-tsm",
                  case_title: "台积电 Case",
                  status: "failed",
                  stage: "failed",
                  created_at: "2026-08-09T00:00:00Z",
                  updated_at: "2026-08-09T00:04:00Z",
                  processed_count: 3,
                  stop_reason: "task_failed",
                  next_action: "查看失败原因",
                  scope: {
                    trigger: "schedule",
                    monitor_version_id: "monitor-v2",
                    factor_ids: ["factor-1"],
                    allowed_source_types: ["company_disclosure"],
                    budget: 12,
                  },
                },
              ],
              next_cursor: null,
              has_more: false,
            };
        return Promise.resolve(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/monitoring"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await user.click(
      await screen.findByRole("button", { name: "展开本次运行记录" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "本次运行事件暂不可读取",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "不会用当前配置替代",
    );

    eventLedgerAvailable = true;
    await user.click(
      screen.getByRole("button", { name: "重新读取本次运行事件" }),
    );
    expect(
      await screen.findByRole("complementary", { name: "全局运行记录" }),
    ).toHaveTextContent("已冻结范围");
  });

  it("explains that queued work cannot advance while the execution service is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const workerStatus = String(input).endsWith(
          "/research-runs/worker-status",
        );
        const payload = workerStatus
          ? {
              status: "unavailable",
              last_seen_at: null,
              mode: null,
              state: null,
            }
          : {
              items: [
                {
                  run_id: "run-queued",
                  case_id: "event-tsm",
                  case_title: "台积电 Case",
                  status: "queued",
                  stage: "planning",
                  created_at: "2026-08-09T00:00:00Z",
                  updated_at: "2026-08-09T00:04:00Z",
                  processed_count: 0,
                  stop_reason: null,
                  next_action: "查看本次运行",
                  scope: {
                    trigger: "manual",
                    factor_ids: ["factor-1"],
                    allowed_source_types: ["company_disclosure"],
                    budget: 12,
                  },
                },
              ],
              next_cursor: null,
              has_more: false,
            };
        return Promise.resolve(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
    render(
      <MemoryRouter initialEntries={["/monitoring"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("执行器未启动；已排队的研究不会自动推进。"),
    ).toBeVisible();
  });

  it("keeps reviewed claims, market observations and disclosed fund holdings in separate layers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              String(input).endsWith(
                "/research-cases/event-tsm/market-expression",
              )
                ? {
                    case_id: "event-tsm",
                    as_of: "2026-08-09",
                    cutoff: "2026-08-09T23:59:59Z",
                    claims: [
                      {
                        id: "claim-1",
                        text: "订单将增长",
                        claim_kind: "research_opinion",
                        asserted_period: null,
                        asserted_by: "某券商",
                        reviewed_by: "human:researcher",
                        review_reason: "已审核",
                        reviewed_at: "2026-08-09T00:00:00Z",
                        source: {
                          source_statement_id: "statement-1",
                          document_version_id: "doc-report-1",
                          document_title: "研报",
                          source_url: "https://example.test/report",
                          locator: { page: 12 },
                          available_at: "2026-08-09T00:00:00Z",
                          permission_status: "not_recorded",
                        },
                      },
                    ],
                    factors: [
                      {
                        id: "factor-1",
                        report_claim_id: "claim-1",
                        name: "订单转化",
                        expected_direction: "positive",
                        metric_name: "订单金额",
                        allowed_source_types: ["company_disclosure"],
                        verification_window_start: "2026-07-01",
                        verification_window_end: "2026-10-31",
                        support_condition: "订单增长",
                        refutation_condition: "订单下降",
                        next_verification_event: "财报",
                        reviewed_by: "human:researcher",
                        review_reason: "已审核",
                        reviewed_at: "2026-08-09T00:00:00Z",
                        verification: {
                          outcome: "supported",
                          rationale: "披露支持",
                          reviewed_by: "human:researcher",
                          reviewed_at: "2026-08-09T00:00:00Z",
                          source: {
                            source_statement_id: "statement-1",
                            document_version_id: "doc-report-1",
                            document_title: "研报",
                            source_url: "https://example.test/report",
                            locator: { page: 12 },
                            available_at: "2026-08-09T00:00:00Z",
                            permission_status: "not_recorded",
                          },
                        },
                      },
                    ],
                    fundamentals: [
                      {
                        id: "impact-1",
                        key_factor_id: "factor-1",
                        company_id: "company-1",
                        company_name: "供应链公司",
                        stock_id: "stock-1",
                        stock_code: "688000.SH",
                        stock_name: "供应链公司",
                        metric_name: "订单金额",
                        expected_direction: "positive",
                        rationale: "已审核传导",
                        reviewed_by: "human:researcher",
                        review_reason: "已审核",
                        reviewed_at: "2026-08-09T00:00:00Z",
                        source: {
                          source_statement_id: "statement-1",
                          document_version_id: "doc-report-1",
                          document_title: "研报",
                          source_url: "https://example.test/report",
                          locator: { page: 12 },
                          available_at: "2026-08-09T00:00:00Z",
                          permission_status: "not_recorded",
                        },
                      },
                    ],
                    market_observations: [
                      {
                        id: "observation-1",
                        key_factor_id: "factor-1",
                        stock_id: "stock-1",
                        stock_code: "688000.SH",
                        stock_name: "供应链公司",
                        event_at: "2026-08-01T09:30:00Z",
                        available_at: "2026-08-09T00:00:00Z",
                        window_label: "T0 至 T+5",
                        benchmark: "中证全指",
                        price_source: "licensed_provider",
                        relative_return: 0.034,
                        reviewed_by: "human:researcher",
                        review_reason: "仅观测",
                        reviewed_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                    fund_exposure: [
                      {
                        fund_id: "fund-1",
                        fund_code: "000001",
                        fund_name: "示例成长基金",
                        disclosed_exposure: 0.056,
                        positions: [
                          {
                            stock_id: "stock-1",
                            stock_code: "688000.SH",
                            stock_name: "供应链公司",
                            weight: 0.056,
                            report_period: "2026-06-30",
                            published_at: "2026-07-20T00:00:00Z",
                            acquired_at: "2026-08-09T00:00:00Z",
                            source: "licensed_provider",
                            coverage_status: "not_recorded",
                            freshness_status: "unknown",
                          },
                        ],
                      },
                    ],
                  }
                : { items: [], next_cursor: null, has_more: false },
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/market"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", {
        name: "研报主张、后续验证与基金披露分层呈现",
      }),
    ).toBeVisible();
    expect(screen.getByText("研究意见")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "定位到冻结原文" }),
    ).toHaveAttribute(
      "href",
      "/events/event-tsm/documents?document=doc-report-1",
    );
    expect(screen.getByText(/定位 \{"page":12\} · 可得/)).toBeVisible();
    expect(screen.getByText("许可：未记录")).toBeVisible();
    expect(screen.getByText("得到支持")).toBeVisible();
    expect(screen.getByText("验证指标").nextElementSibling).toHaveTextContent(
      "订单金额",
    );
    expect(screen.getByText("验证窗口").nextElementSibling).toHaveTextContent(
      "2026-07-01 至 2026-10-31",
    );
    expect(screen.getByText("因素审核").nextElementSibling).toHaveTextContent(
      "human:researcher",
    );
    expect(screen.getByText(/价格源/)).toHaveTextContent("授权供应商资料");
    expect(screen.getByText(/来源 授权供应商资料/)).toBeVisible();
    expect(screen.getByText(/事件窗口观测/)).toBeVisible();
    expect(
      screen.getByText("这是市场观测，不自动表述为研报或因素造成。"),
    ).toBeVisible();
    expect(screen.getByText(/报告期 2026-06-30/)).toBeVisible();
  });

  it("shows only this Case's reviewed relations separately from AI candidates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              String(input).endsWith("/event-research/event-tsm/relations")
                ? {
                    reviewed_relations: [
                      {
                        id: "relation-1",
                        source_case: {
                          case_id: "event-tsm",
                          title: "台积电 Case",
                          lifecycle_status: "researching",
                        },
                        target_case: {
                          case_id: "event-alphabet",
                          title: "Alphabet Case",
                          lifecycle_status: "published",
                        },
                        relation_type: "shared_driver",
                        reason: "共同验证资本开支",
                        created_by: "human:researcher",
                        review_state: "reviewed",
                        created_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                    candidate_relations: [
                      {
                        id: "relation-2",
                        source_case: {
                          case_id: "event-tsm",
                          title: "台积电 Case",
                          lifecycle_status: "researching",
                        },
                        target_case: {
                          case_id: "event-other",
                          title: "候选 Case",
                          lifecycle_status: "researching",
                        },
                        relation_type: "potential_conflict",
                        reason: "等待人工核对",
                        created_by: "ai:relation-proposal",
                        review_state: "machine_generated",
                        created_at: "2026-08-09T00:00:00Z",
                      },
                    ],
                  }
                : { items: [], next_cursor: null, has_more: false },
            ),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/relations"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", {
        name: "只显示与这个 Case 直接相连的研究",
      }),
    ).toBeVisible();
    expect(screen.getByText("共同验证资本开支")).toBeVisible();
    expect(screen.getByText("等待人工核对")).toBeVisible();
    expect(screen.getByText("不得自动进入本 Case")).toBeVisible();
  });

  it("offers a separate human review action for a Case relation candidate", async () => {
    setResearchOsApi(new MockResearchOsApi(new MockResearchAdapter()));
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/relations"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "审核关联候选" }));
    expect(screen.getByRole("heading", { name: "审核关联候选" })).toBeVisible();
    await user.selectOptions(screen.getByLabelText("审核结果"), "modified");
    await user.selectOptions(screen.getByLabelText("关系类型"), "follow_up_validation");
    await user.type(screen.getByLabelText("审核理由"), "需作为后续验证单独跟踪。");
    await user.click(screen.getByRole("button", { name: "保存可回放的审核记录" }));

    expect(await screen.findByText(/已记录：修改后确认/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "审核关联候选" })).not.toBeInTheDocument();
    expect(screen.getByText("需作为后续验证单独跟踪。")).toBeVisible();
    expect(screen.getByText(/源候选：可能冲突 · 候选：需求节奏可能不同/)).toBeVisible();
  });
});
