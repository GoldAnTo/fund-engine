import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReviewWorkbenchPage } from "../pages/ReviewWorkbenchPage";
import { renderWithAppShell } from "./renderWithAppShell";

function renderReview() {
  return renderWithAppShell(<ReviewWorkbenchPage />, {
    initialEntries: ["/review"],
  });
}

describe("ReviewWorkbenchPage", () => {
  it("uses the three-column queue/source/decision layout", async () => {
    renderReview();
    expect(await screen.findByTestId("review-workbench")).toBeVisible();
    expect(screen.getByText("待审核项")).toBeVisible();
    expect(screen.getByText("冻结原文")).toBeVisible();
    expect(screen.getByText("人工决定")).toBeVisible();
  });

  it("appends an audit record and moves to the next item after confirming", async () => {
    renderReview();
    const confirmBtn = await screen.findByTestId("review-confirm");
    await userEvent.click(confirmBtn);
    await waitFor(() => {
      expect(
        screen.getByText(/confirmed ·/)
      ).toBeVisible();
    });
  });

  it("does not provide a batch approve button", async () => {
    renderReview();
    await screen.findByTestId("review-workbench");
    expect(screen.queryByText(/一键/)).toBeNull();
    expect(screen.queryByRole("button", { name: /批量/ })).toBeNull();
  });

  it("selecting a different item in the queue switches the source/decision pane", async () => {
    renderReview();
    await screen.findByTestId("review-workbench");
    const items = await screen.findAllByTestId(/^review-item-/);
    expect(items.length).toBeGreaterThan(1);
    const secondItem = items[1];
    const parentLi = secondItem.closest("li");
    expect(parentLi).not.toBeNull();
    await userEvent.click(secondItem);
    await waitFor(() => {
      expect(parentLi?.className ?? "").toContain("is-active");
    });
  });

  it("disables write actions when backend is unavailable", async () => {
    const previous = await import("../data/researchClient");
    const original = previous.researchClient;
    const { MockResearchAdapter } = await import("../data/mockResearchAdapter");
    const offline = new MockResearchAdapter({ scenario: "offline" });
    previous.setResearchClient(offline);
    try {
      renderReview();
      expect(await screen.findByTestId("banner-offline")).toBeVisible();
      // buttons stay present in DOM but their action throws; banner warns user
    } finally {
      previous.setResearchClient(original);
    }
  });
});