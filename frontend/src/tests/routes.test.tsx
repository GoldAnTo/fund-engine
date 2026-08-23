import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { resetResearchOsApi, setResearchOsApi } from "../app/researchOsApi";
import { ResearchOsRoutes } from "../app/routes";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { MockResearchOsApi } from "../data/mockResearchOsApi";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import {
  resetUnderwritingResearchApi,
  setUnderwritingResearchApi,
  type ResearchRevisionBoundary,
  type UnderwritingResearchApi,
} from "../data/underwritingResearchApi";

let fetchMock: ReturnType<typeof vi.fn>;

const caseRoutes = [
  "/events/case-route",
  "/events/case-route/history",
  "/events/case-route/scope",
  "/events/case-route/evidence",
  "/events/case-route/documents",
  "/events/case-route/review",
  "/events/case-route/wiki",
  "/events/case-route/protocol",
  "/events/case-route/market",
  "/events/case-route/stocks/stock-route",
  "/events/case-route/funds/fund-route",
  "/events/case-route/monitor",
  "/events/case-route/monitor/config",
  "/events/case-route/relations",
];

describe("Research OS route inventory", () => {
  beforeEach(() => {
    resetResearchClient();
    resetResearchOsApi();
    resetUnderwritingResearchApi();
    fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    resetResearchClient();
    resetResearchOsApi();
    resetUnderwritingResearchApi();
    vi.unstubAllGlobals();
  });

  it.each([
    ["/", /暂时无法读取事件研究/],
    ["/events", /暂时无法读取事件研究/],
    ["/network", /无法读取跨 Case 关联/],
    ["/monitoring", /无法读取实际运行记录/],
    ["/governance/case-admissions", /当前身份不能读取历史 Case 准入队列/],
    ["/retired-prototype-route", /暂时无法读取事件研究/],
  ])("renders an explicit live-data error for %s", async (path, message) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(fetchMock).toHaveBeenCalled();
  });

  it("renders the automatic research entry without loading an existing Case list", async () => {
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "告诉系统你想研究什么" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "开始自动研究" })).toBeDisabled();
    expect(screen.queryByText(/无法读取可归入 Case 清单/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /自动研究/ })).toHaveLength(2);
    expect(screen.queryByText("资料收件箱")).not.toBeInTheDocument();
    expect(screen.queryByText(/从事件开始/)).not.toBeInTheDocument();
  });

  it("routes the canonical automatic research URL to its safe process-page error", async () => {
    render(
      <MemoryRouter initialEntries={["/events/case-route/automatic-research"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "暂时无法读取这项自动研究",
    );
    expect(fetchMock).toHaveBeenCalled();
  });

  it("keeps unrelated global review-run controls off the automatic process route", async () => {
    const adapter = new MockResearchAdapter();
    const started = await adapter.startAutomaticResearch("自动过程页隔离");
    const api = new MockResearchOsApi(adapter);
    const activeRuns = vi.spyOn(api, "activeRuns");
    setResearchClient(adapter);
    setResearchOsApi(api);

    render(
      <MemoryRouter initialEntries={[`/events/${started.caseId}/automatic-research`]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "自动过程页隔离" })).toBeVisible();
    await waitFor(() => expect(activeRuns).toHaveBeenCalled());
    expect(screen.queryByText(/等待人工审核/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /审核关键证据/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开运行详情" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /待我审核/ })).toBeVisible();
  });

  it("retains the global review-run strip on reviewed research routes", async () => {
    const adapter = new MockResearchAdapter();
    const api = new MockResearchOsApi(adapter);
    setResearchClient(adapter);
    setResearchOsApi(api);

    render(
      <MemoryRouter initialEntries={["/events"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("等待人工审核");
    expect(strip).toHaveTextContent("审核 1 条关键证据");
  });

  it.each(caseRoutes)(
    "renders a Case-specific live-data error for %s",
    async (path) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <ResearchOsRoutes />
        </MemoryRouter>,
      );

      expect(await screen.findByRole("alert")).toHaveTextContent(
        /无法读取这个 Case/,
      );
      expect(fetchMock).toHaveBeenCalled();
    },
  );

  it("renders the preparation route and exposes its live-data failure", async () => {
    render(
      <MemoryRouter initialEntries={["/events/case-route/preparation"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("backend_unavailable");
    expect(screen.getByRole("heading", { name: "无法读取研究准备" })).toBeVisible();
    expect(fetchMock).toHaveBeenCalled();
  });

  it("isolates a deep-linked archive and identifies it from its checked history", async () => {
    const revision = {
      schema_version: "underwriting.v1" as const,
      id: "revision-identity",
      object_id: "company-id",
      basis_id: "basis-id",
      version_kind: "frozen-company-research",
      sequence: 1,
      content_hash: "a".repeat(64),
      cutoff: "2025-05-15T15:59:59Z",
      source_manifest_hash: "b".repeat(64),
      parent_refs: [],
    };
    const boundary: ResearchRevisionBoundary = {
      schema_version: "underwriting.v1",
      revision_id: revision.id,
      object_id: revision.object_id,
      basis_id: revision.basis_id,
      version_kind: revision.version_kind,
      content_hash: revision.content_hash,
      cutoff: revision.cutoff,
      source_manifest_hash: revision.source_manifest_hash,
      answerability: {
        schema_version: "underwriting.v1",
        reference: "00000000-0000-4000-8000-000000000001",
        content_hash: "c".repeat(64),
        state: "not_answerable",
        blockers: ["missing_key_baseline"],
        research_debt_keys: ["industry_utilisation"],
        resolvable_within_mandate: true,
        resolution_requirements: ["核验行业有效产能与利用率口径"],
      },
      unknown_evidence_gaps: [],
    };
    setUnderwritingResearchApi({
      listArchives: vi.fn(),
      history: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        object_id: "company-id",
        version_kind: "frozen-company-research",
        object_kind: "company",
        canonical_name: "只从冻结历史返回的公司",
        external_key: "IMMUTABLE.ONLY",
        revisions: [revision],
      }),
      revision: vi.fn().mockResolvedValue(revision),
      diff: vi.fn(),
      boundary: vi.fn().mockResolvedValue(boundary),
      candidateEvidence: vi.fn(),
    } satisfies UnderwritingResearchApi);

    render(
      <MemoryRouter initialEntries={["/underwriting/research/company-id/frozen-company-research"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("navigation", { name: "不可变研究档案导航" })).toBeVisible();
    expect(screen.queryByRole("complementary", { name: "研究工作台导航" })).not.toBeInTheDocument();
    const identityHeading = await screen.findByRole("heading", { name: "只从冻结历史返回的公司" });
    expect(identityHeading).toBeVisible();
    expect(identityHeading.parentElement).toHaveTextContent("IMMUTABLE.ONLY");
    expect(identityHeading.parentElement).toHaveTextContent("公司");
    expect(await screen.findByRole("complementary", { name: "研究边界" }))
      .toHaveTextContent("研究尚需验证");
    expect(screen.getByText("核验行业有效产能与利用率口径")).toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
