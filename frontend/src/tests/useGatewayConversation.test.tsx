import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { GatewayConversationClient, GatewayConversationSnapshot, GatewayRoleEvent, GatewayStreamInput } from "@/gateway/contracts";
import { useGatewayConversation, type GatewayConversationState } from "@/gateway/useGatewayConversation";
import { GatewayHttpError } from "@/gateway/HttpGatewayClient";

const conversationId = "conversation-1";
const runSpecId = "run-1";
const snapshot: GatewayConversationSnapshot = {
  conversationId, title: "Research", createdAt: "2026-09-09T00:00:00Z", updatedAt: "2026-09-09T00:00:00Z",
  latestSequence: 1, eventRetentionFloor: 1, messages: [], events: [], roles: [],
  runs: [{ runSpecId, nativeCaseId: "case-1", nativeRunId: "native-1", status: "running", parentRunSpecId: null, createdAt: "2026-09-09T00:00:00Z" }],
};
const completed: GatewayRoleEvent = {
  sequence: 2, runSpecId, role: "sources_evidence", type: "role_completed", status: "completed",
  summary: "Evidence acquired", reasonCode: null, artifacts: [{ kind: "evidence_link", id: "evidence-1" }], occurredAt: "2026-09-09T00:01:00Z",
};

function setup() {
  let resolveReplacement!: (value: GatewayConversationSnapshot) => void;
  let rejectReplacement!: (error: Error) => void;
  const streams: GatewayStreamInput[] = [];
  const getSnapshot = vi.fn<GatewayConversationClient["getSnapshot"]>().mockResolvedValueOnce(snapshot)
    .mockImplementation(() => new Promise((resolve, reject) => { resolveReplacement = resolve; rejectReplacement = reject; }));
  const client: GatewayConversationClient = {
    getSnapshot,
    streamEvents: vi.fn(async (stream) => { streams.push(stream); stream.onConnection?.("live"); }),
    getResearch: vi.fn(), getEvidenceDetail: vi.fn(), getTaskTrace: vi.fn(), getSession: vi.fn(),
    listConversations: vi.fn(), startConversation: vi.fn(), sendMessage: vi.fn(),
  };
  const renders: GatewayConversationState[] = [];
  const view = renderHook(({ activeClient, activeConversationId }) => {
    const state = useGatewayConversation({ client: activeClient, conversationId: activeConversationId });
    renders.push(state);
    return state;
  }, { initialProps: { activeClient: client, activeConversationId: conversationId } });
  return { view, client, renders, streams, getSnapshot, resolveReplacement: (value: GatewayConversationSnapshot) => resolveReplacement(value),
    rejectReplacement: (error: Error) => rejectReplacement(error) };
}

describe("useGatewayConversation refresh continuity", () => {
  it.each(["client", "conversation"])("masks previous private state on the first render after a %s change", async (change) => {
    const { view, client, renders, streams } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    const progress = { runSpecId, revision: 1, eventSequence: 3, status: "active" as const };
    act(() => streams[0]!.onTeamProgress?.(progress));
    expect(view.result.current.snapshot).toEqual(snapshot);
    expect(view.result.current.teamProgressByRun[runSpecId]).toEqual(progress);
    const activeClient = change === "client" ? { ...client, getSnapshot: vi.fn(() => new Promise<GatewayConversationSnapshot>(() => undefined)) } : client;
    renders.length = 0;
    view.rerender({ activeClient, activeConversationId: change === "conversation" ? "conversation-2" : conversationId });
    expect(renders.length).toBeGreaterThan(0);
    for (const state of renders) {
      expect(state.snapshot).toBeNull();
      expect(state.teamProgressByRun).toEqual({});
    }
    act(() => streams[0]!.onTeamProgress?.({ ...progress, revision: 2 }));
    expect(view.result.current.teamProgressByRun).toEqual({});
  });

  it("keeps authorized workspace and team progress visible until a terminal event snapshot arrives", async () => {
    const { view, streams, getSnapshot, resolveReplacement } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    const teamProgress = { runSpecId, revision: 1, eventSequence: 3, status: "active" as const };
    act(() => streams[0]!.onTeamProgress?.(teamProgress));
    act(() => streams[0]!.onEvent(completed));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2));
    expect(view.result.current.snapshot?.runs[0]?.status).toBe("running");
    expect(view.result.current.snapshot?.events).toContainEqual(completed);
    expect(view.result.current.teamProgressByRun[runSpecId]).toEqual(teamProgress);
    expect(streams[0]!.signal?.aborted).toBe(true);
    await act(async () => resolveReplacement({ ...snapshot, latestSequence: 2, events: [completed], runs: [{ ...snapshot.runs[0]!, status: "succeeded" }] }));
    expect(view.result.current.snapshot?.runs[0]?.status).toBe("succeeded");
    expect(streams[1]?.afterSequence).toBe(2);
  });

  it("keeps the existing workspace while recovering a retention gap", async () => {
    const { view, streams, getSnapshot } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0]!.onSnapshotRequired?.({ conversationId, latestSequence: 8, reason: "retention_gap" }));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2));
    expect(view.result.current.snapshot).toEqual(snapshot);
  });

  it("keeps the workspace visible when a confirmed mutation requests a fresh snapshot", async () => {
    const { view, streams, getSnapshot } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    const progress = { runSpecId, revision: 2, eventSequence: 9, status: "active" as const };
    act(() => streams[0]!.onTeamProgress?.(progress));
    act(() => view.result.current.retry());
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2));
    expect(view.result.current.snapshot).toEqual(snapshot);
    expect(view.result.current.teamProgressByRun[runSpecId]).toEqual(progress);
  });

  it("clears private workspace before replacement when artifact access shrinks and ignores late frames", async () => {
    const { view, streams, getSnapshot } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0]!.onSnapshotRequired?.({ conversationId, latestSequence: 1, reason: "artifact_access_changed" }));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2));
    expect(view.result.current.snapshot).toBeNull();
    act(() => streams[0]!.onEvent(completed));
    expect(view.result.current.snapshot).toBeNull();
  });

  it.each([401, 403, 404])("clears retained content when snapshot revalidation returns HTTP %s", async (status) => {
    const { view, streams, getSnapshot, rejectReplacement } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    act(() => streams[0]!.onTeamProgress?.({ runSpecId, revision: 1, eventSequence: 3, status: "active" }));
    act(() => streams[0]!.onEvent(completed));
    await waitFor(() => expect(getSnapshot).toHaveBeenCalledTimes(2));
    await act(async () => rejectReplacement(new GatewayHttpError(status)));
    expect(view.result.current.snapshot).toBeNull();
    expect(view.result.current.teamProgressByRun).toEqual({});
    expect(view.result.current.connection).toBe("paused");
  });

  it("clears team progress on access revocation and ignores late events from the revoked stream", async () => {
    const { view, streams, getSnapshot } = setup();
    await waitFor(() => expect(streams).toHaveLength(1));
    const progress = { runSpecId, revision: 1, eventSequence: 3, status: "active" as const };
    act(() => streams[0]!.onTeamProgress?.(progress));
    act(() => streams[0]!.onAccessRevoked?.());
    expect(view.result.current.snapshot).toBeNull();
    expect(view.result.current.teamProgressByRun).toEqual({});
    act(() => {
      streams[0]!.onTeamProgress?.({ ...progress, revision: 2 });
      streams[0]!.onEvent(completed);
    });
    expect(view.result.current.teamProgressByRun).toEqual({});
    expect(getSnapshot).toHaveBeenCalledTimes(1);
    expect(streams[0]!.signal?.aborted).toBe(true);
  });
});
