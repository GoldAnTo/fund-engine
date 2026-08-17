export type AutomaticResearchStatus = "queued" | "running" | "completed" | "failed";

export type AutomaticStageKey = "acquire" | "parse" | "admit" | "analyze" | "conclude";

export type AutomaticStageStatus = "pending" | "running" | "completed" | "failed";

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
    hour12: false,
  }).format(timestamp);
}
