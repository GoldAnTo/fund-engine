import { useEffect, useRef, useState } from "react";
import { GatewayHttpError } from "./HttpGatewayClient";
import { clearPendingSubmission, hasPendingSubmissions, preparePendingSubmission, type PendingPrincipal, type PendingOperation } from "./pendingIdempotency";
import { hasTeamClient, type GatewayTeamClient, type TeamCommandInput, type TeamMessageInput, type TeamReviewInput } from "./team";

type Payload<T> = Omit<T, "idempotencyKey" | "signal">;
export type TeamMutation = { kind: "message"; body: Payload<TeamMessageInput> } |
  { kind: "command"; body: Payload<TeamCommandInput> } | { kind: "review"; body: Payload<TeamReviewInput> };
type Pending = { intent: string; mutation: TeamMutation; idempotencyKey: string; storageKey: string; storageNotice: string };
type Props = { client: Partial<GatewayTeamClient>; conversationId: string; runSpecId: string; principal: PendingPrincipal; onConfirmed: () => void; onAuthorizationLost: () => void; onInvalidated: () => void };

/** Keeps the exact original revision and body when the server outcome is unknown. */
export function useTeamMutation({ client, conversationId, runSpecId, principal, onConfirmed, onAuthorizationLost, onInvalidated }: Props) {
  const pending = useRef<Pending | null>(null);
  const controller = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const scopeToken = useRef(1);
  const scopeEffectMounted = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [unknown, setUnknown] = useState(false);
  const scopeKey = JSON.stringify({ conversationId, runSpecId, principal: { ...principal, roles: [...principal.roles].sort() } });
  const isCurrent = (token: number, current: AbortController): boolean =>
    scopeToken.current === token && controller.current === current && !current.signal.aborted;

  useEffect(() => {
    if (scopeEffectMounted.current) {
      controller.current?.abort();
      controller.current = null;
      pending.current = null;
      inFlight.current = false;
      setBusy(false);
      setNotice("");
      setUnknown(false);
    }
    scopeEffectMounted.current = true;
    const token = scopeToken.current;
    return () => {
      if (scopeToken.current === token) scopeToken.current += 1;
      controller.current?.abort();
      controller.current = null;
      pending.current = null;
      inFlight.current = false;
    };
  }, [scopeKey]);

  useEffect(() => {
    let live = true;
    void hasPendingSubmissions({ principal, scope: { type: "team", conversationId, runSpecId }, operations: ["team.message", "team.command", "team.review"] })
      .then((check) => { if (live && check.notice) setNotice(check.notice); });
    return () => { live = false; };
  }, [conversationId, principal, runSpecId]);

  async function performPrepared(request: Pending, current: AbortController, token: number): Promise<boolean> {
    if (!hasTeamClient(client) || current.signal.aborted || scopeToken.current !== token) return false;
    if (controller.current && controller.current !== current) controller.current.abort();
    controller.current = current;
    pending.current = request;
    setBusy(true); setNotice(""); setUnknown(false);
    const base = { idempotencyKey: request.idempotencyKey, signal: current.signal };
    try {
      const operation = request.mutation;
      if (operation.kind === "message") await client.sendTeamMessage(conversationId, runSpecId, { ...operation.body, ...base });
      else if (operation.kind === "command") await client.commandTeam(conversationId, runSpecId, { ...operation.body, ...base });
      else await client.reviewTeam(conversationId, runSpecId, { ...operation.body, ...base });
      if (!isCurrent(token, current)) return false;
      pending.current = null;
      await clearPendingSubmission(request.storageKey);
      if (!isCurrent(token, current)) return false;
      setNotice("请求已确认，正在读取团队最新状态。");
      onConfirmed();
      return true;
    } catch (error) {
      if (!isCurrent(token, current)) return false;
      if (error instanceof GatewayHttpError && [400, 401, 403, 404, 409, 422].includes(error.status)) {
        pending.current = null;
        await clearPendingSubmission(request.storageKey);
        if (!isCurrent(token, current)) return false;
        if (error.status === 401 || error.status === 403) { onAuthorizationLost(); return false; }
        if (error.status === 404) { setNotice("本轮团队或引用已失效，已清除旧内容。请重新读取。"); onInvalidated(); }
        else if (error.status === 409) { setNotice("团队版本或提交记录已变化，本次请求未获确认。请核对最新版本后再提交。"); onInvalidated(); }
        else setNotice("请求未通过服务端校验，未执行本次操作。请核对内容与字符限制后重新提交。");
      } else {
        setUnknown(true);
        setNotice(`无法确认本次提交是否已送达。重试将沿用原始内容、收件人、版本与提交标识。刷新不会自动重发；请先核对已确认请求历史。${request.storageNotice ? ` ${request.storageNotice}` : ""}`);
      }
      return false;
    } finally {
      if (scopeToken.current === token && controller.current === current) {
        inFlight.current = false;
        controller.current = null;
        setBusy(false);
      }
    }
  }

  async function perform(request: Pending): Promise<boolean> {
    if (inFlight.current || !hasTeamClient(client)) return false;
    const current = new AbortController();
    inFlight.current = true;
    return performPrepared(request, current, scopeToken.current);
  }

  async function submit(mutation: TeamMutation) {
    // A server read may advance the visible revision after a lost response.
    // An unchanged user intent must retry the exact original request first.
    if (inFlight.current || !hasTeamClient(client)) return false;
    const current = new AbortController();
    const token = scopeToken.current;
    inFlight.current = true;
    controller.current?.abort();
    controller.current = current;
    setBusy(true);
    setNotice("");
    setUnknown(false);
    const bodyForDigest = { ...mutation.body, expected_revision: undefined };
    const intent = JSON.stringify({ kind: mutation.kind, body: bodyForDigest });
    try {
      if (pending.current?.intent === intent) return await performPrepared(pending.current, current, token);
      const prepared = await preparePendingSubmission({
        principal,
        scope: { type: "team", conversationId, runSpecId },
        operation: operationFor(mutation.kind),
        body: bodyForDigest,
        expectedRevision: mutation.body.expected_revision,
        signal: current.signal,
      });
      if (!isCurrent(token, current)) return false;
      const request = { intent, mutation: withExpectedRevision(mutation, prepared.expectedRevision ?? mutation.body.expected_revision), idempotencyKey: prepared.idempotencyKey, storageKey: prepared.storageKey, storageNotice: prepared.notice };
      return await performPrepared(request, current, token);
    } catch {
      if (isCurrent(token, current)) {
        return false;
      }
      return false;
    } finally {
      if (scopeToken.current === token && controller.current === current) {
        inFlight.current = false;
        controller.current = null;
        setBusy(false);
      }
    }
  }
  return { busy, notice, unknown, submit, retry: () => pending.current ? perform(pending.current) : Promise.resolve(false) };
}
export type TeamMutationController = ReturnType<typeof useTeamMutation>;

function operationFor(kind: TeamMutation["kind"]): PendingOperation {
  return kind === "message" ? "team.message" : kind === "command" ? "team.command" : "team.review";
}

function withExpectedRevision(mutation: TeamMutation, expectedRevision: number): TeamMutation {
  if (mutation.kind === "message") return { kind: "message", body: { ...mutation.body, expected_revision: expectedRevision } };
  if (mutation.kind === "command") return { kind: "command", body: { ...mutation.body, expected_revision: expectedRevision } };
  return { kind: "review", body: { ...mutation.body, expected_revision: expectedRevision } };
}
