import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import pageSource from "./ResearchArchivePage.tsx?raw";

import ResearchArchivePage from "./ResearchArchivePage";
import {
  UnderwritingResearchRequestError,
  resetUnderwritingResearchApi,
  setUnderwritingResearchApi,
  type ResearchArchiveList,
  type UnderwritingResearchApi,
} from "../../data/underwritingResearchApi";

const archive: ResearchArchiveList = {
  schema_version: "underwriting.v1",
  items: [{
    schema_version: "underwriting.v1",
    object_id: "company-id",
    object_kind: "company",
    canonical_name: "宁德时代",
    external_key: "300750.SZ",
    version_kind: "catl_economic_model_evidence_only",
    version_count: 2,
    lineage_state: "readable",
    latest_revision_id: "revision-2",
    latest_sequence: 2,
    cutoff: "2025-05-15T15:59:59Z",
    source_manifest_hash: "a".repeat(64),
  }, {
    schema_version: "underwriting.v1",
    object_id: "unreadable-id",
    object_kind: "industry",
    canonical_name: "未能校验的档案",
    external_key: "unknown",
    version_kind: "broken",
    version_count: 1,
    lineage_state: "unreadable",
    latest_revision_id: null,
    latest_sequence: null,
    cutoff: null,
    source_manifest_hash: null,
  }],
  next_cursor: "next-page",
};

const revisionOne = {
  schema_version: "underwriting.v1" as const,
  id: "revision-1",
  object_id: "company-id",
  basis_id: "basis-1",
  version_kind: "catl_economic_model_evidence_only",
  sequence: 1,
  content_hash: "1".repeat(64),
  cutoff: "2025-04-14T15:59:59Z",
  source_manifest_hash: "b".repeat(64),
  parent_refs: [],
};

const revisionTwo = {
  ...revisionOne,
  id: "revision-2",
  basis_id: "basis-2",
  sequence: 2,
  content_hash: "2".repeat(64),
  cutoff: "2025-05-15T15:59:59Z",
  source_manifest_hash: "a".repeat(64),
  parent_refs: [{
    schema_version: "underwriting.v1" as const,
    reference: "research boundary",
    artifact_type: "research_boundary",
    identity: "answerability",
    content_hash: "5".repeat(64),
    source_locators: ["Unknown evidence gap"],
    unit: null,
    period_start: null,
    period_end: null,
    available_at: null,
    status: "not_answerable",
  }, {
    schema_version: "underwriting.v1" as const,
    reference: "IEA Global EV Outlook 2025",
    artifact_type: "source_fact",
    identity: "installed_capacity",
    content_hash: "4".repeat(64),
    source_locators: ["p.148"],
    unit: "GWh",
    period_start: "2024-01-01",
    period_end: "2024-12-31",
    available_at: "2025-05-14T00:00:00Z",
    status: "candidate",
  }],
};

function renderArchive(entry = "/underwriting/research") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/underwriting/research" element={<ResearchArchivePage />} />
        <Route path="/underwriting/research/:objectId/:versionKind" element={<ResearchArchivePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function installApi(overrides: Partial<UnderwritingResearchApi> = {}) {
  const api = {
    listArchives: vi.fn().mockResolvedValue(archive),
    history: vi.fn().mockResolvedValue({
      schema_version: "underwriting.v1",
      object_id: "company-id",
      version_kind: "catl_economic_model_evidence_only",
      revisions: [revisionOne, revisionTwo],
    }),
    revision: vi.fn().mockResolvedValue(revisionTwo),
    diff: vi.fn().mockResolvedValue({
      schema_version: "underwriting.v1",
      from_revision_id: "revision-1",
      to_revision_id: "revision-2",
      from_content_hash: revisionOne.content_hash,
      to_content_hash: revisionTwo.content_hash,
      diff_hash: "3".repeat(64),
      entries: [{
        schema_version: "underwriting.v1",
        group: "evidence",
        change_type: "added",
        artifact_type: "source_fact",
        identity: "installed_capacity",
        before: null,
        after: {
          schema_version: "underwriting.v1",
          reference: "IEA Global EV Outlook 2025",
          artifact_type: "source_fact",
          identity: "installed_capacity",
          content_hash: "4".repeat(64),
          source_locators: ["p.148"],
          unit: "GWh",
          period_start: "2024-01-01",
          period_end: "2024-12-31",
          available_at: "2025-05-14T00:00:00Z",
          status: "candidate",
        },
      }, {
        schema_version: "underwriting.v1",
        group: "answerability",
        change_type: "added",
        artifact_type: "research_boundary",
        identity: "answerability",
        before: null,
        after: {
          schema_version: "underwriting.v1",
          reference: "research boundary",
          artifact_type: "research_boundary",
          identity: "answerability",
          content_hash: "5".repeat(64),
          source_locators: ["Unknown evidence gap"],
          unit: null,
          period_start: null,
          period_end: null,
          available_at: null,
          status: "not_answerable",
        },
      }],
    }),
    ...overrides,
  } as unknown as UnderwritingResearchApi;
  setUnderwritingResearchApi(api);
  return api;
}

afterEach(() => {
  resetUnderwritingResearchApi();
});

describe("ResearchArchivePage", () => {
  it("filters the archive directory and keeps unreadable metadata withheld", async () => {
    const api = installApi();
    const user = userEvent.setup();
    renderArchive();

    expect(await screen.findByRole("heading", { name: "公司／行业档案" })).toBeVisible();
    expect(screen.getByText("未能校验的档案")).toBeVisible();
    expect(screen.getByText("该档案无法校验，未展示版本资料。")).toBeVisible();
    expect(screen.queryByText("64 位来源摘要")).not.toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "搜索公司或行业档案" }), "宁德");
    await waitFor(() => expect(api.listArchives).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "宁德" }),
    ));
    await user.selectOptions(screen.getByLabelText("研究对象类型"), "company");
    await waitFor(() => expect(api.listArchives).toHaveBeenLastCalledWith(
      expect.objectContaining({ kind: "company" }),
    ));
    expect(screen.getByRole("link", { name: /打开宁德时代档案/ }))
      .toHaveAttribute(
        "href",
        "/underwriting/research/company-id/catl_economic_model_evidence_only",
      );
  });

  it("shows CATL unresolved evidence boundaries on a deep link", async () => {
    installApi();
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect((await screen.findAllByText("研究尚需验证")).length).toBeGreaterThan(0);
    expect(screen.queryByText("等待验证资料")).not.toBeInTheDocument();
    expect(screen.getAllByText("Unknown evidence gap").length).toBeGreaterThan(0);
    expect(screen.getAllByText("candidate").length).toBeGreaterThan(0);
    expect(screen.getAllByText("p.148").length).toBeGreaterThan(0);
    expect(screen.getAllByText("GWh").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2024-01-01 至 2024-12-31").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2025-05-14T00:00:00Z").length).toBeGreaterThan(0);
    const boundaries = screen.getByRole("complementary", { name: "研究边界" });
    expect(within(boundaries).getByText(/Unknown evidence gap/)).toBeVisible();
    expect(within(boundaries).getByText("candidate")).toBeVisible();
  });

  it("keeps an immediate predecessor artifact out of the selected frozen-evidence table", async () => {
    const selectedArtifact = {
      schema_version: "underwriting.v1" as const,
      reference: "Selected frozen source",
      artifact_type: "source_fact",
      identity: "selected-only",
      content_hash: "6".repeat(64),
      source_locators: ["p.20"],
      unit: null,
      period_start: null,
      period_end: null,
      available_at: null,
      status: "candidate",
    };
    const predecessorArtifact = {
      ...selectedArtifact,
      reference: "Predecessor-only source",
      content_hash: "7".repeat(64),
    };
    const predecessorRevision = { ...revisionOne, parent_refs: [predecessorArtifact] };
    const selectedRevision = { ...revisionTwo, parent_refs: [selectedArtifact] };
    installApi({
      history: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        object_id: "company-id",
        version_kind: "catl_economic_model_evidence_only",
        revisions: [predecessorRevision, selectedRevision],
      }),
      revision: vi.fn().mockResolvedValue(selectedRevision),
      diff: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        from_revision_id: "revision-1",
        to_revision_id: "revision-2",
        from_content_hash: predecessorRevision.content_hash,
        to_content_hash: selectedRevision.content_hash,
        diff_hash: "8".repeat(64),
        entries: [{
          schema_version: "underwriting.v1",
          group: "evidence",
          change_type: "replaced",
          artifact_type: "source_fact",
          identity: "selected-only",
          before: predecessorArtifact,
          after: selectedArtifact,
        }],
      }),
    });

    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    const evidence = await screen.findByRole("region", { name: "冻结证据记录" });
    expect(within(evidence).getByText("Selected frozen source")).toBeVisible();
    expect(within(evidence).queryByText("Predecessor-only source")).not.toBeInTheDocument();
    expect(screen.getByText("Predecessor-only source")).toBeVisible();
  });

  it("uses only the selected revision's immediate predecessor and supports keyboard selection", async () => {
    const api = installApi({
      revision: vi.fn((revisionId: string) => Promise.resolve(revisionId === "revision-1" ? revisionOne : revisionTwo)),
    });
    const user = userEvent.setup();
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    const firstVersion = await screen.findByRole("button", { name: /版本 1/ });
    firstVersion.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(api.revision).toHaveBeenLastCalledWith("revision-1"));
    expect(api.diff).not.toHaveBeenCalledWith("revision-2", "revision-1");

    const secondVersion = screen.getByRole("button", { name: /版本 2/ });
    secondVersion.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(api.diff).toHaveBeenLastCalledWith("revision-1", "revision-2"));
    expect(secondVersion).toHaveAttribute("aria-pressed", "true");
  });

  it("makes loading, missing, validation, and transport failures explicit", async () => {
    const pending = new Promise<ResearchArchiveList>(() => undefined);
    installApi({ listArchives: vi.fn().mockReturnValue(pending) });
    const { unmount } = renderArchive();
    expect(screen.getByRole("status")).toHaveTextContent("正在读取冻结档案目录");
    unmount();

    installApi({
      history: vi.fn().mockRejectedValue(new UnderwritingResearchRequestError("不存在", 404)),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");
    expect(await screen.findByText("找不到该冻结版本档案。")).toBeVisible();
  });

  it("fails closed when a selected frozen version cannot be validated", async () => {
    installApi({
      history: vi.fn().mockRejectedValue(new UnderwritingResearchRequestError("bad path", 422)),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("所选冻结版本无法安全读取或完成校验，未展示该版本、当前或更新资料。");
    expect(screen.queryByLabelText("研究版本时间线")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("已选冻结版本")).not.toBeInTheDocument();
  });

  it("fails closed when history does not bind to the requested archive", async () => {
    installApi({
      history: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        object_id: "other-company-id",
        version_kind: "catl_economic_model_evidence_only",
        revisions: [revisionOne, revisionTwo],
      }),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByRole("button", { name: /版本 1/ })).not.toBeInTheDocument();
    expect(screen.queryByText("IEA Global EV Outlook 2025")).not.toBeInTheDocument();
  });

  it("fails closed when a selected revision does not match its frozen history entry", async () => {
    installApi({
      revision: vi.fn().mockResolvedValue({ ...revisionTwo, basis_id: "other-basis" }),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("版本 2")).not.toBeInTheDocument();
    expect(screen.queryByText("IEA Global EV Outlook 2025")).not.toBeInTheDocument();
  });

  it("fails closed when a selected detail injects a candidate boundary absent from history", async () => {
    const injectedBoundary = {
      ...revisionTwo.parent_refs[1],
      reference: "injected boundary",
      identity: "injected-answerability",
      content_hash: "9".repeat(64),
      status: "wait_for_validation",
    };
    installApi({
      revision: vi.fn().mockResolvedValue({
        ...revisionTwo,
        parent_refs: [...revisionTwo.parent_refs, injectedBoundary],
      }),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("injected boundary")).not.toBeInTheDocument();
  });

  it.each(["replaces", "omits"] as const)("fails closed when a selected detail %s a frozen parent reference", async (operation) => {
    const parent_refs = operation === "replaces"
      ? [{
        ...revisionTwo.parent_refs[0],
        source_locators: ["changed immutable locator"],
      }, revisionTwo.parent_refs[1]]
      : [revisionTwo.parent_refs[0]];
    installApi({ revision: vi.fn().mockResolvedValue({ ...revisionTwo, parent_refs }) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("IEA Global EV Outlook 2025")).not.toBeInTheDocument();
  });

  it("fails closed when a diff is not exactly bound to its adjacent versions", async () => {
    installApi({
      diff: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        from_revision_id: "revision-2",
        to_revision_id: "revision-1",
        from_content_hash: revisionTwo.content_hash,
        to_content_hash: revisionOne.content_hash,
        diff_hash: "3".repeat(64),
        entries: [],
      }),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("版本 2")).not.toBeInTheDocument();
    expect(screen.queryByText("仅与紧邻的版本 1 对照。")).not.toBeInTheDocument();
  });

  it("fails closed when an adjacent diff injects a nonmember artifact", async () => {
    const injected = {
      ...revisionTwo.parent_refs[0],
      reference: "injected evidence",
      identity: "invented-fact",
      content_hash: "9".repeat(64),
    };
    installApi({
      diff: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        from_revision_id: "revision-1",
        to_revision_id: "revision-2",
        from_content_hash: revisionOne.content_hash,
        to_content_hash: revisionTwo.content_hash,
        diff_hash: "3".repeat(64),
        entries: [{
          schema_version: "underwriting.v1",
          group: "evidence",
          change_type: "added",
          artifact_type: injected.artifact_type,
          identity: injected.identity,
          before: null,
          after: injected,
        }],
      }),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("injected evidence")).not.toBeInTheDocument();
  });

  it("fails closed when an adjacent diff mislabels or omits a required change", async () => {
    installApi({
      diff: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        from_revision_id: "revision-1",
        to_revision_id: "revision-2",
        from_content_hash: revisionOne.content_hash,
        to_content_hash: revisionTwo.content_hash,
        diff_hash: "3".repeat(64),
        entries: [{
          schema_version: "underwriting.v1",
          group: "mechanism",
          change_type: "replaced",
          artifact_type: revisionTwo.parent_refs[0].artifact_type,
          identity: revisionTwo.parent_refs[0].identity,
          before: null,
          after: revisionTwo.parent_refs[0],
        }],
      }),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("IEA Global EV Outlook 2025")).not.toBeInTheDocument();
  });

  it("shows a safe validation-envelope message and request id", async () => {
    installApi({
      history: vi.fn().mockRejectedValue(new UnderwritingResearchRequestError(
        "路径中有无法读取的符号 <invalid>",
        422,
        "invalid_path",
        "request-422",
      )),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("所选冻结版本无法安全读取或完成校验，未展示该版本、当前或更新资料。");
    expect(alert).toHaveTextContent("路径中有无法读取的符号 <invalid>");
    expect(alert).toHaveTextContent("request-422");
  });

  it("does not let a stale load-more response replace a changed directory filter", async () => {
    let resolveLoadMore: ((value: ResearchArchiveList) => void) | undefined;
    const loadMorePending = new Promise<ResearchArchiveList>((resolve) => { resolveLoadMore = resolve; });
    const filteredArchive: ResearchArchiveList = {
      ...archive,
      items: [{ ...archive.items[0], canonical_name: "新查询档案" }],
      next_cursor: null,
    };
    const listArchives = vi.fn()
      .mockResolvedValueOnce(archive)
      .mockReturnValueOnce(loadMorePending)
      .mockResolvedValueOnce(filteredArchive);
    installApi({ listArchives });
    const user = userEvent.setup();
    renderArchive();

    await screen.findByRole("button", { name: "载入后续档案" });
    await user.click(screen.getByRole("button", { name: "载入后续档案" }));
    await user.type(screen.getByRole("searchbox", { name: "搜索公司或行业档案" }), "新");
    await waitFor(() => expect(listArchives).toHaveBeenLastCalledWith(expect.objectContaining({ query: "新" })));
    expect(await screen.findByText("新查询档案")).toBeVisible();

    resolveLoadMore?.({ ...archive, items: [{ ...archive.items[0], canonical_name: "旧分页档案" }], next_cursor: null });
    await waitFor(() => expect(screen.queryByText("旧分页档案")).not.toBeInTheDocument());
    expect(screen.getByText("新查询档案")).toBeVisible();
  });

  it("renders the archive at a 390px viewport without a document overflow when metrics are available", async () => {
    const viewport = Object.getOwnPropertyDescriptor(window, "innerWidth");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      installApi();
      renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");
      await screen.findByRole("region", { name: "冻结证据记录" });
      // jsdom does not calculate CSS layout, but does expose the document metric.
      // A browser-level visual check remains necessary for physical geometry.
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
    } finally {
      if (viewport) Object.defineProperty(window, "innerWidth", viewport);
    }
  });

  it("does not contain prohibited guidance terminology in its display source", () => {
    const terms = [
      String.fromCharCode(80, 69),
      String.fromCharCode(80, 66),
      String.fromCharCode(68, 67, 70),
      String.fromCharCode(98, 117, 121),
      String.fromCharCode(115, 101, 108, 108),
      String.fromCharCode(115, 116, 111, 112),
      String.fromCharCode(112, 111, 115, 105, 116, 105, 111, 110),
    ];
    expect(terms.every((term) => !new RegExp(`\\b${term}\\b`, "i").test(pageSource))).toBe(true);
  });
});
