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
export type Graph = Schemas["GraphResponse"];
export type FundExposure = Schemas["FundExposureResponse"];
export type MarketExpression = Schemas["MarketExpressionResponse"];
export type ResearchNetwork = Schemas["ResearchNetworkResponse"];
export type MetricDefinition = Schemas["MetricDefinitionDTO"];
export type OutcomeBinding = Schemas["OutcomeBindingDTO"];
export type Researchability = Schemas["ResearchabilityDTO"];
export type MechanismTemplate = Schemas["MechanismTemplateDTO"];
export type CaseMechanismProtocol = Schemas["CaseMechanismProtocolDTO"];

export const researchOsApi = {
  monitor: (caseId: string) => request<MonitorDetail>(`/research-cases/${caseId}/monitor`),
  saveMonitor: (caseId: string, input: Schemas["UpdateCaseMonitorRequest"]) => request<Monitor>(`/research-cases/${caseId}/monitor`, { method: "PUT", body: JSON.stringify(input) }),
  setMonitorStatus: (caseId: string, status: "active" | "paused", changeReason: string) => request<Monitor>(`/research-cases/${caseId}/monitor/${status}`, { method: "POST", body: JSON.stringify({ actor: "human:researcher", change_reason: changeReason }) }),
  runEvents: (runId: string) => request<Schemas["ResearchRunEventsResponse"]>(`/research-runs/${runId}/events`),
  activeRuns: () => request<Schemas["ActiveResearchRunsResponse"]>("/research-runs/active"),
  network: () => request<ResearchNetwork>("/event-research/network"),
  caseRelations: (caseId: string) => request<ResearchNetwork>(`/event-research/${caseId}/relations`),
  graph: (caseId: string) => request<Graph>(`/research-cases/${caseId}/graph?research_mode=true`),
  exposure: (caseId: string) => request<FundExposure>(`/research-cases/${caseId}/fund-exposure`),
  marketExpression: (caseId: string) => request<MarketExpression>(`/research-cases/${caseId}/market-expression`),
  metrics: () => request<MetricDefinition[]>("/metric-definitions"),
  createMetric: (input: Schemas["MetricDefinitionRequest"]) => request<MetricDefinition>("/metric-definitions", { method: "POST", body: JSON.stringify(input) }),
  createOutcomeBinding: (thesisId: string, input: Schemas["OutcomeBindingRequest"]) => request<OutcomeBinding>(`/theses/${thesisId}/outcome-bindings`, { method: "POST", body: JSON.stringify(input) }),
  approveOutcomeBinding: (bindingId: string, input: Schemas["ApproveOutcomeBindingRequest"]) => request<OutcomeBinding>(`/outcome-bindings/${bindingId}/approve`, { method: "POST", body: JSON.stringify(input) }),
  researchability: (thesisId: string) => request<Researchability>(`/theses/${thesisId}/researchability`),
  mechanismTemplates: () => request<MechanismTemplate[]>("/mechanism-templates"),
  caseMechanismProtocol: (caseId: string) => request<CaseMechanismProtocol>(`/research-cases/${caseId}/mechanism-protocol`),
  selectMechanismTemplate: (caseId: string, input: Schemas["SelectMechanismTemplateRequest"]) => request<Schemas["MechanismSelectionDTO"]>(`/research-cases/${caseId}/mechanism-selection`, { method: "POST", body: JSON.stringify(input) }),
  createVerificationRule: (edgeId: string, input: Schemas["VerificationRuleRequest"]) => request<Schemas["VerificationRuleDTO"]>(`/mechanism-edges/${edgeId}/verification-rules`, { method: "POST", body: JSON.stringify(input) }),
};
