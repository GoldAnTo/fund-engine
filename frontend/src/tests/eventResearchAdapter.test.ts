import { describe, expect, it, vi, afterEach } from "vitest";
import { HttpResearchAdapter } from "../data/httpResearchAdapter";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { EventWorkflowError } from "../domain/eventWorkflow";

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function errorResponse(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as unknown as Response;
}

describe("event research adapters", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("maps an extracted event without inventing optional fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          event_title: "公司宣布新指引，盘后下跌",
          company_name: null,
          ticker: null,
          event_at: null,
          market_reaction: "盘后下跌",
          summary: null,
          research_question: "新指引是否改变了市场预期？",
          candidate_factors: ["因素一", "因素二", "因素三"],
          confirmation_required: true,
        }),
      ),
    );
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await expect(
      adapter.extractEventResearch({ rawInput: "公司宣布新指引，盘后下跌。", sourceUrl: "" }),
    ).resolves.toMatchObject({
      companyName: null,
      ticker: null,
      confirmationRequired: true,
    });
  });

  it("returns separate event rows and an actionable lifecycle", async () => {
    const adapter = new MockResearchAdapter();
    const events = await adapter.listEventResearch();

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "event-alphabet", status: "researching" }),
        expect.objectContaining({ id: "event-tsm", status: "awaiting_key_review" }),
      ]),
    );
  });

  it("preserves the typed workflow initialization action instead of falling back", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => errorResponse(409, {
      schema_version: "v1",
      error: {
        code: "workflow_not_initialized",
        message: "research workflow is not initialized",
        request_id: "request-1",
        details: {
          safe_action: {
            kind: "initialize_workflow",
            label: "确认命题并开始研究",
            method: "POST",
            href: "/api/v1/event-research/case-a/workflow/confirm",
            payload: {
              scope_version_id: "scope-uuid",
              scope_version: 2,
              idempotency_key: "workflow:initialize:scope-uuid:v2",
            },
          },
        },
      },
    })));
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    const error = await adapter.getEventWorkflow("case-a").catch((reason) => reason);
    expect(error).toBeInstanceOf(EventWorkflowError);
    expect(error).toMatchObject({
      code: "workflow_not_initialized",
      safeAction: {
        kind: "initialize_workflow",
        payload: { scope_version_id: "scope-uuid", scope_version: 2 },
      },
    });
  });

  it("confirms the exact scope with the typed safe-action idempotency data", async () => {
    const fetch = vi.fn(async () => jsonResponse({
      orchestration_id: "orch-1",
      case_id: "case-a",
      scope_version_id: "scope-uuid",
      research_run_id: "run-1",
      state: "planning_acquisition",
      user_stage: "acquisition",
      current_system_action: "正在规划资料获取",
      system_action_reason: "范围已确认",
      next_action: null,
      version: 1,
    }));
    vi.stubGlobal("fetch", fetch);
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await adapter.confirmEventWorkflow("case-a", "scope-uuid", {
      scopeVersion: 2,
      idempotencyKey: "workflow:initialize:scope-uuid:v2",
    });

    expect(fetch).toHaveBeenCalledWith(
      "http://api.test/api/v1/event-research/case-a/workflow/confirm",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          scope_version_id: "scope-uuid",
          scope_version: 2,
          idempotency_key: "workflow:initialize:scope-uuid:v2",
        }),
      }),
    );
  });

  it("reads stable ledger pages and submits scope decisions without client identity", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, has_more: false, next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse({
        orchestration_id: "orch-1",
        case_id: "case-a",
        scope_version_id: "scope-uuid",
        research_run_id: "run-1",
        state: "planning_acquisition",
        user_stage: "acquisition",
        current_system_action: "正在规划下一轮资料获取",
        system_action_reason: "关键目标仍缺一手披露",
        next_action: null,
        version: 5,
      }));
    vi.stubGlobal("fetch", fetch);
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await adapter.getEventWorkflowLedger("case-a", { status: "admitted", cursor: "cursor-1", limit: 25 });
    await adapter.decideEventScope("case-a", {
      kind: "keep_scope",
      reason: "保持当前范围，未解决项保留为未知",
      expectedVersion: 4,
      idempotencyKey: "decision-1",
    });

    expect(fetch.mock.calls[0][0]).toBe("http://api.test/api/v1/event-research/case-a/workflow/ledger?status=admitted&cursor=cursor-1&limit=25");
    const decisionInit = fetch.mock.calls[1][1] as RequestInit;
    expect(fetch.mock.calls[1][0]).toBe("http://api.test/api/v1/event-research/case-a/workflow/decision");
    expect(JSON.parse(String(decisionInit.body))).toEqual({
      kind: "keep_scope",
      reason: "保持当前范围，未解决项保留为未知",
      expected_version: 4,
      idempotency_key: "decision-1",
    });
    expect(String(decisionInit.body)).not.toContain("actor");
    expect(String(decisionInit.body)).not.toContain("reviewer");
  });

  it("reads Case runtime health separately from the durable workflow", async () => {
    const fetch = vi.fn(async () => jsonResponse({
      runtime_status: "unavailable",
      required_services: [
        { name: "scheduler", status: "unavailable", state: null, last_seen_at: null },
        { name: "acquisition-worker", status: "unavailable", state: null, last_seen_at: null },
      ],
      durable_checkpoint: {
        workflow_state: "planning_acquisition",
        user_stage: "acquisition",
        version: 3,
        system_action: "正在规划资料获取",
        saved_at: "2026-08-17T08:00:00Z",
        last_transition: "scope_confirmed",
        last_transition_at: "2026-08-17T07:59:00Z",
      },
      recovery: {
        automatic: true,
        status: "healthy",
        message: "服务恢复后会从耐久检查点继续，不会从头重做。",
      },
      message: "不会用运行健康推断 Case 阶段。",
    }));
    vi.stubGlobal("fetch", fetch);
    const adapter = new HttpResearchAdapter({ baseUrl: "http://api.test/api/v1" });

    await expect(adapter.getCaseRuntimeStatus("case-a")).resolves.toMatchObject({
      runtimeStatus: "unavailable",
      requiredServices: [
        { name: "scheduler", status: "unavailable" },
        { name: "acquisition-worker", status: "unavailable" },
      ],
      durableCheckpoint: {
        workflowState: "planning_acquisition",
        lastTransition: "scope_confirmed",
      },
      recovery: { automatic: true },
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://api.test/api/v1/event-research/case-a/runtime-status",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
