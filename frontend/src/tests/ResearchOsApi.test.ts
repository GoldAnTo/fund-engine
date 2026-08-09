import { afterEach, describe, expect, it, vi } from "vitest";

import { resetResearchOsApi, researchOsApi, setResearchOsApi, type MonitorDetail, type ResearchOsApi } from "../app/researchOsApi";

describe("research OS API selection", () => {
  afterEach(() => {
    resetResearchOsApi();
    vi.unstubAllGlobals();
  });

  it("uses the explicitly selected API instead of fetching the live ledger", async () => {
    const monitor = { monitor: null, history: [], latest_run: null, confirmed_factors: [] } satisfies MonitorDetail;
    const localApi = { monitor: vi.fn().mockResolvedValue(monitor) } as unknown as ResearchOsApi;
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    setResearchOsApi(localApi);

    await expect(researchOsApi.monitor("event-tsm")).resolves.toEqual(monitor);

    expect(localApi.monitor).toHaveBeenCalledWith("event-tsm");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
