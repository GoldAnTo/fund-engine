import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchOsRequestError, resetResearchOsApi, researchOsApi, setResearchOsApi, type MonitorDetail, type ResearchOsApi } from "../app/researchOsApi";
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

  it("preserves typed V1 error details and falls back to the response request id", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: {
        code: "validation_failed",
        message: "available_at must include a timezone",
      },
    }), {
      status: 422,
      headers: { "x-request-id": "req-forecast-1" },
    })));

    const failure = await researchOsApi.recordActualMetricObservation("event-tsm", {} as never).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ResearchOsRequestError);
    expect(failure).toMatchObject({
      status: 422,
      code: "validation_failed",
      message: "available_at must include a timezone",
      requestId: "req-forecast-1",
    });
  });

  it("uses safe fallback messages when an error body has no supported string detail", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("not json", { status: 502 }))
      .mockResolvedValueOnce(new Response("{", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ msg: "invalid" }] }), { status: 422 })));

    for (const status of [502, 503, 422]) {
      const failure = await researchOsApi.network().catch((error: unknown) => error);
      expect(failure).toMatchObject({
        message: `Research OS request failed (${status})`,
        status,
      });
    }
  });

  it("uses a legacy string detail without rendering object values", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      detail: "Legacy request is invalid",
    }), { status: 400 })));

    const failure = await researchOsApi.network().catch((error: unknown) => error);

    expect(failure).toMatchObject({ message: "Legacy request is invalid", status: 400 });
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
