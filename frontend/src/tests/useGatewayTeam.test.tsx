import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GatewayHttpError, GatewaySchemaError, GatewayTimeoutError } from "@/gateway/HttpGatewayClient";
import { parseTeam, type GatewayTeamClient } from "@/gateway/team";
import { useGatewayTeam } from "@/gateway/useGatewayTeam";
import { teamWire, teamConversationId as conversationId, teamRunId as runSpecId } from "./teamFixtures";

const data = () => parseTeam(teamWire(), { conversationId, runSpecId });
const clientFor = (getTeam: GatewayTeamClient["getTeam"]): GatewayTeamClient => ({ getTeam, sendTeamMessage: vi.fn(), commandTeam: vi.fn(), reviewTeam: vi.fn() });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
describe("useGatewayTeam", () => {
  it.each([new TypeError("Failed to fetch"), new GatewayTimeoutError(), ...[408, 429, 500, 503, 504].map((status) => new GatewayHttpError(status))])("retains the authorized team through a transient %s and clears staleness only after recovery", async (failure) => {
    vi.useFakeTimers();
    const saved = data();
    let recover!: (value: ReturnType<typeof data>) => void;
    const getTeam = vi.fn().mockResolvedValueOnce(saved).mockRejectedValueOnce(failure)
      .mockImplementationOnce(() => new Promise((resolve) => { recover = resolve; }));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(view.result.current.state).toMatchObject({ kind: "available", data: saved, refreshing: false, stale: true });
    expect(onAuthorizationLost).not.toHaveBeenCalled();
    await act(async () => view.result.current.refresh());
    expect(view.result.current.state).toMatchObject({ kind: "available", data: saved, refreshing: true, stale: true });
    await act(async () => recover({ ...saved, event_sequence: saved.event_sequence + 1 }));
    expect(view.result.current.state.kind).toBe("available");
    expect(view.result.current.state).not.toMatchObject({ stale: true });
  });
  it.each([new GatewaySchemaError(), new GatewayHttpError(400), ...[401, 403, 404].map((status) => new GatewayHttpError(status))])("clears even a retained stale team after a definitive %s", async (failure) => {
    vi.useFakeTimers();
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockRejectedValueOnce(new GatewayHttpError(503)).mockRejectedValue(failure);
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(view.result.current.state).toMatchObject({ kind: "available", stale: true });
    await act(async () => view.result.current.refresh());
    expect(view.result.current.state.kind).toBe(failure instanceof GatewayHttpError && failure.status === 404 ? "expired" : failure instanceof GatewayHttpError && [401, 403].includes(failure.status) ? "unauthorized" : "error");
    expect(view.result.current.state).not.toHaveProperty("data");
  });
  it("does not invent a team after the first failed read", async () => {
    vi.useFakeTimers();
    const client = clientFor(vi.fn().mockRejectedValue(new GatewayHttpError(503)));
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost: vi.fn() }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(view.result.current.state).toEqual({ kind: "error", schema: false });
  });
  it("immediately removes stale cached outputs when authorized evidence shrinks", async () => {
    vi.useFakeTimers();
    const client = clientFor(vi.fn().mockResolvedValueOnce(data()).mockRejectedValue(new GatewayHttpError(503)));
    const access = (...ids: string[]) => JSON.stringify([runSpecId, ids]);
    const view = renderHook(({ artifacts }) => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: artifacts, onAuthorizationLost: vi.fn() }),
      { initialProps: { artifacts: access("evidence_link:a", "evidence_link:b") } });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(view.result.current.state).toMatchObject({ kind: "available", stale: true });
    view.rerender({ artifacts: access("evidence_link:b") });
    expect(view.result.current.state).toEqual({ kind: "loading" });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(view.result.current.state).toEqual({ kind: "error", schema: false });
  });
  it("hides a previous client's private output immediately even when the research and authorization keys match", async () => {
    vi.useFakeTimers();
    let finishOld!: (value: ReturnType<typeof data>) => void;
    let finishReplacement!: (value: ReturnType<typeof data>) => void;
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const replacementClient = clientFor(vi.fn(() => new Promise<ReturnType<typeof data>>((resolve) => { finishReplacement = resolve; })));
    const renders: ReturnType<typeof useGatewayTeam>["state"][] = [];
    const view = renderHook(({ activeClient }) => {
      const team = useGatewayTeam({ client: activeClient, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost });
      renders.push(team.state);
      return team;
    }, { initialProps: { activeClient: client } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(view.result.current.state.kind).toBe("available");
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    renders.length = 0;
    view.rerender({ activeClient: replacementClient });
    expect(renders.length).toBeGreaterThan(0);
    expect(renders.every((state) => state.kind === "loading")).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => finishOld(data()));
    expect(view.result.current.state.kind).toBe("loading");
    const replacementData = { ...data(), revision: 2 };
    await act(async () => finishReplacement(replacementData));
    expect(view.result.current.state).toMatchObject({ kind: "available", data: replacementData });
  });
  it("keeps routine polling silent", async () => {
    vi.useFakeTimers();
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockImplementation(() => new Promise(() => undefined));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(view.result.current.state).toMatchObject({ kind: "available", refreshing: false });
    expect(getTeam).toHaveBeenCalledTimes(2);
  });
  it("shows an explicit refresh while keeping the current team available", async () => {
    vi.useFakeTimers();
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockImplementation(() => new Promise(() => undefined));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => view.result.current.refresh());
    expect(view.result.current.state).toMatchObject({ kind: "available", refreshing: true });
    expect(getTeam).toHaveBeenCalledTimes(2);
  });
  it("retains authorized team outputs when new evidence arrives while revalidating", async () => {
    vi.useFakeTimers();
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockImplementation(() => new Promise(() => undefined));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const access = (...ids: string[]) => JSON.stringify([runSpecId, ids]);
    const view = renderHook(({ artifacts }) => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: artifacts, onAuthorizationLost }),
      { initialProps: { artifacts: access("evidence_link:a") } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    view.rerender({ artifacts: access("evidence_link:a", "evidence_link:b") });
    expect(view.result.current.state.kind).toBe("available");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getTeam).toHaveBeenCalledTimes(2);
    expect(view.result.current.state).toMatchObject({ kind: "available", data: data() });
    view.rerender({ artifacts: access("evidence_link:b") });
    expect(view.result.current.state.kind).toBe("loading");
  });
  it("waits for a slow read, then polls three seconds after completion without overlap", async () => {
    vi.useFakeTimers();
    let finish!: (value: ReturnType<typeof data>) => void;
    const getTeam = vi.fn<GatewayTeamClient["getTeam"]>(() => new Promise<ReturnType<typeof data>>((resolve) => { finish = resolve; }));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(getTeam).toHaveBeenCalledOnce();
    await act(async () => finish(data()));
    expect(view.result.current.state.kind).toBe("available");
    await act(async () => { await vi.advanceTimersByTimeAsync(2999); });
    expect(getTeam).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(getTeam).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(getTeam.mock.calls.at(-1)?.[2]?.aborted).toBe(true);
  });
  it("clears private outputs on a same-sequence authorization change and ignores late data", async () => {
    vi.useFakeTimers();
    let finishOld!: (value: ReturnType<typeof data>) => void;
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; })).mockImplementation(() => new Promise(() => undefined));
    const client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    const view = renderHook(({ access }) => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: access, onAuthorizationLost }), { initialProps: { access: "a" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(view.result.current.state.kind).toBe("available");
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    view.rerender({ access: "b" });
    expect(view.result.current.state.kind).toBe("loading");
    await act(async () => finishOld(data()));
    expect(view.result.current.state.kind).toBe("loading");
  });
  it("suspends polling while hidden and revalidates when visible", async () => {
    vi.useFakeTimers();
    let hidden = false;
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
    const getTeam = vi.fn().mockResolvedValue(data()), client = clientFor(getTeam), onAuthorizationLost = vi.fn();
    renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    hidden = true;
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(getTeam).toHaveBeenCalledOnce();
    hidden = false;
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(0); });
    expect(getTeam).toHaveBeenCalledTimes(2);
  });
  it.each([401, 403, 404])("clears output and stops automatic retries on HTTP %s", async (status) => {
    vi.useFakeTimers();
    const getTeam = vi.fn().mockResolvedValueOnce(data()).mockRejectedValue(new GatewayHttpError(status));
    const onAuthorizationLost = vi.fn(), client = clientFor(getTeam);
    const view = renderHook(() => useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey: "a", onAuthorizationLost }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(view.result.current.state.kind).toBe(status === 404 ? "expired" : "unauthorized");
    expect(onAuthorizationLost).toHaveBeenCalledTimes(status === 404 ? 0 : 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(getTeam).toHaveBeenCalledTimes(2);
  });
});
