import { useCallback, useEffect, useRef, useState } from "react";

import { GatewayHttpError } from "./HttpGatewayClient";
import { applyExecutionProgress, applyRoleEvent, needsAuthoritativeSnapshot } from "./conversationProjection";
import type {
  GatewayConnectionState,
  GatewayConversationClient,
  GatewayConversationSnapshot,
  GatewayTeamProgress,
} from "./contracts";

export type GatewayConversationIssue =
  | "none"
  | "auth_required"
  | "unavailable"
  | "projection_unavailable"
  | "access_revoked";

type UseGatewayConversationOptions = {
  client: GatewayConversationClient;
  conversationId: string;
  onAccessRevoked?: () => void;
  onAuthorizationRequired?: () => void;
};

export type GatewayConversationState = {
  snapshot: GatewayConversationSnapshot | null;
  connection: GatewayConnectionState;
  issue: GatewayConversationIssue;
  teamProgressByRun: Record<string, GatewayTeamProgress>;
  retry: () => void;
};

/**
 * Owns snapshot replacement and one fetch-SSE observer.  A new snapshot is the
 * source of truth for native run state; streamed role events update only the
 * safe role projection while observation is live.
 */
export function useGatewayConversation({
  client,
  conversationId,
  onAccessRevoked,
  onAuthorizationRequired,
}: UseGatewayConversationOptions): GatewayConversationState {
  const [snapshot, setSnapshot] = useState<GatewayConversationSnapshot | null>(null);
  const [connection, setConnection] = useState<GatewayConnectionState>("connecting");
  const [issue, setIssue] = useState<GatewayConversationIssue>("none");
  const [teamProgressByRun, setTeamProgressByRun] = useState<Record<string, GatewayTeamProgress>>({});
  const [reloadVersion, setReloadVersion] = useState(0);
  const observedContextRef = useRef({ client, conversationId });
  const recoveryRef = useRef({ conversationId, used: false });
  const accessRevokedRef = useRef(onAccessRevoked);
  const authorizationRequiredRef = useRef(onAuthorizationRequired);

  useEffect(() => {
    accessRevokedRef.current = onAccessRevoked;
    authorizationRequiredRef.current = onAuthorizationRequired;
  }, [onAccessRevoked, onAuthorizationRequired]);

  useEffect(() => {
    if (recoveryRef.current.conversationId !== conversationId) {
      recoveryRef.current = { conversationId, used: false };
    }
  }, [conversationId]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const contextChanged = observedContextRef.current.client !== client || observedContextRef.current.conversationId !== conversationId;
    observedContextRef.current = { client, conversationId };
    if (contextChanged) {
      setSnapshot(null);
      setTeamProgressByRun({});
      setConnection("connecting");
    }
    setIssue("none");

    function requestReplacementSnapshot(clearPrivateContent = false): void {
      if (!active || controller.signal.aborted) return;
      controller.abort();
      // Progress and cursor recovery revalidate in place. Only an access change
      // invalidates displayed content before the replacement request returns.
      if (clearPrivateContent) {
        setSnapshot(null);
        setTeamProgressByRun({});
      }
      setConnection("reconnecting");
      setReloadVersion((version) => version + 1);
    }

    async function observe(): Promise<void> {
      try {
        const loadedSnapshot = await client.getSnapshot(
          conversationId,
          controller.signal,
        );
        if (!active || controller.signal.aborted) return;
        setSnapshot(loadedSnapshot);

        await client.streamEvents({
          conversationId,
          afterSequence: loadedSnapshot.latestSequence,
          signal: controller.signal,
          onConnection: (nextConnection) => {
            if (active && !controller.signal.aborted) {
              setConnection(nextConnection);
            }
          },
          onEvent: (event) => {
            if (!active || controller.signal.aborted) return;
            setSnapshot((current) => current ? applyRoleEvent(current, event) : current);
            if (needsAuthoritativeSnapshot(event)) {
              requestReplacementSnapshot();
            }
          },
          onExecutionProgress: (progress) => {
            if (!active || controller.signal.aborted) return;
            setSnapshot((current) => current ? applyExecutionProgress(current, progress) : current);
          },
          onTeamProgress: (progress) => {
            if (!active || controller.signal.aborted) return;
            setTeamProgressByRun((current) => {
              const previous = current[progress.runSpecId];
              if (previous && previous.revision >= progress.revision && previous.eventSequence >= progress.eventSequence) return current;
              return { ...current, [progress.runSpecId]: progress };
            });
          },
          onSnapshotRequired: (required) => requestReplacementSnapshot(required.reason !== "retention_gap"),
          onAccessRevoked: () => {
            if (!active || controller.signal.aborted) return;
            controller.abort();
            setSnapshot(null);
            setTeamProgressByRun({});
            setIssue("access_revoked");
            setConnection("access_revoked");
            accessRevokedRef.current?.();
          },
          onStreamError: (code) => {
            if (!active || controller.signal.aborted) return;
            if (code === "projection_unavailable") {
              setIssue("projection_unavailable");
              setConnection("paused");
            }
          },
        });
      } catch (error) {
        if (!active || controller.signal.aborted) return;
        if (error instanceof GatewayHttpError && error.status === 422) {
          if (!recoveryRef.current.used) {
            recoveryRef.current.used = true;
            requestReplacementSnapshot();
            return;
          }
          setIssue("unavailable");
          setConnection("paused");
          return;
        }
        if (
          error instanceof GatewayHttpError &&
          (error.status === 401 || error.status === 403)
        ) {
          controller.abort();
          setSnapshot(null);
          setTeamProgressByRun({});
          setIssue("auth_required");
          setConnection("paused");
          authorizationRequiredRef.current?.();
          return;
        }
        if (error instanceof GatewayHttpError && error.status === 404) {
          controller.abort();
          setSnapshot(null);
          setTeamProgressByRun({});
        }
        setIssue("unavailable");
        setConnection("paused");
      }
    }

    void observe();
    return () => {
      active = false;
      controller.abort();
    };
  }, [client, conversationId, reloadVersion]);

  const retry = useCallback(() => {
    recoveryRef.current = { conversationId, used: false };
    setConnection("reconnecting");
    setIssue("none");
    setReloadVersion((version) => version + 1);
  }, [conversationId]);

  // Identity and route parameters change before effects run. Never expose the
  // previous context's snapshot or progress for that intervening render.
  const sameContext = observedContextRef.current.client === client && observedContextRef.current.conversationId === conversationId;
  return { snapshot: sameContext && snapshot?.conversationId === conversationId ? snapshot : null, connection, issue,
    teamProgressByRun: sameContext ? teamProgressByRun : {}, retry };
}
