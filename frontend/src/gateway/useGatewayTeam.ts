import { useCallback, useEffect, useRef, useState } from "react";
import { GatewayHttpError, GatewaySchemaError, GatewayTimeoutError } from "./HttpGatewayClient";
import { hasTeamClient, type GatewayTeam, type GatewayTeamClient } from "./team";

export type TeamReadState = { kind: "available"; data: GatewayTeam; refreshing: boolean; stale?: boolean } |
  { kind: "loading" | "unsupported" | "expired" | "unauthorized" } | { kind: "error"; schema: boolean };
type Props = { client: Partial<GatewayTeamClient>; conversationId: string; runSpecId: string; authorizedArtifactsKey: string; onAuthorizationLost: () => void; enabled?: boolean };

/** One authorized read at a time; completion, visibility and explicit refresh schedule the next. */
export function useGatewayTeam({ client, conversationId, runSpecId, authorizedArtifactsKey, onAuthorizationLost, enabled = true }: Props) {
  const [invalidation, setInvalidation] = useState(0);
  const key = JSON.stringify([conversationId, runSpecId, authorizedArtifactsKey, invalidation]);
  const [stored, setStored] = useState<{ client: Props["client"]; key: string; state: TeamReadState }>({ client, key, state: { kind: "loading" } });
  const authorizationLost = useRef(onAuthorizationLost);
  authorizationLost.current = onAuthorizationLost;
  const schedule = useRef<() => void>(() => undefined);
  const refresh = useCallback(() => schedule.current(), []);
  const invalidate = useCallback(() => setInvalidation((revision) => revision + 1), []);

  useEffect(() => {
    if (!enabled) {
      setStored({ client, key, state: { kind: "loading" } });
      return;
    }
    if (!hasTeamClient(client)) {
      setStored({ client, key, state: { kind: "unsupported" } });
      return;
    }
    let active = true, inFlight = false, queued = false, stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    function request(showRefreshing = false) {
      if (!active || document.hidden) return;
      if (inFlight) { queued = true; return; }
      inFlight = true;
      queued = false;
      stopped = false;
      const current = new AbortController();
      controller = current;
      setStored((previous) => previous.client === client && retainsAuthorization(previous.key, key) && previous.state.kind === "available"
        ? { client, key, state: { ...previous.state, refreshing: showRefreshing } } : { client, key, state: { kind: "loading" } });
      void Promise.resolve().then(() => current.signal.aborted ? undefined : client.getTeam!(conversationId, runSpecId, current.signal))
        .then((data) => {
          if (!data || !active || current.signal.aborted) return;
          setStored((previous) => previous.client === client && previous.key === key && previous.state.kind === "available"
            && (previous.state.data.revision > data.revision || previous.state.data.event_sequence > data.event_sequence)
            ? { client, key, state: { ...previous.state, refreshing: false } }
            : { client, key, state: { kind: "available", data, refreshing: false } });
        }).catch((error: unknown) => {
          if (!active || current.signal.aborted) return;
          if (error instanceof GatewayHttpError && [401, 403, 404].includes(error.status)) {
            stopped = true;
            setStored({ client, key, state: { kind: error.status === 404 ? "expired" : "unauthorized" } });
            if (error.status !== 404) authorizationLost.current();
          } else setStored((previous) => isTransientReadFailure(error) && previous.client === client && previous.key === key && previous.state.kind === "available"
            ? { client, key, state: { ...previous.state, refreshing: false, stale: true } }
            : { client, key, state: { kind: "error", schema: error instanceof GatewaySchemaError } });
        }).finally(() => {
          inFlight = false;
          if (active && !stopped && !document.hidden) timer = window.setTimeout(() => request(queued), queued ? 0 : 3000);
        });
    }
    schedule.current = () => {
      window.clearTimeout(timer);
      if (inFlight) queued = true;
      else request(true);
    };
    const visibilityChanged = () => {
      window.clearTimeout(timer);
      if (document.hidden) controller?.abort();
      else if (!stopped) schedule.current();
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    timer = window.setTimeout(request, 0);
    return () => {
      active = false;
      controller?.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
      schedule.current = () => undefined;
    };
  }, [client, conversationId, runSpecId, key, enabled]);

  const state: TeamReadState = !enabled ? { kind: "loading" } : !hasTeamClient(client) ? { kind: "unsupported" } : stored.client === client && retainsAuthorization(stored.key, key) ? stored.state : { kind: "loading" };
  return { state, refresh, invalidate };
}

function isTransientReadFailure(error: unknown): boolean {
  if (error instanceof GatewayHttpError) return [408, 429].includes(error.status) || (error.status >= 500 && error.status < 600);
  return error instanceof TypeError || error instanceof GatewayTimeoutError
    || (error instanceof DOMException && ["NetworkError", "TimeoutError"].includes(error.name));
}

/** Newly authorized evidence keeps existing outputs safe; any loss clears them immediately. */
function retainsAuthorization(previousKey: string, nextKey: string): boolean {
  if (previousKey === nextKey) return true;
  const [previousConversation, previousRun, previousArtifacts, previousInvalidation] = JSON.parse(previousKey);
  const [nextConversation, nextRun, nextArtifacts, nextInvalidation] = JSON.parse(nextKey);
  if (previousConversation !== nextConversation || previousRun !== nextRun || previousInvalidation !== nextInvalidation) return false;
  try {
    const previous: unknown = JSON.parse(previousArtifacts);
    const next: unknown = JSON.parse(nextArtifacts);
    if (!Array.isArray(previous) || previous.length !== 2 || previous[0] !== previousRun
      || !Array.isArray(next) || next.length !== 2 || next[0] !== nextRun
      || !Array.isArray(previous[1]) || !previous[1].every((id: unknown) => typeof id === "string")
      || !Array.isArray(next[1]) || !next[1].every((id: unknown) => typeof id === "string")) return false;
    const authorized = new Set(next[1]);
    return previous[1].every((id: string) => authorized.has(id));
  } catch {
    // Opaque or unknown authorization keys cannot establish retained access.
    return false;
  }
}
