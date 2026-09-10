import type { GatewayExecutionProgress, GatewayExecutionTask, GatewayExecutionUpdate } from "./contracts";
import { GatewaySchemaError } from "./GatewaySchemaError";

function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new GatewaySchemaError();
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some((key) => !(key in result))) throw new GatewaySchemaError();
  return result;
}

function choice<const T extends readonly string[]>(value: unknown, choices: T): T[number] {
  if (typeof value !== "string" || !choices.includes(value)) throw new GatewaySchemaError();
  return value as T[number];
}

function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new GatewaySchemaError();
  return value;
}

function timestamp(value: unknown): string {
  if (typeof value !== "string") throw new GatewaySchemaError();
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) throw new GatewaySchemaError();
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number) as [number, number, number, number, number, number];
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > monthDays[month - 1]!
      || hour > 23 || minute > 59 || second > 59) throw new GatewaySchemaError();
  return value;
}

function uuid(value: unknown): string {
  if (typeof value !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new GatewaySchemaError();
  return value;
}

function task(value: unknown): GatewayExecutionTask {
  const row = record(value, ["task_id", "task_type", "status", "stage", "source_kind", "source_name", "providers", "attempt", "updated_at", "retry_at", "counts"]);
  const counts = record(row.counts, ["discovered", "fetched", "frozen", "admitted", "exceptions"]);
  if (!Array.isArray(row.providers) || row.providers.length > 3) throw new GatewaySchemaError();
  const sourceKind = choice(row.source_kind, ["intake_material", "external_sources"]);
  const sourceName = choice(row.source_name, ["用户提供材料", "合规外部来源"]);
  const providers = row.providers.map((provider) => choice(provider, ["gildata", "sse", "szse"]));
  if (new Set(providers).size !== providers.length
      || (sourceKind === "intake_material" ? sourceName !== "用户提供材料" || providers.length > 0 : sourceName !== "合规外部来源")) {
    throw new GatewaySchemaError();
  }
  return {
    taskId: uuid(row.task_id), taskType: choice(row.task_type, ["support", "contradict", "alternative", "alternative_explanation", "intake_material"]),
    status: choice(row.status, ["queued", "running", "retry_wait", "succeeded", "partial", "failed", "cancelled"]),
    stage: choice(row.stage, ["queued", "searching", "fetching", "freezing", "extracting", "admitting", "succeeded", "partial", "failed", "cancelled"]),
    sourceKind, sourceName, providers, attempt: integer(row.attempt), updatedAt: timestamp(row.updated_at),
    retryAt: row.retry_at === null ? null : timestamp(row.retry_at),
    counts: { discovered: integer(counts.discovered), fetched: integer(counts.fetched), frozen: integer(counts.frozen),
      admitted: integer(counts.admitted), exceptions: integer(counts.exceptions) },
  };
}

export function parseExecutionProgress(value: unknown): GatewayExecutionProgress {
  const row = record(value, ["projection_state", "stage", "updated_at", "reason_code", "next_action", "worker_state", "worker_last_seen_at", "worker_scope", "tasks"]);
  if (!Array.isArray(row.tasks) || row.tasks.length > 1000) throw new GatewaySchemaError();
  const tasks = row.tasks.map(task);
  if (new Set(tasks.map((item) => item.taskId)).size !== tasks.length) throw new GatewaySchemaError();
  const projectionState = choice(row.projection_state, ["available", "unavailable"]);
  if (projectionState === "unavailable" && tasks.length > 0) throw new GatewaySchemaError();
  return {
    projectionState,
    stage: choice(row.stage, ["queued", "planning", "retrieve", "analyze", "assessing", "conclude", "complete", "failed", "cancelled", "unknown"]),
    updatedAt: timestamp(row.updated_at),
    reasonCode: row.reason_code === null ? null : choice(row.reason_code,
      ["source_unavailable", "source_policy_blocked", "execution_state_unavailable", "native_execution_failed", "worker_unavailable", "research_subject_missing"]),
    nextAction: choice(row.next_action, ["wait_for_execution", "wait_for_retry", "check_execution", "review_result", "none"]),
    workerState: choice(row.worker_state, ["online", "offline", "unknown"]),
    workerLastSeenAt: row.worker_last_seen_at === null ? null : timestamp(row.worker_last_seen_at),
    workerScope: choice(row.worker_scope, ["acquisition_service"]), tasks,
  };
}

export function parseExecutionUpdate(value: unknown): GatewayExecutionUpdate {
  const row = record(value, ["run_spec_id", "execution"]);
  return { runSpecId: uuid(row.run_spec_id), execution: parseExecutionProgress(row.execution) };
}
