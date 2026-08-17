import type {
  ResearchPreparationStatus,
  ResearchPreparationStages,
  ResearchPreparationSystemStep,
  ResearchPreparationReviewStep,
} from "./researchPreparation";

export type EventLifecycleStatus =
  | "extracting"
  | "researching"
  | "awaiting_key_review"
  | "continuing"
  | "awaiting_scope"
  | "draft_ready"
  | "published"
  | "exhausted";

export type EventSourceType =
  | "pasted_snapshot"
  | "uploaded_file"
  | "licensed_provider"
  | "public_url";

export interface EventExtractionInput {
  rawInput: string;
  sourceUrl?: string;
  sourceType?: EventSourceType;
  sourceMetadata?: Record<string, unknown>;
}

export interface EventExtraction {
  eventTitle: string | null;
  companyName: string | null;
  ticker: string | null;
  eventAt: string | null;
  marketReaction: string | null;
  summary: string | null;
  researchQuestion: string;
  candidateFactors: string[];
  confirmationRequired: boolean;
}

export interface CreateEventResearchInput extends EventExtraction {
  rawInput: string;
  sourceUrl?: string;
  sourceType?: EventSourceType;
  sourceMetadata?: Record<string, unknown>;
  eventTitle: string;
  researchProtocolRequired?: boolean;
  createdBy: string;
}

export interface CreateUploadedEventResearchInput extends CreateEventResearchInput {
  file: File;
}

export interface EventLifecycle {
  status: EventLifecycleStatus;
  activeRunId: string | null;
  currentRound: number;
  summary: string;
  currentGap: string | null;
  nextHumanAction: string | null;
}

export interface EventResearchListItem {
  id: string;
  workflowMode: "reviewed" | "automatic";
  eventTitle: string;
  companyName: string | null;
  ticker: string | null;
  eventAt: string | null;
  status: EventLifecycleStatus;
  statusSummary: string;
  nextHumanAction: string | null;
  nextActionKind?: EventNextActionKind | null;
  updatedAt: string;
}

export type EventNextActionKind = import("../contracts/v1").components["schemas"]["EventNextActionDTO"]["kind"];

export interface EventFactor {
  thesisId?: string;
  statement: string;
  description?: string | null;
  position: number;
  reviewedSupportCount: number;
  reviewedContradictionCount: number;
  pendingProposalCount: number;
  currentGap: string | null;
}

export interface WorkbenchProgress {
  verified: number;
  pending: number;
  invalidSource: number;
  currentGap: string | null;
}

/** Safe preparation projection carried by the existing event workbench read model. */
export interface EventPreparationSummary {
  status: ResearchPreparationStatus;
  revision: number;
  researchRunId: string | null;
  nextAttemptAt: string | null;
  lastErrorMessage: string | null;
  system: ResearchPreparationStages<Pick<ResearchPreparationSystemStep, "state">>;
  review: ResearchPreparationStages<ResearchPreparationReviewStep>;
}

export interface EventResearchScope {
  version: number;
  factors: EventResearchScopeFactor[];
  unmappedEvidenceCount: number;
}

export interface EventResearchScopeFactor {
  statement: string;
  description?: string | null;
}

export type EventResearchScopeFactorInput = EventResearchScopeFactor | string;

export interface EventEvidenceCitation {
  caseId: string;
  factorStatement: string;
  role: string;
  reviewState: string;
  sourceTitle: string | null;
  sourceUrl: string | null;
  documentVersionId: string | null;
  sourceVisibleInCase: boolean;
  excerpt: string;
  locator: Record<string, unknown>;
  availableAt: string;
}

export interface EventReviewQueueSummary {
  total: number;
  reviewed: number;
  pending: number;
  invalidSource: number;
  currentRound: number;
  nextAction: string | null;
}

export type EventSourceStatus =
  | "accessible"
  | "pasted_unverified"
  | "invalid";

/** A proposal and its frozen-source context for event evidence review. */
export interface EventReviewQueueItem {
  proposalId: string;
  proposalVersion: number;
  status: string;
  proposedAt: string;
  linkId: string;
  thesisId: string | null;
  caseId: string;
  thesisStatement: string | null;
  aiRole: string | null;
  aiReason: string | null;
  aiScope: Record<string, unknown>;
  statementId: string | null;
  statementText: string | null;
  statementKind: string | null;
  spanId: string | null;
  verbatimText: string | null;
  locator: Record<string, unknown>;
  documentVersionId: string | null;
  documentSourceUrl: string | null;
  documentPublishedAt: string | null;
  availableAt: string | null;
  sourceTitle: string | null;
  sourceStatus: EventSourceStatus;
  sourceStatusReason: string;
  canAccept: boolean;
  proposalReason: string;
  position: number | null;
}

export interface EventReviewQueue {
  summary: EventReviewQueueSummary;
  items: EventReviewQueueItem[];
}

export interface EventWorkbench {
  event: EventResearchListItem;
  lifecycle: EventLifecycle;
  conclusion: { state: "cannot_conclude" | "ai_draft" | "published"; text: string; confidence: "low" | "medium" | "high"; citations: EventEvidenceCitation[] };
  factors: EventFactor[];
  evidence: EventEvidenceCitation[];
  progress: WorkbenchProgress;
  scope: EventResearchScope;
  nextAction: { kind: EventNextActionKind; label: string; count?: number };
  preparation?: EventPreparationSummary | null;
}

export interface EventConclusionVersion {
  id: string;
  sequence: number;
  state: "ai_draft" | "published";
  text: string;
  primaryFactor: string | null;
  scopeVersion: number | null;
  basedOnConclusionId: string | null;
  reviewer: string | null;
  evidenceCount: number;
  createdAt: string;
}

export interface EventResearchContinuation {
  runId: string;
  lifecycle: EventLifecycle;
}

export interface PublishedMaterialDecision {
  documentVersionId: string;
  decision: "reopen" | "no_change";
  decisionEventId: string;
  runId: string | null;
  recoveryRequired: boolean;
  lifecycle: EventLifecycle;
}

export interface EventResearchClient {
  extractEventResearch(input: EventExtractionInput): Promise<EventExtraction>;
  createEventResearch(input: CreateEventResearchInput): Promise<{ caseId: string; briefId: string; lifecycle: EventLifecycle }>;
  createEventResearchFromUpload(input: CreateUploadedEventResearchInput): Promise<{ caseId: string; briefId: string; lifecycle: EventLifecycle }>;
  attachEventMaterial(input: { caseId: string; rawInput: string; sourceUrl?: string; sourceType: EventSourceType; sourceMetadata: Record<string, unknown>; actor: string }): Promise<{ documentVersionId: string }>;
  uploadEventMaterial(input: { caseId: string; file: File; sourceMetadata: Record<string, unknown>; actor: string }): Promise<{ documentVersionId: string; parseState: "parsed" | "partial" | "failed"; nextAction: "review_original" | "supplement_original" }>;
  listEventResearch(status?: EventLifecycleStatus): Promise<EventResearchListItem[]>;
  getEventWorkbench(caseId: string): Promise<EventWorkbench>;
  getEventConclusionHistory(caseId: string): Promise<EventConclusionVersion[]>;
  continueEventResearch(input: { caseId: string; documentVersionId: string; reason: string; triggeredBy: string }): Promise<EventResearchContinuation>;
  decidePublishedMaterial(input: { caseId: string; rawInput: string; sourceUrl?: string; sourceType: EventSourceType; sourceMetadata: Record<string, unknown>; decision: "reopen" | "no_change"; reason: string; actor: string }): Promise<PublishedMaterialDecision>;
  decidePublishedUploadedMaterial(input: { caseId: string; file: File; sourceMetadata: Record<string, unknown>; decision: "reopen" | "no_change"; reason: string; actor: string }): Promise<PublishedMaterialDecision>;
  updateEventResearchScope(input: { caseId: string; factors: EventResearchScopeFactorInput[]; changedBy: string; changeReason: string }): Promise<EventResearchScope & { reclassifiedEvidenceCount: number }>;
  getEventReviewQueue(caseId: string): Promise<EventReviewQueue>;
  publishEventConclusion(input: { caseId: string; text: string; reviewer: string }): Promise<{ conclusionId: string; state: "published" }>;
  getResearchPreparation(caseId: string): Promise<import("./researchPreparation").ResearchPreparation>;
  listResearchPreparationEvents(caseId: string, cursor?: { afterSeq?: number; limit?: number }): Promise<import("./researchPreparation").ResearchPreparationEventsPage>;
  confirmResearchPreparationClaims(input: import("./researchPreparation").ConfirmResearchPreparationClaimsInput): Promise<import("./researchPreparation").ResearchPreparation>;
  confirmResearchPreparationProtocol(input: import("./researchPreparation").ConfirmResearchPreparationProtocolInput): Promise<import("./researchPreparation").ResearchPreparation>;
  retryResearchPreparation(input: import("./researchPreparation").RetryResearchPreparationInput): Promise<import("./researchPreparation").ResearchPreparation>;
  authorizeResearchPreparation(input: import("./researchPreparation").AuthorizeResearchPreparationInput): Promise<import("./researchPreparation").ResearchPreparation>;
}
