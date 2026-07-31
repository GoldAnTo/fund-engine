import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

describe("ResearchWorkbenchPage", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(mockData),
    }) as unknown as typeof fetch;
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
});
