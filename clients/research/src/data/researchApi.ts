/** Event research wire DTOs; snake_case matches backend/app/schemas/v1/event_research.py. */
export interface ResearchItem {
  case_id: string; event_title: string; workflow_mode: "reviewed" | "automatic";
  company_name: string | null; ticker: string | null; event_at: string | null;
  lifecycle_status: string; status_summary: string; next_human_action: string | null;
  next_action_kind: string; updated_at: string;
}
export interface ResearchLifecycle {
  status: string; active_run_id: string | null; current_round: number;
  status_summary: string; current_gap: string | null; next_human_action: string | null;
}
export interface ResearchEvidence {
  case_id: string; factor_statement: string; role: string; review_state: string;
  source_title: string | null; source_url: string | null; document_version_id: string;
  source_visible_in_case: boolean; excerpt: string; locator: Record<string, unknown>; available_at: string;
}
export interface ResearchWorkbenchData {
  event: ResearchItem; lifecycle: ResearchLifecycle;
  conclusion: { state: string; text: string; confidence: string; citations: ResearchEvidence[] };
  factors: Array<{ thesis_id: string; statement: string; description: string | null; position: number; reviewed_support_count: number; reviewed_contradiction_count: number; pending_proposal_count: number; current_gap: string | null }>;
  evidence: ResearchEvidence[];
  progress: { verified: number; pending: number; invalid_source: number; current_gap: string | null };
  scope: { version: number; factors: Array<{ statement: string; description: string | null }>; unmapped_evidence_count: number };
  next_action: { kind: string; label: string; count: number | null };
  preparation?: { status: string; revision: number; research_run_id: string | null; next_attempt_at: string | null; last_error_message: string | null; system: Record<string, { state: string }>; review: Record<string, { state: string }> } | null;
}
export interface CreateResearchInput {
  event_title: string; raw_input: string; research_question: string; candidate_factors: string[];
  created_by: string; source_type: "pasted_snapshot"; source_url?: string; research_protocol_required: boolean;
}
export class ResearchApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); this.name = "ResearchApiError"; }
}
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const strings = (value: unknown, keys: string[]): boolean => isRecord(value) && keys.every((key) => typeof value[key] === "string");
const optionalText = (value: unknown): boolean => value == null || typeof value === "string";
const validItem = (value: unknown): boolean => strings(value, ["case_id", "event_title", "lifecycle_status", "status_summary", "updated_at"]);
function validWorkbench(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const { lifecycle, conclusion, progress, factors, evidence, next_action: nextAction } = value;
  return validItem(value.event)
    && strings(lifecycle, ["status", "status_summary"]) && isRecord(lifecycle)
    && optionalText(lifecycle.current_gap) && optionalText(lifecycle.next_human_action)
    && strings(conclusion, ["state", "text", "confidence"])
    && isRecord(progress) && ["verified", "pending", "invalid_source"].every((key) => typeof progress[key] === "number") && optionalText(progress.current_gap)
    && strings(nextAction, ["kind", "label"])
    && Array.isArray(factors) && factors.every((factor) => strings(factor, ["thesis_id", "statement"]) && isRecord(factor) && optionalText(factor.description) && optionalText(factor.current_gap) && ["reviewed_support_count", "reviewed_contradiction_count", "pending_proposal_count"].every((key) => typeof factor[key] === "number"))
    && Array.isArray(evidence) && evidence.every((entry) => strings(entry, ["document_version_id", "factor_statement", "role", "review_state", "excerpt", "available_at"]) && isRecord(entry) && optionalText(entry.source_title) && optionalText(entry.source_url));
}
const statusMessages: Record<number, string> = {
  401: "登录凭据不可用，请检查登录或服务连接配置后重试。",
  403: "当前身份没有此操作的权限，请联系管理员检查研究空间权限。",
  409: "研究状态已变化，请刷新后核对当前版本再重试。",
  404: "研究不存在或已不可访问，请刷新列表后重新选择。",
  422: "提交内容不符合要求，请检查必填项和候选因素后重试。",
  503: "研究服务暂时不可用，请稍后重试。",
};
export async function request<T>(path: string, signal: AbortSignal, validate: (value: unknown) => boolean, body?: object, base = "/api/v1/event-research", idempotencyKey?: string, method?: "PUT"): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      method: method ?? (body ? "POST" : "GET"), credentials: "same-origin", signal,
      headers: body ? { "Content-Type": "application/json", Accept: "application/json", ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}) } : { Accept: "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ResearchApiError("无法连接研究服务，请检查连接后重试。", 0);
  }
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const envelope = isRecord(payload) && isRecord(payload.error) ? payload.error : null;
    const requestId = typeof envelope?.request_id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(envelope.request_id) ? envelope.request_id : null;
    const message = statusMessages[response.status] ?? `研究服务请求失败（${response.status}），请稍后重试。`;
    throw new ResearchApiError(`${message}${requestId ? ` 请求编号：${requestId}` : ""}`, response.status);
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!validate(payload)) throw new ResearchApiError("研究服务返回的数据格式不正确，请重试或联系管理员。", response.status);
  return payload as T;
}
export const researchApi = {
  list: (signal: AbortSignal) => request<{ items: ResearchItem[] }>("", signal, (value) => isRecord(value) && Array.isArray(value.items) && value.items.every(validItem)),
  workbench: (caseId: string, signal: AbortSignal) => request<ResearchWorkbenchData>(`/${encodeURIComponent(caseId)}/workbench`, signal, (value) => validWorkbench(value) && isRecord(value) && isRecord(value.event) && value.event.case_id === caseId),
  create: (input: CreateResearchInput, signal: AbortSignal, idempotencyKey?: string) => request<{ case_id: string; brief_id: string; lifecycle: ResearchLifecycle }>("", signal, (value) => strings(value, ["case_id"]) && isRecord(value) && Boolean(value.case_id), input, undefined, idempotencyKey),
};
