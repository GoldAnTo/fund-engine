import { afterEach, describe, expect, it, vi } from "vitest";

import { resetResearchOsApi, researchOsApi, setResearchOsApi, type MonitorDetail, type ResearchOsApi } from "../app/researchOsApi";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { MockResearchOsApi } from "../data/mockResearchOsApi";
import { researchClient } from "../data/researchClient";

describe("research OS API selection", () => {
  afterEach(() => {
    resetResearchOsApi();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("uses the explicitly selected API instead of fetching the live ledger", async () => {
    const monitor = { monitor: null, history: [], latest_run: null, confirmed_factors: [] } satisfies MonitorDetail;
    const localApi = { monitor: vi.fn().mockResolvedValue(monitor) } as unknown as ResearchOsApi;
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    setResearchOsApi(localApi);

    await expect(researchOsApi.monitor("event-tsm")).resolves.toEqual(monitor);

    expect(localApi.monitor).toHaveBeenCalledWith("event-tsm");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("keeps live Research OS calls in the same authenticated browser session", async () => {
    vi.stubEnv("VITE_RESEARCH_BEARER_TOKEN", "team-token");
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      reviewed_relations: [], candidate_relations: [], resolved_candidates: [],
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    await researchOsApi.network();

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/event-research/network",
      expect.objectContaining({
        credentials: "include",
        headers: expect.objectContaining({ Authorization: "Bearer team-token" }),
      }),
    );
  });

  it("does not expose retired prototype screen calls on the active client", () => {
    for (const retiredMethod of [
      "getWorkspaceOverviewView",
      "getWorkspaceOverviewScreen",
      "getNewResearchView",
      "getResearchPlanView",
      "getLibraryView",
      "getDataCenterView",
      "getVersionsView",
      "getThemeIndexView",
      "getThemeWorkbenchView",
    ]) {
      expect(researchClient).not.toHaveProperty(retiredMethod);
    }
  });
});

describe("mock Research OS event run projection", () => {
  it("projects the TSM monitor latest run from the authoritative event lifecycle", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);

    expect((await api.monitor("event-tsm")).latest_run).toMatchObject({
      id: "run-demo-1",
      status: "awaiting_review",
      stage: "review",
    });

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome: "needs_more_evidence",
      reason: "需要补充资本开支与自由现金流的季度桥接数据。",
      reviewer_id: "human:researcher",
      expected_version: 1,
    });

    expect((await api.monitor("event-tsm")).latest_run).toMatchObject({
      id: "run-demo-1",
      status: "running",
      stage: "retrieve",
    });
    expect((await api.monitor("event-alphabet")).latest_run).toBeNull();

    const archivedAdapter = new MockResearchAdapter();
    const archivedApi = new MockResearchOsApi(archivedAdapter);
    await archivedAdapter.reviewProposal("proposal-event-tsm", {
      outcome: "confirmed",
      reason: "原始披露足以支持该因素。",
      reviewer_id: "human:researcher",
      expected_version: 1,
    });

    expect((await archivedApi.monitor("event-tsm")).latest_run).toMatchObject({
      id: "run-demo-1",
      status: "succeeded",
      stage: "complete",
    });
  });

  it("uses the continuing workbench scope in the frozen run event", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const customFactors = [
      {
        statement: "自定义资本开支因素",
        description: "调整后的首要补证范围",
      },
      {
        statement: "自定义现金流因素",
        description: "调整后的次要补证范围",
      },
    ];

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome: "rejected",
      reason: "该材料与市场反应缺少直接关联。",
      reviewer_id: "human:researcher",
      expected_version: 1,
    });
    await adapter.updateEventResearchScope({
      caseId: "event-tsm",
      factors: customFactors,
      changedBy: "human:researcher",
      changeReason: "驳回后调整研究范围",
    });

    const workbench = await adapter.getEventWorkbench("event-tsm");
    expect(workbench.lifecycle.status).toBe("continuing");
    const scopeEvent = (await api.runEvents("run-demo-1")).items[0];
    expect(scopeEvent.details).toMatchObject({
      monitor_version_id: `event-tsm-scope-v${workbench.scope.version}`,
      factor_ids: workbench.factors.map((factor) => factor.thesisId),
      factor_statements: customFactors.map((factor) => factor.statement),
    });

    const fallbackScope = (await new MockResearchOsApi().runEvents("run-demo-1"))
      .items[0].details;
    expect(fallbackScope).toMatchObject({
      monitor_version_id: "monitor-event-tsm-v1",
      factor_ids: ["factor-capex", "factor-margin"],
    });
  });

  it("archives the TSM run after a researcher confirms its key evidence", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);

    expect((await api.activeRuns()).items[0]).toMatchObject({
      case_id: "event-tsm",
      status: "awaiting_review",
      stage: "review",
    });

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome: "confirmed",
      reason: "原始披露足以支持该因素。",
      reviewer_id: "human:researcher",
      expected_version: 1,
    });

    expect((await api.activeRuns()).items).toEqual([]);
    expect((await api.runs()).items[0]).toMatchObject({
      case_id: "event-tsm",
      status: "succeeded",
      stage: "complete",
    });
    const events = (await api.runEvents("run-demo-1")).items;
    expect(events[events.length - 1]).toMatchObject({
      status: "completed",
      stage: "review",
      message: "关键证据已由研究员确认，结论草案待复核",
    });
  });

  it("keeps the TSM run active while the system retrieves requested evidence", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    const reason = "需要补充资本开支与自由现金流的季度桥接数据。";

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome: "needs_more_evidence",
      reason,
      reviewer_id: "human:researcher",
      expected_version: 1,
    });

    expect((await api.activeRuns()).items[0]).toMatchObject({
      case_id: "event-tsm",
      status: "running",
      stage: "retrieve",
    });
    const events = (await api.runEvents("run-demo-1")).items;
    expect(events[events.length - 1]).toMatchObject({
      status: "running",
      stage: "retrieve",
      message: "审核要求已记录，系统继续补证",
      details: { review_reason: reason },
    });
  });

  it("archives the TSM run when the researcher rejects its candidate", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome: "rejected",
      reason: "该材料与市场反应缺少直接关联。",
      reviewer_id: "human:researcher",
      expected_version: 1,
    });

    expect((await api.activeRuns()).items).toEqual([]);
    expect((await api.runs()).items[0]).toMatchObject({
      case_id: "event-tsm",
      status: "succeeded",
      stage: "complete",
    });
    const events = (await api.runEvents("run-demo-1")).items;
    expect(events[events.length - 1]).toMatchObject({
      status: "completed",
      stage: "review",
      message: "候选已处理，当前范围需要调整",
    });
  });

  it("keeps the TSM run archived after the confirmed conclusion is published", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);

    await adapter.reviewProposal("proposal-event-tsm", {
      outcome: "confirmed",
      reason: "原始披露足以支持该因素。",
      reviewer_id: "human:researcher",
      expected_version: 1,
    });
    await adapter.publishEventConclusion({
      caseId: "event-tsm",
      text: "资本开支上调构成当前市场担忧的重要可验证因素。",
      reviewer: "human:researcher",
    });

    expect((await api.activeRuns()).items).toEqual([]);
    expect((await api.runs()).items[0]).toMatchObject({
      case_id: "event-tsm",
      status: "succeeded",
      stage: "complete",
    });
    const events = (await api.runEvents("run-demo-1")).items;
    expect(events[events.length - 1]).toMatchObject({
      status: "completed",
      stage: "complete",
      message: "结论已发布，进入持续跟踪",
    });
    expect(events).not.toContainEqual(
      expect.objectContaining({ status: "awaiting_review" }),
    );
  });
});
