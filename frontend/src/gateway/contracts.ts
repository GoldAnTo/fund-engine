export type GatewayRole =
  | "scope_identity"
  | "sources_evidence"
  | "analysis_counter_evidence"
  | "compilation_checks";

export type GatewayRoleStatus =
  | "queued"
  | "running"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled";

export type GatewayNativeRunStatus =
  | "queued"
  | "running"
  | "waiting_for_sources"
  | "succeeded"
  | "failed"
  | "cancelled";

export type GatewaySafeEventType =
  | "role_queued"
  | "role_started"
  | "role_progress"
  | "role_blocked"
  | "role_completed"
  | "role_failed"
  | "identity_decision"
  | "source_waiting"
  | "evidence_available"
  | "rejected_source"
  | "candidate"
  | "gap"
  | "draft_ref"
  | "validation"
  | "command_accepted"
  | "command_rejected";

export type GatewaySafeReasonCode =
  | "unsupported_in_gateway_p0"
  | "native_execution_failed"
  | "authorization_required"
  | "cutoff_unavailable"
  | "source_policy_blocked"
  | "source_unavailable"
  | "evidence_gap"
  | "validation_failed"
  | "command_rejected";

export type GatewayArtifactKind =
  | "research_case"
  | "research_run"
  | "evidence_link"
  | "document_version"
  | "draft"
  | "validation";

export type GatewayArtifactReference = {
  kind: GatewayArtifactKind;
  id: string;
  caseId?: string;
  locatorAvailable?: boolean;
};

export type GatewayReceipt = {
  conversationId: string;
  intentId: string;
  runSpecId: string;
  nativeCaseId: string;
  nativeRunId: string;
  status: "queued";
  latestSequence: number;
};

/**
 * A P0 request that was understood but deliberately did not start a native
 * research run.  Keep this structurally separate from a successful receipt so
 * callers cannot accidentally render a made-up run.
 */
export type GatewayRejectedIntentReceipt = {
  receiptKind: "intent";
  intentId: string;
  conversationId: string;
  intentKind: "unsupported";
  outcome: "rejected";
  reasonCode: "unsupported_in_gateway_p0";
};

export type GatewaySubmissionReceipt =
  | GatewayReceipt
  | GatewayRejectedIntentReceipt;

export type GatewaySession = {
  tenantId: string;
  roles: string[];
  subjectId: string | null;
};

export type GatewayConversationSummary = {
  conversationId: string;
  title: string | null;
  createdAt: string;
  updatedAt: string;
  latestSequence: number;
};

export type GatewayConversationList = {
  conversations: GatewayConversationSummary[];
};

export type GatewayMessage = {
  id: string;
  sequence: number;
  text: string;
  createdAt: string;
  /** Older projections omit this; it is a safe display classification only. */
  kind?: "user" | "system";
};

export type GatewayRunScope = {
  topic: string;
  subjectLabel: string | null;
  evidenceCutoffAt: string;
  caseEvidenceCutoffDate: string | null;
  sourcePolicy: {
    inputKind: "topic" | "material";
    allowedSourceTypes: string[];
    allowedSourceRoles: ("company_disclosure" | "licensed_provider")[];
    hasIntakeMaterial: boolean;
  };
};

export type GatewayRun = {
  runSpecId: string;
  nativeCaseId: string;
  nativeRunId: string;
  status: GatewayNativeRunStatus;
  parentRunSpecId: string | null;
  createdAt: string;
  scope?: GatewayRunScope | null;
  execution?: GatewayExecutionProgress;
};

export type GatewayExecutionTask = {
  taskId: string;
  taskType: "support" | "contradict" | "alternative" | "alternative_explanation" | "intake_material";
  status: "queued" | "running" | "retry_wait" | "succeeded" | "partial" | "failed" | "cancelled";
  stage: "queued" | "searching" | "fetching" | "freezing" | "extracting" | "admitting" | "succeeded" | "partial" | "failed" | "cancelled";
  sourceKind: "intake_material" | "external_sources";
  sourceName: "用户提供材料" | "合规外部来源";
  providers: ("gildata" | "sse" | "szse")[];
  attempt: number;
  updatedAt: string;
  retryAt: string | null;
  counts: { discovered: number; fetched: number; frozen: number; admitted: number; exceptions: number };
};

export type GatewayExecutionProgress = {
  projectionState: "available" | "unavailable";
  stage: "queued" | "planning" | "retrieve" | "analyze" | "assessing" | "conclude" | "complete" | "failed" | "cancelled" | "unknown";
  updatedAt: string;
  reasonCode: null | "source_unavailable" | "source_policy_blocked" | "execution_state_unavailable" | "native_execution_failed" | "worker_unavailable" | "research_subject_missing";
  nextAction: "wait_for_execution" | "wait_for_retry" | "check_execution" | "review_result" | "none";
  workerState: "online" | "offline" | "unknown";
  workerLastSeenAt: string | null;
  workerScope: "acquisition_service";
  tasks: GatewayExecutionTask[];
};

export type GatewayExecutionUpdate = { runSpecId: string; execution: GatewayExecutionProgress };

export type GatewayTeamProgress = {
  runSpecId: string;
  revision: number;
  eventSequence: number;
  status: "active" | "paused" | "cancelled";
};

export type GatewayRoleProjection = {
  runSpecId: string;
  role: GatewayRole;
  status: GatewayRoleStatus;
  latestSequence: number;
};

export type GatewayRoleEvent = {
  sequence: number;
  runSpecId: string;
  role: GatewayRole;
  type: GatewaySafeEventType;
  status: GatewayRoleStatus | null;
  summary: string | null;
  reasonCode: GatewaySafeReasonCode | null;
  artifacts: GatewayArtifactReference[];
  occurredAt: string;
};

export type GatewayConversationSnapshot = GatewayConversationSummary & {
  eventRetentionFloor: number;
  messages: GatewayMessage[];
  runs: GatewayRun[];
  roles: GatewayRoleProjection[];
  events: GatewayRoleEvent[];
};

export type GatewayConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "paused"
  | "access_revoked";

export type GatewayStreamErrorCode =
  | "access_revoked"
  | "projection_unavailable";

export type GatewaySnapshotRequired = {
  conversationId: string;
  latestSequence: number;
  reason?: "retention_gap" | "artifact_access_changed";
};

export type GatewayStreamCallbacks = {
  onEvent: (event: GatewayRoleEvent) => void;
  onExecutionProgress?: (progress: GatewayExecutionUpdate) => void;
  onTeamProgress?: (progress: GatewayTeamProgress) => void;
  onSnapshotRequired?: (snapshot: GatewaySnapshotRequired) => void;
  onConnection?: (state: GatewayConnectionState) => void;
  onAccessRevoked?: () => void;
  onStreamError?: (code: GatewayStreamErrorCode) => void;
};

export type GatewayStartInput = {
  initialMessage: string;
  idempotencyKey: string;
  signal?: AbortSignal;
};

export type GatewayMessageInput = {
  text: string;
  idempotencyKey: string;
  signal?: AbortSignal;
};

export type GatewayStreamInput = GatewayStreamCallbacks & {
  conversationId: string;
  afterSequence: number;
  signal?: AbortSignal;
};

export interface GatewayConversationClient extends Partial<import("./team").GatewayTeamClient> {
  getResearch(conversationId: string, runSpecId: string, signal?: AbortSignal): Promise<import("./researchContent").GatewayResearchContent>;
  getEvidenceDetail(conversationId: string, runSpecId: string, evidenceLinkId: string, signal?: AbortSignal): Promise<import("./researchContent").GatewayEvidenceDetail>;
  getTaskTrace(conversationId: string, runSpecId: string, taskId: string, signal?: AbortSignal): Promise<import("./researchContent").GatewayTaskTrace>;
  getSession(signal?: AbortSignal): Promise<GatewaySession>;
  listConversations(signal?: AbortSignal): Promise<GatewayConversationList>;
  startConversation(input: GatewayStartInput): Promise<GatewaySubmissionReceipt>;
  sendMessage(
    conversationId: string,
    input: GatewayMessageInput,
  ): Promise<GatewaySubmissionReceipt>;
  getSnapshot(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<GatewayConversationSnapshot>;
  streamEvents(input: GatewayStreamInput): Promise<void>;
}
