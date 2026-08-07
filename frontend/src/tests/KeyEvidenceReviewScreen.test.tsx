import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { KeyEvidenceReviewScreen } from "../pages/prototype/KeyEvidenceReviewScreen";

describe("KeyEvidenceReviewScreen", () => {
  const adapter = new MockResearchAdapter();
  const reviewProposal = vi.spyOn(adapter, "reviewProposal");

  beforeEach(() => {
    setResearchClient(adapter);
  });
  afterEach(() => resetResearchClient());

  it("shows one original evidence item and a bounded review decision", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/events/event-tsm/review"]}><Routes><Route path="/events/:caseId/review" element={<KeyEvidenceReviewScreen />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("原始证据")).toBeVisible();
    expect(screen.getByRole("button", { name: "采纳" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "需要更多材料" }));
    expect(reviewProposal).toHaveBeenCalledWith("proposal-event-tsm", expect.objectContaining({
      outcome: "rejected", expected_version: 1,
    }));
    expect(await screen.findByText("已记录：系统会围绕该缺口继续检索。")).toBeVisible();
  });
});
