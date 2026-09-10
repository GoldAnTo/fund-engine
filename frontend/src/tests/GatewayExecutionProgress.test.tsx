import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { GatewayRoutes } from "@/app/GatewayRoutes";
import { GatewaySchemaError, HttpGatewayClient } from "@/gateway/HttpGatewayClient";
import type { GatewayConversationClient, GatewayStreamInput } from "@/gateway/contracts";

const cid = "11111111-1111-4111-8111-111111111111";
const rid = "22222222-2222-4222-8222-222222222222";
const tid = "33333333-3333-4333-8333-333333333333";
const at = "2026-09-05T05:00:04Z";
const executionWire = {
  projection_state: "available", stage: "retrieve", updated_at: at,
  reason_code: null, next_action: "wait_for_execution",
  worker_state: "online", worker_last_seen_at: at, worker_scope: "acquisition_service",
  tasks: [{
    task_id: tid, task_type: "support", status: "running", stage: "extracting",
    source_kind: "intake_material", source_name: "用户提供材料", providers: [],
    attempt: 1, updated_at: at, retry_at: null,
    counts: { discovered: 0, fetched: 0, frozen: 1, admitted: 0, exceptions: 0 },
  }],
};
const runWire = {
  run_spec_id: rid, native_case_id: cid, native_run_id: rid,
  status: "waiting_for_sources", parent_run_spec_id: null, created_at: at,
};
const snapshotWire = {
  conversation_id: cid, title: "谷歌财报核验", created_at: at, updated_at: at,
  latest_sequence: 14, event_retention_floor: 1, messages: [], roles: [], events: [],
  runs: [{ ...runWire, execution: executionWire }],
};

function clientWithSnapshot(wire: unknown) {
  return new HttpGatewayClient({ fetch: vi.fn(async () => new Response(JSON.stringify(wire), {
    headers: { "Content-Type": "application/json" },
  })) });
}

describe("safe execution progress transport", () => {
  it("decodes current material task progress without treating it as external evidence", async () => {
    const snapshot = await clientWithSnapshot(snapshotWire).getSnapshot(cid);
    expect(snapshot.runs[0]?.execution).toMatchObject({
      projectionState: "available", updatedAt: at, workerState: "online",
      tasks: [{ taskId: tid, sourceKind: "intake_material", sourceName: "用户提供材料", providers: [],
        status: "running", stage: "extracting", counts: { frozen: 1, admitted: 0 } }],
    });
    expect(snapshot.runs[0]?.status).toBe("waiting_for_sources");
  });

  it("continues to accept older snapshots without execution metadata", async () => {
    const snapshot = await clientWithSnapshot({ ...snapshotWire, runs: [runWire] }).getSnapshot(cid);
    expect(snapshot.runs[0]?.execution).toBeUndefined();
  });

  it.each([
    { ...executionWire, raw_trace: "private prompt" },
    { ...executionWire, worker_state: "probably_alive" },
    { ...executionWire, updated_at: "invalid time" },
    { ...executionWire, updated_at: "2026-02-30T05:00:04Z" },
    { ...executionWire, updated_at: "2026-09-05T24:00:00Z" },
    { ...executionWire, tasks: [{ ...executionWire.tasks[0], error_detail: "secret" }] },
    { ...executionWire, tasks: [{ ...executionWire.tasks[0], source_name: "untrusted provider text" }] },
    { ...executionWire, tasks: [{ ...executionWire.tasks[0], providers: ["unknown-service"] }] },
    { ...executionWire, tasks: [{ ...executionWire.tasks[0], counts: { ...executionWire.tasks[0]?.counts, admitted: -1 } }] },
  ])("rejects unsafe/invalid execution metadata %#", async (execution) => {
    await expect(clientWithSnapshot({ ...snapshotWire, runs: [{ ...runWire, execution }] }).getSnapshot(cid))
      .rejects.toBeInstanceOf(GatewaySchemaError);
  });

  it("delivers execution frames without consuming the role-event replay cursor", async () => {
    const controller = new AbortController();
    const seen: unknown[] = [];
    const frames = [
      `event: execution_progress\ndata: ${JSON.stringify({ run_spec_id: rid, execution: executionWire })}\n\n`,
      'id: 15\nevent: role_event\ndata: ' + JSON.stringify({ sequence: 15, run_spec_id: rid,
        role: "sources_evidence", type: "role_progress", status: "running", summary: "Role progress recorded",
        reason_code: null, artifacts: [], occurred_at: at }) + "\n\n",
    ].join("");
    const client = new HttpGatewayClient({ fetch: vi.fn(async () => new Response(frames,
      { headers: { "Content-Type": "text/event-stream" } })) });
    await client.streamEvents({ conversationId: cid, afterSequence: 14, signal: controller.signal,
      onExecutionProgress: (progress) => seen.push(progress),
      onEvent: (event) => { seen.push(event.sequence); controller.abort(); },
    });
    expect(seen).toEqual([expect.objectContaining({ runSpecId: rid, execution: expect.objectContaining({ workerState: "online" }) }), 15]);
  });
});

describe("visible execution process", () => {
  async function renderResearch(wire: unknown = snapshotWire) {
    const snapshot = await clientWithSnapshot(wire).getSnapshot(cid);
    let stream: GatewayStreamInput | undefined;
    const client: GatewayConversationClient = {
      getResearch: async (conversationId, runSpecId) => ({ conversationId, runSpecId, state: "pending", reasonCode: "result_pending", draftId: null, result: null, evidence: [], totalEvidence: 0, truncated: false, warnings: [], assessmentReview: null }),
      getEvidenceDetail: async () => { throw new Error("no fixture evidence"); },
      getTaskTrace: async () => { throw new Error("no fixture task history"); },
      getSession: async () => ({ tenantId: "local", subjectId: "researcher", roles: [] }),
      listConversations: async () => ({ conversations: [] }),
      getSnapshot: vi.fn(async () => snapshot),
      streamEvents: async (input) => { stream = input; input.onConnection?.("live"); },
      startConversation: async () => { throw new Error("not used"); },
      sendMessage: async () => { throw new Error("not used"); },
    };
    render(<MemoryRouter initialEntries={[`/research/${cid}`]}>
      <GatewayRoutes client={client} />
    </MemoryRouter>);
    await screen.findByRole("heading", { name: "谷歌财报核验" });
    await waitFor(() => expect(stream).toBeDefined());
    await userEvent.click(await screen.findByRole("tab", { name: "协作时间线" }));
    await userEvent.click(screen.getByText("原生 Gateway 执行内容", { selector: "summary" }));
    return { client, snapshot, get stream() { return stream!; } };
  }

  it("shows what is executing, its source and actual counts while professional roles remain honest", async () => {
    await renderResearch();
    expect(screen.getByRole("heading", { name: "运行进度" })).toBeInTheDocument();
    expect(screen.getAllByText("用户提供材料").length).toBeGreaterThan(0);
    expect(screen.getByText("提取材料中的陈述")).toBeInTheDocument();
    expect(screen.getByText(/材料处理不等于外部事实核验/)).toBeInTheDocument();
    expect(screen.getByText(/来源处理服务在线/)).toBeInTheDocument();
    expect(screen.getByText(/已准入 0/)).toBeInTheDocument();
    expect(screen.getAllByText("当前客户端尚未启用专业团队接口。").length).toBeGreaterThan(0);
  });

  it("updates the current task from a progress frame without requiring a terminal event or page refresh", async () => {
    const view = await renderResearch();
    const previous = view.snapshot.runs[0]!.execution!;
    await act(async () => view.stream.onExecutionProgress?.({ runSpecId: rid, execution: {
      ...previous, reasonCode: "worker_unavailable", nextAction: "check_execution", workerState: "offline",
    } }));
    expect(screen.getByText(/来源处理服务离线/)).toBeInTheDocument();
    expect(screen.getByText(/任务尚未完成/)).toBeInTheDocument();
    expect(view.client.getSnapshot).toHaveBeenCalledTimes(1);
  });

  it("explains the missing research subject and how to create a corrected scope instead of waiting forever", async () => {
    await renderResearch({ ...snapshotWire, runs: [{ ...runWire, execution: { ...executionWire,
      reason_code: "research_subject_missing", next_action: "check_execution",
    } }] });
    expect(screen.getByText(/未识别到研究主体/)).toBeInTheDocument();
    expect(screen.getByText(/调整范围：研究主体：公司名称/)).toBeInTheDocument();
    expect(screen.queryByText(/后台正在调度或处理任务/)).not.toBeInTheDocument();
  });

  it("counts failed tasks as ended without presenting them as successful results", async () => {
    await renderResearch({ ...snapshotWire, runs: [{ ...runWire, status: "failed", execution: { ...executionWire,
      reason_code: "research_subject_missing", next_action: "check_execution",
      tasks: [{ ...executionWire.tasks[0], status: "failed", stage: "failed" }],
    } }] });
    await userEvent.setup().click(screen.getByRole("tab", { name: "执行过程" }));
    expect(screen.getByText(/已结束 1 \/ 1 项任务/)).toBeInTheDocument();
    expect(screen.getByText(/失败 1 项/)).toBeInTheDocument();
  });

  it("keeps current work in view and progressively reveals a large task queue", async () => {
    const tasks = Array.from({ length: 12 }, (_, index) => ({ ...executionWire.tasks[0],
      task_id: `33333333-3333-4333-8333-${String(index + 1).padStart(12, "0")}`,
      status: index === 11 ? "running" : "queued", stage: index === 11 ? "extracting" : "queued",
    }));
    await renderResearch({ ...snapshotWire, runs: [{ ...runWire, execution: { ...executionWire, tasks } }] });
    expect(within(screen.getByRole("list", { name: "当前资料任务" })).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("提取材料中的陈述")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "展开全部 12 项任务" }));
    expect(within(screen.getByRole("list", { name: "当前资料任务" })).getAllByRole("listitem")).toHaveLength(12);
  });

  it("removes all private progress when access is revoked and ignores late frames", async () => {
    const view = await renderResearch();
    await act(async () => view.stream.onAccessRevoked?.());
    expect(screen.queryByRole("heading", { name: "运行进度" })).not.toBeInTheDocument();
    await act(async () => view.stream.onExecutionProgress?.({ runSpecId: rid, execution: view.snapshot.runs[0]!.execution! }));
    expect(screen.queryByText("用户提供材料")).not.toBeInTheDocument();
  });
});
