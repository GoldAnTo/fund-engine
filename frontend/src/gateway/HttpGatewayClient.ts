import type {
  GatewayArtifactKind,
  GatewayArtifactReference,
  GatewayConnectionState,
  GatewayConversationClient,
  GatewayConversationList,
  GatewayConversationSnapshot,
  GatewayConversationSummary,
  GatewayMessage,
  GatewayMessageInput,
  GatewayNativeRunStatus,
  GatewayReceipt,
  GatewayRunScope,
  GatewayRole,
  GatewayRoleEvent,
  GatewayRoleProjection,
  GatewayRoleStatus,
  GatewaySafeEventType,
  GatewaySafeReasonCode,
  GatewaySession,
  GatewaySubmissionReceipt,
  GatewaySnapshotRequired,
  GatewayStartInput,
  GatewayStreamErrorCode,
  GatewayStreamInput,
  GatewayTeamProgress,
} from "./contracts";
import { GatewaySchemaError } from "./GatewaySchemaError";
import { parseExecutionProgress, parseExecutionUpdate } from "./executionProgress";
import { parseResearchContent, parseEvidenceDetail, parseTaskTrace } from "./researchContent";
import { parseTeam, parseTeamReceipt, type TeamMessageInput, type TeamCommandInput, type TeamReviewInput } from "./team";
import { parseStudy, parseStudyList, parseStudyDetail, parseStudyActivity, parseStudyRevision, parseStudyMonitor, type CompanyStudyClient, type CreateStudy, type StudyKind, type ConfigureStudyMonitor } from './companyStudy';
export { GatewaySchemaError } from "./GatewaySchemaError";

const roleValues = new Set<GatewayRole>([
  "scope_identity",
  "sources_evidence",
  "analysis_counter_evidence",
  "compilation_checks",
]);
const roleStatusValues = new Set<GatewayRoleStatus>([
  "queued",
  "running",
  "blocked",
  "completed",
  "failed",
  "cancelled",
]);
const nativeRunStatusValues = new Set<GatewayNativeRunStatus>([
  "queued",
  "running",
  "waiting_for_sources",
  "succeeded",
  "failed",
  "cancelled",
]);
const eventTypeValues = new Set<GatewaySafeEventType>([
  "role_queued",
  "role_started",
  "role_progress",
  "role_blocked",
  "role_completed",
  "role_failed",
  "identity_decision",
  "source_waiting",
  "evidence_available",
  "rejected_source",
  "candidate",
  "gap",
  "draft_ref",
  "validation",
  "command_accepted",
  "command_rejected",
]);
const reasonCodeValues = new Set<GatewaySafeReasonCode>([
  "unsupported_in_gateway_p0",
  "native_execution_failed",
  "authorization_required",
  "cutoff_unavailable",
  "source_policy_blocked",
  "source_unavailable",
  "evidence_gap",
  "validation_failed",
  "command_rejected",
]);
const artifactKindValues = new Set<GatewayArtifactKind>([
  "research_case",
  "research_run",
  "evidence_link",
  "document_version",
  "draft",
  "validation",
]);
const gatewayScopeSourceRoleValues = new Set(["company_disclosure", "licensed_provider"]);

type FetchLike = typeof fetch;
type Sleep = (milliseconds: number, signal?: AbortSignal) => Promise<void>;

export type HttpGatewayClientOptions = {
  baseUrl?: string;
  getAuthorization?: () => string | null | undefined;
  fetch?: FetchLike;
  requestTimeoutMs?: number;
  sseHandshakeTimeoutMs?: number;
  sseIdleTimeoutMs?: number;
  reconnect?: {
    initialDelayMs?: number;
    maxDelayMs?: number;
  };
  sleep?: Sleep;
};

type SseFrame = {
  event: string;
  id?: string;
  data: string;
};

type StreamOutcome = "closed" | "snapshot_required" | "access_revoked" | "paused";

export class GatewayHttpError extends Error {
  readonly status: number;
  readonly code?: "company_study_revision_changed";

  constructor(status: number, code?: "company_study_revision_changed") {
    super(`Gateway request failed with HTTP ${status}`);
    this.name = "GatewayHttpError";
    this.status = status;
    this.code = code;
  }
}

export class GatewayTimeoutError extends Error {
  constructor() {
    super("Gateway request timed out; delivery may be unknown");
    this.name = "GatewayTimeoutError";
  }
}

export class HttpGatewayClient implements GatewayConversationClient, CompanyStudyClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  private readonly getAuthorization?: () => string | null | undefined;
  private readonly initialDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly sleep: Sleep;
  private readonly requestTimeoutMs: number;
  private readonly sseHandshakeTimeoutMs: number;
  private readonly sseIdleTimeoutMs: number;

  constructor(options: HttpGatewayClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/$/, "");
    this.fetchImpl = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.getAuthorization = options.getAuthorization;
    this.initialDelayMs = options.reconnect?.initialDelayMs ?? 400;
    this.maxDelayMs = options.reconnect?.maxDelayMs ?? 5_000;
    this.sleep = options.sleep ?? sleepWithAbort;
    this.requestTimeoutMs = options.requestTimeoutMs ?? 30_000;
    this.sseHandshakeTimeoutMs = options.sseHandshakeTimeoutMs ?? 30_000;
    this.sseIdleTimeoutMs = options.sseIdleTimeoutMs ?? 45_000;
  }

  async getSession(signal?: AbortSignal): Promise<GatewaySession> {
    return parseSession(await this.getJson("/research-session", signal));
  }

  async listStudies(signal?:AbortSignal) { return parseStudyList(await this.getJson('/company-studies',signal)); }
  async getStudy(id:string,signal?:AbortSignal) { return parseStudyDetail(await this.getJson(`/company-studies/${encodeURIComponent(id)}`,signal),id); }
  async createStudy(body:CreateStudy,key:string,signal?:AbortSignal) { return parseStudy(await this.postJson('/company-studies',body,key,signal)); }
  async createStudyActivity(id:string,body:{kind:StudyKind;text:string},key:string,signal?:AbortSignal) { return parseStudyActivity(await this.postJson(`/company-studies/${encodeURIComponent(id)}/activities`,body,key,signal),id); }
  async linkStudyConversation(id:string,body:{conversation_id:string},key:string,signal?:AbortSignal) { return parseStudyActivity(await this.postJson(`/company-studies/${encodeURIComponent(id)}/links`,body,key,signal),id); }
  async adoptStudyRevision(id:string,body:{activity_id:string;expected_revision:number;note:string},key:string,signal?:AbortSignal) { return parseStudyRevision(await this.postJson(`/company-studies/${encodeURIComponent(id)}/revisions`,body,key,signal)); }
  async configureStudyMonitor(id:string,body:ConfigureStudyMonitor,key:string,signal?:AbortSignal) { return parseStudyMonitor(await this.postJson(`/company-studies/${encodeURIComponent(id)}/monitor`,body,key,signal)); }
  async retryStudyActivity(id:string,activityId:string,key:string,signal?:AbortSignal) { return parseStudyActivity(await this.postJson(`/company-studies/${encodeURIComponent(id)}/activities/${encodeURIComponent(activityId)}/retry`,{},key,signal),id); }

  async getResearch(conversationId: string, runSpecId: string, signal?: AbortSignal) {
    return parseResearchContent(await this.getJson(`${this.runPath(conversationId, runSpecId)}/research`, signal), { conversationId, runSpecId });
  }

  async getEvidenceDetail(conversationId: string, runSpecId: string, evidenceLinkId: string, signal?: AbortSignal) {
    return parseEvidenceDetail(await this.getJson(`${this.runPath(conversationId, runSpecId)}/evidence/${encodeURIComponent(evidenceLinkId)}`, signal), { conversationId, runSpecId }, evidenceLinkId);
  }

  async getTaskTrace(conversationId: string, runSpecId: string, taskId: string, signal?: AbortSignal) {
    return parseTaskTrace(await this.getJson(`${this.runPath(conversationId, runSpecId)}/tasks/${encodeURIComponent(taskId)}/trace`, signal), { conversationId, runSpecId }, taskId);
  }

  async getTeam(conversationId: string, runSpecId: string, signal?: AbortSignal) {
    return parseTeam(await this.getJson(`${this.runPath(conversationId, runSpecId)}/team`, signal), { conversationId, runSpecId });
  }

  async sendTeamMessage(conversationId: string, runSpecId: string, input: TeamMessageInput) {
    const { idempotencyKey, signal, ...body } = input;
    return parseTeamReceipt(await this.postJson(`${this.runPath(conversationId, runSpecId)}/team/messages`, body, idempotencyKey, signal), { conversationId, runSpecId });
  }

  async commandTeam(conversationId: string, runSpecId: string, input: TeamCommandInput) {
    const { idempotencyKey, signal, ...body } = input;
    return parseTeamReceipt(await this.postJson(`${this.runPath(conversationId, runSpecId)}/team/commands`, body, idempotencyKey, signal), { conversationId, runSpecId });
  }

  async reviewTeam(conversationId: string, runSpecId: string, input: TeamReviewInput) {
    const { idempotencyKey, signal, ...body } = input;
    return parseTeamReceipt(await this.postJson(`${this.runPath(conversationId, runSpecId)}/team/reviews`, body, idempotencyKey, signal), { conversationId, runSpecId });
  }

  private runPath(conversationId: string, runSpecId: string): string {
    return `/research-conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runSpecId)}`;
  }

  async listConversations(signal?: AbortSignal): Promise<GatewayConversationList> {
    return parseConversationList(
      await this.getJson("/research-conversations", signal),
    );
  }

  async startConversation(
    input: GatewayStartInput,
  ): Promise<GatewaySubmissionReceipt> {
    return parseSubmissionReceipt(
      await this.postJson(
        "/research-conversations",
        { initial_message: input.initialMessage },
        input.idempotencyKey,
        input.signal,
      ),
    );
  }

  async sendMessage(
    conversationId: string,
    input: GatewayMessageInput,
  ): Promise<GatewaySubmissionReceipt> {
    return parseSubmissionReceipt(
      await this.postJson(
        `/research-conversations/${encodeURIComponent(conversationId)}/messages`,
        { text: input.text },
        input.idempotencyKey,
        input.signal,
      ),
    );
  }

  async getSnapshot(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<GatewayConversationSnapshot> {
    const snapshot = parseSnapshot(
      await this.getJson(
        `/research-conversations/${encodeURIComponent(conversationId)}/snapshot`,
        signal,
      ),
    );
    if (snapshot.conversationId !== conversationId) {
      throw new GatewaySchemaError("Gateway snapshot does not match its requested conversation");
    }
    return snapshot;
  }

  async streamEvents(input: GatewayStreamInput): Promise<void> {
    let cursor = requireCursor(input.afterSequence);
    let reconnectAttempt = 0;

    while (!input.signal?.aborted) {
      let outcome: StreamOutcome | undefined;
      const watchdog = createSseWatchdog(
        input.signal, this.sseHandshakeTimeoutMs, this.sseIdleTimeoutMs,
      );
      try {
        input.onConnection?.(
          reconnectAttempt === 0 ? "connecting" : "reconnecting",
        );
        const response = await watchdog.headers(this.request(
          `/research-conversations/${encodeURIComponent(input.conversationId)}/events?after_sequence=${cursor}`,
          {
            method: "GET",
            headers: {
              Accept: "text/event-stream",
              "Last-Event-ID": String(cursor),
            },
          },
          watchdog.signal,
        ));
        if (!response.ok) {
          throw new GatewayHttpError(response.status);
        }
        if (!response.headers.get("Content-Type")?.includes("text/event-stream")) {
          throw new GatewaySchemaError("Gateway event response must be an SSE stream");
        }
        input.onConnection?.("live");

        outcome = await readSseFrames(response, watchdog.signal, (frame) => {
          if (frame.event === "execution_progress") {
            if (frame.id !== undefined) throw new GatewaySchemaError("Execution progress must not advance the event cursor");
            input.onExecutionProgress?.(parseExecutionUpdate(parseJson(frame.data)));
            return "continue";
          }
          if (frame.event === "team_progress") {
            if (frame.id !== undefined) throw new GatewaySchemaError("Team progress must not advance the event cursor");
            input.onTeamProgress?.(parseTeamProgress(parseJson(frame.data)));
            return "continue";
          }
          if (frame.event === "role_event") {
            const event = parseRoleEvent(frame.data);
            if (frame.id !== undefined && frame.id !== String(event.sequence)) {
              throw new GatewaySchemaError("Gateway event id must match its sequence");
            }
            if (event.sequence <= cursor) return "continue";
            if (event.sequence !== cursor + 1) {
              input.onSnapshotRequired?.({
                conversationId: input.conversationId,
                latestSequence: event.sequence,
                reason: "retention_gap",
              });
              return "snapshot_required";
            }
            input.onEvent(event);
            cursor = event.sequence;
            input.onConnection?.("live");
            return "continue";
          }
          if (frame.event === "snapshot_required") {
            input.onSnapshotRequired?.(
              parseSnapshotRequired(frame.data, input.conversationId, cursor),
            );
            return "snapshot_required";
          }
          if (frame.event === "stream_error") {
            const code = parseStreamErrorCode(frame.data);
            if (code === "access_revoked") {
              input.onAccessRevoked?.();
              input.onConnection?.("access_revoked");
              return "access_revoked";
            }
            input.onStreamError?.(code);
            input.onConnection?.("paused");
            return "paused";
          }
          throw new GatewaySchemaError("Gateway returned an unknown stream event");
        }, watchdog.activity);
      } catch (error) {
        if (input.signal?.aborted) return;
        if (isTerminalStreamError(error)) throw error;
        // Network faults and temporary gateway availability failures are
        // observed as a transient connection loss.  Resume from the last
        // accepted safe cursor after the bounded backoff below.
      } finally {
        watchdog.close();
      }

      if (
        input.signal?.aborted ||
        outcome === "snapshot_required" ||
        outcome === "access_revoked" ||
        outcome === "paused"
      ) {
        return;
      }

      reconnectAttempt += 1;
      input.onConnection?.("reconnecting");
      await this.sleep(
        reconnectDelay(
          reconnectAttempt,
          this.initialDelayMs,
          this.maxDelayMs,
        ),
        input.signal,
      );
    }
  }

  private async getJson(path: string, signal?: AbortSignal): Promise<unknown> {
    return this.withRequestDeadline(signal, async (requestSignal) => {
      const response = await this.request(path, { method: "GET" }, requestSignal);
      return readJson(response);
    });
  }

  private async postJson(
    path: string,
    body: object,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<unknown> {
    if (!idempotencyKey.trim()) {
      throw new GatewaySchemaError("Idempotency-Key is required");
    }
    return this.withRequestDeadline(signal, async (requestSignal) => {
      const response = await this.request(
        path,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify(body),
        },
        requestSignal,
      );
      return readJson(response);
    });
  }

  private async withRequestDeadline<T>(
    signal: AbortSignal | undefined,
    operation: (requestSignal: AbortSignal) => Promise<T>,
  ): Promise<T> {
    const controller = new AbortController();
    const forwardAbort = () => controller.abort(signal?.reason);
    let rejectOnAbort!: () => void;
    const aborted = new Promise<never>((_, reject) => {
      rejectOnAbort = () => reject(controller.signal.reason);
      controller.signal.addEventListener("abort", rejectOnAbort, { once: true });
    });
    signal?.addEventListener("abort", forwardAbort, { once: true });
    const timeout = window.setTimeout(
      () => controller.abort(new GatewayTimeoutError()),
      this.requestTimeoutMs,
    );
    try {
      if (signal?.aborted) forwardAbort();
      // Include body reading in the deadline. A timeout is an unknown write
      // outcome, so callers retain their existing idempotency key for retry.
      return await Promise.race([
        aborted,
        controller.signal.aborted ? aborted : operation(controller.signal),
      ]);
    } finally {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", forwardAbort);
      controller.signal.removeEventListener("abort", rejectOnAbort);
    }
  }

  private async request(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<Response> {
    const headers = new Headers(init.headers);
    const authorization = this.getAuthorization?.()?.trim();
    if (authorization) {
      headers.set("Authorization", authorization);
    }
    return this.fetchImpl(`${this.baseUrl}${path}`, {
      ...init,
      headers,
      signal,
      credentials: "include",
      cache: "no-store",
    });
  }
}

function isTerminalStreamError(error: unknown): boolean {
  if (error instanceof GatewaySchemaError) return true;
  if (!(error instanceof GatewayHttpError)) return false;
  if (error.status === 401 || error.status === 403 || error.status === 422) {
    return true;
  }
  return error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429;
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) {
    let code: "company_study_revision_changed" | undefined;
    if (response.status === 409) {
      // Carry only a known, confirmed rejection. Never expose raw error bodies.
      try {
        const body = await response.json();
        if (body?.schema_version === "v1" && body?.error?.code === "conflict" &&
            body?.error?.message === "company_study_revision_changed") code = "company_study_revision_changed";
      } catch { /* Unknown conflicts retain their original submission. */ }
    }
    throw new GatewayHttpError(response.status, code);
  }
  try {
    return await response.json();
  } catch {
    throw new GatewaySchemaError("Gateway response must be JSON");
  }
}

function parseSubmissionReceipt(value: unknown): GatewaySubmissionReceipt {
  if (hasReceiptKind(value, "intent")) {
    return parseRejectedIntentReceipt(value);
  }
  return parseReceipt(value);
}

function hasReceiptKind(value: unknown, receiptKind: string): boolean {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).receipt_kind === receiptKind
  );
}

function parseRejectedIntentReceipt(value: unknown): GatewaySubmissionReceipt {
  const record = exactRecord(value, [
    "receipt_kind",
    "intent_id",
    "conversation_id",
    "intent_kind",
    "outcome",
    "reason_code",
  ]);
  if (
    record.receipt_kind !== "intent" ||
    !isString(record.intent_id) ||
    !isString(record.conversation_id) ||
    record.intent_kind !== "unsupported" ||
    record.outcome !== "rejected" ||
    record.reason_code !== "unsupported_in_gateway_p0"
  ) {
    throw new GatewaySchemaError("Gateway rejected-intent receipt is invalid");
  }
  return {
    receiptKind: "intent",
    intentId: record.intent_id,
    conversationId: record.conversation_id,
    intentKind: "unsupported",
    outcome: "rejected",
    reasonCode: "unsupported_in_gateway_p0",
  };
}

function parseReceipt(value: unknown): GatewayReceipt {
  const record = exactRecord(value, [
    "conversation_id",
    "intent_id",
    "run_spec_id",
    "native_case_id",
    "native_run_id",
    "status",
    "latest_sequence",
  ]);
  if (
    !isString(record.conversation_id) ||
    !isString(record.intent_id) ||
    !isString(record.run_spec_id) ||
    !isString(record.native_case_id) ||
    !isString(record.native_run_id) ||
    record.status !== "queued"
  ) {
    throw new GatewaySchemaError("Gateway receipt is invalid");
  }
  return {
    conversationId: record.conversation_id,
    intentId: record.intent_id,
    runSpecId: record.run_spec_id,
    nativeCaseId: record.native_case_id,
    nativeRunId: record.native_run_id,
    status: "queued",
    latestSequence: requireNonNegativeInteger(record.latest_sequence),
  };
}

function parseSession(value: unknown): GatewaySession {
  const record = exactRecord(value, ["tenant_id", "roles", "subject_id"]);
  if (
    !isString(record.tenant_id) ||
    !Array.isArray(record.roles) ||
    !record.roles.every(isString) ||
    !(isString(record.subject_id) || record.subject_id === null)
  ) {
    throw new GatewaySchemaError("Gateway session is invalid");
  }
  return {
    tenantId: record.tenant_id,
    roles: [...record.roles],
    subjectId: record.subject_id,
  };
}

function parseConversationList(value: unknown): GatewayConversationList {
  const record = exactRecord(value, ["conversations"]);
  if (!Array.isArray(record.conversations)) {
    throw new GatewaySchemaError("Gateway conversation list is invalid");
  }
  return { conversations: record.conversations.map(parseConversationSummary) };
}

function parseConversationSummary(value: unknown): GatewayConversationSummary {
  const record = exactRecord(value, [
    "conversation_id",
    "title",
    "created_at",
    "updated_at",
    "latest_sequence",
  ]);
  if (
    !isString(record.conversation_id) ||
    !(isString(record.title) || record.title === null) ||
    !isDateTime(record.created_at) ||
    !isDateTime(record.updated_at)
  ) {
    throw new GatewaySchemaError("Gateway conversation summary is invalid");
  }
  return {
    conversationId: record.conversation_id,
    title: record.title,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
    latestSequence: requireNonNegativeInteger(record.latest_sequence),
  };
}

function parseSnapshot(value: unknown): GatewayConversationSnapshot {
  const record = exactRecord(value, [
    "conversation_id",
    "title",
    "created_at",
    "updated_at",
    "latest_sequence",
    "event_retention_floor",
    "messages",
    "runs",
    "roles",
    "events",
  ]);
  if (
    !Array.isArray(record.messages) ||
    !Array.isArray(record.runs) ||
    !Array.isArray(record.roles) ||
    !Array.isArray(record.events)
  ) {
    throw new GatewaySchemaError("Gateway snapshot is invalid");
  }
  const summary = parseConversationSummary({
    conversation_id: record.conversation_id,
    title: record.title,
    created_at: record.created_at,
    updated_at: record.updated_at,
    latest_sequence: record.latest_sequence,
  });
  return {
    ...summary,
    eventRetentionFloor: requirePositiveInteger(record.event_retention_floor),
    messages: record.messages.map(parseMessage),
    runs: record.runs.map(parseRun),
    roles: record.roles.map(parseRoleProjection),
    events: record.events.map(parseRoleEventValue),
  };
}

function parseMessage(value: unknown): GatewayMessage {
  const record = exactRecord(
    value,
    ["id", "sequence", "text", "created_at", "kind"],
    ["kind"],
  );
  if (
    !isString(record.id) ||
    !isString(record.text) ||
    !isDateTime(record.created_at) ||
    !(record.kind === undefined || record.kind === "user" || record.kind === "system")
  ) {
    throw new GatewaySchemaError("Gateway message is invalid");
  }
  const message: GatewayMessage = {
    id: record.id,
    sequence: requirePositiveInteger(record.sequence),
    text: record.text,
    createdAt: record.created_at,
  };
  if (record.kind === "user" || record.kind === "system") {
    message.kind = record.kind;
  }
  return message;
}

function parseRun(value: unknown) {
  const record = exactRecord(value, [
    "run_spec_id",
    "native_case_id",
    "native_run_id",
    "status",
    "parent_run_spec_id",
    "created_at",
    "scope",
    "execution",
  ], ["scope", "execution"]);
  if (
    !isString(record.run_spec_id) ||
    !isString(record.native_case_id) ||
    !isString(record.native_run_id) ||
    !nativeRunStatusValues.has(record.status as GatewayNativeRunStatus) ||
    !(isString(record.parent_run_spec_id) || record.parent_run_spec_id === null) ||
    !isDateTime(record.created_at)
  ) {
    throw new GatewaySchemaError("Gateway run is invalid");
  }
  return {
    runSpecId: record.run_spec_id,
    nativeCaseId: record.native_case_id,
    nativeRunId: record.native_run_id,
    status: record.status as GatewayNativeRunStatus,
    parentRunSpecId: record.parent_run_spec_id,
    createdAt: record.created_at,
    ...(record.scope === undefined ? {} : { scope: record.scope === null ? null : parseRunScope(record.scope) }),
    ...(record.execution === undefined || record.execution === null ? {} : { execution: parseExecutionProgress(record.execution) }),
  };
}

function parseRunScope(value: unknown): GatewayRunScope {
  const record = exactRecord(value, [
    "topic",
    "subject_label",
    "evidence_cutoff_at",
    "case_evidence_cutoff_date",
    "source_policy",
  ]);
  if (
    !isString(record.topic) ||
    !record.topic.trim() ||
    !(record.subject_label === null || (isString(record.subject_label) && Boolean(record.subject_label.trim()))) ||
    !isDateTime(record.evidence_cutoff_at) ||
    !(record.case_evidence_cutoff_date === null || isDateOnly(record.case_evidence_cutoff_date))
  ) {
    throw new GatewaySchemaError("Gateway run scope is invalid");
  }
  const sourcePolicy = parseRunScopeSourcePolicy(record.source_policy);
  return {
    topic: record.topic,
    subjectLabel: record.subject_label,
    evidenceCutoffAt: record.evidence_cutoff_at,
    caseEvidenceCutoffDate: record.case_evidence_cutoff_date,
    sourcePolicy,
  };
}

function parseRunScopeSourcePolicy(value: unknown): GatewayRunScope["sourcePolicy"] {
  const record = exactRecord(value, [
    "input_kind",
    "allowed_source_types",
    "allowed_source_roles",
    "has_intake_material",
  ]);
  if (
    (record.input_kind !== "topic" && record.input_kind !== "material") ||
    !Array.isArray(record.allowed_source_types) ||
    !record.allowed_source_types.every((item) => isString(item) && Boolean(item.trim())) ||
    new Set(record.allowed_source_types).size !== record.allowed_source_types.length ||
    !Array.isArray(record.allowed_source_roles) ||
    !record.allowed_source_roles.every((item) => gatewayScopeSourceRoleValues.has(item)) ||
    new Set(record.allowed_source_roles).size !== record.allowed_source_roles.length ||
    typeof record.has_intake_material !== "boolean"
  ) {
    throw new GatewaySchemaError("Gateway run source policy is invalid");
  }
  return {
    inputKind: record.input_kind,
    allowedSourceTypes: [...record.allowed_source_types],
    allowedSourceRoles: [...record.allowed_source_roles] as GatewayRunScope["sourcePolicy"]["allowedSourceRoles"],
    hasIntakeMaterial: record.has_intake_material,
  };
}

function parseRoleProjection(value: unknown): GatewayRoleProjection {
  const record = exactRecord(value, [
    "run_spec_id",
    "role",
    "status",
    "latest_sequence",
  ]);
  if (
    !isString(record.run_spec_id) ||
    !roleValues.has(record.role as GatewayRole) ||
    !roleStatusValues.has(record.status as GatewayRoleStatus)
  ) {
    throw new GatewaySchemaError("Gateway role projection is invalid");
  }
  return {
    runSpecId: record.run_spec_id,
    role: record.role as GatewayRole,
    status: record.status as GatewayRoleStatus,
    latestSequence: requireNonNegativeInteger(record.latest_sequence),
  };
}

function parseRoleEvent(data: string): GatewayRoleEvent {
  return parseRoleEventValue(parseJson(data));
}

function parseTeamProgress(value: unknown): GatewayTeamProgress {
  const record = exactRecord(value, [
    "run_spec_id",
    "revision",
    "event_sequence",
    "status",
  ]);
  if (
    !isString(record.run_spec_id) ||
    !isNonNegativeInteger(record.revision) || record.revision < 1 ||
    !isNonNegativeInteger(record.event_sequence) ||
    (record.status !== "active" && record.status !== "paused" && record.status !== "cancelled")
  ) {
    throw new GatewaySchemaError("Gateway team progress is invalid");
  }
  return {
    runSpecId: record.run_spec_id,
    revision: record.revision,
    eventSequence: record.event_sequence,
    status: record.status,
  };
}

function parseRoleEventValue(value: unknown): GatewayRoleEvent {
  const record = exactRecord(value, [
    "sequence",
    "run_spec_id",
    "role",
    "type",
    "status",
    "summary",
    "reason_code",
    "artifacts",
    "occurred_at",
  ]);
  if (
    !isString(record.run_spec_id) ||
    !roleValues.has(record.role as GatewayRole) ||
    !eventTypeValues.has(record.type as GatewaySafeEventType) ||
    !(record.status === null || roleStatusValues.has(record.status as GatewayRoleStatus)) ||
    !(record.summary === null || isString(record.summary)) ||
    !(record.reason_code === null || reasonCodeValues.has(record.reason_code as GatewaySafeReasonCode)) ||
    !Array.isArray(record.artifacts) ||
    record.artifacts.length > 32 ||
    !isDateTime(record.occurred_at)
  ) {
    throw new GatewaySchemaError("Gateway role event is invalid");
  }
  return {
    sequence: requirePositiveInteger(record.sequence),
    runSpecId: record.run_spec_id,
    role: record.role as GatewayRole,
    type: record.type as GatewaySafeEventType,
    status: record.status as GatewayRoleStatus | null,
    summary: record.summary,
    reasonCode: record.reason_code as GatewaySafeReasonCode | null,
    artifacts: record.artifacts.map(parseArtifact),
    occurredAt: record.occurred_at,
  };
}

function parseArtifact(value: unknown): GatewayArtifactReference {
  const record = exactRecord(value, [
    "kind",
    "id",
    "case_id",
    "locator_available",
  ], ["case_id", "locator_available"]);
  if (
    !artifactKindValues.has(record.kind as GatewayArtifactKind) ||
    !isString(record.id) ||
    !(record.case_id === undefined || record.case_id === null || isString(record.case_id)) ||
    !(record.locator_available === undefined || record.locator_available === null || typeof record.locator_available === "boolean")
  ) {
    throw new GatewaySchemaError("Gateway artifact reference is invalid");
  }
  const artifact: GatewayArtifactReference = {
    kind: record.kind as GatewayArtifactKind,
    id: record.id,
  };
  if (isString(record.case_id)) artifact.caseId = record.case_id;
  if (typeof record.locator_available === "boolean") {
    artifact.locatorAvailable = record.locator_available;
  }
  return artifact;
}

function parseSnapshotRequired(
  data: string,
  conversationId: string,
  latestSequence: number,
): GatewaySnapshotRequired {
  const record = exactRecord(
    parseJson(data),
    ["conversation_id", "latest_sequence", "reason"],
    ["conversation_id", "latest_sequence", "reason"],
  );
  if (
    !(record.conversation_id === undefined || isString(record.conversation_id)) ||
    !(record.latest_sequence === undefined || isNonNegativeInteger(record.latest_sequence)) ||
    !(
      record.reason === undefined ||
      record.reason === "retention_gap" ||
      record.reason === "artifact_access_changed"
    )
  ) {
    throw new GatewaySchemaError("Gateway snapshot request is invalid");
  }
  const snapshot: GatewaySnapshotRequired = {
    conversationId: record.conversation_id ?? conversationId,
    latestSequence: record.latest_sequence ?? latestSequence,
  };
  if (
    record.reason === "retention_gap" ||
    record.reason === "artifact_access_changed"
  ) {
    snapshot.reason = record.reason;
  }
  return snapshot;
}

function parseStreamErrorCode(data: string): GatewayStreamErrorCode {
  const record = exactRecord(parseJson(data), ["code"]);
  if (
    record.code !== "access_revoked" &&
    record.code !== "projection_unavailable"
  ) {
    throw new GatewaySchemaError("Gateway stream error is invalid");
  }
  return record.code;
}

/** A fresh controller for each connection: headers deadline, then idle time.
 * No total-stream deadline: any nonempty transport chunk, including heartbeat
 * comments, renews the idle timer. Never advance the cursor here.
 */
function createSseWatchdog(parent: AbortSignal | undefined, handshakeMs: number, idleMs: number) {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(parent?.reason);
  let rejectAbort!: () => void;
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(controller.signal.reason);
    controller.signal.addEventListener("abort", rejectAbort, { once: true });
  });
  let timer: number | undefined;
  const arm = (milliseconds: number) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => controller.abort(new GatewayTimeoutError()), milliseconds);
  };
  parent?.addEventListener("abort", forwardAbort, { once: true });
  arm(handshakeMs);
  if (parent?.aborted) forwardAbort();
  return {
    signal: controller.signal,
    async headers(request: Promise<Response>): Promise<Response> {
      const response = await Promise.race([aborted, request.then(value => {
        // A mocked or unusual transport can resolve after abort. Dispose of
        // that stale body instead of leaving an abandoned connection open.
        if (controller.signal.aborted) {
          void value.body?.cancel().catch(() => undefined);
          throw controller.signal.reason;
        }
        return value;
      })]);
      arm(idleMs);
      return response;
    },
    activity() { if (!controller.signal.aborted) arm(idleMs); },
    close() {
      window.clearTimeout(timer);
      parent?.removeEventListener("abort", forwardAbort);
      controller.signal.removeEventListener("abort", rejectAbort);
      controller.abort();
    },
  };
}

async function readSseFrames(
  response: Response,
  signal: AbortSignal | undefined,
  onFrame: (frame: SseFrame) => "continue" | StreamOutcome,
  onChunk: () => void,
): Promise<StreamOutcome> {
  if (!response.body) {
    return "closed";
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  let event = "message";
  let id: string | undefined;
  let data: string[] = [];
  let cancelReader = false;
  const abortReader = () => {
    // Abort must also settle a reader currently waiting for the next chunk.
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", abortReader, { once: true });

  const dispatch = (): StreamOutcome | undefined => {
    if (data.length === 0) return undefined;
    const outcome = onFrame({ event, id, data: data.join("\n") });
    event = "message";
    id = undefined;
    data = [];
    return outcome === "continue" ? undefined : outcome;
  };

  const processLine = (line: string): StreamOutcome | undefined => {
    if (!line) return dispatch();
    if (line.startsWith(":")) return undefined;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1
      ? ""
      : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") event = value;
    if (field === "id") id = value;
    if (field === "data") data.push(value);
    return undefined;
  };

  try {
    while (!signal?.aborted) {
      const { done, value } = await reader.read();
      if (!done && value.byteLength > 0) onChunk();
      buffered += decoder.decode(value, { stream: !done });
      let lineEnd = buffered.search(/\r?\n/);
      while (lineEnd !== -1) {
        const line = buffered.slice(0, lineEnd);
        buffered = buffered.slice(lineEnd + (buffered[lineEnd] === "\r" ? 2 : 1));
        const outcome = processLine(line);
        if (outcome) {
          cancelReader = true;
          return outcome;
        }
        if (signal?.aborted) {
          cancelReader = true;
          return "closed";
        }
        lineEnd = buffered.search(/\r?\n/);
      }
      if (done) break;
    }
    if (!signal?.aborted && buffered) {
      const outcome = processLine(buffered.replace(/\r$/, ""));
      if (outcome) {
        cancelReader = true;
        return outcome;
      }
    }
    if (!signal?.aborted) {
      const outcome = dispatch();
      if (outcome) {
        cancelReader = true;
        return outcome;
      }
    }
    return "closed";
  } catch (error) {
    cancelReader = true;
    throw error;
  } finally {
    signal?.removeEventListener("abort", abortReader);
    if (cancelReader || signal?.aborted) {
      try {
        void reader.cancel().catch(() => undefined);
      } catch {
        // Closing the local observation is best effort; it must never issue a
        // Gateway command or obscure the original stream outcome.
      }
    }
    reader.releaseLock();
  }
}

function exactRecord(
  value: unknown,
  keys: string[],
  optionalKeys: string[] = [],
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new GatewaySchemaError();
  }
  const record = value as Record<string, unknown>;
  const allowed = new Set(keys);
  if (Object.keys(record).some((key) => !allowed.has(key))) {
    throw new GatewaySchemaError("Gateway payload contains unexpected fields");
  }
  for (const key of keys) {
    if (!optionalKeys.includes(key) && !(key in record)) {
      throw new GatewaySchemaError("Gateway payload is missing required fields");
    }
  }
  return record;
}

function parseJson(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    throw new GatewaySchemaError("Gateway event data must be JSON");
  }
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isDateTime(value: unknown): value is string {
  return isString(value) && !Number.isNaN(Date.parse(value));
}

function isDateOnly(value: unknown): value is string {
  return isString(value) && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function requireNonNegativeInteger(value: unknown): number {
  if (!isNonNegativeInteger(value)) {
    throw new GatewaySchemaError("Gateway sequence must be a non-negative integer");
  }
  return value;
}

function requirePositiveInteger(value: unknown): number {
  if (!isNonNegativeInteger(value) || value < 1) {
    throw new GatewaySchemaError("Gateway sequence must be a positive integer");
  }
  return value;
}

function requireCursor(value: number): number {
  return requireNonNegativeInteger(value);
}

function reconnectDelay(
  attempt: number,
  initialDelayMs: number,
  maxDelayMs: number,
): number {
  return Math.min(maxDelayMs, initialDelayMs * 2 ** Math.max(0, attempt - 1));
}

function sleepWithAbort(
  milliseconds: number,
  signal?: AbortSignal,
): Promise<void> {
  if (signal?.aborted || milliseconds <= 0) return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = window.setTimeout(finish, milliseconds);
    function finish(): void {
      signal?.removeEventListener("abort", finish);
      window.clearTimeout(timeout);
      resolve();
    }
    signal?.addEventListener("abort", finish, { once: true });
  });
}
