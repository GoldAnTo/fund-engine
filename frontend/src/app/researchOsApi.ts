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

export const researchOsApi = {
  monitor: (caseId: string) => request<MonitorDetail>(`/research-cases/${caseId}/monitor`),
  saveMonitor: (caseId: string, input: Schemas["UpdateCaseMonitorRequest"]) => request<Monitor>(`/research-cases/${caseId}/monitor`, { method: "PUT", body: JSON.stringify(input) }),
  runEvents: (runId: string) => request<Schemas["ResearchRunEventsResponse"]>(`/research-runs/${runId}/events`),
  activeRuns: () => request<Schemas["ActiveResearchRunsResponse"]>("/research-runs/active"),
  network: () => request<ResearchNetwork>("/event-research/network"),
  graph: (caseId: string) => request<Graph>(`/research-cases/${caseId}/graph?research_mode=true`),
  exposure: (caseId: string) => request<FundExposure>(`/research-cases/${caseId}/fund-exposure`),
  marketExpression: (caseId: string) => request<MarketExpression>(`/research-cases/${caseId}/market-expression`),
};
