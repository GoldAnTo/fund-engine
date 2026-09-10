import type {
  GatewayConversationSnapshot,
  GatewayExecutionUpdate,
  GatewayRoleEvent,
  GatewayRoleProjection,
  GatewayRoleStatus,
} from "./contracts";

const terminalRoleStatuses = new Set<GatewayRoleStatus>([
  "completed",
  "failed",
  "cancelled",
]);

/** Full authorized replacement: withdrawn source/task details must not linger. */
export function applyExecutionProgress(snapshot: GatewayConversationSnapshot, update: GatewayExecutionUpdate): GatewayConversationSnapshot {
  if (!snapshot.runs.some((run) => run.runSpecId === update.runSpecId)) return snapshot;
  return { ...snapshot, runs: snapshot.runs.map((run) => run.runSpecId === update.runSpecId
    ? { ...run, execution: update.execution } : run) };
}

/**
 * Incorporate a safe stream event into the locally held projection.  Native
 * run state intentionally remains snapshot-owned: a role event is not proof
 * that a native run has reached a terminal state.
 */
export function applyRoleEvent(
  snapshot: GatewayConversationSnapshot,
  event: GatewayRoleEvent,
): GatewayConversationSnapshot {
  if (snapshot.events.some((existing) => existing.sequence === event.sequence)) {
    return snapshot;
  }

  const roles = event.status === null
    ? snapshot.roles
    : updateRoleProjection(snapshot.roles, event);
  const events = [...snapshot.events, event].sort(
    (left, right) => left.sequence - right.sequence,
  );

  return {
    ...snapshot,
    latestSequence: Math.max(snapshot.latestSequence, event.sequence),
    roles,
    events,
  };
}

export function needsAuthoritativeSnapshot(event: GatewayRoleEvent): boolean {
  return event.status !== null && terminalRoleStatuses.has(event.status);
}

function updateRoleProjection(
  roles: GatewayRoleProjection[],
  event: GatewayRoleEvent,
): GatewayRoleProjection[] {
  const index = roles.findIndex(
    (role) => role.runSpecId === event.runSpecId && role.role === event.role,
  );
  if (index === -1) {
    return [
      ...roles,
      {
        runSpecId: event.runSpecId,
        role: event.role,
        status: event.status as GatewayRoleStatus,
        latestSequence: event.sequence,
      },
    ];
  }

  return roles.map((role, roleIndex) => {
    if (roleIndex !== index) return role;
    return {
      ...role,
      status: event.status as GatewayRoleStatus,
      latestSequence: Math.max(role.latestSequence, event.sequence),
    };
  });
}
