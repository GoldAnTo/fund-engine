/**
 * Stable frontend vocabulary for the review-gated research preparation flow.
 * No preparation action implies provider execution; authorization is the only
 * command that can materialize its already-reviewed plan into a research run.
 */

export type ResearchPreparationStatus =
  | "preparing"
  | "awaiting_claim_review"
  | "awaiting_protocol_confirmation"
  | "awaiting_plan_authorization"
  | "recoverable_failure"
  | "authorized";

export type ResearchPreparationSystemStepState =
  | "queued"
  | "running"
  | "succeeded"
  | "retrying"
  | "failed"
  | "stale";

export type ResearchPreparationReviewStepState =
  | "locked"
  | "awaiting_review"
  | "confirmed"
  | "stale";

export type ResearchPreparationArtifactState = "current" | "stale" | "superseded";

export type ResearchPreparationEventStep =
  | "parse_claims"
  | "draft_protocol"
  | "draft_evidence_plan";

export interface ResearchPreparationInitialMaterial {
  documentVersionId: string;
  title: string | null;
  parseState: string;
}

export interface ResearchPreparationProgress {
  completedSteps: number;
  totalSteps: number;
  currentStep: ResearchPreparationEventStep | null;
  failedStep: ResearchPreparationEventStep | null;
}

export interface ResearchPreparationSystemStep {
  state: ResearchPreparationSystemStepState;
  artifactSequence: number | null;
}

export interface ResearchPreparationReviewStep {
  state: ResearchPreparationReviewStepState;
}

export interface ResearchPreparationStages<T> {
  candidateClaims: T;
  protocol: T;
  evidencePlan: T;
}

export interface ResearchPreparationArtifact {
  sequence: number;
  state: ResearchPreparationArtifactState;
  payload: Record<string, unknown>;
  contextFingerprint: string | null;
  /** Source-use policy prevents this artifact from being displayed or approved. */
  displayWithheld: boolean;
}

export interface ResearchPreparation {
  caseId: string;
  caseTitle?: string | null;
  initialMaterial?: ResearchPreparationInitialMaterial | null;
  progress?: ResearchPreparationProgress | null;
  revision: number;
  status: ResearchPreparationStatus;
  researchRunId: string | null;
  system: ResearchPreparationStages<ResearchPreparationSystemStep>;
  review: ResearchPreparationStages<ResearchPreparationReviewStep>;
  nextAttemptAt: string | null;
  lastErrorMessage: string | null;
  artifacts: ResearchPreparationStages<ResearchPreparationArtifact | null>;
  authorizedEvidencePlan: Record<string, unknown> | null;
}

export interface ResearchPreparationEvent {
  seq: number;
  type: string;
  step: ResearchPreparationEventStep | null;
  message: string | null;
  detail: Record<string, unknown> | null;
  createdAt: string;
}

export interface ResearchPreparationEventsPage {
  items: ResearchPreparationEvent[];
  nextAfterSeq: number | null;
}

export interface ConfirmResearchPreparationClaimsInput {
  caseId: string;
  revision: number;
  actor: string;
  decisions: Array<{
    candidateId: string;
    outcome: "confirmed" | "modified" | "rejected";
    reason: string;
    normalizedText?: string | null;
  }>;
}

export interface ConfirmResearchPreparationProtocolInput {
  caseId: string;
  revision: number;
  actor: string;
  draftSequence: number;
  edits?: Record<string, unknown>;
}

export interface RetryResearchPreparationInput {
  caseId: string;
  revision: number;
  actor: string;
}

export interface AuthorizeResearchPreparationInput {
  caseId: string;
  revision: number;
  actor: string;
  planSequence: number;
  idempotencyKey: string;
}

/** A revision/idempotency conflict that UI can handle without parsing text. */
export class ConflictError extends Error {
  readonly status = 409;
  constructor(message = "当前准备版本已变化，请刷新后重试。") {
    super(message);
    this.name = "ConflictError";
  }
}
