import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  EventWorkflow,
  EventWorkflowClient,
  WorkflowLedgerPage,
} from "../domain/eventWorkflow";
import { EventWorkflowOverview } from "../features/case/EventWorkflowOverview";

const NOW = "2026-08-16T08:00:00Z";

function workflow(
  state: EventWorkflow["state"] = "acquiring",
  overrides: Partial<EventWorkflow> = {},
): EventWorkflow {
  const userAction = state === "needs_scope_decision"
    ? {
        kind: "scope_decision",
        label: "决定是否调整研究边界",
        reason: "关键目标仍缺一手披露",
        recommendation: { kind: "revise_scope", label: "调整研究边界" },
        alternatives: [
          { kind: "keep_scope", label: "保持当前研究范围", impact: "未解决目标保留为未知" },
          { kind: "stop", label: "停止本次研究", impact: "停止后续自动补证" },
        ],
        impact: "创建新的冻结范围版本后，系统才能按新边界继续补证。",
        payload: { attempted_rounds: 2, unresolved_goals: ["goal-support"] },
      }
    : null;
  return {
    orchestrationId: "orch-1",
    researchRunId: "run-1",
    researchExecution: {
      jobId: "research-job-1",
      status: "running",
      step: "synthesizing",
      attempt: 1,
      failureCount: 0,
      startedAt: "2026-08-16T07:56:00Z",
      finishedAt: null,
      recoveryCount: 0,
      lastRecoveredAt: null,
    },
    event: {
      caseId: "case-a",
      title: "台积电上调 CoWoS 指引",
      researchQuestion: "新增产能是否会转化为供应链订单？",
    },
    scope: {
      id: "scope-1",
      version: 1,
      factors: [
        { statement: "CoWoS 月产能", description: null, position: 1 },
        { statement: "供应链订单", description: null, position: 2 },
      ],
    },
    state,
    stages: [
      { code: "event_intake", displayName: "建立事件", status: "completed", reason: "资料已冻结" },
      { code: "scope_confirmation", displayName: "确认命题", status: "completed", reason: "范围 v1 已确认" },
      { code: "source_acquisition", displayName: "主动补证", status: state === "acquiring" || state === "needs_scope_decision" ? "active" : "completed", reason: "按证据目标补证" },
      { code: "evidence_synthesis", displayName: "证据归并", status: state === "monitoring" ? "completed" : "pending", reason: "等待资料覆盖" },
      { code: "thesis_adjudication", displayName: "命题判定", status: state === "monitoring" ? "completed" : "pending", reason: "等待证据归并" },
      { code: "report_monitoring", displayName: "报告与监测", status: state === "monitoring" ? "active" : "pending", reason: state === "monitoring" ? "持续监测已启用" : "等待命题判定" },
    ],
    systemAction: {
      label: state === "monitoring" ? "正在等待下一验证事件" : "正在获取交易所披露",
      reason: state === "monitoring" ? "报告已生成，监测规则已启用" : "支持目标仍缺一手来源",
      startedAt: "2026-08-16T07:56:00Z",
      heartbeatAt: "2026-08-16T07:59:50Z",
      leaseExpiresAt: "2026-08-16T08:04:50Z",
      retryAt: null,
      recoveryStatus: "healthy",
    },
    userAction,
    userActionSummary: userAction ? "需要你决定研究边界" : "当前无需操作",
    events: [
      {
        id: "event-2",
        sequence: 2,
        transition: "acquisition_goals_dispatched",
        actor: "worker:research-orchestration",
        message: "资料获取目标与查询计划已冻结并派发",
        payload: { query: "TSMC CoWoS capacity 2026", result_count: 4 },
        createdAt: "2026-08-16T07:58:00Z",
      },
      {
        id: "event-1",
        sequence: 1,
        transition: "scope_confirmed",
        actor: "tenant:research",
        message: "研究范围已确认",
        payload: {},
        createdAt: "2026-08-16T07:56:00Z",
      },
    ],
    acquisition: {
      seriesCount: 1,
      rounds: [{
        seriesId: "series-1",
        queryPlanId: "query-plan-1",
        goalId: "goal-support",
        round: 1,
        jobId: "job-1",
        status: "running",
        stage: "fetching",
        attempt: 1,
        retryAt: null,
        leaseExpiresAt: "2026-08-16T08:04:50Z",
        finishedAt: null,
        errorCode: null,
        errorDetail: null,
        recoveryStatus: "healthy",
        plannerVersion: "planner-v1",
        policyVersion: "policy-v1",
        frozenInputs: { entity: "TSMC", metric: "CoWoS capacity" },
        orderedQueryCount: 3,
        expansionTrigger: null,
      }],
    },
    sourceLedger: {
      counts: {
        total: 2,
        reviewed: 0,
        automaticallyAdmitted: 1,
        deduplicated: 1,
        quarantined: 0,
        byStatus: { admitted: 1, deduplicated: 1 },
      },
      items: [ledgerItem("ledger-1", "admitted")],
      total: 2,
      hasMore: true,
    },
    coverage: [{
      goalId: "goal-support",
      thesisId: "thesis-1",
      objective: "找到支持 CoWoS 扩产的权威披露",
      status: "continue",
      reasonCodes: ["authority_missing"],
      requiredAuthorityCount: 1,
      observedAuthorityCount: 0,
      requiredIndependentSourceCount: 2,
      observedIndependentSourceCount: 1,
      contrarySearchCompleted: true,
      evidenceLinkIds: ["evidence-1"],
      unresolved: ["缺一手产能口径"],
      unknown: [],
      evaluationRound: 1,
    }],
    conclusion: state === "monitoring" ? {
      id: "conclusion-1",
      state: "ai_draft",
      text: "当前证据支持扩产，但订单传导仍需继续验证。",
      primaryFactor: "CoWoS 月产能",
      evidenceLinkIds: ["evidence-1"],
      citations: [ledgerItem("ledger-1", "admitted")],
      reviewer: null,
      systemGenerated: true,
      humanReviewed: false,
      reviewLabel: "系统生成，未经人工审核",
      createdAt: "2026-08-16T07:59:00Z",
    } : null,
    monitor: state === "monitoring" ? {
      id: "monitor-1",
      version: 1,
      status: "active",
      frequency: "daily",
      factorIds: ["thesis-1"],
      sourceTypes: ["exchange"],
      nextVerificationEvent: "下一次季度法说会",
      createdAt: "2026-08-16T08:00:00Z",
    } : null,
    recovery: {
      status: "healthy",
      reason: null,
      source: "worker",
      attempt: 1,
      evaluatedAt: NOW,
      orchestrationHeartbeatAt: "2026-08-16T07:59:50Z",
      workerHeartbeatAt: "2026-08-16T07:59:50Z",
      leaseExpiresAt: "2026-08-16T08:04:50Z",
      retryAt: null,
      failedAt: null,
      lastCheckpoint: { stage: "fetching" },
      decisionDiagnostic: null,
    },
    version: 4,
    updatedAt: NOW,
    ...overrides,
  };
}

function ledgerItem(
  recordId: string,
  status: EventWorkflow["sourceLedger"]["items"][number]["status"],
): EventWorkflow["sourceLedger"]["items"][number] {
  return {
    recordId,
    recordType: "retrieval_artifact",
    status,
    reason: status === "deduplicated" ? "内容哈希与已冻结版本一致" : "来源与时间范围符合当前策略",
    reasonCode: status === "deduplicated" ? "same_content_hash" : "policy_admitted",
    recordedAt: "2026-08-16T07:58:30Z",
    evidenceLinkId: status === "admitted" ? "evidence-1" : null,
    reviewState: status === "admitted" ? "automatically_admitted" : null,
    role: "supports",
    sourceRole: "primary_disclosure",
    mappingDisposition: status === "admitted" ? "mapped" : "merged",
    mappingKind: "goal_evidence",
    adapterKey: "sse",
    attemptId: "attempt-1",
    sourceUrl: "https://example.test/disclosure",
    finalUrl: "https://exchange.example.test/final-disclosure",
    retrievedAt: "2026-08-16T07:58:20Z",
    contentSha256: "8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b8b",
    publicationKey: "tsmc:2026q2:cowos",
    dedupRelation: status === "deduplicated" ? "same_content" : null,
    admissionOutcome: status === "admitted" ? "automatically_admitted" : null,
    drilldown: { kind: "acquisition_evidence", href: "/api/v1/acquisition/jobs/job-1/evidence" },
    drilldownUnavailableReason: null,
  };
}

function client(initial: EventWorkflow): EventWorkflowClient & {
  getEventWorkflow: ReturnType<typeof vi.fn>;
  getEventWorkflowLedger: ReturnType<typeof vi.fn>;
  decideEventScope: ReturnType<typeof vi.fn>;
  resumeProtocolWorkflow: ReturnType<typeof vi.fn>;
} {
  return {
    getEventWorkflow: vi.fn().mockResolvedValue(initial),
    getEventWorkflowLedger: vi.fn().mockResolvedValue({
      items: [ledgerItem("ledger-1", "admitted"), ledgerItem("ledger-2", "deduplicated")],
      total: 2,
      hasMore: false,
      nextCursor: null,
    } satisfies WorkflowLedgerPage),
    confirmEventWorkflow: vi.fn(),
    decideEventScope: vi.fn().mockResolvedValue({ ...initial, state: "planning_acquisition", userAction: null, userActionSummary: "当前无需操作" }),
    resumeProtocolWorkflow: vi.fn(),
  };
}

function renderOverview(caseId: string, api: EventWorkflowClient) {
  return render(
    <MemoryRouter>
      <EventWorkflowOverview caseId={caseId} client={api} />
    </MemoryRouter>,
  );
}

describe("EventWorkflowOverview", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(NOW));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the acquiring process and tells the user no action is required", async () => {
    const api = client(workflow("acquiring"));
    renderOverview("case-a", api);

    expect(await screen.findByRole("heading", { name: "正在获取交易所披露" })).toBeInTheDocument();
    expect(screen.getByText("当前无需操作")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "事件研究六阶段" })).toHaveTextContent("主动补证");
    expect(screen.queryAllByRole("button", { name: /继续研究|提交决定/ })).toHaveLength(0);

    await act(async () => { vi.advanceTimersByTime(5_000); });
    await waitFor(() => expect(api.getEventWorkflow).toHaveBeenCalledTimes(2));
  });

  it("refreshes service health while an active workflow remains open", async () => {
    const current = workflow("acquiring");
    const api = client(current);
    const runtimeBase = {
      durableCheckpoint: {
        workflowState: "acquiring",
        userStage: "acquisition",
        version: 4,
        systemAction: "正在获取交易所披露",
        savedAt: NOW,
        lastTransition: "acquisition_started",
        lastTransitionAt: NOW,
      },
      recovery: {
        automatic: true,
        status: "healthy",
        message: "服务恢复后会从耐久检查点继续，不会从头重做。",
      },
      message: "不会用运行健康推断 Case 阶段。",
    } as const;
    api.getCaseRuntimeStatus = vi.fn()
      .mockResolvedValueOnce({
        ...runtimeBase,
        runtimeStatus: "healthy",
        requiredServices: [
          { name: "scheduler", status: "healthy", state: "polling", lastSeenAt: NOW },
          { name: "acquisition-worker", status: "healthy", state: "polling", lastSeenAt: NOW },
        ],
      })
      .mockResolvedValueOnce({
        ...runtimeBase,
        runtimeStatus: "unavailable",
        requiredServices: [
          { name: "scheduler", status: "unavailable", state: null, lastSeenAt: null },
          { name: "acquisition-worker", status: "healthy", state: "polling", lastSeenAt: NOW },
        ],
      });

    renderOverview("case-a", api);
    await screen.findByText(/流程调度服务、资料获取服务运行正常/);

    await act(async () => { vi.advanceTimersByTime(5_000); });

    await screen.findByText("流程调度服务暂不可用");
    expect(api.getCaseRuntimeStatus).toHaveBeenCalledTimes(2);
  });

  it("navigates a revise-scope recommendation to the real scope editor without submitting", async () => {
    const blocked = workflow("needs_scope_decision");
    const api = client(blocked);
    renderOverview("case-a", api);

    expect(await screen.findByText("关键目标仍缺一手披露")).toBeInTheDocument();
    const actions = screen.getAllByRole("link", { name: "调整研究范围" });
    expect(actions).toHaveLength(1);
    expect(actions[0]).toHaveAttribute("href", "/events/case-a/scope");
    expect(api.decideEventScope).not.toHaveBeenCalled();
  });

  it("routes a protocol-blocked workflow to the real protocol editor as the only primary action", async () => {
    const blocked = workflow("needs_scope_decision");
    blocked.userAction = {
      kind: "protocol_completion",
      label: "完成研究协议",
      reason: "新加入的研究因素尚未固定验证协议",
      recommendation: { kind: "complete_research_protocol", label: "完成研究协议" },
      alternatives: [],
      impact: "协议通过后系统才能按新范围继续补证。",
      payload: { scope_version_id: "scope-2", scope_version: 2 },
    };
    renderOverview("case-a", client(blocked));

    const action = await screen.findByRole("link", { name: "完成研究协议" });
    expect(action).toHaveAttribute("href", "/events/case-a/protocol?focus=required");
    expect(document.querySelectorAll(".event-workflow-primary-action")).toHaveLength(1);
    expect(screen.queryByText(/无法映射为受支持/)).not.toBeInTheDocument();
  });

  it("keeps real keep-scope and stop alternatives actionable without adding another primary action", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const blocked = workflow("needs_scope_decision");
    const api = client(blocked);
    renderOverview("case-a", api);

    await user.click(await screen.findByText("查看其他选择及影响"));
    expect(document.querySelectorAll(".event-workflow-primary-action")).toHaveLength(1);
    expect(screen.getByText("未解决目标保留为未知")).toBeInTheDocument();
    expect(screen.getByText("停止后续自动补证")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保持当前研究范围" }));
    await waitFor(() => expect(api.decideEventScope).toHaveBeenCalledWith(
      "case-a",
      expect.objectContaining({
        kind: "keep_scope",
        expectedVersion: 4,
        idempotencyKey: "workflow:scope-decision:orch-1:v4:keep_scope",
      }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
  });

  it("submits keep-scope with the projected version and continues automatic work", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const blocked = workflow("needs_scope_decision");
    blocked.userAction = {
      ...blocked.userAction!,
      label: "保持当前研究范围",
      recommendation: { kind: "keep_scope", label: "保持当前研究范围" },
    };
    const api = client(blocked);
    api.getEventWorkflow
      .mockReset()
      .mockResolvedValueOnce(blocked)
      .mockResolvedValue(workflow("acquiring"));
    renderOverview("case-a", api);

    await user.click(await screen.findByRole("button", { name: "保持当前研究范围" }));

    await waitFor(() => expect(api.decideEventScope).toHaveBeenCalledWith(
      "case-a",
      expect.objectContaining({
        kind: "keep_scope",
        expectedVersion: 4,
        idempotencyKey: "workflow:scope-decision:orch-1:v4:keep_scope",
      }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(screen.getByText("当前无需操作")).toBeInTheDocument();

    await act(async () => { vi.advanceTimersByTime(5_000); });
    await waitFor(() => expect(api.getEventWorkflow).toHaveBeenCalledTimes(3));
  });

  it("stops workflow polling but keeps runtime health current during monitoring", async () => {
    const api = client(workflow("monitoring"));
    api.getCaseRuntimeStatus = vi.fn().mockResolvedValue({
      runtimeStatus: "healthy",
      requiredServices: [
        { name: "scheduler", status: "healthy", state: "polling", lastSeenAt: NOW },
      ],
      durableCheckpoint: {
        workflowState: "monitoring",
        userStage: "report_monitoring",
        version: 4,
        systemAction: "正在等待下一验证事件",
        savedAt: NOW,
        lastTransition: "monitoring_started",
        lastTransitionAt: NOW,
      },
      recovery: {
        automatic: true,
        status: "healthy",
        message: "服务恢复后会从耐久检查点继续，不会从头重做。",
      },
      message: "不会用运行健康推断 Case 阶段。",
    });
    renderOverview("case-a", api);

    expect(await screen.findByText("系统生成，未经人工审核")).toBeInTheDocument();
    for (let poll = 0; poll < 3; poll += 1) {
      await act(async () => { vi.advanceTimersByTime(5_000); });
      await waitFor(() => expect(api.getCaseRuntimeStatus).toHaveBeenCalledTimes(poll + 2));
    }
    expect(api.getEventWorkflow).toHaveBeenCalledTimes(1);
    expect(api.getCaseRuntimeStatus).toHaveBeenCalledTimes(4);
  });

  it("marks a stale heartbeat as stale without inventing a fallback state", async () => {
    const stale = workflow("acquiring", {
      systemAction: {
        ...workflow().systemAction,
        heartbeatAt: "2026-08-16T07:40:00Z",
        recoveryStatus: "stale",
      },
      recovery: {
        ...workflow().recovery,
        status: "stale",
        reason: "worker heartbeat exceeded freshness window",
        workerHeartbeatAt: "2026-08-16T07:40:00Z",
      },
    });
    renderOverview("case-a", client(stale));

    expect(await screen.findByText(/^心跳已过期，上次记录/)).toBeInTheDocument();
    expect(screen.getByText(/恢复状态尚未由服务端确认/)).toBeInTheDocument();
  });

  it("does not infer a stale heartbeat when the server still reports healthy", async () => {
    const oldButHealthy = workflow("acquiring", {
      systemAction: {
        ...workflow().systemAction,
        heartbeatAt: "2026-08-16T06:00:00Z",
        recoveryStatus: "healthy",
      },
      recovery: {
        ...workflow().recovery,
        status: "healthy",
        workerHeartbeatAt: "2026-08-16T06:00:00Z",
      },
    });
    renderOverview("case-a", client(oldButHealthy));

    expect(await screen.findByText(/^心跳正常，更新于/)).toBeInTheDocument();
    expect(screen.queryByText(/心跳已过期/)).not.toBeInTheDocument();
  });

  it("reports checkpoint recovery only for a server-owned recovering state", async () => {
    const recovering = workflow("recovering", {
      systemAction: { ...workflow().systemAction, recoveryStatus: "recovering" },
      recovery: { ...workflow().recovery, status: "recovering", reason: "worker restarted from checkpoint" },
    });
    renderOverview("case-a", client(recovering));

    expect(await screen.findByText(/系统正在按检查点恢复/)).toBeInTheDocument();
    expect(screen.getByText(/worker restarted from checkpoint/)).toBeInTheDocument();
  });

  it("isolates an older request when the selected event changes", async () => {
    let resolveOld!: (value: EventWorkflow) => void;
    const oldRequest = new Promise<EventWorkflow>((resolve) => { resolveOld = resolve; });
    const api = client(workflow());
    api.getEventWorkflow
      .mockReset()
      .mockImplementationOnce(() => oldRequest)
      .mockResolvedValueOnce(workflow("monitoring", {
        event: { caseId: "case-b", title: "英伟达更新 Blackwell 交付节奏", researchQuestion: "交付变化是否影响供应链收入确认？" },
      }));

    const view = renderOverview("case-a", api);
    view.rerender(<MemoryRouter><EventWorkflowOverview caseId="case-b" client={api} /></MemoryRouter>);
    expect(await screen.findByText("英伟达更新 Blackwell 交付节奏")).toBeInTheDocument();

    await act(async () => { resolveOld(workflow("acquiring")); });
    expect(screen.queryByText("台积电上调 CoWoS 指引")).not.toBeInTheDocument();
    expect(screen.getByText("英伟达更新 Blackwell 交付节奏")).toBeInTheDocument();
  });

  it("does not let an old Case decision keep the new Case busy", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let resolveOldDecision!: () => void;
    const oldDecision = new Promise<never>((resolve) => {
      resolveOldDecision = resolve as () => void;
    });
    const blockedA = workflow("needs_scope_decision");
    blockedA.userAction = {
      ...blockedA.userAction!,
      label: "保持当前研究范围",
      recommendation: { kind: "keep_scope", label: "保持当前研究范围" },
    };
    const blockedB = workflow("needs_scope_decision", {
      event: { caseId: "case-b", title: "事件 B", researchQuestion: "事件 B 的问题" },
      orchestrationId: "orch-b",
    });
    blockedB.userAction = {
      ...blockedB.userAction!,
      label: "停止本次研究",
      recommendation: { kind: "stop", label: "停止本次研究" },
    };
    const api = client(blockedA);
    api.getEventWorkflow.mockReset().mockResolvedValueOnce(blockedA).mockResolvedValueOnce(blockedB);
    api.decideEventScope.mockImplementationOnce(() => oldDecision);

    const view = renderOverview("case-a", api);
    await user.click(await screen.findByRole("button", { name: "保持当前研究范围" }));
    view.rerender(<MemoryRouter><EventWorkflowOverview caseId="case-b" client={api} /></MemoryRouter>);

    const newAction = await screen.findByRole("button", { name: "停止本次研究" });
    expect(newAction).toBeEnabled();
    expect(api.decideEventScope).toHaveBeenCalledWith(
      "case-a",
      expect.any(Object),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await act(async () => { resolveOldDecision(); await Promise.resolve(); });
    expect(newAction).toBeEnabled();
  });

  it("places the next user action before the system process in semantic order", async () => {
    renderOverview("case-a", client(workflow("needs_scope_decision")));
    const userRegion = await screen.findByRole("region", { name: "你现在应该做什么" });
    const systemRegion = screen.getByRole("region", { name: "系统正在做什么" });
    expect(userRegion.compareDocumentPosition(systemRegion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("uses product language by default and keeps raw runtime fields in technical details", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderOverview("case-a", client(workflow()));
    const overview = await screen.findByRole("region", { name: "当前事件研究流程" });
    expect(screen.getByText("运行正常")).toBeVisible();
    expect(screen.getByText("继续补证")).toBeVisible();
    const technicalDetails = screen.getAllByText("技术详情");
    expect(technicalDetails.length).toBeGreaterThan(0);
    expect(screen.queryByText(/acquisition_goals_dispatched/)).not.toBeInTheDocument();
    expect(screen.queryByText(/worker:research-orchestration/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"recovery_status": "healthy"/)).not.toBeInTheDocument();
    expect(overview.querySelector(".event-workflow-system__meta")?.textContent).not.toContain("healthy");

    const process = screen.getByRole("region", { name: "系统做过什么" });
    await user.click(within(process).getAllByText("技术详情")[0]);
    expect(screen.getByText(/acquisition_goals_dispatched/)).toBeVisible();
    expect(screen.getByText(/worker:research-orchestration/)).toBeVisible();
  });

  it("expands the stable source ledger and preserves provenance fields", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = client(workflow());
    renderOverview("case-a", api);
    await screen.findByText("来源与时间范围符合当前策略");

    await user.click(screen.getByRole("button", { name: "展开完整资料台账" }));
    expect(await screen.findByText("内容哈希与已冻结版本一致")).toBeInTheDocument();
    const ledger = screen.getByRole("region", { name: "来源资料台账" });
    expect(within(ledger).getAllByText("公司或监管机构原始披露").length).toBeGreaterThan(0);
    expect(within(ledger).getAllByText("支持当前命题").length).toBeGreaterThan(0);
    expect(within(ledger).getAllByText("系统规则准入").length).toBeGreaterThan(0);
    expect(within(ledger).queryByText("primary_disclosure")).not.toBeInTheDocument();
    expect(within(ledger).queryByText("https://exchange.example.test/final-disclosure")).not.toBeInTheDocument();
    expect(within(ledger).queryByText(/8b8b8b8b/)).not.toBeInTheDocument();
    expect(within(ledger).queryByText("automatically_admitted")).not.toBeInTheDocument();

    const admittedRow = within(ledger).getByText("来源与时间范围符合当前策略").closest("article");
    expect(admittedRow).not.toBeNull();
    await user.click(within(admittedRow as HTMLElement).getByText("查看获取与溯源详情"));
    expect(within(admittedRow as HTMLElement).getByText(/primary_disclosure/)).toBeVisible();
    expect(within(admittedRow as HTMLElement).getByText(/https:\/\/exchange\.example\.test\/final-disclosure/)).toBeVisible();
    expect(within(admittedRow as HTMLElement).getByText(/8b8b8b8b/)).toBeVisible();
    expect(within(admittedRow as HTMLElement).getByText(/automatically_admitted/)).toBeVisible();
    expect(within(admittedRow as HTMLElement).getByText(/"mapping_disposition": "mapped"/)).toBeVisible();
    expect(api.getEventWorkflowLedger).toHaveBeenCalledWith(
      "case-a",
      expect.objectContaining({ limit: 50, signal: expect.any(AbortSignal) }),
    );
  });

  it("keeps stages and ledger readable at a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 360 });
    fireEvent(window, new Event("resize"));
    renderOverview("case-a", client(workflow()));

    const stages = await screen.findByRole("list", { name: "事件研究六阶段" });
    expect(stages.children).toHaveLength(6);
    expect(screen.getByRole("region", { name: "来源资料台账" })).toHaveAttribute("data-responsive", "stack");
    expect(document.querySelector("table")).not.toBeInTheDocument();
  });

  it("shows a typed workflow error and never renders legacy status as a fallback", async () => {
    const api = client(workflow());
    api.getEventWorkflow.mockRejectedValueOnce(Object.assign(new Error("统一流程尚未初始化"), { code: "workflow_not_initialized" }));
    renderOverview("case-a", api);

    expect(await screen.findByRole("alert")).toHaveTextContent("统一流程尚未初始化");
    expect(screen.queryByText("资料识别中")).not.toBeInTheDocument();
    expect(screen.queryByText("运行状态暂不可确认")).not.toBeInTheDocument();
  });

  it("treats a missing heartbeat as unknown and only reports recovery when the server does", async () => {
    const noHeartbeat = workflow("acquiring", {
      systemAction: { ...workflow().systemAction, heartbeatAt: null, recoveryStatus: null },
      recovery: {
        ...workflow().recovery,
        status: null,
        workerHeartbeatAt: null,
        orchestrationHeartbeatAt: null,
        reason: null,
      },
    });
    renderOverview("case-a", client(noHeartbeat));

    expect(await screen.findByText("心跳待确认，服务端尚未记录运行心跳")).toBeInTheDocument();
    expect(screen.queryByText(/按检查点恢复/)).not.toBeInTheDocument();
  });

  it("does not let an ignored ledger abort overwrite the next event or keep it busy", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let resolveOldLedger!: (value: WorkflowLedgerPage) => void;
    const oldLedger = new Promise<WorkflowLedgerPage>((resolve) => { resolveOldLedger = resolve; });
    const api = client(workflow());
    api.getEventWorkflow
      .mockReset()
      .mockResolvedValueOnce(workflow())
      .mockResolvedValueOnce(workflow("acquiring", {
        event: { caseId: "case-b", title: "事件 B", researchQuestion: "事件 B 的研究问题" },
      }));
    api.getEventWorkflowLedger
      .mockReset()
      .mockImplementationOnce(() => oldLedger)
      .mockResolvedValueOnce({ items: [ledgerItem("ledger-b", "deduplicated")], total: 1, hasMore: false, nextCursor: null });

    const view = renderOverview("case-a", api);
    await screen.findByText("台积电上调 CoWoS 指引");
    await user.click(screen.getByRole("button", { name: "展开完整资料台账" }));
    view.rerender(<MemoryRouter><EventWorkflowOverview caseId="case-b" client={api} /></MemoryRouter>);
    expect(await screen.findByText("事件 B")).toBeInTheDocument();

    await act(async () => resolveOldLedger({
      items: [{ ...ledgerItem("ledger-old", "admitted"), reason: "旧 Case 台账污染" }],
      total: 1,
      hasMore: false,
      nextCursor: null,
    }));
    expect(screen.queryByText("旧 Case 台账污染")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "展开完整资料台账" }));
    expect(await screen.findByText("内容哈希与已冻结版本一致")).toBeInTheDocument();
  });

  it("keeps one polling chain when a decision follows a polling response", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = client(workflow("acquiring"));
    api.getEventWorkflow
      .mockReset()
      .mockResolvedValueOnce(workflow("acquiring"))
      .mockResolvedValueOnce(workflow("needs_scope_decision", {
        userAction: {
          ...workflow("needs_scope_decision").userAction!,
          label: "保持当前研究范围",
          recommendation: { kind: "keep_scope", label: "保持当前研究范围" },
        },
      }))
      .mockResolvedValue(workflow("acquiring"));
    renderOverview("case-a", api);
    await screen.findByText("正在获取交易所披露");
    await act(async () => { vi.advanceTimersByTime(5_000); });
    const action = await screen.findByRole("button", { name: "保持当前研究范围" });
    await user.click(action);
    await act(async () => { vi.advanceTimersByTime(5_000); });
    await waitFor(() => expect(api.getEventWorkflow).toHaveBeenCalledTimes(4));
    await act(async () => { vi.advanceTimersByTime(50); });
    expect(api.getEventWorkflow).toHaveBeenCalledTimes(4);
  });

  it("re-reads the complete projection once after a terminal decision and does not poll again", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const blocked = workflow("needs_scope_decision");
    blocked.userAction = {
      ...blocked.userAction!,
      label: "停止本次研究",
      recommendation: { kind: "stop", label: "停止本次研究" },
    };
    const api = client(blocked);
    const terminal = workflow("cancelled", {
      stages: blocked.stages.map((stage) => ({
        ...stage,
        status: stage.code === "source_acquisition" ? "cancelled" : stage.status,
        reason: stage.code === "source_acquisition" ? "用户已停止本次研究" : stage.reason,
      })),
      events: [{
        id: "event-stop",
        sequence: 9,
        transition: "scope_stopped",
        actor: "user:researcher",
        message: "本次研究已停止",
        payload: {},
        createdAt: NOW,
      }],
      userAction: null,
      userActionSummary: "当前无需操作",
    });
    api.getEventWorkflow.mockReset().mockResolvedValueOnce(blocked).mockResolvedValueOnce(terminal);
    api.decideEventScope.mockResolvedValue({
      orchestrationId: "orch-1",
      caseId: "case-a",
      scopeVersionId: "scope-1",
      researchRunId: "run-1",
      state: "cancelled",
      userStage: "acquisition",
      currentSystemAction: "已停止本次研究",
      systemActionReason: "用户明确停止",
      nextAction: null,
      version: 5,
    });
    renderOverview("case-a", api);

    await user.click(await screen.findByRole("button", { name: "停止本次研究" }));
    expect(await screen.findByText("本次研究已停止")).toBeInTheDocument();
    expect(screen.getByText(/用户已停止本次研究/)).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(15_000); });

    expect(api.getEventWorkflow).toHaveBeenCalledTimes(2);
  });

  it("clears the old projection when a submitted decision cannot be re-read and retries only the GET", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const blocked = workflow("needs_scope_decision");
    blocked.userAction = {
      ...blocked.userAction!,
      label: "停止本次研究",
      recommendation: { kind: "stop", label: "停止本次研究" },
    };
    const api = client(blocked);
    api.getEventWorkflow
      .mockReset()
      .mockResolvedValueOnce(blocked)
      .mockRejectedValueOnce(new Error("projection unavailable"))
      .mockResolvedValueOnce(workflow("cancelled", {
        userAction: null,
        userActionSummary: "当前无需操作",
        events: [],
      }));
    api.decideEventScope.mockResolvedValue({
      orchestrationId: "orch-1",
      caseId: "case-a",
      scopeVersionId: "scope-1",
      researchRunId: "run-1",
      state: "cancelled",
      userStage: "acquisition",
      currentSystemAction: "已停止本次研究",
      systemActionReason: "用户明确停止",
      nextAction: null,
      version: 5,
    });
    renderOverview("case-a", api);

    await user.click(await screen.findByRole("button", { name: "停止本次研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("决定已提交但最新流程暂不可读取");
    expect(screen.queryByText("关键目标仍缺一手披露")).not.toBeInTheDocument();
    expect(api.decideEventScope).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取最新流程" }));
    expect(await screen.findByText("当前无需操作")).toBeInTheDocument();
    expect(api.getEventWorkflow).toHaveBeenCalledTimes(3);
    expect(api.decideEventScope).toHaveBeenCalledTimes(1);
  });

  it("reveals source URLs and drilldown only after opening traceability details", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderOverview("case-a", client(workflow()));

    const ledger = await screen.findByRole("region", { name: "来源资料台账" });
    expect(within(ledger).queryByText("https://exchange.example.test/final-disclosure")).not.toBeInTheDocument();
    expect(within(ledger).queryByRole("link", { name: "查看可验证记录" })).not.toBeInTheDocument();
    await user.click(within(ledger).getByText("查看获取与溯源详情"));
    expect(within(ledger).getByText(/https:\/\/exchange\.example\.test\/final-disclosure/)).toBeVisible();
    expect(within(ledger).getByRole("link", { name: "查看可验证记录" })).toHaveAttribute(
      "href",
      "/api/v1/acquisition/jobs/job-1/evidence",
    );
  });

  it("explains empty process, coverage, and source ledger states", async () => {
    renderOverview("case-a", client(workflow("acquiring", {
      events: [],
      coverage: [],
      sourceLedger: {
        counts: { total: 0, reviewed: 0, automaticallyAdmitted: 0, deduplicated: 0, quarantined: 0, byStatus: {} },
        items: [],
        total: 0,
        hasMore: false,
      },
    })));

    expect(await screen.findByText("流程刚刚建立，尚未记录过程事件。")).toBeInTheDocument();
    expect(screen.getByText("资料获取计划尚未生成证据目标。")).toBeInTheDocument();
    expect(screen.getByText("尚未记录搜索候选或冻结资料，系统开始获取后会在这里留下可追溯记录。")).toBeInTheDocument();
  });

  it("does not guess an unsupported scope recommendation", async () => {
    const unsupported = workflow("needs_scope_decision");
    unsupported.userAction = {
      ...unsupported.userAction!,
      recommendation: { kind: "expand_scope", label: "扩大研究范围" },
    };
    renderOverview("case-a", client(unsupported));

    expect(await screen.findByText("系统建议无法映射为受支持的范围决定，请刷新或联系管理员。")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "调整研究范围" })).not.toBeInTheDocument();
  });
});
