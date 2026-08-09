import type { components } from "../contracts/v1";

type Schemas = components["schemas"];
const baseUrl = (import.meta.env.VITE_RESEARCH_API_URL || "/api/v1").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { headers: { "content-type": "application/json", ...(init?.headers ?? {}) }, ...init });
  if (!response.ok) throw new Error(`Research OS request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export type Monitor = Schemas["CaseMonitorDTO"];
export type MonitorDetail = Schemas["CaseMonitorDetailResponse"];
export type RunEvent = Schemas["ResearchRunEventsItemDTO"];
export type ActiveResearchRun = Schemas["ActiveResearchRunDTO"];
export type ResearchRunArchive = Schemas["ResearchRunArchiveDTO"];
export type Graph = Schemas["GraphResponse"];
export type FundExposure = Schemas["FundExposureResponse"];
export type MarketExpression = Schemas["MarketExpressionResponse"];
export type SourceStatementOptions = Schemas["SourceStatementOptionsResponse"];
export type ResearchNetwork = Schemas["ResearchNetworkResponse"];
export type MetricDefinition = Schemas["MetricDefinitionDTO"];
export type OutcomeBinding = Schemas["OutcomeBindingDTO"];
export type Researchability = Schemas["ResearchabilityDTO"];
export type MechanismTemplate = Schemas["MechanismTemplateDTO"];
export type CaseMechanismProtocol = Schemas["CaseMechanismProtocolDTO"];
export type AtomicClaimCandidate = Schemas["AtomicClaimCandidateDTO"];
export type AtomicClaimReview = Schemas["AtomicClaimReviewDTO"];

const httpResearchOsApi = {
  monitor: (caseId: string) => request<MonitorDetail>(`/research-cases/${caseId}/monitor`),
  saveMonitor: (caseId: string, input: Schemas["UpdateCaseMonitorRequest"]) => request<Monitor>(`/research-cases/${caseId}/monitor`, { method: "PUT", body: JSON.stringify(input) }),
  setMonitorStatus: (caseId: string, status: "active" | "paused", changeReason: string) => request<Monitor>(`/research-cases/${caseId}/monitor/${status}`, { method: "POST", body: JSON.stringify({ actor: "human:researcher", change_reason: changeReason }) }),
  scopeHistory: (caseId: string) => request<Schemas["EventResearchScopeHistoryResponse"]>(`/event-research/${caseId}/scope-history`),
  startMonitorRun: (caseId: string) => request<Schemas["ResearchRunResponse"]>(`/research-cases/${caseId}/monitor/runs`, { method: "POST" }),
  startFactorMonitorRun: (caseId: string, keyFactorId: string) => request<Schemas["ResearchRunResponse"]>(`/research-cases/${caseId}/monitor/factor-runs`, { method: "POST", body: JSON.stringify({ key_factor_id: keyFactorId }) }),
  cancelRun: (runId: string, changeReason: string) => request<Schemas["CancelRunResponse"]>(`/research-runs/${runId}/cancel`, { method: "POST", body: JSON.stringify({ actor: "human:researcher", change_reason: changeReason }) }),
  runEvents: (runId: string) => request<Schemas["ResearchRunEventsResponse"]>(`/research-runs/${runId}/events`),
  activeRuns: () => request<Schemas["ActiveResearchRunsResponse"]>("/research-runs/active"),
  runs: () => request<Schemas["ResearchRunArchiveResponse"]>("/research-runs"),
  network: () => request<ResearchNetwork>("/event-research/network"),
  caseRelations: (caseId: string) => request<ResearchNetwork>(`/event-research/${caseId}/relations`),
  graph: (caseId: string) => request<Graph>(`/research-cases/${caseId}/graph?research_mode=true`),
  exposure: (caseId: string) => request<FundExposure>(`/research-cases/${caseId}/fund-exposure`),
  marketExpression: (caseId: string) => request<MarketExpression>(`/research-cases/${caseId}/market-expression`),
  sourceStatements: (caseId: string) => request<SourceStatementOptions>(`/research-cases/${caseId}/source-statements`),
  createReportClaim: (caseId: string, input: Schemas["RegisterReportClaimRequest"]) => request<Schemas["ReportClaimDTO"]>(`/research-cases/${caseId}/report-claims`, { method: "POST", body: JSON.stringify(input) }),
  createKeyFactor: (caseId: string, input: Schemas["RegisterKeyFactorRequest"]) => request<Schemas["KeyFactorDTO"]>(`/research-cases/${caseId}/key-factors`, { method: "POST", body: JSON.stringify(input) }),
  createClaimVerification: (caseId: string, factorId: string, input: Schemas["RegisterClaimVerificationRequest"]) => request<Schemas["ClaimVerificationDTO"]>(`/research-cases/${caseId}/key-factors/${factorId}/verifications`, { method: "POST", body: JSON.stringify(input) }),
  metrics: () => request<MetricDefinition[]>("/metric-definitions"),
  createMetric: (input: Schemas["MetricDefinitionRequest"]) => request<MetricDefinition>("/metric-definitions", { method: "POST", body: JSON.stringify(input) }),
  createOutcomeBinding: (thesisId: string, input: Schemas["OutcomeBindingRequest"]) => request<OutcomeBinding>(`/theses/${thesisId}/outcome-bindings`, { method: "POST", body: JSON.stringify(input) }),
  approveOutcomeBinding: (bindingId: string, input: Schemas["ApproveOutcomeBindingRequest"]) => request<OutcomeBinding>(`/outcome-bindings/${bindingId}/approve`, { method: "POST", body: JSON.stringify(input) }),
  researchability: (thesisId: string) => request<Researchability>(`/theses/${thesisId}/researchability`),
  mechanismTemplates: () => request<MechanismTemplate[]>("/mechanism-templates"),
  caseMechanismProtocol: (caseId: string) => request<CaseMechanismProtocol>(`/research-cases/${caseId}/mechanism-protocol`),
  selectMechanismTemplate: (caseId: string, input: Schemas["SelectMechanismTemplateRequest"]) => request<Schemas["MechanismSelectionDTO"]>(`/research-cases/${caseId}/mechanism-selection`, { method: "POST", body: JSON.stringify(input) }),
  createVerificationRule: (caseId: string, edgeId: string, input: Schemas["VerificationRuleRequest"]) => request<Schemas["VerificationRuleDTO"]>(`/research-cases/${caseId}/mechanism-edges/${edgeId}/verification-rules`, { method: "POST", body: JSON.stringify(input) }),
  atomicClaims: (caseId: string) => request<Schemas["AtomicClaimQueueResponse"]>(`/research-cases/${caseId}/atomic-claims`),
  reviewAtomicClaim: (candidateId: string, input: Schemas["AtomicClaimReviewRequest"]) => request<AtomicClaimReview>(`/atomic-claims/${candidateId}/reviews`, { method: "POST", body: JSON.stringify(input) }),
  createDocumentSupplement: (documentId: string, input: Schemas["CreateDocumentSupplementRequest"]) => request<Schemas["CreateDocumentSupplementResponse"]>(`/documents/${documentId}/supplements`, { method: "POST", body: JSON.stringify(input) }),
};

/**
 * The Research OS has its own V1 endpoints in addition to the legacy research
 * client.  Keep that boundary replaceable so the explicitly requested mock
 * workspace never silently falls back to the live ledger.
 */
export type ResearchOsApi = typeof httpResearchOsApi;

let selectedResearchOsApi: ResearchOsApi = httpResearchOsApi;

export function setResearchOsApi(api: ResearchOsApi): void {
  selectedResearchOsApi = api;
}

export function resetResearchOsApi(): void {
  selectedResearchOsApi = httpResearchOsApi;
}

export const researchOsApi: ResearchOsApi = {
  monitor: (caseId) => selectedResearchOsApi.monitor(caseId),
  saveMonitor: (caseId, input) => selectedResearchOsApi.saveMonitor(caseId, input),
  setMonitorStatus: (caseId, status, changeReason) => selectedResearchOsApi.setMonitorStatus(caseId, status, changeReason),
  scopeHistory: (caseId) => selectedResearchOsApi.scopeHistory(caseId),
  startMonitorRun: (caseId) => selectedResearchOsApi.startMonitorRun(caseId),
  startFactorMonitorRun: (caseId, keyFactorId) => selectedResearchOsApi.startFactorMonitorRun(caseId, keyFactorId),
  cancelRun: (runId, changeReason) => selectedResearchOsApi.cancelRun(runId, changeReason),
  runEvents: (runId) => selectedResearchOsApi.runEvents(runId),
  activeRuns: () => selectedResearchOsApi.activeRuns(),
  runs: () => selectedResearchOsApi.runs(),
  network: () => selectedResearchOsApi.network(),
  caseRelations: (caseId) => selectedResearchOsApi.caseRelations(caseId),
  graph: (caseId) => selectedResearchOsApi.graph(caseId),
  exposure: (caseId) => selectedResearchOsApi.exposure(caseId),
  marketExpression: (caseId) => selectedResearchOsApi.marketExpression(caseId),
  sourceStatements: (caseId) => selectedResearchOsApi.sourceStatements(caseId),
  createReportClaim: (caseId, input) => selectedResearchOsApi.createReportClaim(caseId, input),
  createKeyFactor: (caseId, input) => selectedResearchOsApi.createKeyFactor(caseId, input),
  createClaimVerification: (caseId, factorId, input) => selectedResearchOsApi.createClaimVerification(caseId, factorId, input),
  metrics: () => selectedResearchOsApi.metrics(),
  createMetric: (input) => selectedResearchOsApi.createMetric(input),
  createOutcomeBinding: (thesisId, input) => selectedResearchOsApi.createOutcomeBinding(thesisId, input),
  approveOutcomeBinding: (bindingId, input) => selectedResearchOsApi.approveOutcomeBinding(bindingId, input),
  researchability: (thesisId) => selectedResearchOsApi.researchability(thesisId),
  mechanismTemplates: () => selectedResearchOsApi.mechanismTemplates(),
  caseMechanismProtocol: (caseId) => selectedResearchOsApi.caseMechanismProtocol(caseId),
  selectMechanismTemplate: (caseId, input) => selectedResearchOsApi.selectMechanismTemplate(caseId, input),
  createVerificationRule: (caseId, edgeId, input) => selectedResearchOsApi.createVerificationRule(caseId, edgeId, input),
  atomicClaims: (caseId) => selectedResearchOsApi.atomicClaims(caseId),
  reviewAtomicClaim: (candidateId, input) => selectedResearchOsApi.reviewAtomicClaim(candidateId, input),
  createDocumentSupplement: (documentId, input) => selectedResearchOsApi.createDocumentSupplement(documentId, input),
};
