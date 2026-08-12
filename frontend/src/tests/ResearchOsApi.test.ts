import { afterEach, describe, expect, it, vi } from "vitest";

import { resetResearchOsApi, researchOsApi, setResearchOsApi, type MonitorDetail, type ResearchOsApi } from "../app/researchOsApi";
import { researchClient } from "../data/researchClient";

describe("research OS API selection", () => {
  afterEach(() => {
    resetResearchOsApi();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("uses the explicitly selected API instead of fetching the live ledger", async () => {
    const monitor = {
      monitor: null,
      history: [],
      latest_run: null,
      confirmed_factors: [],
      available_confirmed_factors: [],
    } satisfies MonitorDetail;
    const localApi = { monitor: vi.fn().mockResolvedValue(monitor) } as unknown as ResearchOsApi;
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    setResearchOsApi(localApi);

    await expect(researchOsApi.monitor("event-tsm")).resolves.toEqual(monitor);

    expect(localApi.monitor).toHaveBeenCalledWith("event-tsm");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("keeps live Research OS calls in the same authenticated browser session", async () => {
    vi.stubEnv("VITE_RESEARCH_BEARER_TOKEN", "team-token");
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      reviewed_relations: [], candidate_relations: [], resolved_candidates: [],
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    await researchOsApi.network();

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/event-research/network",
      expect.objectContaining({
        credentials: "include",
        headers: expect.objectContaining({ Authorization: "Bearer team-token" }),
      }),
    );
  });

  it("does not expose retired prototype screen calls on the active client", () => {
    for (const retiredMethod of [
      "getWorkspaceOverviewView",
      "getWorkspaceOverviewScreen",
      "getNewResearchView",
      "getResearchPlanView",
      "getLibraryView",
      "getDataCenterView",
      "getVersionsView",
      "getThemeIndexView",
      "getThemeWorkbenchView",
    ]) {
      expect(researchClient).not.toHaveProperty(retiredMethod);
    }
  });
});
