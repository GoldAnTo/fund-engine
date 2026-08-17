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
