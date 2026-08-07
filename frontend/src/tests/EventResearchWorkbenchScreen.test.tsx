import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventResearchWorkbenchScreen } from "../pages/prototype/EventResearchWorkbenchScreen";

describe("EventResearchWorkbenchScreen", () => {
  beforeEach(() => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "getEventWorkbench").mockResolvedValue({
      event: { id: "event-draft", eventTitle: "财报后的异常下跌", companyName: "示例公司", ticker: "XYZ", eventAt: null, status: "draft_ready", statusSummary: "等待结论复核", nextHumanAction: "审核结论草案", updatedAt: "2026-08-07T00:00:00Z" },
      lifecycle: { status: "draft_ready", activeRunId: "run-1", currentRound: 3, summary: "关键证据已审核", currentGap: null, nextHumanAction: "审核结论草案" },
      conclusion: { state: "ai_draft", text: "当前证据最支持资本开支压力这一解释。", citations: [] },
      factors: [], evidence: [], nextAction: { kind: "review_conclusion", label: "审核结论草案" },
    });
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("takes the user to a bounded conclusion review when the system draft is ready", async () => {
    render(<MemoryRouter initialEntries={["/events/event-draft"]}><Routes><Route path="/events/:caseId" element={<EventResearchWorkbenchScreen />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("AI 结论草案")).toBeVisible();
    expect(screen.getByRole("link", { name: "审核结论草案" })).toHaveAttribute("href", "/events/event-draft/conclusion");
  });
});
