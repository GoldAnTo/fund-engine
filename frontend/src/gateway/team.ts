import { GatewaySchemaError } from "./GatewaySchemaError";

export const professionalRoles = ["industry", "finance", "strategy", "quality"] as const;
export type ProfessionalRole = typeof professionalRoles[number];
export const professionalRoleLabels: Record<ProfessionalRole, string> = { industry: "产业分析师", finance: "财务分析师", strategy: "策略分析师", quality: "AI 质控审核员" };
export type TeamCitation = { evidence_link_id: string; quote: string };
export type TeamFinding = { statement: string; basis: "supported" | "contradicted" | "uncertain"; citations: TeamCitation[] };
export type TeamCheck = { kind: "citations" | "periods" | "units" | "source_independence" | "counter_evidence" | "completeness"; status: "pass" | "warning" | "blocker"; detail: string; task_ids: string[] };
export type TeamOutput = {
  id: string; content: { summary: string; findings: TeamFinding[]; gaps: string[]; checks: TeamCheck[]; limitations: string[] };
  evidence_ids: string[]; dependency_output_ids: string[]; created_at: string;
};
export type TeamAttempt = {
  call_id: string; attempt: number; operation: string; provider: string; requested_model: string; model: string | null;
  provider_request_id: string | null; finish_reason: string | null; prompt_tokens: number | null; completion_tokens: number | null;
  total_tokens: number | null; usage_status: "known" | "partial" | "unknown"; latency_ms: number; outcome: string; retryable: boolean; retry_delay_seconds: number | null; repaired?: boolean;
};
export type TeamTask = {
  id: string; role: ProfessionalRole; revision: number; status: "queued" | "running" | "blocked" | "succeeded" | "failed" | "cancelled";
  reason_code: string | null; attempt: number; instruction: string; dependency_ids: string[]; created_at: string; updated_at: string;
  output_state: "none" | "available" | "withheld"; output: TeamOutput | null; attempts: TeamAttempt[];
};
export type TeamReview = { id: string; revision: number; decision: "approved" | "changes_requested"; comment: string; reviewed_by: string; output_ids: string[]; created_at: string };
export type GatewayTeam = { conversation_id: string; run_spec_id: string; status: "not_started" | "active" | "paused" | "cancelled"; revision: number; event_sequence: number; tasks: TeamTask[]; reviews: TeamReview[] };
export type TeamReceipt = { request_id: string; conversation_id: string; run_spec_id: string; revision: number; task_ids: string[] };
type RequestBase = { expected_revision: number; idempotencyKey: string; signal?: AbortSignal };
export type TeamMessageInput = RequestBase & { text: string; recipient: "team" | ProfessionalRole };
export type TeamCommandInput = RequestBase & { kind: "start" | "pause" | "resume" | "cancel" | "retry"; task_id?: string };
export type TeamReviewInput = RequestBase & { output_ids: string[]; decision: "approved" | "changes_requested"; comment: string };
export interface GatewayTeamClient {
  getTeam(conversationId: string, runSpecId: string, signal?: AbortSignal): Promise<GatewayTeam>;
  sendTeamMessage(conversationId: string, runSpecId: string, input: TeamMessageInput): Promise<TeamReceipt>;
  commandTeam(conversationId: string, runSpecId: string, input: TeamCommandInput): Promise<TeamReceipt>;
  reviewTeam(conversationId: string, runSpecId: string, input: TeamReviewInput): Promise<TeamReceipt>;
}
export function hasTeamClient(client: Partial<GatewayTeamClient>): client is GatewayTeamClient {
  return typeof client.getTeam === "function" && typeof client.sendTeamMessage === "function" && typeof client.commandTeam === "function" && typeof client.reviewTeam === "function";
}
export function tasksForRevision(team: GatewayTeam, revision: number): TeamTask[] {
  return professionalRoles.flatMap((role) => {
    const latest = team.tasks.filter((task) => task.role === role && task.revision <= revision)
      .sort((a, b) => b.revision - a.revision || b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))[0];
    return latest ? [latest] : [];
  });
}

type Scope = { conversationId: string; runSpecId: string };
function invalid(): never { throw new GatewaySchemaError("Professional team payload is invalid or belongs to another scope"); }
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== keys.length || keys.some((key) => !Object.prototype.hasOwnProperty.call(item, key))) return invalid();
  return item;
}
function string(value: unknown, max = 20000): string { if (typeof value !== "string" || value.length > max) return invalid(); return value; }
function nullableString(value: unknown): string | null { return value === null ? null : string(value); }
function id(value: unknown): string { const result = string(value, 36); return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(result) ? result : invalid(); }
function integer(value: unknown, min = 0): number { return typeof value === "number" && Number.isSafeInteger(value) && value >= min ? value : invalid(); }
function number(value: unknown): number { return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : invalid(); }
function nullableNumber(value: unknown, whole = false): number | null { return value === null ? null : whole ? integer(value) : number(value); }
function boolean(value: unknown): boolean { return typeof value === "boolean" ? value : invalid(); }
function enumValue<T extends string>(value: unknown, choices: readonly T[]): T { return choices.includes(value as T) ? value as T : invalid(); }
function array<T>(value: unknown, parse: (entry: unknown) => T, max = 4000): T[] { if (!Array.isArray(value) || value.length > max) return invalid(); return value.map(parse); }
function ids(value: unknown, max = 4000): string[] { const result = array(value, id, max); if (new Set(result).size !== result.length) return invalid(); return result; }
function date(value: unknown): string { const result = string(value, 50); return /^\d{4}-\d{2}-\d{2}T/.test(result) && Number.isFinite(Date.parse(result)) ? result : invalid(); }
function scope(item: Record<string, unknown>, expected: Scope) {
  const conversation_id = id(item.conversation_id), run_spec_id = id(item.run_spec_id);
  if (conversation_id !== expected.conversationId || run_spec_id !== expected.runSpecId) return invalid();
  return { conversation_id, run_spec_id };
}
function output(value: unknown): TeamOutput {
  const item = record(value, ["id", "content", "evidence_ids", "dependency_output_ids", "created_at"]);
  const content = record(item.content, ["summary", "findings", "gaps", "checks", "limitations"]);
  const evidence_ids = ids(item.evidence_ids);
  const findings = array(content.findings, (raw): TeamFinding => {
    const finding = record(raw, ["statement", "basis", "citations"]);
    const citations = array(finding.citations, (rawCitation): TeamCitation => {
      const citation = record(rawCitation, ["evidence_link_id", "quote"]);
      const evidence_link_id = id(citation.evidence_link_id);
      if (!evidence_ids.includes(evidence_link_id)) return invalid();
      return { evidence_link_id, quote: string(citation.quote, 8000) };
    }, 20);
    const basis = enumValue(finding.basis, ["supported", "contradicted", "uncertain"]);
    if (basis !== "uncertain" && !citations.length) return invalid();
    if (new Set(citations.map((entry) => entry.evidence_link_id)).size !== citations.length) return invalid();
    return { statement: string(finding.statement, 4000), basis, citations };
  }, 24);
  const checks = array(content.checks, (raw): TeamCheck => {
    const check = record(raw, ["kind", "status", "detail", "task_ids"]);
    return { kind: enumValue(check.kind, ["citations", "periods", "units", "source_independence", "counter_evidence", "completeness"]),
      status: enumValue(check.status, ["pass", "warning", "blocker"]), detail: string(check.detail, 2000), task_ids: ids(check.task_ids, 10) };
  }, 20);
  return { id: id(item.id), content: { summary: string(content.summary, 4000), findings, checks,
    gaps: array(content.gaps, (entry) => string(entry, 2000), 24), limitations: array(content.limitations, (entry) => string(entry, 2000), 16) },
    evidence_ids, dependency_output_ids: ids(item.dependency_output_ids), created_at: date(item.created_at) };
}
function attempt(value: unknown): TeamAttempt {
  const baseKeys = ["call_id", "attempt", "operation", "provider", "requested_model", "model", "provider_request_id", "finish_reason", "prompt_tokens", "completion_tokens", "total_tokens", "usage_status", "latency_ms", "outcome", "retryable", "retry_delay_seconds"];
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const raw = value as Record<string, unknown>;
  const hasRepaired = Object.prototype.hasOwnProperty.call(raw, "repaired");
  const item = record(raw, hasRepaired ? [...baseKeys, "repaired"] : baseKeys);
  return { call_id: string(item.call_id), attempt: integer(item.attempt, 1), operation: string(item.operation), provider: string(item.provider), requested_model: string(item.requested_model),
    model: nullableString(item.model), provider_request_id: nullableString(item.provider_request_id), finish_reason: nullableString(item.finish_reason),
    prompt_tokens: nullableNumber(item.prompt_tokens, true), completion_tokens: nullableNumber(item.completion_tokens, true), total_tokens: nullableNumber(item.total_tokens, true),
    usage_status: enumValue(item.usage_status, ["known", "partial", "unknown"]), latency_ms: number(item.latency_ms), outcome: string(item.outcome), retryable: boolean(item.retryable),
    retry_delay_seconds: nullableNumber(item.retry_delay_seconds), repaired: hasRepaired ? boolean(item.repaired) : undefined };
}
function task(value: unknown): TeamTask {
  const item = record(value, ["id", "role", "revision", "status", "reason_code", "attempt", "instruction", "dependency_ids", "created_at", "updated_at", "output_state", "output", "attempts"]);
  const output_state = enumValue(item.output_state, ["none", "available", "withheld"]);
  if ((output_state === "available") !== (item.output !== null)) return invalid();
  return { id: id(item.id), role: enumValue(item.role, professionalRoles), revision: integer(item.revision, 1),
    status: enumValue(item.status, ["queued", "running", "blocked", "succeeded", "failed", "cancelled"]), reason_code: nullableString(item.reason_code),
    attempt: integer(item.attempt), instruction: string(item.instruction), dependency_ids: ids(item.dependency_ids), created_at: date(item.created_at), updated_at: date(item.updated_at),
    output_state, output: item.output === null ? null : output(item.output), attempts: array(item.attempts, attempt) };
}
export function parseTeam(value: unknown, expected: Scope): GatewayTeam {
  const item = record(value, ["conversation_id", "run_spec_id", "status", "revision", "event_sequence", "tasks", "reviews"]);
  const revision = integer(item.revision), tasks = array(item.tasks, task), taskIds = new Set(tasks.map((entry) => entry.id));
  if (taskIds.size !== tasks.length) return invalid();
  for (const entry of tasks) {
    if (entry.revision > revision || entry.dependency_ids.some((dependency) => !taskIds.has(dependency) || dependency === entry.id)) return invalid();
    if (entry.output?.content.checks.some((check) => check.task_ids.some((dependency) => !entry.dependency_ids.includes(dependency)))) return invalid();
  }
  const reviews = array(item.reviews, (raw): TeamReview => {
    const review = record(raw, ["id", "revision", "decision", "comment", "reviewed_by", "output_ids", "created_at"]);
    const reviewedRevision = integer(review.revision, 1), output_ids = ids(review.output_ids, 4);
    if (reviewedRevision > revision || output_ids.length !== 4) return invalid();
    return { id: id(review.id), revision: reviewedRevision, decision: enumValue(review.decision, ["approved", "changes_requested"]),
      comment: string(review.comment, 4000), reviewed_by: string(review.reviewed_by), output_ids, created_at: date(review.created_at) };
  });
  const status = enumValue(item.status, ["not_started", "active", "paused", "cancelled"]);
  if (status === "not_started" && (revision !== 0 || tasks.length || reviews.length)) return invalid();
  return { ...scope(item, expected), status, revision, event_sequence: integer(item.event_sequence), tasks, reviews };
}
export function parseTeamReceipt(value: unknown, expected: Scope): TeamReceipt {
  const item = record(value, ["request_id", "conversation_id", "run_spec_id", "revision", "task_ids"]);
  return { ...scope(item, expected), request_id: id(item.request_id), revision: integer(item.revision), task_ids: ids(item.task_ids) };
}
