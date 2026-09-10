import { setResearchOsApi } from "./researchOsApi";
import { MockResearchOsApi } from "../data/mockResearchOsApi";
import { setResearchClient } from "../data/researchClient";

export async function configureLegacyMockClient() {
  const localMockAdapterModule = "../data/" + "mockResearchAdapter";
  const { MockResearchAdapter } = await import(/* @vite-ignore */ localMockAdapterModule);
  const mockAdapter = new MockResearchAdapter();
  setResearchClient(mockAdapter);
  setResearchOsApi(new MockResearchOsApi(mockAdapter));
}
