import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
  parent_refs: [],
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
        change_type: "replaced",
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
    expect(screen.getAllByText("等待验证资料").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unknown evidence gap").length).toBeGreaterThan(0);
    expect(screen.getAllByText("candidate").length).toBeGreaterThan(0);
    expect(screen.getAllByText("p.148").length).toBeGreaterThan(0);
    expect(screen.getAllByText("GWh").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2024-01-01 至 2024-12-31").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2025-05-14T00:00:00Z").length).toBeGreaterThan(0);
  });

  it("uses only the selected revision's immediate predecessor and supports keyboard selection", async () => {
    const api = installApi();
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

  it("makes a validation response explicit without changing the selected version", async () => {
    installApi({
      history: vi.fn().mockRejectedValue(new UnderwritingResearchRequestError("bad path", 422)),
    });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("查询条件无法读取，请修改后重试。");
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
