import type { Conclusion, ResearchClient, ReviewOutcome } from "../domain/types";
import { MockResearchAdapter } from "./mockResearchAdapter";

// Default client is the mock adapter. Replace with HttpResearchAdapter later
// without touching page-level components.
let _client: ResearchClient = new MockResearchAdapter();

export function setResearchClient(client: ResearchClient): void {
  _client = client;
}

export function resetResearchClient(): void {
  _client = new MockResearchAdapter();
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
};

// Re-export common types so call sites don't need to dig into the adapter.
export type { Conclusion, ReviewOutcome };