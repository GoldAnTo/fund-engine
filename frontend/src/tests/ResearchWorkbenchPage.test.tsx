import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { WorkbenchResponse } from "../types";
import { ResearchWorkbenchPage } from "../pages/ResearchWorkbenchPage";

vi.mock("cytoscape", () => ({
  default: () => ({ on: () => {}, destroy: () => {} }),
}));

const mockData: WorkbenchResponse = {
  case: { id: "c1", title: "AI 算力链", industry_topic: "ai_compute" },
  focus_thesis: { id: "t1", statement: "GPU 需求将增长" },
  assessment: {
    id: "a1",
    conclusion: "supported",
    rationale: "证据支持",
    gaps: ["缺反证"],
    provisional: true,
  },
  review: null,
  major_gap: "缺反证",
  graph: {
    nodes: [
      { id: "t1", kind: "thesis", label: "GPU 需求将增长" },
      {
        id: "s1",
        kind: "statement",
        label: "CapEx 披露",
        statement_kind: "disclosed_fact",
      },
    ],
    edges: [
      {
        id: "l1",
        kind: "evidence",
        source: "t1",
        target: "s1",
        role: "supports",
        reason: "orders rose",
        review_state: "machine_generated",
      },
    ],
  },
  evidence_drawer_records: [
    {
      link_id: "l1",
      statement_id: "s1",
      statement_text: "CapEx 披露",
      statement_kind: "disclosed_fact",
      span_id: "sp1",
      verbatim_text: "财报第 32 页，表格第 4 行：CapEx 同比增长 40%",
      locator: { page: 32, table: 4, row: 4 },
      reason: "orders rose",
      role: "supports",
      scope: { segment: "DC" },
      period: "2026-03-31",
      review_state: "machine_generated",
    },
  ],
  stock_valuation_snapshots: [],
  fund_holding_disclosures: [],
};

function mockFetch(data: WorkbenchResponse) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  }) as unknown as typeof fetch;
}

describe("ResearchWorkbenchPage", () => {
  beforeEach(() => {
    mockFetch(mockData);
  });

  it("labels an unreviewed AI assessment as provisional", async () => {
    render(<ResearchWorkbenchPage caseId="c1" />);
    expect(
      await screen.findByText("AI 临时判断，未经人工复核")
    ).toBeVisible();
  });

  it("opens the exact source span when an evidence edge is selected", async () => {
    render(<ResearchWorkbenchPage caseId="c1" />);
    const btn = await screen.findByRole("button", {
      name: "查看证据：CapEx 披露",
    });
    await userEvent.click(btn);
    await waitFor(() => {
      expect(screen.getByText(/财报第 32 页，表格第 4 行/)).toBeVisible();
    });
  });

  it("refetches workbench with cutoff when a date is entered", async () => {
    render(<ResearchWorkbenchPage caseId="c1" />);
    await screen.findByText("AI 临时判断，未经人工复核");

    const fetchSpy = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    fetchSpy.mockClear();

    const input = screen.getByLabelText(/时间旅行/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "2025-06-01" } });

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("cutoff=2025-06-01")
      );
    });
    expect(screen.getByTestId("time-travel-flag")).toHaveTextContent(
      "2025-06-01"
    );
  });

  it("shows a contradiction comparison when contradicts evidence exists", async () => {
    const data: WorkbenchResponse = {
      ...mockData,
      graph: {
        nodes: mockData.graph.nodes,
        edges: [
          {
            id: "l1",
            kind: "evidence",
            source: "t1",
            target: "s1",
            role: "supports",
            reason: "orders rose",
            review_state: "machine_generated",
          },
          {
            id: "l2",
            kind: "evidence",
            source: "t1",
            target: "s1",
            role: "contradicts",
            reason: "orders fell",
            review_state: "machine_generated",
          },
        ],
      },
      evidence_drawer_records: [
        mockData.evidence_drawer_records[0],
        {
          link_id: "l2",
          statement_id: "s1",
          statement_text: "反证陈述",
          statement_kind: "disclosed_fact",
          span_id: "sp2",
          verbatim_text: "财报显示订单同比下滑",
          locator: { page: 40 },
          reason: "orders fell",
          role: "contradicts",
          scope: { segment: "DC" },
          period: "2026-03-31",
          review_state: "machine_generated",
        },
      ],
    };
    mockFetch(data);

    render(<ResearchWorkbenchPage caseId="c1" />);
    const btn = await screen.findByRole("button", {
      name: "查看证据：反证陈述",
    });
    await userEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId("contradiction-compare")).toBeVisible();
    });
    expect(screen.getByText("矛盾对比")).toBeVisible();
    // 反对分组可见，体现"信息有左有右"
    expect(screen.getByText(/^反对/)).toBeVisible();
  });

  it("renders causal step nodes in the graph fallback list", async () => {
    const data: WorkbenchResponse = {
      ...mockData,
      graph: {
        nodes: [
          { id: "t1", kind: "thesis", label: "GPU 需求将增长" },
          {
            id: "step1",
            kind: "step",
            label: "需求上升",
            sequence: 1,
            description: "GPU 需求上升",
          },
        ],
        edges: [
          { id: "causal1", kind: "causal", source: "step1", target: "t1" },
        ],
      },
    };
    mockFetch(data);

    render(<ResearchWorkbenchPage caseId="c1" />);
    await waitFor(() => {
      expect(
        screen.getByRole("group", { name: "因果链步骤" })
      ).toBeVisible();
    });
    expect(screen.getByText("因果步骤 1：GPU 需求上升")).toBeVisible();
  });
});
