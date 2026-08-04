/// <reference types="vite/client" />
import type { Conclusion, ReviewOutcome } from "../domain/types";
import type { ResearchClient } from "../domain/prototypeTypes";
import { HttpResearchAdapter } from "./httpResearchAdapter";

// The application always reads from the real HTTP ledger by default. Use an
// explicit VITE_RESEARCH_API_URL when the API is on another origin; otherwise
// the same-origin /api/v1 path works with the Vite proxy and production host.
// MockResearchAdapter remains available only through explicit setResearchClient
// calls in tests and prototypes.
function defaultClient(): ResearchClient {
  const baseUrl = import.meta.env.VITE_RESEARCH_API_URL || "/api/v1";
  return new HttpResearchAdapter({ baseUrl: baseUrl.replace(/\/$/, "") });
}

let _client: ResearchClient = defaultClient();

export function setResearchClient(client: ResearchClient): void {
  _client = client;
}

export function resetResearchClient(): void {
  _client = defaultClient();
}

export const researchClient: ResearchClient = {
  getOverview: (q) => _client.getOverview(q),
  getCaseDossier: (id, q) => _client.getCaseDossier(id, q),
  getRelationshipGraph: (id, q) => _client.getRelationshipGraph(id, q),
  getDocuments: (q) => _client.getDocuments(q),
  getDocumentDetail: (id) => _client.getDocumentDetail(id),
  getReviewQueue: () => _client.getReviewQueue(),
  search: (q) => _client.search(q),
  getCaseSummaries: () => _client.getCaseSummaries(),
  submitReviewDecision: (itemId, decision) =>
    _client.submitReviewDecision(itemId, decision),
  getWorkspaceOverviewView: () => _client.getWorkspaceOverviewView(),
  getWorkspaceOverviewScreen: () => _client.getWorkspaceOverviewScreen(),
  getNewResearchView: () => _client.getNewResearchView(),
  createCase: (input) => _client.createCase(input),
  listCaseSummaries: () => _client.listCaseSummaries(),
  getResearchPlanView: (caseId) => _client.getResearchPlanView(caseId),
  getCaseWorkbenchView: (id, options) =>
    _client.getCaseWorkbenchView(id, options),
  getRelationshipGraphView: (id, thesisId) =>
    _client.getRelationshipGraphView(id, thesisId),
  getLibraryView: () => _client.getLibraryView(),
  getDataCenterView: () => _client.getDataCenterView(),
  getVersionsView: (caseId, options) =>
    _client.getVersionsView(caseId, options),
  getThemeIndexView: () => _client.getThemeIndexView(),
  getThemeWorkbenchView: (themeId: string) =>
    _client.getThemeWorkbenchView(themeId),
  getReviewQueueView: (caseId) => _client.getReviewQueueView(caseId),
  submitLinkReview: (linkId, payload) =>
    _client.submitLinkReview(linkId, payload),
  reviewAssessment: (assessmentId, payload) =>
    _client.reviewAssessment(assessmentId, payload),
  rerunThesis: (thesisId) => _client.rerunThesis(thesisId),
  proposeEvidence: (thesisId) => _client.proposeEvidence(thesisId),
  ingestDocuments: (caseId, extra) => _client.ingestDocuments(caseId, extra),
  extractStatements: (versionId) => _client.extractStatements(versionId),
  getDataCenterMetric: (stockId, metricName) =>
    _client.getDataCenterMetric(stockId, metricName),
  listCompanies: (query, cursor) => _client.listCompanies(query, cursor),
  getCompanyDossier: (companyId, opts) =>
    _client.getCompanyDossier(companyId, opts),
  listThemes: () => _client.listThemes(),
  getThemeView: (tag, opts) => _client.getThemeView(tag, opts),
  getConclusionView: (caseId, opts) => _client.getConclusionView(caseId, opts),
};

// Re-export common types so call sites don't need to dig into the adapter.
export type { Conclusion, ReviewOutcome };
