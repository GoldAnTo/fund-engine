import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Fragment, createElement } from "react";
import { MemoryRouter, NavLink, useLocation } from "react-router-dom";

import {
  ResearchArchiveLoadErrorBoundary,
  ResearchOsRoutes,
} from "../app/routes";
import {
  UnderwritingResearchRequestError,
  resetUnderwritingResearchApi,
  setUnderwritingResearchApi,
  underwritingResearchApi,
  type UnderwritingResearchApi,
  type ResearchArchiveList,
} from "./underwritingResearchApi";

describe("underwriting research API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    resetUnderwritingResearchApi();
  });

  it("uses an injected read client instead of the HTTP transport", async () => {
    const listArchives = vi.fn().mockResolvedValue({
      schema_version: "underwriting.v1",
      items: [],
      next_cursor: null,
    });
    const localApi = { listArchives } as unknown as UnderwritingResearchApi;
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    setUnderwritingResearchApi(localApi);

    await expect(underwritingResearchApi.listArchives()).resolves.toMatchObject({
      items: [],
    });

    expect(listArchives).toHaveBeenCalledWith(undefined);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("uses the dedicated underwriting API base when configured", async () => {
    vi.stubEnv("VITE_UNDERWRITING_API_URL", "https://underwriting.example.test/v1/");
    resetUnderwritingResearchApi();
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      schema_version: "underwriting.v1",
      items: [],
      next_cursor: null,
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    await underwritingResearchApi.listArchives();

    expect(fetchSpy).toHaveBeenCalledWith(
      "https://underwriting.example.test/v1/research-archives",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("preserves the schema version attached to every archive item", async () => {
    const archive: ResearchArchiveList = {
      schema_version: "underwriting.v1",
      items: [{
        schema_version: "underwriting.v1",
        object_id: "company-1",
        object_kind: "company",
        canonical_name: "宁德时代",
        external_key: "300750.SZ",
        version_kind: "industry_baseline",
        version_count: 2,
        lineage_state: "readable",
        latest_revision_id: "revision-2",
        latest_sequence: 2,
        cutoff: "2025-05-15T15:59:59Z",
        source_manifest_hash: "a".repeat(64),
      }],
      next_cursor: null,
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(archive), {
      status: 200,
    })));

    await expect(underwritingResearchApi.listArchives()).resolves.toEqual(archive);
  });

  it("requests each research archive resource through a GET-only underwriting URL", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      schema_version: "underwriting.v1",
      items: [],
      next_cursor: null,
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    await underwritingResearchApi.listArchives({
      query: "宁德时代",
      kind: "company",
      limit: 10,
      cursor: "next cursor",
    });
    await underwritingResearchApi.history("object id", "industry baseline");
    await underwritingResearchApi.revision("revision id");
    await underwritingResearchApi.diff("from id", "to id");
    await underwritingResearchApi.boundary("revision id");

    const calls = fetchSpy.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(calls.map(([url]) => url)).toEqual([
      "/api/underwriting/v1/research-archives?query=%E5%AE%81%E5%BE%B7%E6%97%B6%E4%BB%A3&kind=company&limit=10&cursor=next+cursor",
      "/api/underwriting/v1/objects/object%20id/research-versions/industry%20baseline",
      "/api/underwriting/v1/research-versions/revision%20id",
      "/api/underwriting/v1/research-versions/from%20id/diff/to%20id",
      "/api/underwriting/v1/research-versions/revision%20id/boundary",
    ]);
    expect(calls.map(([, init]) => init)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: "GET", credentials: "include" }),
      ]),
    );
    for (const [, init] of calls) {
      expect(init).toMatchObject({ method: "GET" });
      expect(init?.body).toBeUndefined();
    }
  });

  it("preserves a typed underwriting 422 envelope", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      schema_version: "underwriting.v1",
      error: {
        code: "validation_failed",
        message: "cursor is malformed",
        details: { field: "cursor" },
      },
    }), {
      status: 422,
      headers: { "x-request-id": "req-archive-422" },
    })));

    const failure = await underwritingResearchApi.listArchives({ cursor: "bad" })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(UnderwritingResearchRequestError);
    expect(failure).toMatchObject({
      status: 422,
      code: "validation_failed",
      message: "cursor is malformed",
      requestId: "req-archive-422",
    });
  });

  it("reads a deep-linked frozen archive without replacing a failed response", async () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchSpy);

    render(createElement(
      MemoryRouter,
      { initialEntries: ["/underwriting/research/company-1/industry_baseline"] },
      createElement(ResearchOsRoutes),
    ));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "这个冻结版本暂时无法读取，未展示替代资料。",
    );
    expect(screen.getByRole("link", { name: "公司／行业档案目录" }))
      .toHaveAttribute("href", "/underwriting/research");
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/underwriting/v1/objects/company-1/research-versions/industry_baseline",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("keeps a failed archive module explicit instead of substituting newer research", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    function BrokenArchive(): never {
      throw new Error("archive module unavailable");
    }

    render(createElement(
      ResearchArchiveLoadErrorBoundary,
      null,
      createElement(BrokenArchive),
    ));

    expect(screen.getByRole("alert")).toHaveTextContent("档案暂时无法载入");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "不会以当前或最新研究替代这个版本",
    );
    expect(screen.getByRole("link", { name: "返回档案目录" }))
      .toHaveAttribute("href", "/underwriting/research");
    expect(consoleError).toHaveBeenCalled();
  });

  it("recovers the archive load boundary after client navigation changes its route identity", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    function BrokenArchive(): never {
      throw new Error("archive module unavailable");
    }

    function ArchiveRecoveryHarness() {
      const location = useLocation();
      const broken = location.pathname === "/underwriting/research/broken/frozen";
      return createElement(
        Fragment,
        null,
        createElement(NavLink, { to: "/underwriting/research" }, "公司／行业档案目录"),
        createElement(
          ResearchArchiveLoadErrorBoundary,
          {
            resetKey: location.pathname,
            children: broken ? createElement(BrokenArchive) : createElement("p", null, "冻结档案目录已恢复"),
          },
        ),
      );
    }

    const user = (await import("@testing-library/user-event")).default.setup();
    render(createElement(
      MemoryRouter,
      { initialEntries: ["/underwriting/research/broken/frozen"] },
      createElement(ArchiveRecoveryHarness),
    ));

    expect(screen.getByRole("alert")).toHaveTextContent("档案暂时无法载入");
    await user.click(screen.getByRole("link", { name: "公司／行业档案目录" }));

    expect(await screen.findByText("冻结档案目录已恢复")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(consoleError).toHaveBeenCalled();
  });
});
