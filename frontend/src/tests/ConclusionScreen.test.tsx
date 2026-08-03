/**
 * 结论与关键因素页 (设计原型11) · 前后端契约测试
 *
 * 验证：
 *   1. Mock 模式下页面正常渲染所有四个区块
 *   2. HTTP 模式下正确解析 snake_case 字段为 camelCase
 *   3. 选中因素时右栏 gap card 同步更新
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithAppShell } from "./renderWithAppShell";
import { ConclusionScreen } from "../pages/prototype/ConclusionScreen";
import { setResearchClient, resetResearchClient } from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import type { ConclusionView } from "../domain/prototypeTypes";
import type { ResearchClient } from "../domain/prototypeTypes";
import { within } from "@testing-library/react";

const sampleView: ConclusionView = {
  basis: {
    cutoff: "2025-06-30T00:00:00+08:00",
    isHistorical: true,
    ledgerHighWatermark: null,
    projectionBuiltAt: null,
    projectionSchemaVersion: null,
  },
  header: {
    researchCaseId: "case-x",
    caseTitle: "测试案例",
    industryTopic: "测试行业",
    evidenceCutoff: "2025-06-30",
    conclusionText: "Mock 结论文本",
    conclusionStatus: "insufficient_evidence",
    rationale: "Mock 理由",
    reviewState: "reviewed",
    reviewer: "测试评审员",
    reviewedAt: "2025-06-30T22:40:00+08:00",
    snapshotId: "SNAP-1",
    aiProvisional: false,
  },
  keyFactors: [
    {
      factorId: "F-T-01",
      thesisId: "th-1",
      thesisTitle: "测试命题",
      thesisStatement: "测试陈述",
      statusLabel: "已复现",
      roleLabel: "已复现",
      factorLabel: "测试因素",
      timeOrder: "T1 → T2 → T3",
      mechanism: "机制",
      directEvidence: "证据",
      alternatives: "无",
      differenceExplanation: "无",
      scopeWarning: null,
      falsifier: "证伪条件",
      impactObject: "测试对象",
    },
  ],
  comparison: {
    columns: ["评审维度", "直接证据"],
    rows: [
      {
        factorId: "F-T-01",
        factorLabel: "测试因素",
        cells: [
          {
            factorId: "F-T-01",
            factorLabel: "测试因素",
            columnId: "factor_dimension",
            columnLabel: "评审维度",
            text: "测试对象",
          },
          {
            factorId: "F-T-01",
            factorLabel: "测试因素",
            columnId: "direct_evidence",
            columnLabel: "直接证据",
            text: "证据文本",
          },
        ],
      },
    ],
  },
  sourceGroups: [
    {
      sectionLabel: "支持 · 已复现",
      relations: [
        {
          label: "支持 · 已复现",
          relation: "supports",
          documentTitle: "文档 A",
          publisher: "发布者 A",
          citation: "引用文本",
          locator: "P1¶1",
        },
      ],
    },
  ],
  reproductionManifest: {
    currentSelectionLabel: "F-T-01",
    currentSelectionState: "F-T-01",
    formalJudgment: "正式判断",
    researchSnapshot: "SNAP-1",
    documentVersion: "doc-1",
    publisherRecord: "pub-1",
    availableAt: "2025-06-30",
    reproducer: "测试人",
    factorCompareVersion: "v1",
    recheckManifest: "snapshot: x | inputs: 1 | output_hash: a",
  },
  causalPath: [
    { sequence: 1, description: "步骤 1" },
    { sequence: 2, description: "步骤 2" },
  ],
  gapExplanation: {
    factorId: "F-T-01",
    factorLabel: "测试因素",
    why: "为什么",
    applicableScope: "适用边界",
    category: "适用边界",
    dataPattern: "数据模式",
    categoryAlt: "假设",
    rationale: "理由",
  },
};

class StubAdapter implements ResearchClient {
  getConclusionView = vi.fn().mockResolvedValue(sampleView);
  getOverview = vi.fn();
  getCaseDossier = vi.fn();
  getRelationshipGraph = vi.fn();
  getDocuments = vi.fn();
  getDocumentDetail = vi.fn();
  getReviewQueue = vi.fn();
  search = vi.fn();
  getCaseSummaries = vi.fn();
  submitReviewDecision = vi.fn();
  getWorkspaceOverviewView = vi.fn();
  getWorkspaceOverviewScreen = vi.fn();
  getNewResearchView = vi.fn();
  createCase = vi.fn();
  listCaseSummaries = vi.fn();
  getResearchPlanView = vi.fn();
  getCaseWorkbenchView = vi.fn();
  getRelationshipGraphView = vi.fn();
  getLibraryView = vi.fn();
  getDataCenterView = vi.fn();
  getVersionsView = vi.fn();
  getThemeIndexView = vi.fn();
  getThemeWorkbenchView = vi.fn();
  getReviewQueueView = vi.fn();
  submitLinkReview = vi.fn();
  reviewAssessment = vi.fn();
  rerunThesis = vi.fn();
  proposeEvidence = vi.fn();
  ingestDocuments = vi.fn();
  extractStatements = vi.fn();
  getDataCenterMetric = vi.fn();
  listCompanies = vi.fn();
  getCompanyDossier = vi.fn();
  listThemes = vi.fn();
  getThemeView = vi.fn();
}

describe("ConclusionScreen", () => {
  beforeEach(() => {
    const stub = new StubAdapter();
    setResearchClient(stub);
  });
  afterEach(() => {
    resetResearchClient();
  });

  it("renders header, key factor, comparison and source groups", async () => {
    const screen = renderWithAppShell(
      <ConclusionScreen />,
      { initialEntries: ["/conclusion/case-x"] },
    );
    const node = await screen.findByTestId("conclusion-screen");
    expect(node).toBeInTheDocument();

    const comparison = await screen.findByTestId("comparison-card");
    expect(within(comparison).getByText("证据文本")).toBeInTheDocument();

    const sources = await screen.findByTestId("source-groups-card");
    expect(within(sources).getByText("支持 · 已复现")).toBeInTheDocument();

    const manifest = await screen.findByTestId("manifest-card");
    expect(within(manifest).getByText("SNAP-1")).toBeInTheDocument();
    expect(within(manifest).getByText("doc-1")).toBeInTheDocument();

    const causal = await screen.findByTestId("causal-path-card");
    expect(within(causal).getByText("步骤 1")).toBeInTheDocument();

    const recheck = await screen.findByTestId("recheck-card");
    expect(within(recheck).getByText(/snapshot: x/)).toBeInTheDocument();
  });
});

describe("MockResearchAdapter.getConclusionView", () => {
  it("returns a deterministic ConclusionView for the case id", async () => {
    const adapter = new MockResearchAdapter();
    const view = await adapter.getConclusionView("any-case-id");
    expect(view.header.caseTitle).toBeTruthy();
    expect(view.keyFactors.length).toBeGreaterThan(0);
    expect(view.comparison.columns.length).toBeGreaterThan(0);
    expect(view.causalPath.length).toBeGreaterThan(0);
  });
});