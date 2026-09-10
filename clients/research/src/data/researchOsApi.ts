import type { components } from "../contracts/v1";

type Schemas = components["schemas"];
const baseUrl = "/api/v1";

export class ResearchOsRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ResearchOsRequestError";
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

async function errorDetails(response: Response): Promise<{
  message?: string;
  code?: string;
  requestId?: string;
}> {
  try {
    const body: unknown = await response.json();
    const bodyRecord = record(body);
    const error = record(bodyRecord?.error);
    return {
      message: typeof error?.message === "string"
        ? error.message
        : typeof bodyRecord?.detail === "string"
          ? bodyRecord.detail
          : undefined,
      code: typeof error?.code === "string" ? error.code : undefined,
      requestId: typeof error?.request_id === "string"
        ? error.request_id
        : undefined,
    };
  } catch {
    return {};
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const details = await errorDetails(response);
    throw new ResearchOsRequestError(
      details.message ?? `Research OS request failed (${response.status})`,
      response.status,
      details.code,
      details.requestId ?? response.headers.get("x-request-id") ?? undefined,
    );
  }
  return response.json() as Promise<T>;
}

export type Monitor = Schemas["CaseMonitorDTO"];
export type MonitorDetail = Schemas["CaseMonitorDetailResponse"];
export type RunEvent = Schemas["ResearchRunEventsItemDTO"];
export type ActiveResearchRun = Schemas["ActiveResearchRunDTO"];
export type ResearchRunArchive = Schemas["ResearchRunArchiveDTO"];
export type ResearchWorkerStatus = Schemas["ResearchWorkerStatusDTO"];
export type Graph = Schemas["GraphResponse"];
export type FundExposure = Schemas["FundExposureResponse"];
export type MarketExpression = Schemas["MarketExpressionResponse"];
export type SourceStatementOptions = Schemas["SourceStatementOptionsResponse"];
export type KeyFactorCandidateRuns = Schemas["KeyFactorCandidateRunsResponse"];
export type MarketInstrumentBindings = Schemas["MarketInstrumentBindingsResponse"];
export type MarketInstrumentCatalog = Schemas["MarketInstrumentCatalogResponse"];
export type ResearchNetwork = Schemas["ResearchNetworkResponse"];
export type MetricDefinition = Schemas["MetricDefinitionDTO"];
export type OutcomeBinding = Schemas["OutcomeBindingDTO"];
export type Researchability = Schemas["ResearchabilityDTO"];
export type MechanismTemplate = Schemas["MechanismTemplateDTO"];
export type CaseMechanismProtocol = Schemas["CaseMechanismProtocolDTO"];
export type AtomicClaimCandidate = Schemas["AtomicClaimCandidateDTO"];
export type AtomicClaimReview = Schemas["AtomicClaimReviewDTO"];
export type CaseRelationReview = Schemas["CaseRelationReviewDTO"];
export type ResearchSession = Schemas["ResearchSessionDTO"];
export type LegacyCaseAdmissionCandidate = Schemas["LegacyCaseAdmissionCandidateDTO"];
export type FundDisclosureSyncDetail = Schemas["FundDisclosureSyncDetailResponse"];
export type FundDisclosureSyncRun = Schemas["FundDisclosureSyncRunDTO"];
export type ActiveFundDisclosureSyncRun = Schemas["ActiveFundDisclosureSyncRunDTO"];
export type ForecastVerdictHistory = Schemas["ForecastVerdictHistoryResponse"];

export const researchOsApi = {
startFactorMonitorRun: (caseId: string, keyFactorId: string) => request<Schemas["ResearchRunResponse"]>(`/research-cases/${caseId}/monitor/factor-runs`, { method: "POST", body: JSON.stringify({ key_factor_id: keyFactorId }) }),
activeRuns: () => request<Schemas["ActiveResearchRunsResponse"]>("/research-runs/active"),
marketExpression: (caseId: string) => request<MarketExpression>(`/research-cases/${caseId}/market-expression`),
forecastVerdicts: (caseId: string) => request<ForecastVerdictHistory>(`/research-cases/${caseId}/forecast-verdicts?cutoff=${encodeURIComponent(new Date().toISOString())}`),
createForecastTarget: (caseId: string, input: Schemas["CreateForecastTargetRequest"]) => request<Schemas["ForecastTargetDTO"]>(`/research-cases/${caseId}/forecast-targets`, { method: "POST", body: JSON.stringify(input) }),
recordActualMetricObservation: (caseId: string, input: Schemas["RecordActualMetricObservationRequest"]) => request<Schemas["ActualMetricObservationDTO"]>(`/research-cases/${caseId}/actual-metric-observations`, { method: "POST", body: JSON.stringify(input) }),
evaluateForecastTarget: (targetId: string, input: Schemas["EvaluateForecastTargetRequest"]) => request<Schemas["ForecastEvaluationCandidateDTO"]>(`/forecast-targets/${targetId}/evaluate`, { method: "POST", body: JSON.stringify(input) }),
createForecastVerdict: (candidateId: string, input: Schemas["CreateForecastVerdictRequest"]) => request<Schemas["ForecastVerdictDTO"]>(`/forecast-evaluations/${candidateId}/verdicts`, { method: "POST", body: JSON.stringify(input) }),
fundDisclosureSync: (caseId: string) => request<FundDisclosureSyncDetail>(`/research-cases/${caseId}/fund-disclosure-sync`),
saveFundDisclosureSync: (caseId: string, input: Schemas["SaveFundDisclosureSyncConfigRequest"]) => request<Schemas["FundDisclosureSyncConfigDTO"]>(`/research-cases/${caseId}/fund-disclosure-sync/config`, { method: "PUT", body: JSON.stringify(input) }),
startFundDisclosureSync: (caseId: string) => request<FundDisclosureSyncRun>(`/research-cases/${caseId}/fund-disclosure-sync/runs`, { method: "POST" }),
retryFundDisclosureSync: (caseId: string, runId: string) => request<FundDisclosureSyncRun>(`/research-cases/${caseId}/fund-disclosure-sync/runs/${runId}/retry`, { method: "POST" }),
sourceStatements: (caseId: string) => request<SourceStatementOptions>(`/research-cases/${caseId}/source-statements`),
keyFactorCandidateRuns: (caseId: string) => request<KeyFactorCandidateRuns>(`/research-cases/${caseId}/key-factor-candidate-runs`),
startKeyFactorCandidateRun: (caseId: string, input: Schemas["StartKeyFactorCandidateRunRequest"]) => request<Schemas["KeyFactorCandidateRunDTO"]>(`/research-cases/${caseId}/key-factor-candidate-runs`, { method: "POST", body: JSON.stringify(input) }),
marketInstruments: (caseId: string) => request<MarketInstrumentBindings>(`/research-cases/${caseId}/market-instruments`),
marketInstrumentCatalog: (query: string) => request<MarketInstrumentCatalog>(`/market-instruments?query=${encodeURIComponent(query)}`),
createMarketInstrumentBinding: (caseId: string, input: Schemas["RegisterMarketInstrumentBindingRequest"]) => request<Schemas["MarketInstrumentBindingDTO"]>(`/research-cases/${caseId}/market-instruments`, { method: "POST", body: JSON.stringify(input) }),
createFundamentalImpact: (caseId: string, factorId: string, input: Schemas["RegisterFundamentalImpactRequest"]) => request<Schemas["FundamentalImpactDTO"]>(`/research-cases/${caseId}/key-factors/${factorId}/fundamental-impacts`, { method: "POST", body: JSON.stringify(input) }),
createMarketObservation: (caseId: string, factorId: string, input: Schemas["RegisterMarketObservationRequest"]) => request<Schemas["MarketObservationDTO"]>(`/research-cases/${caseId}/key-factors/${factorId}/market-observations`, { method: "POST", body: JSON.stringify(input) }),
createReportClaim: (caseId: string, input: Schemas["RegisterReportClaimRequest"]) => request<Schemas["ReportClaimDTO"]>(`/research-cases/${caseId}/report-claims`, { method: "POST", body: JSON.stringify(input) }),
createKeyFactor: (caseId: string, input: Schemas["RegisterKeyFactorRequest"]) => request<Schemas["KeyFactorDTO"]>(`/research-cases/${caseId}/key-factors`, { method: "POST", body: JSON.stringify(input) }),
createClaimVerification: (caseId: string, factorId: string, input: Schemas["RegisterClaimVerificationRequest"]) => request<Schemas["ClaimVerificationDTO"]>(`/research-cases/${caseId}/key-factors/${factorId}/verifications`, { method: "POST", body: JSON.stringify(input) }),
researchability: (thesisId: string) => request<Researchability>(`/theses/${thesisId}/researchability`)
};
