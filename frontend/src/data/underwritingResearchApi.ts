/// <reference types="vite/client" />

import type { components } from "../contracts/v1";

type Schemas = components["schemas"];

export type UnderwritingErrorEnvelope = Schemas["UnderwritingErrorEnvelope"];
export type ResearchRevision = Schemas["ResearchRevisionResponse"];
export type ResearchRevisionHistory = Schemas["ResearchRevisionHistoryResponse"];
export type ResearchRevisionDiff = Schemas["ResearchRevisionDiffResponse"];
export type ResearchRevisionBoundary = Schemas["ResearchRevisionBoundaryResponse"];
export type CandidateEvidence = Schemas["CandidateEvidenceResponse"];
export type ResearchArchiveItem = Schemas["ResearchArchiveItemResponse"];
export type ResearchArchiveList = Schemas["ResearchArchiveListResponse"];

export type ResearchArchiveQuery = {
  query?: string;
  kind?: ResearchArchiveItem["object_kind"];
  limit?: number;
  cursor?: string;
};

export class UnderwritingResearchRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "UnderwritingResearchRequestError";
  }
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function underwritingError(value: unknown): UnderwritingErrorEnvelope | null {
  const envelope = objectRecord(value);
  const error = objectRecord(envelope?.error);
  if (
    envelope?.schema_version !== "underwriting.v1"
    || typeof error?.code !== "string"
    || typeof error?.message !== "string"
  ) {
    return null;
  }
  return envelope as UnderwritingErrorEnvelope;
}

async function requestError(response: Response): Promise<UnderwritingResearchRequestError> {
  let envelope: UnderwritingErrorEnvelope | null = null;
  try {
    envelope = underwritingError(await response.json());
  } catch {
    // A non-JSON response is still an HTTP error; keep the safe status fallback.
  }
  const error = envelope?.error;
  return new UnderwritingResearchRequestError(
    error?.message ?? `Underwriting research request failed (${response.status})`,
    response.status,
    error?.code,
    error?.request_id ?? response.headers.get("x-request-id") ?? undefined,
    error?.details,
  );
}

function defaultBaseUrl(): string {
  return "/api/underwriting/v1";
}

function archivePath(input: ResearchArchiveQuery | undefined): string {
  const params = new URLSearchParams();
  if (input?.query) params.set("query", input.query);
  if (input?.kind) params.set("kind", input.kind);
  if (input?.limit !== undefined) params.set("limit", String(input.limit));
  if (input?.cursor) params.set("cursor", input.cursor);
  const query = params.toString();
  return query ? `/research-archives?${query}` : "/research-archives";
}

function createHttpUnderwritingResearchApi(baseUrl = defaultBaseUrl()) {
  const request = async <T>(path: string): Promise<T> => {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "GET",
      credentials: "include",
    });
    if (!response.ok) throw await requestError(response);
    return response.json() as Promise<T>;
  };

  return {
    listArchives: (input?: ResearchArchiveQuery) =>
      request<ResearchArchiveList>(archivePath(input)),
    history: (objectId: string, versionKind: string) =>
      request<ResearchRevisionHistory>(
        `/objects/${encodeURIComponent(objectId)}/research-versions/${encodeURIComponent(versionKind)}`,
      ),
    revision: (revisionId: string) =>
      request<ResearchRevision>(`/research-versions/${encodeURIComponent(revisionId)}`),
    diff: (fromRevisionId: string, toRevisionId: string) =>
      request<ResearchRevisionDiff>(
        `/research-versions/${encodeURIComponent(fromRevisionId)}/diff/${encodeURIComponent(toRevisionId)}`,
      ),
    boundary: (revisionId: string) =>
      request<ResearchRevisionBoundary>(
        `/research-versions/${encodeURIComponent(revisionId)}/boundary`,
      ),
    candidateEvidence: (revisionId: string) =>
      request<CandidateEvidence>(
        `/research-versions/${encodeURIComponent(revisionId)}/candidate-evidence`,
      ),
  };
}

export type UnderwritingResearchApi = ReturnType<typeof createHttpUnderwritingResearchApi>;

let selectedUnderwritingResearchApi: UnderwritingResearchApi = createHttpUnderwritingResearchApi();

export function setUnderwritingResearchApi(api: UnderwritingResearchApi): void {
  selectedUnderwritingResearchApi = api;
}

export function resetUnderwritingResearchApi(): void {
  selectedUnderwritingResearchApi = createHttpUnderwritingResearchApi();
}

export const underwritingResearchApi: UnderwritingResearchApi = {
  listArchives: (input) => selectedUnderwritingResearchApi.listArchives(input),
  history: (objectId, versionKind) =>
    selectedUnderwritingResearchApi.history(objectId, versionKind),
  revision: (revisionId) => selectedUnderwritingResearchApi.revision(revisionId),
  diff: (fromRevisionId, toRevisionId) =>
    selectedUnderwritingResearchApi.diff(fromRevisionId, toRevisionId),
  boundary: (revisionId) => selectedUnderwritingResearchApi.boundary(revisionId),
  candidateEvidence: (revisionId) => selectedUnderwritingResearchApi.candidateEvidence(revisionId),
};
