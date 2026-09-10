import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GatewayRoutes } from "@/app/GatewayRoutes";
import { GatewayHttpError } from "@/gateway/HttpGatewayClient";
import type { GatewayConversationClient } from "@/gateway/contracts";
import { parseTeam } from "@/gateway/team";
import { teamReceipt, teamRunId, teamWire } from "./teamFixtures";

const conversationId = "conversation-live-1";

const liveSnapshot = {
  conversationId,
  title: "半导体设备订单变化",
  createdAt: "2026-09-05T00:00:00Z",
  updatedAt: "2026-09-05T00:02:00Z",
  latestSequence: 4,
  eventRetentionFloor: 1,
  messages: [
    {
      id: "message-1",
      sequence: 1,
      text: "研究半导体设备订单变化",
      createdAt: "2026-09-05T00:00:00Z",
      kind: "user" as const,
    },
  ],
  runs: [
    {
      runSpecId: "run-spec-1",
      nativeCaseId: "case-1",
      nativeRunId: "native-run-1",
      status: "waiting_for_sources" as const,
      parentRunSpecId: null,
      createdAt: "2026-09-05T00:00:00Z",
    },
  ],
  roles: [
    {
      runSpecId: "run-spec-1",
      role: "sources_evidence" as const,
      status: "blocked" as const,
      latestSequence: 4,
    },
  ],
  events: [
    {
      sequence: 4,
      runSpecId: "run-spec-1",
      role: "sources_evidence" as const,
      type: "source_waiting" as const,
      status: "blocked" as const,
      summary: "Waiting for approved source",
      reasonCode: "source_unavailable" as const,
      artifacts: [
        {
          kind: "evidence_link" as const,
          id: "private-artifact-id",
          locatorAvailable: true,
        },
      ],
      occurredAt: "2026-09-05T00:02:00Z",
    },
  ],
};

function clientForEmptyWorkspace(): GatewayConversationClient {
  return {
    getResearch: async (conversationId, runSpecId) => ({ conversationId, runSpecId, state: "pending", reasonCode: "result_pending", draftId: null, result: null, evidence: [], totalEvidence: 0, truncated: false, warnings: [], assessmentReview: null }),
    getEvidenceDetail: async () => { throw new Error("no fixture evidence"); },
    getTaskTrace: async () => { throw new Error("no fixture task history"); },
    getSession: async () => ({ tenantId: "team-a", roles: [], subjectId: "alice" }),
    listConversations: async () => ({ conversations: [] }),
    startConversation: async () => {
      throw new Error("not used by this test");
    },
    sendMessage: async () => {
      throw new Error("not used by this test");
    },
    getSnapshot: async () => {
      throw new Error("not used by this test");
    },
    streamEvents: async () => undefined,
  };
}

function renderRoutes(client: GatewayConversationClient, initialEntry = "/"): ReturnType<typeof render> {
  return render(
    <MemoryRouter
      initialEntries={[initialEntry]}
    >
      <GatewayRoutes client={client} />
    </MemoryRouter>,
  );
}

function storageDump(): string {
  return Array.from({ length: sessionStorage.length }, (_, index) => {
    const key = sessionStorage.key(index) ?? "";
    return `${key}:${sessionStorage.getItem(key) ?? ""}`;
  }).join("\n");
}

beforeEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("GatewayRoutes", () => {
  it("provides a collapsible research navigation and closes it after selecting a research", async () => {
    const client = clientForEmptyWorkspace();
    client.listConversations = async () => ({ conversations: [{
      conversationId, title: liveSnapshot.title, createdAt: liveSnapshot.createdAt,
      updatedAt: liveSnapshot.updatedAt, latestSequence: liveSnapshot.latestSequence,
    }] });
    client.getSnapshot = async () => liveSnapshot;
    renderRoutes(client);
    await screen.findByLabelText("研究请求");
    const toggle = screen.getByRole("button", { name: "研究列表" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(toggle.getAttribute("aria-controls")!)).toHaveAttribute("aria-label", "研究空间");
    await userEvent.click(screen.getByRole("link", { name: /半导体设备订单变化/ }));
    await screen.findByRole("heading", { name: liveSnapshot.title });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("link", { name: /半导体设备订单变化/ })).toHaveAttribute("aria-current", "page");
    await userEvent.click(toggle);
    await userEvent.click(screen.getByRole("link", { name: /半导体设备订单变化/ }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
  it("mounts saved event history only when explicitly expanded", async () => {
    const client = clientForEmptyWorkspace();
    client.getSnapshot = async () => liveSnapshot;
    renderRoutes(client, `/research/${conversationId}`);
    await screen.findByRole("heading", { name: liveSnapshot.title });
    expect(screen.queryByText("Waiting for approved source")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "协作时间线" }));
    await userEvent.click(screen.getByText("已确认的研究请求", { selector: "summary" }));
    await userEvent.setup().click(screen.getByText("展开完整执行记录（1 条）"));
    expect(await screen.findByText("Waiting for approved source")).toBeVisible();
  });
  it("explains the authorized result, exact evidence and history reading boundary honestly", async () => {
    renderRoutes(clientForEmptyWorkspace());
    expect(await screen.findByRole("heading", { name: "暂无本轮证据" })).toBeInTheDocument();
    expect(screen.getByLabelText("研究范围")).toHaveTextContent("等待解析");
    expect(screen.queryByRole("complementary", { name: "Gateway 说明" })).not.toBeInTheDocument();
    expect(screen.queryByText("这里仅显示 Gateway 已授权的执行阶段、摘要与安全工件引用。")).not.toBeInTheDocument();
  });
  it("keeps the professional role cards visibly unbound on the empty live workspace", async () => {
    renderRoutes(clientForEmptyWorkspace());

    expect(await screen.findByRole("heading", { name: "发起新的研究" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "产业分析师" })).toBeVisible();
    expect(screen.getAllByRole("article", { name: /工作区$/ })).toHaveLength(4);
    expect(screen.queryByText(/中微公司：刻蚀设备/)).not.toBeInTheDocument();
  });

  it("waits for confirmed identity before loading a deep-linked private conversation", async () => {
    const client = clientForEmptyWorkspace();
    let confirmIdentity!: (session: Awaited<ReturnType<typeof client.getSession>>) => void;
    client.getSession = () => new Promise((resolve) => { confirmIdentity = resolve; });
    const getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.getSnapshot = getSnapshot;

    renderRoutes(client, `/research/${conversationId}`);

    expect(getSnapshot).not.toHaveBeenCalled();
    expect(screen.queryByText("研究半导体设备订单变化")).not.toBeInTheDocument();
    await act(async () => {
      confirmIdentity({ tenantId: "team-a", roles: [], subjectId: "alice" });
    });
    expect(await screen.findByRole("heading", { name: "半导体设备订单变化" })).toBeInTheDocument();
  });

  it("starts a private conversation with one generated idempotency key", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const startConversation = vi.fn().mockResolvedValue({
      conversationId: "conversation-1",
      intentId: "intent-1",
      runSpecId: "run-1",
      nativeCaseId: "case-1",
      nativeRunId: "native-run-1",
      status: "queued",
      latestSequence: 4,
    });
    client.startConversation = startConversation;

    renderRoutes(client);

    await user.type(await screen.findByLabelText("研究请求"), "研究半导体设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    await waitFor(() => expect(startConversation).toHaveBeenCalledTimes(1));
    expect(startConversation.mock.calls[0]?.[0]).toMatchObject({
      initialMessage: "研究半导体设备订单变化",
      idempotencyKey: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
      ),
    });
  });

  it("keeps an overlong start request visible and rejects it locally", async () => {
    const client = clientForEmptyWorkspace();
    const startConversation = vi.fn();
    client.startConversation = startConversation;
    const overlong = `${"研".repeat(20_000)}X`;

    renderRoutes(client);

    const box = await screen.findByLabelText("研究请求");
    fireEvent.change(box, { target: { value: overlong } });
    await userEvent.setup().click(screen.getByRole("button", { name: "发起研究" }));

    expect(screen.getByRole("alert")).toHaveTextContent("研究请求最多 20,000 字");
    expect(box).toHaveValue(overlong);
    expect(startConversation).not.toHaveBeenCalled();
  });

  it("reuses the same idempotency key when start delivery is unknown", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const startConversation = vi
      .fn()
      .mockRejectedValueOnce(new Error("gateway unavailable"))
      .mockResolvedValueOnce({
        conversationId,
        intentId: "intent-retry",
        runSpecId: "run-retry",
        nativeCaseId: "case-retry",
        nativeRunId: "native-run-retry",
        status: "queued",
        latestSequence: 0,
      });
    client.startConversation = startConversation;
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);

    renderRoutes(client);

    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法确认本次提交是否已送达");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    await waitFor(() => expect(startConversation).toHaveBeenCalledTimes(2));
    expect(startConversation.mock.calls[1]?.[0]).toMatchObject({
      idempotencyKey: startConversation.mock.calls[0]?.[0].idempotencyKey,
      initialMessage: "研究设备订单变化",
    });
  });
  it("reuses a pending start idempotency key after remount without storing the request text", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const startConversation = vi
      .fn()
      .mockRejectedValueOnce(new Error("gateway unavailable"))
      .mockResolvedValueOnce({
        conversationId,
        intentId: "intent-retry",
        runSpecId: "run-retry",
        nativeCaseId: "case-retry",
        nativeRunId: "native-run-retry",
        status: "queued",
        latestSequence: 0,
      });
    client.startConversation = startConversation;
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);

    const first = renderRoutes(client);
    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("无法确认本次提交是否已送达");
    const firstKey = startConversation.mock.calls[0]?.[0].idempotencyKey;
    expect(storageDump()).not.toContain("研究设备订单变化");
    first.unmount();

    renderRoutes(client);
    expect(await screen.findByRole("alert")).toHaveTextContent("检测到新建研究存在待确认提交标识");
    expect(startConversation).toHaveBeenCalledTimes(1);
    await user.type(screen.getByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    expect(startConversation).toHaveBeenCalledTimes(2);
    expect(startConversation.mock.calls[1]?.[0]).toMatchObject({ idempotencyKey: firstKey, initialMessage: "研究设备订单变化" });
    expect(storageDump()).not.toContain("fundclaw.pending-idempotency");
  });

  it("uses a new start idempotency key after remount when the user changes the request text", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const startConversation = vi.fn().mockRejectedValueOnce(new Error("gateway unavailable")).mockResolvedValueOnce({
      conversationId, intentId: "intent-new", runSpecId: "run-new", nativeCaseId: "case-new", nativeRunId: "native-run-new", status: "queued", latestSequence: 0,
    });
    client.startConversation = startConversation;
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);

    const first = renderRoutes(client);
    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));
    await screen.findByRole("alert");
    const firstKey = startConversation.mock.calls[0]?.[0].idempotencyKey;
    first.unmount();

    renderRoutes(client);
    await user.type(await screen.findByLabelText("研究请求"), "研究费用变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    expect(startConversation.mock.calls[1]?.[0].idempotencyKey).not.toBe(firstKey);
  });

  it("warns when start idempotency cannot be persisted across refresh", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    client.startConversation = vi.fn().mockRejectedValue(new Error("gateway unavailable"));

    renderRoutes(client);
    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("浏览器无法保存待确认提交标识");
  });

  it("shows storage-unavailable risk before a start request is submitted", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });

    renderRoutes(clientForEmptyWorkspace());

    expect(await screen.findByRole("alert")).toHaveTextContent("浏览器无法保存待确认提交标识");
  });

  it("retains a start idempotency key across a 409 that may still be in progress", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const startConversation = vi
      .fn()
      .mockRejectedValueOnce(new GatewayHttpError(409))
      .mockResolvedValueOnce({
        conversationId,
        intentId: "intent-retry",
        runSpecId: "run-retry",
        nativeCaseId: "case-retry",
        nativeRunId: "native-run-retry",
        status: "queued",
        latestSequence: 0,
      });
    client.startConversation = startConversation;
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);

    renderRoutes(client);

    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));
    const firstKey = startConversation.mock.calls[0]?.[0].idempotencyKey;
    expect(await screen.findByRole("alert")).toHaveTextContent("无法确认本次提交是否已送达");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    await waitFor(() => expect(startConversation).toHaveBeenCalledTimes(2));
    expect(startConversation.mock.calls[1]?.[0]).toMatchObject({ idempotencyKey: firstKey });
  });

  it("treats a write-path auth rejection as known auth loss, not unknown delivery", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    client.startConversation = vi.fn().mockRejectedValue(new GatewayHttpError(401));

    renderRoutes(client);

    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地研究身份暂不可用");
    expect(screen.queryByText("无法确认本次提交是否已送达")).not.toBeInTheDocument();
    expect(screen.queryByText("研究员：alice")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新确认身份" })).toBeInTheDocument();
  });

  it("keeps private content cleared while retrying an identity denied by the server", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    let rejectIdentity!: (error: unknown) => void;
    client.getSession = vi.fn()
      .mockResolvedValueOnce({ tenantId: "team-a", roles: [], subjectId: "alice" })
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectIdentity = reject; }));
    client.listConversations = async () => ({ conversations: [{
      conversationId,
      title: "私有侧栏研究",
      createdAt: "2026-09-05T00:00:00Z",
      updatedAt: "2026-09-05T00:00:00Z",
      latestSequence: 4,
    }] });
    const getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.getSnapshot = getSnapshot;
    client.sendMessage = vi.fn().mockRejectedValue(new GatewayHttpError(401));

    renderRoutes(client, `/research/${conversationId}`);
    await user.type(await screen.findByLabelText("继续研究"), "继续核验订单");
    await user.click(screen.getByRole("button", { name: "提交" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地研究身份暂不可用");
    expect(screen.queryByText("研究员：alice")).not.toBeInTheDocument();
    expect(screen.queryByText("私有侧栏研究")).not.toBeInTheDocument();
    expect(screen.queryByText("研究半导体设备订单变化")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新确认身份" }));
    expect(getSnapshot).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("研究半导体设备订单变化")).not.toBeInTheDocument();
    await act(async () => { rejectIdentity(new GatewayHttpError(403)); });
    expect(await screen.findByRole("alert")).toHaveTextContent("本地研究身份暂不可用");
    expect(getSnapshot).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("私有侧栏研究")).not.toBeInTheDocument();
  });

  it("refreshes the real sidebar after a confirmed receipt without adding an optimistic row", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const listConversations = vi
      .fn()
      .mockResolvedValueOnce({ conversations: [] })
      .mockResolvedValueOnce({
        conversations: [
          {
            conversationId,
            title: "已确认的新研究",
            createdAt: "2026-09-05T00:00:00Z",
            updatedAt: "2026-09-05T00:00:00Z",
            latestSequence: 0,
          },
        ],
      });
    client.listConversations = listConversations;
    client.startConversation = vi.fn().mockResolvedValue({
      conversationId,
      intentId: "intent-confirmed",
      runSpecId: "run-confirmed",
      nativeCaseId: "case-confirmed",
      nativeRunId: "native-run-confirmed",
      status: "queued",
      latestSequence: 0,
    });
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);

    renderRoutes(client);

    await user.type(await screen.findByLabelText("研究请求"), "研究设备订单变化");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    expect(await screen.findByRole("link", { name: /已确认的新研究/ })).toBeInTheDocument();
    expect(listConversations).toHaveBeenCalledTimes(2);
  });

  it("renders real Gateway stages without relabelling them as professional agent work", async () => {
    const client = clientForEmptyWorkspace();
    const getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    const streamEvents = vi.fn().mockResolvedValue(undefined);
    client.getSnapshot = getSnapshot;
    client.streamEvents = streamEvents;

    renderRoutes(client, `/research/${conversationId}`);

    expect(await screen.findByRole("heading", { name: "半导体设备订单变化" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "协作时间线" }));
    await userEvent.click(screen.getByText("原生 Gateway 执行内容", { selector: "summary" }));
    await userEvent.click(screen.getByText("执行阶段概览", { selector: "summary" }));
    expect(screen.getByText("真实 Gateway 执行阶段")).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "协作时间线" }));
    await userEvent.click(screen.getByText("已确认的研究请求", { selector: "summary" }));
    await userEvent.setup().click(screen.getByText("展开完整执行记录（1 条）"));
    expect(screen.getAllByText("资料与证据")).toHaveLength(2);
    expect(screen.getAllByText("等待资料来源").length).toBeGreaterThan(0);
    expect(screen.getByText("Waiting for approved source")).toBeInTheDocument();
    expect(screen.getByText("安全工件引用 1 项 · 可在获授权环境中定位")).toBeInTheDocument();
    expect(screen.queryByText("private-artifact-id")).not.toBeInTheDocument();
    expect(screen.getByText("当前客户端尚未启用专业团队接口。")).toBeInTheDocument();
    expect(screen.queryByText(/中微公司：刻蚀设备/)).not.toBeInTheDocument();
    expect(getSnapshot).toHaveBeenCalledWith(conversationId, expect.any(AbortSignal));
    expect(streamEvents).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId, afterSequence: 4 }),
    );
  });

  it("shows the P0 unsupported notice and reloads a snapshot instead of inventing a run", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    const startConversation = vi.fn().mockResolvedValue({
      receiptKind: "intent",
      intentId: "intent-unsupported",
      conversationId,
      intentKind: "unsupported",
      outcome: "rejected",
      reasonCode: "unsupported_in_gateway_p0",
    });
    const getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.startConversation = startConversation;
    client.getSnapshot = getSnapshot;

    renderRoutes(client);

    await user.type(await screen.findByLabelText("研究请求"), "暂停当前研究");
    await user.click(screen.getByRole("button", { name: "发起研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "该请求暂不受 Gateway P0 支持，未启动新的研究任务。",
    );
    expect(getSnapshot).toHaveBeenCalledWith(conversationId, expect.any(AbortSignal));
    expect(screen.queryByText("科创板半导体设备国产化机会研究")).not.toBeInTheDocument();
    expect(screen.queryByText("已排队")).not.toBeInTheDocument();
  });

  it("replaces the complete snapshot after artifact access changes", async () => {
    const client = clientForEmptyWorkspace();
    const staleSnapshot = {
      ...liveSnapshot,
      title: "旧投影",
      events: [{ ...liveSnapshot.events[0], summary: "Old safe projection" }],
    };
    const replacementSnapshot = {
      ...liveSnapshot,
      title: "已刷新投影",
      latestSequence: 8,
      events: [{ ...liveSnapshot.events[0], sequence: 8, summary: "Approved evidence available" }],
    };
    const getSnapshot = vi
      .fn()
      .mockResolvedValueOnce(staleSnapshot)
      .mockResolvedValueOnce(replacementSnapshot);
    let streamCalls = 0;
    const streamEvents = vi.fn().mockImplementation(async (input) => {
      if (streamCalls++ === 0) {
        input.onSnapshotRequired?.({
          conversationId,
          latestSequence: 8,
          reason: "artifact_access_changed",
        });
      }
    });
    client.getSnapshot = getSnapshot;
    client.streamEvents = streamEvents;

    renderRoutes(client, `/research/${conversationId}`);

    expect(await screen.findByRole("heading", { name: "已刷新投影" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "协作时间线" }));
    await userEvent.click(screen.getByText("已确认的研究请求", { selector: "summary" }));
    await userEvent.setup().click(screen.getByText("展开完整执行记录（1 条）"));
    expect(screen.getByText("Approved evidence available")).toBeInTheDocument();
    expect(screen.queryByText("Old safe projection")).not.toBeInTheDocument();
    expect(getSnapshot).toHaveBeenCalledTimes(2);
  });

  it("reloads one snapshot after a future-cursor 422 rather than retrying that cursor forever", async () => {
    const client = clientForEmptyWorkspace();
    const recoveredSnapshot = { ...liveSnapshot, title: "游标已恢复", latestSequence: 9 };
    const getSnapshot = vi
      .fn()
      .mockResolvedValueOnce(liveSnapshot)
      .mockResolvedValueOnce(recoveredSnapshot);
    client.getSnapshot = getSnapshot;
    client.streamEvents = vi
      .fn()
      .mockRejectedValueOnce(new GatewayHttpError(422))
      .mockResolvedValueOnce(undefined);

    renderRoutes(client, `/research/${conversationId}`);

    expect(await screen.findByRole("heading", { name: "游标已恢复" })).toBeInTheDocument();
    expect(getSnapshot).toHaveBeenCalledTimes(2);
  });

  it("clears the live projection when access is revoked", async () => {
    const client = clientForEmptyWorkspace();
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.streamEvents = vi.fn().mockImplementation(async (input) => {
      input.onAccessRevoked?.();
    });

    renderRoutes(client, `/research/${conversationId}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "访问已撤销，已清除当前私有研究内容。",
    );
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "半导体设备订单变化" })).not.toBeInTheDocument();
      expect(screen.queryByText("研究半导体设备订单变化")).not.toBeInTheDocument();
    });
  });

  it("sends an explicit scope-change prefix unchanged and offers no control shortcut", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    const sendMessage = vi.fn().mockResolvedValue({
      conversationId,
      intentId: "intent-scope-change",
      runSpecId: "run-spec-2",
      nativeCaseId: "case-2",
      nativeRunId: "native-run-2",
      status: "queued",
      latestSequence: 5,
    });
    client.sendMessage = sendMessage;

    renderRoutes(client, `/research/${conversationId}`);

    await user.type(
      await screen.findByLabelText("继续研究"),
      "调整范围: 仅关注订单能见度",
    );
    await user.click(screen.getByRole("button", { name: "提交" }));

    expect(sendMessage).toHaveBeenCalledWith(
      conversationId,
      expect.objectContaining({ text: "调整范围: 仅关注订单能见度" }),
    );
  });

  it("routes ordinary follow-up instructions to the selected professional role", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.getTeam = vi.fn().mockResolvedValue(parseTeam(teamWire(), { conversationId: "00000000-0000-4000-8000-000000000001", runSpecId: teamRunId }));
    client.sendTeamMessage = vi.fn().mockResolvedValue(teamReceipt());
    client.commandTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.reviewTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.sendMessage = vi.fn();

    renderRoutes(client, `/research/${conversationId}`);

    await screen.findByText("industry 已保存摘要");
    await user.selectOptions(screen.getByRole("combobox", { name: "专业团队收件人" }), "finance");
    await user.type(screen.getByLabelText("继续研究"), "请核对现金流与费用口径");
    await user.click(screen.getByRole("button", { name: "提交" }));

    await waitFor(() => expect(client.sendTeamMessage).toHaveBeenCalledWith(
      conversationId,
      "run-spec-1",
      expect.objectContaining({ text: "请核对现金流与费用口径", recipient: "finance", expected_revision: 1 }),
    ));
    expect(client.sendMessage).not.toHaveBeenCalled();
  });

  it("does not send an ordinary follow-up through the native endpoint while team revision is loading", async () => {
    const user = userEvent.setup();
    const client = clientForEmptyWorkspace();
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    const neverTeam = new Promise<Awaited<ReturnType<NonNullable<GatewayConversationClient["getTeam"]>>>>(() => undefined);
    client.getTeam = vi.fn().mockReturnValue(neverTeam);
    client.sendTeamMessage = vi.fn().mockResolvedValue(teamReceipt());
    client.commandTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.reviewTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.sendMessage = vi.fn();

    renderRoutes(client, `/research/${conversationId}`);

    await screen.findAllByText("正在读取专业团队…");
    await user.type(screen.getByLabelText("继续研究"), "请补充行业竞争格局");
    await user.click(screen.getByRole("button", { name: "提交" }));

    expect(screen.getByRole("alert")).toHaveTextContent("专业团队版本尚未读取完成");
    expect(client.sendTeamMessage).not.toHaveBeenCalled();
    expect(client.sendMessage).not.toHaveBeenCalled();
  });

  it("keeps team output and composer drafts through a 503 and blocks even direct form submission until recovery", async () => {
    const client = clientForEmptyWorkspace();
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    const saved = parseTeam(teamWire(), { conversationId: "00000000-0000-4000-8000-000000000001", runSpecId: teamRunId });
    client.getTeam = vi.fn().mockResolvedValueOnce(saved).mockRejectedValueOnce(new GatewayHttpError(503)).mockResolvedValue(saved);
    client.sendTeamMessage = vi.fn().mockResolvedValue(teamReceipt());
    client.commandTeam = vi.fn(); client.reviewTeam = vi.fn(); client.sendMessage = vi.fn();
    renderRoutes(client, `/research/${conversationId}`);
    const summary = await screen.findByText("industry 已保存摘要");
    const box = screen.getByLabelText("继续研究");
    fireEvent.change(box, { target: { value: "保留待发送草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "重新读取团队" }));
    await screen.findByText(/团队状态待更新，当前显示上次成功读取的内容/);
    expect(screen.getByText("industry 已保存摘要")).toBe(summary);
    expect(box).toHaveValue("保留待发送草稿");
    expect(screen.getByRole("button", { name: "提交" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "暂停" })).toBeDisabled();
    fireEvent.submit(box.closest("form")!);
    expect(client.sendTeamMessage).not.toHaveBeenCalled();
    expect(client.sendMessage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新读取团队" }));
    await waitFor(() => expect(screen.queryByText(/团队状态待更新，当前显示上次成功读取的内容/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "提交" })).toBeEnabled();
    expect(box).toHaveValue("保留待发送草稿");
    fireEvent.click(screen.getByRole("button", { name: "提交" }));
    await waitFor(() => expect(client.sendTeamMessage).toHaveBeenCalledOnce());
  });

  it("keeps an overlong professional-team message visible and rejects it locally", async () => {
    const client = clientForEmptyWorkspace();
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.getTeam = vi.fn().mockResolvedValue(parseTeam(teamWire(), { conversationId: "00000000-0000-4000-8000-000000000001", runSpecId: teamRunId }));
    client.sendTeamMessage = vi.fn().mockResolvedValue(teamReceipt());
    client.commandTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.reviewTeam = vi.fn().mockResolvedValue(teamReceipt());
    const overlong = `${"研".repeat(20_000)}X`;

    renderRoutes(client, `/research/${conversationId}`);

    const box = await screen.findByLabelText("继续研究");
    fireEvent.change(box, { target: { value: overlong } });
    await userEvent.setup().click(screen.getByRole("button", { name: "提交" }));

    expect(screen.getByRole("alert")).toHaveTextContent("专业团队指令最多 20,000 字");
    expect(box).toHaveValue(overlong);
    expect(client.sendTeamMessage).not.toHaveBeenCalled();
  });

  it("refreshes the selected team after advanced team_progress frames", async () => {
    const client = clientForEmptyWorkspace();
    const first = parseTeam(teamWire(), { conversationId: "00000000-0000-4000-8000-000000000001", runSpecId: teamRunId });
    const secondWire = teamWire();
    secondWire.revision = 2;
    secondWire.event_sequence = 12;
    secondWire.tasks.push({ ...secondWire.tasks[0]!, id: "00000000-0000-4000-8000-000000000050", revision: 2,
      output: { ...secondWire.tasks[0]!.output, id: "00000000-0000-4000-8000-000000000051", content: { ...secondWire.tasks[0]!.output.content, summary: "industry team_progress 后更新" } } });
    client.getSnapshot = vi.fn().mockResolvedValue(liveSnapshot);
    client.getTeam = vi.fn().mockResolvedValueOnce(first).mockResolvedValue(parseTeam(secondWire, { conversationId: "00000000-0000-4000-8000-000000000001", runSpecId: teamRunId }));
    client.sendTeamMessage = vi.fn().mockResolvedValue(teamReceipt());
    client.commandTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.reviewTeam = vi.fn().mockResolvedValue(teamReceipt());
    client.streamEvents = vi.fn().mockImplementation(async (input) => {
      input.onTeamProgress?.({ runSpecId: "run-spec-1", revision: 1, eventSequence: 8, status: "active" });
      input.onTeamProgress?.({ runSpecId: "run-spec-1", revision: 2, eventSequence: 12, status: "active" });
    });

    renderRoutes(client, `/research/${conversationId}`);

    await screen.findByText("industry team_progress 后更新");
    expect(client.getTeam).toHaveBeenCalledTimes(2);
  });

  it('opens the exact adopted run and team version after asynchronous snapshot loading',async()=>{
    const client=clientForEmptyWorkspace();
    client.getSnapshot=vi.fn().mockResolvedValue({...liveSnapshot,runs:[...liveSnapshot.runs,{...liveSnapshot.runs[0],runSpecId:'run-spec-2',nativeRunId:'native-run-2'}]});
    const wire=teamWire();wire.revision=2;
    client.getTeam=vi.fn().mockResolvedValue(parseTeam(wire,{conversationId:'00000000-0000-4000-8000-000000000001',runSpecId:teamRunId}));
    client.sendTeamMessage=vi.fn();client.commandTeam=vi.fn();client.reviewTeam=vi.fn();
    renderRoutes(client,`/companies/22222222-2222-4222-8222-222222222222/research/${conversationId}?run=run-spec-1&teamRevision=1`);
    await screen.findByText('industry 已保存摘要');
    expect(client.getTeam).toHaveBeenCalledWith(conversationId,'run-spec-1',expect.any(AbortSignal));
    expect(screen.getByRole('combobox',{name:'团队版本'})).toHaveValue('1');
    expect(screen.getByRole('button',{name:'提交'})).toBeDisabled();
  });
  it('keeps an explicitly adopted team version pinned when the live team advances',async()=>{
    const client=clientForEmptyWorkspace();client.getSnapshot=vi.fn().mockResolvedValue(liveSnapshot);
    const later=teamWire();later.revision=2;later.event_sequence=12;
    client.getTeam=vi.fn().mockResolvedValueOnce(parseTeam(teamWire(),{conversationId:'00000000-0000-4000-8000-000000000001',runSpecId:teamRunId})).mockResolvedValue(parseTeam(later,{conversationId:'00000000-0000-4000-8000-000000000001',runSpecId:teamRunId}));
    client.sendTeamMessage=vi.fn();client.commandTeam=vi.fn();client.reviewTeam=vi.fn();
    renderRoutes(client,`/research/${conversationId}?run=run-spec-1&teamRevision=1`);
    await screen.findByText('industry 已保存摘要');
    fireEvent.click(screen.getByRole('button',{name:'重新读取团队'}));
    await waitFor(()=>expect(screen.getByText(/最新版本 2/)).toBeInTheDocument());
    expect(screen.getByRole('combobox',{name:'团队版本'})).toHaveValue('1');
  });
});
