import { GatewaySchemaError } from "./GatewaySchemaError";

export type GatewayEvidenceSummary = {
  evidenceLinkId: string; taskId: string; thesisId: string; thesisStatement: string;
  statement: string; documentVersionId: string; title: string; sourceAuthority: string;
  sourceUrl: string | null; publishedAt: string | null; observedPeriod: string | null;
  relationship: string; reviewState: "automatically_admitted";
};
export type GatewayAutomaticResult = {
  label: "系统生成，未经人工审核"; humanReviewed: false; conclusion: string;
  keyFindings: string[]; counterEvidence: string[]; limitations: string[];
  sources: { title: string | null; url: string | null; role: string; reviewState: "automatically_admitted" }[];
};
export type GatewayResearchContent = {
  conversationId: string; runSpecId: string;
  state: "pending" | "available" | "withheld" | "failed" | "cancelled";
  reasonCode: "result_pending" | "result_not_authorized" | "execution_failed" | "execution_cancelled" | null;
  draftId: string | null; result: GatewayAutomaticResult | null;
  evidence: GatewayEvidenceSummary[]; totalEvidence: number; truncated: boolean; warnings: string[];
  assessmentReview: GatewayAssessmentReview | null;
};
export type GatewayQualityFlag = "mixed_data_periods" | "unknown_data_period" | "user_material_only" |
  "no_primary_disclosure" | "source_independence_unverified" | "retrieval_direction_unverified";
export type GatewayAssessmentItem = {
  assessmentId: string; snapshotId: string; taskId: string; thesisId: string; thesisStatement: string;
  conclusion: "supported" | "contradicted" | "insufficient_evidence"; rationale: string; gaps: string[];
  evidenceLinkIds: string[]; qualityFlags: GatewayQualityFlag[];
};
export type GatewayAssessmentReview = {
  methodVersion: "gateway-assessment-review/v1";
  state: "available" | "unavailable";
  reasonCode: "lineage_unavailable" | "evidence_list_truncated" | null;
  items: GatewayAssessmentItem[]; qualityFlags: GatewayQualityFlag[];
};
export type GatewayEvidenceDetail = GatewayEvidenceSummary & {
  conversationId: string; runSpecId: string; quote: string; sourceSpanId: string;
  locator: { page: number | null; paragraph: number | null }; contentSha256: string; acquiredAt: string;
};
export type GatewayTaskTrace = {
  conversationId: string; runSpecId: string; taskId: string;
  events: { sequence: number; stage: string; status: string; label: string; occurredAt: string }[];
  exceptions: { reasonCode: string; message: string; nextAction: string; count: number }[];
  truncated: boolean;
};

type Scope = { conversationId: string; runSpecId: string };
const summaryKeys = ["evidence_link_id", "task_id", "thesis_id", "thesis_statement", "statement", "document_version_id", "title", "source_authority", "source_url", "published_at", "observed_period", "relationship", "review_state"];
const reasonForState = { pending: "result_pending", available: null, withheld: "result_not_authorized", failed: "execution_failed", cancelled: "execution_cancelled" } as const;
const traceLabels: Record<string, string> = {
  queued: "任务已排队", searching: "来源检索阶段", fetching: "来源获取阶段", freezing: "材料冻结阶段", extracting: "内容解析阶段",
  admitting: "证据准入校验阶段", succeeded: "来源任务已完成", partial: "来源任务部分完成", failed: "来源任务失败",
  cancelled: "来源任务已取消", unknown: "来源任务状态已更新",
};
const traceStatuses = new Set(["queued", "running", "retry_wait", "succeeded", "partial", "failed", "cancelled", "unknown"]);
const traceExceptionMessages: Record<string, string> = {
  source_unavailable: "部分来源暂时不可用", source_policy_blocked: "部分来源不符合当前使用权限", parsing_failed: "部分材料解析失败",
  evidence_not_admitted: "部分材料未通过证据准入", source_version_conflict: "部分来源版本存在冲突", processing_error: "部分材料处理异常",
};
const traceActions = new Set(["wait_for_retry", "check_source_policy", "review_sources", "check_execution"]);

function invalid(): never { throw new GatewaySchemaError("Gateway research content is invalid or belongs to another scope"); }
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== keys.length || keys.some((key) => !Object.prototype.hasOwnProperty.call(item, key))) return invalid();
  return item;
}
function text(value: unknown): string { if (typeof value !== "string") return invalid(); return value; }
function nullableText(value: unknown): string | null { return value === null ? null : text(value); }
function id(value: unknown): string {
  const result = text(value);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(result)) return invalid();
  return result;
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) return invalid();
  return value;
}
function boolean(value: unknown): boolean { if (typeof value !== "boolean") return invalid(); return value; }
function array<T>(value: unknown, parse: (entry: unknown) => T, maximum = 1000): T[] {
  if (!Array.isArray(value) || value.length > maximum) return invalid();
  return value.map(parse);
}
function timestamp(value: unknown): string {
  const result = text(value);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(result) || !Number.isFinite(Date.parse(result))) return invalid();
  return result;
}
function scope(item: Record<string, unknown>, expected: Scope): Scope {
  const conversationId = id(item.conversation_id), runSpecId = id(item.run_spec_id);
  if (conversationId !== expected.conversationId || runSpecId !== expected.runSpecId) return invalid();
  return { conversationId, runSpecId };
}
function review(value: unknown): "automatically_admitted" { if (value !== "automatically_admitted") return invalid(); return value; }
function summary(item: Record<string, unknown>): GatewayEvidenceSummary {
  return { evidenceLinkId: id(item.evidence_link_id), taskId: id(item.task_id), thesisId: id(item.thesis_id),
    thesisStatement: text(item.thesis_statement), statement: text(item.statement), documentVersionId: id(item.document_version_id),
    title: text(item.title), sourceAuthority: text(item.source_authority), sourceUrl: nullableText(item.source_url),
    publishedAt: item.published_at === null ? null : timestamp(item.published_at), observedPeriod: nullableText(item.observed_period),
    relationship: text(item.relationship), reviewState: review(item.review_state) };
}
function automaticResult(value: unknown): GatewayAutomaticResult {
  const item = record(value, ["label", "human_reviewed", "conclusion", "key_findings", "counter_evidence", "limitations", "sources"]);
  if (item.label !== "系统生成，未经人工审核" || item.human_reviewed !== false) return invalid();
  return { label: item.label, humanReviewed: false, conclusion: text(item.conclusion),
    keyFindings: array(item.key_findings, text), counterEvidence: array(item.counter_evidence, text), limitations: array(item.limitations, text),
    sources: array(item.sources, (entry) => { const source = record(entry, ["title", "url", "role", "review_state"]);
      return { title: nullableText(source.title), url: nullableText(source.url), role: text(source.role), reviewState: review(source.review_state) }; }) };
}
export function parseResearchContent(value: unknown, expected: Scope): GatewayResearchContent {
  const hasReview = !!value && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "assessment_review");
  const item = record(value, ["conversation_id", "run_spec_id", "state", "reason_code", "draft_id", "result", "evidence", "total_evidence", "truncated", "warnings", ...(hasReview ? ["assessment_review"] : [])]);
  const state = text(item.state);
  if (!Object.prototype.hasOwnProperty.call(reasonForState, state)) return invalid();
  const checkedState = state as GatewayResearchContent["state"];
  const reasonCode = reasonForState[checkedState];
  if (item.reason_code !== reasonCode || (checkedState === "available") !== (item.result !== null)) return invalid();
  if ((checkedState === "available") !== (item.draft_id !== null)) return invalid();
  const evidence = array(item.evidence, (entry) => summary(record(entry, summaryKeys)), 200);
  const totalEvidence = integer(item.total_evidence), truncated = boolean(item.truncated);
  if (totalEvidence < evidence.length || truncated !== (totalEvidence > evidence.length) || new Set(evidence.map((entry) => entry.evidenceLinkId)).size !== evidence.length) return invalid();
  return { ...scope(item, expected), state: checkedState, reasonCode, draftId: item.draft_id === null ? null : id(item.draft_id),
    result: item.result === null ? null : automaticResult(item.result), evidence, totalEvidence, truncated, warnings: array(item.warnings, text),
    assessmentReview: !hasReview || item.assessment_review === null ? null : assessmentReview(item.assessment_review, evidence, checkedState, truncated) };
}

const qualityFlags = new Set<GatewayQualityFlag>(["mixed_data_periods", "unknown_data_period", "user_material_only",
  "no_primary_disclosure", "source_independence_unverified", "retrieval_direction_unverified"]);
function unique<T>(values: T[]): T[] { if (new Set(values).size !== values.length) return invalid(); return values; }
function flags(value: unknown): GatewayQualityFlag[] {
  return unique(array(value, (entry) => {
    const flag = text(entry) as GatewayQualityFlag;
    if (!qualityFlags.has(flag)) return invalid();
    return flag;
  }, 6));
}
function assessmentReview(value: unknown, evidence: GatewayEvidenceSummary[], state: GatewayResearchContent["state"], truncated: boolean): GatewayAssessmentReview {
  const body = record(value, ["method_version", "state", "reason_code", "items", "quality_flags"]);
  if (body.method_version !== "gateway-assessment-review/v1" || state !== "available") return invalid();
  const allFlags = flags(body.quality_flags);
  const items = array(body.items, (entry): GatewayAssessmentItem => {
    const item = record(entry, ["assessment_id", "snapshot_id", "task_id", "thesis_id", "thesis_statement", "conclusion", "rationale", "gaps", "evidence_link_ids", "quality_flags"]);
    const conclusion = text(item.conclusion);
    if (conclusion !== "supported" && conclusion !== "contradicted" && conclusion !== "insufficient_evidence") return invalid();
    const evidenceLinkIds = unique(array(item.evidence_link_ids, id, 200));
    if (!evidenceLinkIds.length) return invalid();
    return { assessmentId: id(item.assessment_id), snapshotId: id(item.snapshot_id), taskId: id(item.task_id),
      thesisId: id(item.thesis_id), thesisStatement: text(item.thesis_statement), conclusion,
      rationale: text(item.rationale), gaps: array(item.gaps, text, 200), evidenceLinkIds, qualityFlags: flags(item.quality_flags) };
  }, 200);
  if (body.state === "unavailable") {
    if (items.length || allFlags.length || (body.reason_code !== "lineage_unavailable" && body.reason_code !== "evidence_list_truncated")) return invalid();
    if ((body.reason_code === "evidence_list_truncated") !== truncated) return invalid();
    return { methodVersion: body.method_version, state: "unavailable", reasonCode: body.reason_code, items, qualityFlags: allFlags };
  }
  if (body.state !== "available" || body.reason_code !== null || truncated || !items.length) return invalid();
  for (const key of ["assessmentId", "snapshotId", "taskId", "thesisId"] as const) unique(items.map((item) => item[key]));
  const byId = new Map(evidence.map((entry) => [entry.evidenceLinkId, entry]));
  const inputs = unique(items.flatMap((item) => item.evidenceLinkIds));
  if (inputs.length !== evidence.length) return invalid();
  for (const item of items) {
    // An individual assessment can use only user material even when another
    // assessment has official sources. Flags apply to their own input scope.
    for (const input of item.evidenceLinkIds) {
      const entry = byId.get(input);
      if (!entry || entry.thesisId !== item.thesisId || entry.thesisStatement !== item.thesisStatement) return invalid();
    }
  }
  return { methodVersion: body.method_version, state: "available", reasonCode: null, items, qualityFlags: allFlags };
}
export function parseEvidenceDetail(value: unknown, expected: Scope, evidenceLinkId: string): GatewayEvidenceDetail {
  const item = record(value, [...summaryKeys, "conversation_id", "run_spec_id", "quote", "source_span_id", "locator", "content_sha256", "acquired_at"]);
  const evidence = summary(item), locator = record(item.locator, ["page", "paragraph"]), hash = text(item.content_sha256);
  if (evidence.evidenceLinkId !== evidenceLinkId || !/^[0-9a-f]{64}$/i.test(hash)) return invalid();
  return { ...scope(item, expected), ...evidence, quote: text(item.quote), sourceSpanId: id(item.source_span_id),
    locator: { page: locator.page === null ? null : integer(locator.page, 1), paragraph: locator.paragraph === null ? null : integer(locator.paragraph) },
    contentSha256: hash, acquiredAt: timestamp(item.acquired_at) };
}
export function assertEvidenceMatchesSummary(detail: GatewayEvidenceDetail, expected: Pick<GatewayEvidenceSummary, "evidenceLinkId" | "taskId" | "thesisId" | "documentVersionId">): void {
  if (detail.evidenceLinkId !== expected.evidenceLinkId || detail.taskId !== expected.taskId || detail.thesisId !== expected.thesisId || detail.documentVersionId !== expected.documentVersionId) invalid();
}
export function parseTaskTrace(value: unknown, expected: Scope, taskId: string): GatewayTaskTrace {
  const item = record(value, ["conversation_id", "run_spec_id", "task_id", "events", "exceptions", "truncated"]);
  if (id(item.task_id) !== taskId) return invalid();
  const events = array(item.events, (entry) => { const event = record(entry, ["sequence", "stage", "status", "label", "occurred_at"]);
    const stage = text(event.stage), status = text(event.status), label = text(event.label);
    if (!Object.prototype.hasOwnProperty.call(traceLabels, stage) || traceLabels[stage] !== label || !traceStatuses.has(status)) return invalid();
    return { sequence: integer(event.sequence), stage, status, label, occurredAt: timestamp(event.occurred_at) }; }, 100);
  if (events.some((event, index) => index > 0 && event.sequence <= events[index - 1]!.sequence)) return invalid();
  return { ...scope(item, expected), taskId, events,
    exceptions: array(item.exceptions, (entry) => { const exception = record(entry, ["reason_code", "message", "next_action", "count"]);
      const reasonCode = text(exception.reason_code), message = text(exception.message), nextAction = text(exception.next_action);
      if (!Object.prototype.hasOwnProperty.call(traceExceptionMessages, reasonCode) || traceExceptionMessages[reasonCode] !== message || !traceActions.has(nextAction)) return invalid();
      return { reasonCode, message, nextAction, count: integer(exception.count, 1) }; }, 6),
    truncated: boolean(item.truncated) };
}
