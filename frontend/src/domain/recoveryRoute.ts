/**
 * The only URL state that is allowed to target a recovery action.  Keeping it
 * deliberately small prevents a stale or malformed link from silently
 * attaching newly supplied text to a different frozen document.
 */
export interface RecoveryRouteState {
  documentId: string;
  reason: "parse_failed" | "no_claims";
}

const RECOVERY_REASONS = new Set<RecoveryRouteState["reason"]>(["parse_failed", "no_claims"]);

export function decodeRecoveryRouteState(params: URLSearchParams): RecoveryRouteState | null {
  if (params.get("recovery") !== "continue") return null;
  const documentId = params.get("document")?.trim();
  const rawReason = params.get("recovery_reason");
  if (!documentId || !rawReason || !RECOVERY_REASONS.has(rawReason as RecoveryRouteState["reason"])) return null;
  return { documentId, reason: rawReason as RecoveryRouteState["reason"] };
}

export function encodeRecoveryRouteState(state: RecoveryRouteState): URLSearchParams {
  const params = new URLSearchParams();
  params.set("document", state.documentId);
  params.set("recovery", "continue");
  params.set("recovery_reason", state.reason);
  return params;
}
