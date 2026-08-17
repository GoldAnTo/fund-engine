export type AutomaticResearchStatus = "queued" | "running" | "completed" | "failed";

export type AutomaticStageKey = "acquire" | "parse" | "admit" | "analyze" | "conclude";

export type AutomaticStageStatus = "pending" | "running" | "completed" | "failed";

export const AUTOMATIC_STAGE_ORDER = [
  "acquire",
  "parse",
  "admit",
  "analyze",
  "conclude",
] as const satisfies readonly AutomaticStageKey[];

export interface AutomaticResearchStart {
  caseId: string;
  runId: string;
  status: "queued";
}

export interface AutomaticResearchStage {
  key: AutomaticStageKey;
  label: string;
  status: AutomaticStageStatus;
  summary: string;
  startedAt: string | null;
  completedAt: string | null;
}

export interface AutomaticResearchStats {
  sourceCount: number;
  admittedEvidenceCount: number;
  skippedCount: number;
  durationSeconds: number;
}

export interface AutomaticResearchException {
  reason: string;
  stage: string;
  count: number;
}

export interface AutomaticResearchSource {
  title: string | null;
  url: string | null;
  role: string;
  reviewState: "automatically_admitted";
}

export interface AutomaticResearchResult {
  label: "系统生成，未经人工审核";
  humanReviewed: false;
  conclusion: string;
  keyFindings: string[];
  counterEvidence: string[];
  limitations: string[];
  sources: AutomaticResearchSource[];
}

export interface AutomaticResearchView {
  caseId: string;
  runId: string;
  title: string;
  status: AutomaticResearchStatus;
  stages: AutomaticResearchStage[];
  stats: AutomaticResearchStats;
  recentActivity: string[];
  exceptions: AutomaticResearchException[];
  failureReason: string | null;
  result: AutomaticResearchResult | null;
}

export interface AutomaticResearchClient {
  startAutomaticResearch(input: string): Promise<AutomaticResearchStart>;
  getAutomaticResearch(caseId: string): Promise<AutomaticResearchView>;
  retryAutomaticResearch(caseId: string): Promise<AutomaticResearchStart>;
}

const AUTOMATIC_RESEARCH_STATUS_LABELS: Record<AutomaticResearchStatus, string> = {
  queued: "已排队",
  running: "处理中",
  completed: "已完成",
  failed: "未完成",
};

const AUTOMATIC_STAGE_STATUS_LABELS: Record<AutomaticStageStatus, string> = {
  pending: "等待中",
  running: "进行中",
  completed: "已完成",
  failed: "未完成",
};

export function automaticResearchStatusLabel(status: AutomaticResearchStatus): string {
  return AUTOMATIC_RESEARCH_STATUS_LABELS[status];
}

export function automaticStageStatusLabel(status: AutomaticStageStatus): string {
  return AUTOMATIC_STAGE_STATUS_LABELS[status];
}

export function formatAutomaticDuration(value: number | null): string | null {
  if (value === null || !Number.isFinite(value) || value < 0) return null;
  const seconds = Math.floor(value);
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  if (hours > 0) {
    return minutes > 0 ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  }
  if (minutes > 0) {
    return remainder > 0 ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分`;
  }
  return `${remainder} 秒`;
}

export function formatAutomaticTimestamp(value: string | null): string | null {
  if (!value) return null;
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp);
}

export function automaticResearchPollDelay(consecutiveFailures: number): number {
  const safeFailures = Number.isFinite(consecutiveFailures)
    ? Math.max(0, Math.floor(consecutiveFailures))
    : 0;
  return Math.min(2_000 * (2 ** safeFailures), 8_000);
}

function isAutomaticStageKey(value: unknown): value is AutomaticStageKey {
  return typeof value === "string"
    && (AUTOMATIC_STAGE_ORDER as readonly string[]).includes(value);
}

function isAutomaticStageStatus(value: unknown): value is AutomaticStageStatus {
  return value === "pending"
    || value === "running"
    || value === "completed"
    || value === "failed";
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

export function normalizeAutomaticResearchStages(
  value: unknown,
): AutomaticResearchStage[] | null {
  if (!Array.isArray(value) || value.length !== AUTOMATIC_STAGE_ORDER.length) return null;
  const byKey = new Map<AutomaticStageKey, AutomaticResearchStage>();
  for (const candidate of value) {
    if (candidate === null || typeof candidate !== "object" || Array.isArray(candidate)) {
      return null;
    }
    const stage = candidate as Record<string, unknown>;
    if (
      !isAutomaticStageKey(stage.key)
      || byKey.has(stage.key)
      || typeof stage.label !== "string"
      || typeof stage.summary !== "string"
      || !isAutomaticStageStatus(stage.status)
      || !isNullableString(stage.startedAt)
      || !isNullableString(stage.completedAt)
    ) return null;
    byKey.set(stage.key, {
      key: stage.key,
      label: stage.label,
      summary: stage.summary,
      status: stage.status,
      startedAt: stage.startedAt,
      completedAt: stage.completedAt,
    });
  }
  const normalized: AutomaticResearchStage[] = [];
  for (const key of AUTOMATIC_STAGE_ORDER) {
    const stage = byKey.get(key);
    if (!stage) return null;
    normalized.push(stage);
  }
  return normalized;
}

export function normalizeAutomaticResearchView(
  view: AutomaticResearchView,
): AutomaticResearchView | null {
  const stages = normalizeAutomaticResearchStages(view.stages);
  return stages ? { ...view, stages } : null;
}
