import type { components } from "../contracts/v1";

type Schemas = components["schemas"];

export type WorkflowState = Schemas["ResearchWorkflowResponse"]["state"];
export type WorkflowStageCode = Schemas["WorkflowStageDTO"]["code"];
export type WorkflowStageStatus = Schemas["WorkflowStageDTO"]["status"];
export type WorkflowLedgerStatus = Schemas["WorkflowSourceLedgerItemDTO"]["status"];

export interface WorkflowStage {
  code: WorkflowStageCode;
  displayName: string;
  status: WorkflowStageStatus;
  reason: string;
}

export interface WorkflowSystemAction {
  label: string | null;
  reason: string | null;
  startedAt: string | null;
  heartbeatAt: string | null;
  leaseExpiresAt: string | null;
  retryAt: string | null;
  recoveryStatus: string | null;
}

export interface WorkflowUserAction {
  kind: string;
  label: string;
  reason: string;
  recommendation: Record<string, unknown>;
  alternatives: Array<Record<string, unknown>>;
  impact: string;
  payload: Record<string, unknown>;
}

export interface WorkflowEvent {
  id: string;
  sequence: number;
  transition: string;
  actor: string;
  message: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface WorkflowAcquisitionRound {
  seriesId: string;
  queryPlanId: string;
  goalId: string;
  round: number;
  jobId: string;
  status: string;
  stage: string;
  attempt: number;
  retryAt: string | null;
  leaseExpiresAt: string | null;
  finishedAt: string | null;
  errorCode: string | null;
  errorDetail: string | null;
  recoveryStatus: string | null;
  plannerVersion: string;
  policyVersion: string;
  frozenInputs: Record<string, unknown>;
  orderedQueryCount: number;
  expansionTrigger: string | null;
}

export interface WorkflowLedgerItem {
  recordId: string;
  recordType: string;
  status: WorkflowLedgerStatus;
  reason: string;
  reasonCode: string;
  recordedAt: string;
  evidenceLinkId: string | null;
  reviewState: string | null;
  role: string | null;
  sourceRole: string | null;
  mappingDisposition: string | null;
  mappingKind: string | null;
  adapterKey: string | null;
  attemptId: string | null;
  sourceUrl: string | null;
  finalUrl: string | null;
  retrievedAt: string | null;
  contentSha256: string | null;
  publicationKey: string | null;
  dedupRelation: string | null;
  admissionOutcome: string | null;
  drilldown: { kind: Schemas["WorkflowSourceLedgerDrilldownDTO"]["kind"]; href: string } | null;
  drilldownUnavailableReason: string | null;
}

export interface WorkflowLedgerPage {
  items: WorkflowLedgerItem[];
  total: number;
  hasMore: boolean;
  nextCursor: string | null;
}

export interface WorkflowCoverage {
  goalId: string;
  thesisId: string | null;
  objective: string;
  status: string;
  reasonCodes: string[];
  requiredAuthorityCount: number;
  observedAuthorityCount: number;
  requiredIndependentSourceCount: number;
  observedIndependentSourceCount: number;
  contrarySearchCompleted: boolean;
  evidenceLinkIds: string[];
  unresolved: unknown[];
  unknown: unknown[];
  evaluationRound: number;
}

export interface EventWorkflow {
  orchestrationId: string;
  researchRunId: string | null;
  researchExecution: {
    jobId: string;
    status: string;
    step: string | null;
    attempt: number;
    failureCount: number;
    startedAt: string | null;
    finishedAt: string | null;
    recoveryCount: number;
    lastRecoveredAt: string | null;
  } | null;
  event: { caseId: string; title: string; researchQuestion: string };
  scope: {
    id: string;
    version: number;
    factors: Array<{ statement: string; description: string | null; position: number }>;
  };
  state: WorkflowState;
  stages: WorkflowStage[];
  systemAction: WorkflowSystemAction;
  userAction: WorkflowUserAction | null;
  userActionSummary: string;
  events: WorkflowEvent[];
  acquisition: { seriesCount: number; rounds: WorkflowAcquisitionRound[] };
  sourceLedger: {
    counts: {
      total: number;
      reviewed: number;
      automaticallyAdmitted: number;
      deduplicated: number;
      quarantined: number;
      byStatus: Record<string, number>;
    };
    items: WorkflowLedgerItem[];
    total: number;
    hasMore: boolean;
  };
  coverage: WorkflowCoverage[];
  conclusion: {
    id: string;
    state: string;
    text: string;
    primaryFactor: string | null;
    evidenceLinkIds: string[];
    citations: WorkflowLedgerItem[];
    reviewer: string | null;
    systemGenerated: boolean;
    humanReviewed: boolean;
    reviewLabel: string;
    createdAt: string;
  } | null;
  monitor: {
    id: string;
    version: number;
    status: string;
    frequency: string;
    factorIds: string[];
    sourceTypes: string[];
    nextVerificationEvent: string;
    createdAt: string;
  } | null;
  recovery: {
    status: string | null;
    reason: string | null;
    source: Schemas["WorkflowRecoveryDTO"]["source"];
    attempt: number | null;
    evaluatedAt: string;
    orchestrationHeartbeatAt: string | null;
    workerHeartbeatAt: string | null;
    leaseExpiresAt: string | null;
    retryAt: string | null;
    failedAt: string | null;
    lastCheckpoint: Record<string, unknown>;
    decisionDiagnostic: Record<string, unknown> | null;
  };
  version: number;
  updatedAt: string;
}

export interface WorkflowCommandResult {
  orchestrationId: string;
  caseId: string;
  scopeVersionId: string;
  researchRunId: string | null;
  state: WorkflowState;
  userStage: string;
  currentSystemAction: string | null;
  systemActionReason: string | null;
  nextAction: { kind: string; label: string; payload: Record<string, unknown> } | null;
  version: number;
}

export interface ScopeDecisionInput {
  kind: "keep_scope" | "stop";
  reason: string;
  expectedVersion: number;
  idempotencyKey?: string;
}

export type RuntimeHealth = "healthy" | "degraded" | "unavailable";

export interface CaseRuntimeStatus {
  runtimeStatus: RuntimeHealth;
  requiredServices: Array<{
    name: "research-worker" | "acquisition-worker" | "scheduler";
    status: RuntimeHealth;
    state: string | null;
    lastSeenAt: string | null;
  }>;
  durableCheckpoint: {
    workflowState: string;
    userStage: string;
    version: number;
    systemAction: string | null;
    savedAt: string;
    lastTransition: string | null;
    lastTransitionAt: string | null;
  };
  recovery: {
    automatic: boolean;
    status: string;
    message: string;
  };
  message: string;
}

export interface EventWorkflowClient {
  getEventWorkflow(caseId: string, options?: { signal?: AbortSignal }): Promise<EventWorkflow>;
  getCaseRuntimeStatus?(
    caseId: string,
    options?: { signal?: AbortSignal },
  ): Promise<CaseRuntimeStatus>;
  getEventWorkflowLedger(
    caseId: string,
    options?: { status?: WorkflowLedgerStatus; cursor?: string; limit?: number; signal?: AbortSignal },
  ): Promise<WorkflowLedgerPage>;
  confirmEventWorkflow(
    caseId: string,
    scopeVersionId: string,
    options?: { scopeVersion?: number; idempotencyKey?: string },
  ): Promise<WorkflowCommandResult>;
  resumeProtocolWorkflow(
    caseId: string,
    scopeVersionId: string,
    options?: { scopeVersion?: number; idempotencyKey?: string },
  ): Promise<WorkflowCommandResult>;
  decideEventScope(
    caseId: string,
    decision: ScopeDecisionInput,
    options?: { signal?: AbortSignal },
  ): Promise<WorkflowCommandResult | EventWorkflow>;
}

export const ACTIVE_WORKFLOW_STATES = new Set<WorkflowState>([
  "planning_acquisition",
  "acquiring",
  "freezing_sources",
  "assessing_coverage",
  "synthesizing_evidence",
  "adjudicating_thesis",
  "generating_report",
  "retry_wait",
  "recovering",
]);

export function isActiveWorkflowState(state: WorkflowState): boolean {
  return ACTIVE_WORKFLOW_STATES.has(state);
}

export class EventWorkflowError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly safeAction?: Schemas["WorkflowSafeActionDTO"],
  ) {
    super(message);
    this.name = "EventWorkflowError";
  }
}

function mapLedgerItem(dto: Schemas["WorkflowSourceLedgerItemDTO"]): WorkflowLedgerItem {
  return {
    recordId: dto.record_id,
    recordType: dto.record_type,
    status: dto.status,
    reason: dto.reason,
    reasonCode: dto.reason_code,
    recordedAt: dto.recorded_at,
    evidenceLinkId: dto.evidence_link_id,
    reviewState: dto.review_state,
    role: dto.role,
    sourceRole: dto.source_role,
    mappingDisposition: dto.mapping_disposition,
    mappingKind: dto.mapping_kind,
    adapterKey: dto.adapter_key,
    attemptId: dto.attempt_id,
    sourceUrl: dto.source_url,
    finalUrl: dto.final_url,
    retrievedAt: dto.retrieved_at,
    contentSha256: dto.content_sha256,
    publicationKey: dto.publication_key,
    dedupRelation: dto.dedup_relation,
    admissionOutcome: dto.admission_outcome,
    drilldown: dto.drilldown,
    drilldownUnavailableReason: dto.drilldown_unavailable_reason,
  };
}

export function mapWorkflowLedgerPage(dto: Schemas["WorkflowLedgerPageDTO"]): WorkflowLedgerPage {
  return {
    items: dto.items.map(mapLedgerItem),
    total: dto.total,
    hasMore: dto.has_more,
    nextCursor: dto.next_cursor,
  };
}

export function mapEventWorkflow(dto: Schemas["ResearchWorkflowResponse"]): EventWorkflow {
  return {
    orchestrationId: dto.orchestration_id,
    researchRunId: dto.research_run_id,
    researchExecution: dto.research_execution ? {
      jobId: dto.research_execution.job_id,
      status: dto.research_execution.status,
      step: dto.research_execution.step,
      attempt: dto.research_execution.attempt,
      failureCount: dto.research_execution.failure_count,
      startedAt: dto.research_execution.started_at,
      finishedAt: dto.research_execution.finished_at,
      recoveryCount: dto.research_execution.recovery_count,
      lastRecoveredAt: dto.research_execution.last_recovered_at,
    } : null,
    event: {
      caseId: dto.event.case_id,
      title: dto.event.title,
      researchQuestion: dto.event.research_question,
    },
    scope: {
      id: dto.scope.id,
      version: dto.scope.version,
      factors: dto.scope.factors,
    },
    state: dto.state,
    stages: dto.stages.map((stage) => ({
      code: stage.code,
      displayName: stage.display_name,
      status: stage.status,
      reason: stage.reason,
    })),
    systemAction: {
      label: dto.system_action.label,
      reason: dto.system_action.reason,
      startedAt: dto.system_action.started_at,
      heartbeatAt: dto.system_action.heartbeat_at,
      leaseExpiresAt: dto.system_action.lease_expires_at,
      retryAt: dto.system_action.retry_at,
      recoveryStatus: dto.system_action.recovery_status,
    },
    userAction: dto.user_action ? {
      kind: dto.user_action.kind,
      label: dto.user_action.label,
      reason: dto.user_action.reason,
      recommendation: dto.user_action.recommendation,
      alternatives: dto.user_action.alternatives,
      impact: dto.user_action.impact,
      payload: dto.user_action.payload,
    } : null,
    userActionSummary: dto.user_action_summary,
    events: dto.events.map((event) => ({
      id: event.id,
      sequence: event.sequence,
      transition: event.transition,
      actor: event.actor,
      message: event.message,
      payload: event.payload,
      createdAt: event.created_at,
    })),
    acquisition: {
      seriesCount: dto.acquisition.series_count,
      rounds: dto.acquisition.rounds.map((round) => ({
        seriesId: round.series_id,
        queryPlanId: round.query_plan_id,
        goalId: round.goal_id,
        round: round.round,
        jobId: round.job_id,
        status: round.status,
        stage: round.stage,
        attempt: round.attempt,
        retryAt: round.retry_at,
        leaseExpiresAt: round.lease_expires_at,
        finishedAt: round.finished_at,
        errorCode: round.error_code,
        errorDetail: round.error_detail,
        recoveryStatus: round.recovery_status,
        plannerVersion: round.planner_version,
        policyVersion: round.policy_version,
        frozenInputs: round.frozen_inputs,
        orderedQueryCount: round.ordered_query_count,
        expansionTrigger: round.expansion_trigger,
      })),
    },
    sourceLedger: {
      counts: {
        total: dto.source_ledger.counts.total,
        reviewed: dto.source_ledger.counts.reviewed,
        automaticallyAdmitted: dto.source_ledger.counts.automatically_admitted,
        deduplicated: dto.source_ledger.counts.deduplicated,
        quarantined: dto.source_ledger.counts.quarantined,
        byStatus: dto.source_ledger.counts.by_status,
      },
      items: dto.source_ledger.items.map(mapLedgerItem),
      total: dto.source_ledger.total,
      hasMore: dto.source_ledger.has_more,
    },
    coverage: dto.coverage.map((item) => ({
      goalId: item.goal_id,
      thesisId: item.thesis_id,
      objective: item.objective,
      status: item.status,
      reasonCodes: item.reason_codes,
      requiredAuthorityCount: item.required_authority_count,
      observedAuthorityCount: item.observed_authority_count,
      requiredIndependentSourceCount: item.required_independent_source_count,
      observedIndependentSourceCount: item.observed_independent_source_count,
      contrarySearchCompleted: item.contrary_search_completed,
      evidenceLinkIds: item.evidence_link_ids,
      unresolved: item.unresolved,
      unknown: item.unknown,
      evaluationRound: item.evaluation_round,
    })),
    conclusion: dto.conclusion ? {
      id: dto.conclusion.id,
      state: dto.conclusion.state,
      text: dto.conclusion.text,
      primaryFactor: dto.conclusion.primary_factor,
      evidenceLinkIds: dto.conclusion.evidence_link_ids,
      citations: dto.conclusion.citations.map(mapLedgerItem),
      reviewer: dto.conclusion.reviewer,
      systemGenerated: dto.conclusion.system_generated,
      humanReviewed: dto.conclusion.human_reviewed,
      reviewLabel: dto.conclusion.review_label,
      createdAt: dto.conclusion.created_at,
    } : null,
    monitor: dto.monitor ? {
      id: dto.monitor.id,
      version: dto.monitor.version,
      status: dto.monitor.status,
      frequency: dto.monitor.frequency,
      factorIds: dto.monitor.factor_ids,
      sourceTypes: dto.monitor.source_types,
      nextVerificationEvent: dto.monitor.next_verification_event,
      createdAt: dto.monitor.created_at,
    } : null,
    recovery: {
      status: dto.recovery.status,
      reason: dto.recovery.reason,
      source: dto.recovery.source,
      attempt: dto.recovery.attempt,
      evaluatedAt: dto.recovery.evaluated_at,
      orchestrationHeartbeatAt: dto.recovery.orchestration_heartbeat_at,
      workerHeartbeatAt: dto.recovery.worker_heartbeat_at,
      leaseExpiresAt: dto.recovery.lease_expires_at,
      retryAt: dto.recovery.retry_at,
      failedAt: dto.recovery.failed_at,
      lastCheckpoint: dto.recovery.last_checkpoint,
      decisionDiagnostic: dto.recovery.decision_diagnostic,
    },
    version: dto.version,
    updatedAt: dto.updated_at,
  };
}

export function mapWorkflowCommand(dto: Schemas["ConfirmResearchWorkflowResponse"]): WorkflowCommandResult {
  return {
    orchestrationId: dto.orchestration_id,
    caseId: dto.case_id,
    scopeVersionId: dto.scope_version_id,
    researchRunId: dto.research_run_id,
    state: dto.state,
    userStage: dto.user_stage,
    currentSystemAction: dto.current_system_action,
    systemActionReason: dto.system_action_reason,
    nextAction: dto.next_action,
    version: dto.version,
  };
}
