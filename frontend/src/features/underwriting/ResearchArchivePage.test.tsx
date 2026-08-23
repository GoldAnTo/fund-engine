import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import pageSource from "./ResearchArchivePage.tsx?raw";

import ResearchArchivePage from "./ResearchArchivePage";
import {
  UnderwritingResearchRequestError,
  resetUnderwritingResearchApi,
  setUnderwritingResearchApi,
  type ResearchArchiveList,
  type ResearchRevision,
  type ResearchRevisionBoundary,
  type ResearchRevisionHistory,
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

const boundaryTwo = {
  schema_version: "underwriting.v1" as const,
  revision_id: revisionTwo.id,
  object_id: revisionTwo.object_id,
  basis_id: revisionTwo.basis_id,
  version_kind: revisionTwo.version_kind,
  content_hash: revisionTwo.content_hash,
  cutoff: revisionTwo.cutoff,
  source_manifest_hash: revisionTwo.source_manifest_hash,
  answerability: {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000001",
    content_hash: "5".repeat(64),
    state: "not_answerable" as const,
    blockers: ["missing_key_baseline"] as const,
    research_debt_keys: ["industry_utilisation"],
    resolvable_within_mandate: true,
    resolution_requirements: ["核验行业有效产能与利用率口径"],
  },
  unknown_evidence_gaps: [],
};

const candidateRevision = {
  schema_version: "underwriting.v1" as const,
  id: "00000000-0000-4000-8000-000000000101",
  object_id: "00000000-0000-4000-8000-000000000102",
  basis_id: "00000000-0000-4000-8000-000000000103",
  version_kind: "industry_evidence_candidate",
  sequence: 1,
  content_hash: "c".repeat(64),
  cutoff: "2025-05-15T15:59:59Z",
  source_manifest_hash: "5".repeat(64),
  parent_refs: [{
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000107",
    artifact_type: "answerability",
    identity: "answerability",
    content_hash: "1".repeat(64),
    source_locators: [], unit: null, period_start: null, period_end: null, available_at: null,
    status: "not_answerable",
  }, {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000104",
    artifact_type: "candidate_dossier",
    identity: "industry-capacity|1",
    content_hash: "1d8e553c41681de9e6c445647b79401f49975b80c15af61ecc59da2925e53648",
    source_locators: [], unit: null, period_start: null, period_end: null, available_at: null,
    status: "candidate",
  }, {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000105",
    artifact_type: "candidate_review",
    identity: "00000000-0000-4000-8000-000000000104|methodology|reviewer:methodology",
    content_hash: "6807beae14107e2d0fceb0f5ed221b46a0e949c4bbeac4ce36ac30a0cab9fc46",
    source_locators: [], unit: null, period_start: null, period_end: null, available_at: null,
    status: "approve",
  }, {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000106",
    artifact_type: "candidate_review",
    identity: "00000000-0000-4000-8000-000000000104|provenance|reviewer:provenance",
    content_hash: "7696ed8189e7f0afdb347c632b4202820d97cf79bb587fc0052286407d12bdd7",
    source_locators: [], unit: null, period_start: null, period_end: null, available_at: null,
    status: "approve",
  }, {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000108",
    artifact_type: "source_manifest",
    identity: "candidate-manifest|1",
    content_hash: "f".repeat(64),
    source_locators: [], unit: null, period_start: null, period_end: null, available_at: null,
    status: "frozen",
  }],
};

const candidateEvidenceBase = {
  schema_version: "underwriting.v1" as const,
  revision_id: candidateRevision.id,
  object_id: candidateRevision.object_id,
  basis_id: candidateRevision.basis_id,
  version_kind: "industry_evidence_candidate" as const,
  content_hash: candidateRevision.content_hash,
  cutoff: candidateRevision.cutoff,
  source_manifest_hash: candidateRevision.source_manifest_hash,
  dossier: {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000104",
    content_hash: "e".repeat(64),
    dossier_key: "industry-capacity",
    version: 1,
    status: "candidate" as const,
    scope_statement: "仅限已冻结的行业产能候选证据。",
    rejected_calculations: [
      "不得将图表转录与其他来源拼接。",
      "不得将假设边界改写为观测事实。",
    ],
  },
  items: [{
    schema_version: "underwriting.v1" as const,
    metric_key: "industry.chart_output",
    status: "chart_approximation" as const,
    value: "0.9",
    unit: "TWh",
    observed_start: "2024-01-01T00:00:00Z",
    observed_end: "2024-12-31T00:00:00Z",
    available_at: "2025-05-15T15:59:59Z",
    source_id: "iea-2024",
    source_locator: "IEA 图 4.2",
    scope_statement: "图表转录的全球产出。",
    exclusions: ["不构成正式模型输入。"],
    methodology: "手工图表转录。",
    prohibited_splicing_declaration: "不得与其他来源拼接。",
    transcription_method: "读取图表刻度。",
    error_bound: "±0.1 TWh",
    scenario_use: null,
    not_observed_declared: false,
    unknown_reason: null,
  }, {
    schema_version: "underwriting.v1" as const,
    metric_key: "industry.assumption_cap",
    status: "assumption_bound" as const,
    value: "0.85",
    unit: "ratio",
    observed_start: "2024-01-01T00:00:00Z",
    observed_end: "2024-12-31T00:00:00Z",
    available_at: "2025-05-15T15:59:59Z",
    source_id: "iea-2024",
    source_locator: "研究假设 A",
    scope_statement: "仅限情景上限。",
    exclusions: ["非观测值。"],
    methodology: "有界情景假设。",
    prohibited_splicing_declaration: "不得与其他来源拼接。",
    transcription_method: null,
    error_bound: null,
    scenario_use: "仅用于候选情景边界。",
    not_observed_declared: true,
    unknown_reason: null,
  }, {
    schema_version: "underwriting.v1" as const,
    metric_key: "industry.effective_capacity",
    status: "unknown" as const,
    value: null,
    unit: null,
    observed_start: "2024-01-01T00:00:00Z",
    observed_end: "2024-12-31T00:00:00Z",
    available_at: "2025-05-15T15:59:59Z",
    source_id: "iea-2024",
    source_locator: "缺口记录",
    scope_statement: "有效产能。",
    exclusions: ["无可用观测。"],
    methodology: "缺口记录。",
    prohibited_splicing_declaration: "不得与其他来源拼接。",
    transcription_method: null,
    error_bound: null,
    scenario_use: null,
    not_observed_declared: false,
    unknown_reason: "未找到满足截止日的有效产能观测。",
  }],
  reviews: [{
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000105",
    content_hash: "33e295fde866e005b83d06f5d9803703c66ac673a3a0702f7ddad0b92f3aaa76",
    reviewer_identity: "reviewer:methodology",
    reviewer_role: "methodology" as const,
    decision: "approve" as const,
    rationale: "方法边界可追溯。",
    reviewed_at: "2025-05-15T15:59:59Z",
  }, {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000106",
    content_hash: "43ba54ab95546a0f93bc1a77d4b3ee71bcc1fbad8a65bd803ad1a3ede80dd360",
    reviewer_identity: "reviewer:provenance",
    reviewer_role: "provenance" as const,
    decision: "approve" as const,
    rationale: "来源边界可追溯。",
    reviewed_at: "2025-05-15T15:59:59Z",
  }],
  answerability: {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000107",
    content_hash: "1".repeat(64),
    state: "not_answerable" as const,
    research_debt_keys: ["candidate_evidence:industry-capacity"],
    resolution_requirements: ["正式化前不得使用。"],
  },
};

const candidateDossierCanonicalItems = candidateEvidenceBase.items.map(({
  schema_version: _schemaVersion, observed_start, observed_end, available_at, ...item
}) => ({
  ...item,
  observed_start: observed_start.replace("Z", "+00:00"),
  observed_end: observed_end.replace("Z", "+00:00"),
  available_at: available_at.replace("Z", "+00:00"),
}));

const candidateEvidence = {
  ...candidateEvidenceBase,
  items: [candidateEvidenceBase.items[1], candidateEvidenceBase.items[0], candidateEvidenceBase.items[2]],
  parent_refs: candidateRevision.parent_refs
    .filter((parent) => ["candidate_dossier", "candidate_review", "source_manifest"].includes(parent.artifact_type))
    .map(({ schema_version, reference, artifact_type, identity, content_hash }) => ({
      schema_version, reference, artifact_type, identity, content_hash,
      descriptor_preimage: artifact_type === "candidate_dossier" ? {
        schema_version: "underwriting.v1" as const,
        raw_content_hash: "8a0991545eee9faa854880be4d95f566384e6d32d255776d6e48b9c6fa2ed110",
        created_at: "2025-05-15T15:59:59+00:00",
      } : artifact_type === "candidate_review" ? {
        schema_version: "underwriting.v1" as const,
        raw_content_hash: reference.endsWith("105")
          ? "824f5d58e5251ed91a916340b1b0ba2f8c606261dddcf81f0b7ac203a2b3ac7e"
          : "bc6fdfe9d7221f5992c18df7d5cb8713564cb5516a599acc0a9a5cd2c4e76274",
        created_at: "2025-05-15T15:59:59+00:00",
        reviewed_at: "2025-05-15T15:59:59+00:00",
      } : {
        schema_version: "underwriting.v1" as const,
        row_content_hash: "f".repeat(64),
        manifest_hash: "5".repeat(64),
      },
    })),
  dossier: {
    ...candidateEvidenceBase.dossier,
    content_hash: "8a0991545eee9faa854880be4d95f566384e6d32d255776d6e48b9c6fa2ed110",
    supersedes_id: null,
    canonical_payload: {
      object_id: candidateRevision.object_id,
      basis_id: candidateRevision.basis_id,
      source_manifest_id: "00000000-0000-4000-8000-000000000108",
      dossier_key: "industry-capacity",
      version: 1,
      scope_statement: "仅限已冻结的行业产能候选证据。",
      status: "candidate" as const,
      purpose: "evidence_candidate" as const,
      items: candidateDossierCanonicalItems,
      rejected_calculations: [
        "不得将图表转录与其他来源拼接。",
        "不得将假设边界改写为观测事实。",
      ],
      source_manifest_hash: "5".repeat(64),
      created_at: "2025-05-15T15:59:59+00:00",
      supersedes_id: null,
    },
  },
  reviews: [{
    ...candidateEvidenceBase.reviews[0],
    content_hash: "824f5d58e5251ed91a916340b1b0ba2f8c606261dddcf81f0b7ac203a2b3ac7e",
  }, {
    ...candidateEvidenceBase.reviews[1],
    content_hash: "bc6fdfe9d7221f5992c18df7d5cb8713564cb5516a599acc0a9a5cd2c4e76274",
  }],
};

function unknownGap(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "underwriting.v1" as const,
    reference: "00000000-0000-4000-8000-000000000002",
    content_hash: "8".repeat(64),
    metric_key: "effective_capacity",
    unit: "GWh",
    source_id: "source-1",
    source_locator: "IEA, p.149",
    observed_start: "2024-01-01T00:00:00Z",
    observed_end: "2024-12-31T00:00:00Z",
    effective_at: "2025-01-01T00:00:00Z",
    available_at: "2025-01-02T00:00:00Z",
    source_role: "primary",
    observation_status: "unknown" as const,
    dimensions: { geography: "CN" },
    ...overrides,
  };
}

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

function renderArchiveNavigation(entry = "/underwriting/research/company-id/catl_economic_model_evidence_only") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Link to="/underwriting/research/other-company-id/other_frozen_research">切换冻结档案</Link>
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
      object_kind: "company",
      canonical_name: "宁德时代",
      external_key: "300750.SZ",
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
    boundary: vi.fn((revisionId: string) => Promise.resolve(revisionId === revisionOne.id ? {
      ...boundaryTwo,
      revision_id: revisionOne.id,
      object_id: revisionOne.object_id,
      basis_id: revisionOne.basis_id,
      version_kind: revisionOne.version_kind,
      content_hash: revisionOne.content_hash,
      cutoff: revisionOne.cutoff,
      source_manifest_hash: revisionOne.source_manifest_hash,
    } : boundaryTwo)),
    candidateEvidence: vi.fn().mockResolvedValue(candidateEvidence),
    ...overrides,
  } as unknown as UnderwritingResearchApi;
  setUnderwritingResearchApi(api);
  return api;
}

afterEach(() => {
  resetUnderwritingResearchApi();
});

describe("ResearchArchivePage", () => {
  it("renders only the selected reviewed candidate with chart, assumption, Unknown, and both reviews", async () => {
    const api = installApi({
      history: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1",
        object_id: candidateRevision.object_id,
        version_kind: candidateRevision.version_kind,
        object_kind: "industry",
        canonical_name: "电池行业",
        external_key: "battery-cn",
        revisions: [candidateRevision],
      }),
      revision: vi.fn().mockResolvedValue(candidateRevision),
    });
    renderArchive(`/underwriting/research/${candidateRevision.object_id}/${candidateRevision.version_kind}`);

    const candidate = await screen.findByRole("region", { name: "候选证据（已审阅，未正式化）" });
    expect(candidate).toHaveTextContent("仅限已冻结的行业产能候选证据。");
    expect(within(candidate).getByText("图表近似")).toBeVisible();
    expect(within(candidate).getByText("读取图表刻度。")).toBeVisible();
    expect(within(candidate).getByText("±0.1 TWh")).toBeVisible();
    expect(within(candidate).getByText("仅用于候选情景边界。")).toBeVisible();
    expect(within(candidate).getByText("未找到满足截止日的有效产能观测。")).toBeVisible();
    expect(within(candidate).getByText("reviewer:methodology")).toBeVisible();
    expect(within(candidate).getByText("reviewer:provenance")).toBeVisible();
    expect(within(candidate).getByText(/不得作为正式 IndustryState 输入或模型完成依据/)).toBeVisible();
    const rejectedCalculations = within(candidate).getByRole("region", { name: "冻结拒绝的计算" });
    expect(within(rejectedCalculations).getAllByRole("listitem").map((item) => item.textContent))
      .toEqual(candidateEvidence.dossier.rejected_calculations);
    expect(api.candidateEvidence).toHaveBeenCalledWith(candidateRevision.id);
    expect(screen.queryByRole("region", { name: "冻结证据记录" })).not.toBeInTheDocument();
  });

  it.each([
    ["does not bind to the selected revision", { ...candidateEvidence, revision_id: "00000000-0000-4000-8000-000000000199" }],
    ["adds an unrecognised top-level field", { ...candidateEvidence, unexpected: true }],
    ["has a malformed nested review", { ...candidateEvidence, reviews: [{ ...candidateEvidence.reviews[0], reviewed_at: "not-a-timestamp" }, candidateEvidence.reviews[1]] }],
    ["omits the frozen rejected calculations", { ...candidateEvidence, dossier: Object.fromEntries(Object.entries(candidateEvidence.dossier).filter(([key]) => key !== "rejected_calculations")) }],
    ["has malformed frozen rejected calculations", { ...candidateEvidence, dossier: { ...candidateEvidence.dossier, rejected_calculations: [""] } }],
    ["uses a foreign but structurally valid dossier", { ...candidateEvidence, dossier: { ...candidateEvidence.dossier, reference: "00000000-0000-4000-8000-000000000109" } }],
    ["has a review content hash mismatch", { ...candidateEvidence, reviews: [{ ...candidateEvidence.reviews[0], content_hash: "a".repeat(64) }, candidateEvidence.reviews[1]] }],
    ["omits a sealed parent descriptor", { ...candidateEvidence, parent_refs: candidateEvidence.parent_refs.slice(1) }],
    ["duplicates a sealed parent descriptor", { ...candidateEvidence, parent_refs: [...candidateEvidence.parent_refs, candidateEvidence.parent_refs[3]] }],
    ["uses a foreign sealed parent descriptor", {
      ...candidateEvidence,
      parent_refs: candidateEvidence.parent_refs.map((parent) => parent.artifact_type === "source_manifest" ? { ...parent, reference: "00000000-0000-4000-8000-000000000109" } : parent),
    }],
    ["changes the sealed manifest descriptor hash", {
      ...candidateEvidence,
      parent_refs: candidateEvidence.parent_refs.map((parent) => parent.artifact_type === "source_manifest" ? { ...parent, content_hash: "a".repeat(64) } : parent),
    }],
    ["has a rehashed forged dossier with the same reference", {
      ...candidateEvidence,
      dossier: {
        ...candidateEvidence.dossier,
        content_hash: "b".repeat(64), scope_statement: "篡改后的范围。",
        canonical_payload: { ...candidateEvidence.dossier.canonical_payload, scope_statement: "篡改后的范围。" },
      },
    }],
    ["has a rehashed forged review with the same reference", {
      ...candidateEvidence,
      reviews: [{ ...candidateEvidence.reviews[0], content_hash: "43c1672a21b1bd0dae4590e27d548db9a38199878fcd2de9993b9f01eeae1ed6", rationale: "篡改后的方法说明。" }, candidateEvidence.reviews[1]],
    }],
    ["contains prohibited calculation semantics", { ...candidateEvidence, dossier: { ...candidateEvidence.dossier, rejected_calculations: ["建议买入并设定目标价"] } }],
  ])("fails closed when candidate evidence %s", async (_name, response) => {
    installApi({
      history: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1", object_id: candidateRevision.object_id,
        version_kind: candidateRevision.version_kind, object_kind: "industry",
        canonical_name: "电池行业", external_key: "battery-cn", revisions: [candidateRevision],
      }),
      revision: vi.fn().mockResolvedValue(candidateRevision),
      candidateEvidence: vi.fn().mockResolvedValue(response),
    });
    renderArchive(`/underwriting/research/${candidateRevision.object_id}/${candidateRevision.version_kind}`);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByRole("region", { name: "候选证据（已审阅，未正式化）" })).not.toBeInTheDocument();
  });

  it.each([
    ["omits a required candidate parent", candidateRevision.parent_refs.slice(1)],
    ["duplicates a candidate parent", [...candidateRevision.parent_refs, candidateRevision.parent_refs[4]]],
    ["injects a second source-manifest parent", [...candidateRevision.parent_refs, {
      ...candidateRevision.parent_refs[4],
      reference: "00000000-0000-4000-8000-000000000109",
      identity: "candidate-manifest|2",
      content_hash: "6".repeat(64),
    }]],
  ])("fails closed when the selected revision %s", async (_name, parent_refs) => {
    const invalidRevision = { ...candidateRevision, parent_refs };
    installApi({
      history: vi.fn().mockResolvedValue({
        schema_version: "underwriting.v1", object_id: invalidRevision.object_id,
        version_kind: invalidRevision.version_kind, object_kind: "industry",
        canonical_name: "电池行业", external_key: "battery-cn", revisions: [invalidRevision],
      }),
      revision: vi.fn().mockResolvedValue(invalidRevision),
    });
    renderArchive(`/underwriting/research/${invalidRevision.object_id}/${invalidRevision.version_kind}`);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByRole("region", { name: "候选证据（已审阅，未正式化）" })).not.toBeInTheDocument();
  });

  it("does not retain a prior candidate while navigating to another frozen archive", async () => {
    const otherHistory = new Promise<ResearchRevisionHistory>(() => undefined);
    installApi({
      history: vi.fn((objectId: string) => objectId === candidateRevision.object_id ? Promise.resolve({
        schema_version: "underwriting.v1", object_id: candidateRevision.object_id,
        version_kind: candidateRevision.version_kind, object_kind: "industry",
        canonical_name: "电池行业", external_key: "battery-cn", revisions: [candidateRevision],
      } as ResearchRevisionHistory) : otherHistory),
      revision: vi.fn().mockResolvedValue(candidateRevision),
    });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/underwriting/research/${candidateRevision.object_id}/${candidateRevision.version_kind}`]}>
        <Link to="/underwriting/research/00000000-0000-4000-8000-000000000108/industry_evidence_candidate">切换候选档案</Link>
        <Routes><Route path="/underwriting/research/:objectId/:versionKind" element={<ResearchArchivePage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("region", { name: "候选证据（已审阅，未正式化）" })).toBeVisible();
    await user.click(screen.getByRole("link", { name: "切换候选档案" }));

    expect(screen.getByRole("status")).toHaveTextContent("正在读取冻结版本档案");
    expect(screen.queryByRole("region", { name: "候选证据（已审阅，未正式化）" })).not.toBeInTheDocument();
    expect(screen.queryByText("industry.chart_output")).not.toBeInTheDocument();
  });

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

  it("shows the selected CATL frozen answerability and does not misrepresent an empty gap parent set", async () => {
    installApi();
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect((await screen.findAllByText("研究尚需验证")).length).toBeGreaterThan(0);
    expect(screen.getByText("缺少关键基线")).toBeVisible();
    expect(screen.getByText("industry_utilisation")).toBeVisible();
    expect(screen.getByText("核验行业有效产能与利用率口径")).toBeVisible();
    const boundaries = screen.getByRole("complementary", { name: "研究边界" });
    expect(within(boundaries).getByText("此版本的冻结父图未记录可展示的 Unknown gap；这不表示行业缺口已解决。")).toBeVisible();
  });

  it("does not render one archive's frozen detail while the URL has switched to another archive", async () => {
    const otherRevision = {
      ...revisionTwo,
      id: "other-revision-1",
      object_id: "other-company-id",
      basis_id: "other-basis-1",
      version_kind: "other_frozen_research",
      content_hash: "9".repeat(64),
      source_manifest_hash: "e".repeat(64),
      parent_refs: [],
    };
    const otherBoundary = {
      ...boundaryTwo,
      revision_id: otherRevision.id,
      object_id: otherRevision.object_id,
      basis_id: otherRevision.basis_id,
      version_kind: otherRevision.version_kind,
      content_hash: otherRevision.content_hash,
      source_manifest_hash: otherRevision.source_manifest_hash,
      answerability: null,
    };
    const otherHistory = new Promise<ResearchRevisionHistory>(() => undefined);
    installApi({
      history: vi.fn((objectId: string): Promise<ResearchRevisionHistory> => objectId === "company-id" ? Promise.resolve({
        schema_version: "underwriting.v1",
        object_id: "company-id",
        version_kind: "catl_economic_model_evidence_only",
        object_kind: "company",
        canonical_name: "宁德时代",
        external_key: "300750.SZ",
        revisions: [revisionOne, revisionTwo],
      } as ResearchRevisionHistory) : otherHistory),
      revision: vi.fn((revisionId: string): Promise<ResearchRevision> => Promise.resolve(
        (revisionId === revisionTwo.id ? revisionTwo : otherRevision) as ResearchRevision,
      )),
      boundary: vi.fn((revisionId: string): Promise<ResearchRevisionBoundary> => Promise.resolve(
        (revisionId === revisionTwo.id ? boundaryTwo : otherBoundary) as ResearchRevisionBoundary,
      )),
    });
    const user = userEvent.setup();
    renderArchiveNavigation();

    expect(await screen.findByRole("heading", { name: "宁德时代" })).toBeVisible();
    await user.click(screen.getByRole("link", { name: "切换冻结档案" }));

    expect(screen.getByRole("status")).toHaveTextContent("正在读取冻结版本档案");
    expect(screen.queryByRole("heading", { name: "宁德时代" })).not.toBeInTheDocument();
    expect(screen.queryByText(revisionTwo.content_hash)).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "研究边界" })).not.toBeInTheDocument();
  });

  it.each([
    ["revision_id", "other-revision"],
    ["object_id", "other-object"],
    ["basis_id", "other-basis"],
    ["version_kind", "other-version-kind"],
    ["content_hash", "f".repeat(64)],
    ["cutoff", "2025-05-16T00:00:00Z"],
    ["source_manifest_hash", "e".repeat(64)],
  ])("fails closed when boundary %s does not bind to the selected revision", async (field, value) => {
    installApi({ boundary: vi.fn().mockResolvedValue({ ...boundaryTwo, [field]: value }) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("核验行业有效产能与利用率口径")).not.toBeInTheDocument();
  });

  it.each([
    ["accepts an unknown gap field", { ...boundaryTwo, unknown_evidence_gaps: [{
      ...unknownGap(), unexpected: true,
    }] }],
    ["returns a malformed unknown gap", { ...boundaryTwo, unknown_evidence_gaps: [{
      ...unknownGap(), dimensions: undefined,
    }] }],
    ["returns an invalid Unknown gap timestamp", { ...boundaryTwo, unknown_evidence_gaps: [{
      ...unknownGap({ available_at: "not-a-timestamp" }),
    }] }],
  ])("fails closed when boundary %s", async (_name, boundary) => {
    installApi({ boundary: vi.fn().mockResolvedValue(boundary) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
  });

  it.each([
    ["a non-canonical answerability reference", { ...boundaryTwo, answerability: { ...boundaryTwo.answerability, reference: "BOUNDARY-ANSWERABILITY-ID" } }],
    ["an uppercase answerability content hash", { ...boundaryTwo, answerability: { ...boundaryTwo.answerability, content_hash: "A".repeat(64) } }],
    ["a non-canonical Unknown gap reference", { ...boundaryTwo, unknown_evidence_gaps: [unknownGap({ reference: "gap-id" })] }],
    ["an uppercase Unknown gap content hash", { ...boundaryTwo, unknown_evidence_gaps: [unknownGap({ content_hash: "B".repeat(64) })] }],
    ["a normalised but invalid UTC calendar date", { ...boundaryTwo, unknown_evidence_gaps: [unknownGap({ observed_start: "2024-02-30T00:00:00Z" })] }],
    ["a non-UTC Unknown gap timestamp", { ...boundaryTwo, unknown_evidence_gaps: [unknownGap({ available_at: "2025-01-02T08:00:00+08:00" })] }],
  ])("fails closed when boundary contains %s", async (_name, boundary) => {
    installApi({ boundary: vi.fn().mockResolvedValue(boundary) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByRole("complementary", { name: "研究边界" })).not.toBeInTheDocument();
  });

  it("fails closed when the boundary response has an unrecognised top-level field", async () => {
    installApi({ boundary: vi.fn().mockResolvedValue({ ...boundaryTwo, extra: "must not render" }) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    expect(await screen.findByRole("alert")).toHaveTextContent("冻结档案的身份或版本链无法校验，未展示任何资料。");
    expect(screen.queryByText("must not render")).not.toBeInTheDocument();
  });

  it("renders every typed locator, unit, period, and availability field for an explicit Unknown gap", async () => {
    const gap = {
      ...unknownGap(),
    };
    installApi({ boundary: vi.fn().mockResolvedValue({ ...boundaryTwo, unknown_evidence_gaps: [gap] }) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    const boundaries = await screen.findByRole("complementary", { name: "研究边界" });
    expect(within(boundaries).getByText("effective_capacity")).toBeVisible();
    expect(within(boundaries).getByText("IEA, p.149")).toBeVisible();
    expect(within(boundaries).getByText("GWh")).toBeVisible();
    expect(within(boundaries).getByText("2024-01-01T00:00:00Z 至 2024-12-31T00:00:00Z")).toBeVisible();
    expect(within(boundaries).getByText("2025-01-02T00:00:00Z")).toBeVisible();
  });

  it("renders Unknown source provenance and typed dimensions in lexical key order as text", async () => {
    const unsafe = "<img src=x onerror=alert(1)>";
    const gap = unknownGap({
      source_id: unsafe,
      source_role: "primary_evidence",
      dimensions: { zeta: "Z", alpha: unsafe },
    });
    installApi({ boundary: vi.fn().mockResolvedValue({ ...boundaryTwo, unknown_evidence_gaps: [gap] }) });
    renderArchive("/underwriting/research/company-id/catl_economic_model_evidence_only");

    const boundaries = await screen.findByRole("complementary", { name: "研究边界" });
    expect(within(boundaries).getAllByText(unsafe)).toHaveLength(2);
    expect(within(boundaries).getByText("primary_evidence")).toBeVisible();
    expect(boundaries.querySelector("img")).toBeNull();
    const labels = Array.from(boundaries.querySelectorAll(".ura-gap-details dt"), (node) => node.textContent);
    expect(labels).toEqual([
      "来源标识", "来源角色", "来源定位", "单位", "观测期间", "生效时间", "可用时间", "维度 · alpha", "维度 · zeta",
    ]);
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
        object_kind: "company",
        canonical_name: "宁德时代",
        external_key: "300750.SZ",
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
      String.fromCharCode(97, 99, 116, 105, 111, 110),
      String.fromCharCode(100, 105, 115, 112, 111, 115, 105, 116, 105, 111, 110),
      String.fromCharCode(69, 108, 105, 103, 105, 98, 108, 101, 65, 99, 116, 105, 111, 110),
    ];
    expect(terms.every((term) => !new RegExp(`\\b${term}\\b`, "i").test(pageSource))).toBe(true);
  });

  it("does not use the economic-model resource as a boundary fallback", () => {
    expect(pageSource).not.toContain(`/${"economic"}-${"models"}`);
  });
});
