import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GatewayHttpError } from "@/gateway/HttpGatewayClient";
import { useTeamMutation, type TeamMutation } from "@/gateway/useTeamMutation";
import type { GatewayTeamClient } from "@/gateway/team";
import type { PendingPrincipal } from "@/gateway/pendingIdempotency";

const principal: PendingPrincipal = { tenantId: "team-a", subjectId: "alice", roles: ["researcher"] };
const baseProps = { conversationId: "conversation-1", runSpecId: "run-1", principal, onConfirmed: vi.fn(), onAuthorizationLost: vi.fn(), onInvalidated: vi.fn() };

function clientWith(overrides: Partial<GatewayTeamClient> = {}): GatewayTeamClient {
  return { getTeam: vi.fn(), sendTeamMessage: vi.fn(), commandTeam: vi.fn(), reviewTeam: vi.fn(), ...overrides };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("team mutation outcomes", () => {
  it("retries an unknown directed message with the exact original revision and key after a new poll", async () => {
    const sendTeamMessage = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValue({});
    const client = clientWith({ sendTeamMessage });
    const onConfirmed = vi.fn(), onAuthorizationLost = vi.fn(), onInvalidated = vi.fn();
    const view = renderHook(() => useTeamMutation({ client, conversationId: "c", runSpecId: "r", principal, onConfirmed, onAuthorizationLost, onInvalidated }));
    await act(async () => { await view.result.current.submit({ kind: "message", body: { recipient: "finance", text: "核对现金流", expected_revision: 1 } }); });
    expect(view.result.current.unknown).toBe(true);
    await act(async () => { await view.result.current.submit({ kind: "message", body: { recipient: "finance", text: "核对现金流", expected_revision: 2 } }); });
    expect(sendTeamMessage.mock.calls[1]![2]).toMatchObject({ expected_revision: 1, idempotencyKey: sendTeamMessage.mock.calls[0]![2].idempotencyKey });
    expect(onConfirmed).toHaveBeenCalledOnce();
  });

  it.each([400, 422, 409, 404, 401, 403])("classifies HTTP %s as a known rejection, not unknown delivery", async (status) => {
    const commandTeam = vi.fn().mockRejectedValue(new GatewayHttpError(status));
    const client = clientWith({ commandTeam });
    const onConfirmed = vi.fn(), onAuthorizationLost = vi.fn(), onInvalidated = vi.fn();
    const view = renderHook(() => useTeamMutation({ client, conversationId: "c", runSpecId: "r", principal, onConfirmed, onAuthorizationLost, onInvalidated }));
    await act(async () => { await view.result.current.submit({ kind: "command", body: { kind: "pause", expected_revision: 1 } }); });
    expect(view.result.current.unknown).toBe(false);
    expect(onAuthorizationLost).toHaveBeenCalledTimes([401, 403].includes(status) ? 1 : 0);
    expect(onInvalidated).toHaveBeenCalledTimes([404, 409].includes(status) ? 1 : 0);
    expect(onConfirmed).not.toHaveBeenCalled();
  });

  it.each([
    { label: "message", mutation: { kind: "message", body: { recipient: "finance", text: "核对现金流", expected_revision: 1 } } satisfies TeamMutation, method: "sendTeamMessage" as const },
    { label: "command", mutation: { kind: "command", body: { kind: "retry", task_id: "task-1", expected_revision: 1 } } satisfies TeamMutation, method: "commandTeam" as const },
    { label: "review", mutation: { kind: "review", body: { decision: "approved", comment: "已核对", output_ids: ["out-1", "out-2", "out-3", "out-4"], expected_revision: 1 } } satisfies TeamMutation, method: "reviewTeam" as const },
  ])("reuses a persisted unknown $label key and original revision after unmount", async ({ mutation, method }) => {
    const write = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce({});
    const client = clientWith({ [method]: write });
    const first = renderHook(() => useTeamMutation({ ...baseProps, client }));
    await act(async () => { await first.result.current.submit(mutation); });
    const firstKey = write.mock.calls[0]![2].idempotencyKey;
    first.unmount();

    const remounted = renderHook(() => useTeamMutation({ ...baseProps, client }));
    await act(async () => { await remounted.result.current.submit({ ...mutation, body: { ...mutation.body, expected_revision: 9 } } as TeamMutation); });

    expect(write).toHaveBeenCalledTimes(2);
    expect(write.mock.calls[1]![2]).toMatchObject({ idempotencyKey: firstKey, expected_revision: 1 });
    expect(remounted.result.current.notice).toContain("请求已确认");
  });

  it("isolates pending team messages by principal", async () => {
    const sendTeamMessage = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce({});
    const client = clientWith({ sendTeamMessage });
    const first = renderHook(() => useTeamMutation({ ...baseProps, client }));
    await act(async () => { await first.result.current.submit({ kind: "message", body: { recipient: "finance", text: "核对现金流", expected_revision: 1 } }); });
    const firstKey = sendTeamMessage.mock.calls[0]![2].idempotencyKey;
    first.unmount();

    const otherPrincipal = renderHook(() => useTeamMutation({ ...baseProps, principal: { ...principal, subjectId: "bob" }, client }));
    await act(async () => { await otherPrincipal.result.current.submit({ kind: "message", body: { recipient: "finance", text: "核对现金流", expected_revision: 9 } }); });

    expect(sendTeamMessage.mock.calls[1]![2]).toMatchObject({ expected_revision: 9 });
    expect(sendTeamMessage.mock.calls[1]![2].idempotencyKey).not.toBe(firstKey);
  });

  it("clears persisted team mutations after a known rejection", async () => {
    const commandTeam = vi.fn().mockRejectedValueOnce(new GatewayHttpError(422)).mockResolvedValueOnce({});
    const client = clientWith({ commandTeam });
    const view = renderHook(() => useTeamMutation({ ...baseProps, client }));
    await act(async () => { await view.result.current.submit({ kind: "command", body: { kind: "pause", expected_revision: 1 } }); });
    const rejectedKey = commandTeam.mock.calls[0]![2].idempotencyKey;

    await act(async () => { await view.result.current.submit({ kind: "command", body: { kind: "pause", expected_revision: 2 } }); });

    expect(commandTeam.mock.calls[1]![2]).toMatchObject({ expected_revision: 2 });
    expect(commandTeam.mock.calls[1]![2].idempotencyKey).not.toBe(rejectedKey);
  });

  it("warns honestly when storage is unavailable and the delivery outcome is unknown", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const sendTeamMessage = vi.fn().mockRejectedValue(new TypeError("offline"));
    const client = clientWith({ sendTeamMessage });
    const view = renderHook(() => useTeamMutation({ ...baseProps, client }));

    await act(async () => { await view.result.current.submit({ kind: "message", body: { recipient: "team", text: "核对风险", expected_revision: 1 } }); });

    expect(view.result.current.notice).toContain("浏览器无法保存待确认提交标识");
  });

  it("does not submit when the hook unmounts before preparation finishes", async () => {
    const subtle = globalThis.crypto.subtle;
    const originalDigest = subtle.digest.bind(subtle);
    let digestCalls = 0;
    let releaseDigest!: () => void;
    const digestGate = new Promise<void>((resolve) => { releaseDigest = resolve; });
    vi.spyOn(subtle, "digest").mockImplementation(async (...args) => {
      digestCalls += 1;
      await digestGate;
      return originalDigest(...args);
    });
    const sendTeamMessage = vi.fn().mockResolvedValue({});
    const client = clientWith({ sendTeamMessage });
    const view = renderHook(() => useTeamMutation({ ...baseProps, client }));

    let submit: Promise<boolean>;
    act(() => { submit = view.result.current.submit({ kind: "message", body: { recipient: "team", text: "离开页面", expected_revision: 1 } }); });
    await waitForCondition(() => digestCalls > 0);
    view.unmount();
    releaseDigest();

    expect(await submit!).toBe(false);
    expect(sendTeamMessage).not.toHaveBeenCalled();
    expect(sessionStorage.length).toBe(0);
  });

  it("fences callbacks from an old scope after the selected run changes", async () => {
    let confirmSend!: () => void;
    const sendTeamMessage = vi.fn().mockImplementation(() => new Promise<void>((resolve) => { confirmSend = resolve; }));
    const client = clientWith({ sendTeamMessage });
    const onConfirmed = vi.fn();
    const view = renderHook(
      ({ runSpecId }) => useTeamMutation({ ...baseProps, client, runSpecId, onConfirmed }),
      { initialProps: { runSpecId: "run-1" } },
    );

    let submit: Promise<boolean>;
    await act(async () => {
      submit = view.result.current.submit({ kind: "message", body: { recipient: "team", text: "旧轮次", expected_revision: 1 } });
      await waitForCondition(() => sendTeamMessage.mock.calls.length === 1);
    });
    await act(async () => {
      view.rerender({ runSpecId: "run-2" });
    });
    await act(async () => {
      confirmSend();
      await submit!;
    });

    expect(onConfirmed).not.toHaveBeenCalled();
    expect(view.result.current.busy).toBe(false);
  });
});

async function waitForCondition(condition: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error("condition was not met");
}
