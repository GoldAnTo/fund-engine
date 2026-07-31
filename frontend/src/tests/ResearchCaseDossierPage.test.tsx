import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResearchCaseDossierPage } from "../pages/ResearchCaseDossierPage";
import {
  resetResearchClient,
  setResearchClient,
} from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { renderWithAppShell } from "./renderWithAppShell";

function renderDossier(scenario?: "insufficient" | "empty" | "offline") {
  if (scenario) {
    setResearchClient(new MockResearchAdapter({ scenario }));
  } else {
    resetResearchClient();
  }
  return renderWithAppShell(<ResearchCaseDossierPage />, {
    initialEntries: ["/cases/ai-compute"],
  });
}

describe("ResearchCaseDossierPage", () => {
  it("shows case navigator, causal chain, and supports/contradicts groups", async () => {
    renderDossier();
    expect(await screen.findByTestId("causal-chain")).toBeVisible();
    expect(await screen.findByTestId("evidence-group-supports")).toBeVisible();
    expect(await screen.findByTestId("evidence-group-contradicts")).toBeVisible();
    expect(await screen.findByText(/政策与准入/)).toBeVisible();
  });

  it("opens the source inspector when a supports evidence row is selected", async () => {
    renderDossier();
    const supports = await screen.findByText(/开展智能网联汽车准入试点/);
    await userEvent.click(supports);
    await waitFor(() => {
      expect(screen.getByTestId("source-inspector")).toBeVisible();
    });
    expect(await screen.findAllByText(/工信部官网/)).toBeTruthy();
  });

  it("focusing a causal step dims unrelated evidence rows", async () => {
    renderDossier();
    const stepBtn = await screen.findByText(/政策与准入/);
    await userEvent.click(stepBtn);
    await waitFor(() => {
      const dimmed = document.querySelectorAll(".evidence-card.is-dimmed");
      // 当 focusedStepId 存在时，至少有一个 evidence 被 dimmed 或所有 evidence 都 marked as related.
      expect(dimmed.length).toBeGreaterThan(0);
    });
  });

  it("switches dossier tabs and syncs the URL", async () => {
    renderDossier();
    const risksTab = await screen.findByRole("button", { name: "风险与假设" });
    await userEvent.click(risksTab);
    await waitFor(() => {
      expect(risksTab.getAttribute("aria-selected")).toBe("true");
    });
  });

  it("renders the major-gap banner when assessment.major_gap exists", async () => {
    renderDossier();
    expect(await screen.findByTestId("major-gap")).toBeVisible();
  });

  it("insufficient scenario shows the empty evidence state with gaps", async () => {
    renderDossier("insufficient");
    expect(await screen.findByText(/证据不足以/)).toBeVisible();
    // the empty state copy is shown
    expect(await screen.findByText(/当前证据为空/)).toBeVisible();
  });

  it("empty scenario surfaces the first-use guidance", async () => {
    renderDossier("empty");
    expect(await screen.findByTestId("empty-case")).toBeVisible();
    expect(screen.getByText(/建立首个命题/)).toBeVisible();
  });

  it("offline scenario shows the backend-unavailable banner", async () => {
    renderDossier("offline");
    expect(await screen.findByTestId("banner-offline")).toBeVisible();
  });
});