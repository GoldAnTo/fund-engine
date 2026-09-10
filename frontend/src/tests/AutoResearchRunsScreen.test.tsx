import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { AutoResearchRunsScreen } from "../pages/prototype/AutoResearchRunsScreen";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { setResearchClient } from "../data/researchClient";
import { renderWithAppShell } from "./renderWithAppShell";

describe("AutoResearchRunsScreen", () => {
  it("renders the run list fields and links to detail", async () => {
    setResearchClient(new MockResearchAdapter());
    renderWithAppShell(<AutoResearchRunsScreen />, { initialEntries: ["/auto-research/runs"] });
    expect(await screen.findByTestId("run-list-item")).toHaveAttribute("href", "/auto-research/runs/run-aic-001");
    expect(screen.getByText("evidence_search")).toBeVisible();
    expect(screen.getByText("等待人工审核 2 条提议关系")).toBeVisible();
  });

  it("renders required detail sections", async () => {
    setResearchClient(new MockResearchAdapter());
    renderWithAppShell(<AutoResearchRunsScreen />, { initialEntries: ["/auto-research/runs/run-aic-001"] });
    expect(await screen.findByTestId("run-detail")).toBeVisible();
    expect(screen.getByText("pending_proposals")).toBeVisible();
    expect(screen.getByText("failed_tasks")).toBeVisible();
  });
});
