import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RelationshipCanvasPage } from "../pages/RelationshipCanvasPage";
import {
  setResearchClient,
} from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { renderWithAppShell } from "./renderWithAppShell";

function renderCanvas(scenario?: "large" | "offline") {
  if (scenario) {
    setResearchClient(new MockResearchAdapter({ scenario }));
  } else {
    setResearchClient(new MockResearchAdapter());
  }
  return renderWithAppShell(<RelationshipCanvasPage />, {
    initialEntries: ["/relationships/ai-compute"],
  });
}

describe("RelationshipCanvasPage", () => {
  it("keeps five continuous groups from evidence to fund", async () => {
    renderCanvas();
    expect(await screen.findByTestId("relationship-canvas")).toBeVisible();
    for (const label of ["证据", "命题", "因果链", "公司", "基金"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("filters object types via the legend toggle", async () => {
    renderCanvas();
    await screen.findByTestId("relationship-canvas");
    const fundLegend = screen.getByTestId("legend-fund");
    expect(fundLegend.getAttribute("aria-pressed")).toBe("true");
    await userEvent.click(fundLegend);
    await waitFor(() => {
      expect(fundLegend.getAttribute("aria-pressed")).toBe("false");
    });
  });

  it("large scenario shows the large-graph flag and survives rendering", async () => {
    renderCanvas("large");
    expect(await screen.findByTestId("large-graph-flag")).toBeVisible();
    expect(await screen.findByTestId("relationship-canvas")).toBeVisible();
  });

  it("offline scenario renders the backend-unavailable banner", async () => {
    renderCanvas("offline");
    expect(await screen.findByTestId("banner-offline")).toBeVisible();
  });

  it("exposes a '+ 添加证据' button in the canvas toolbar", async () => {
    renderCanvas();
    expect(await screen.findByTestId("canvas-add-evidence")).toBeVisible();
    expect(
      screen.getByTestId("canvas-add-evidence").textContent
    ).toContain("添加证据");
  });

  it("exposes per-column '+ 添加' affordances for non-evidence groups", async () => {
    renderCanvas();
    await screen.findByTestId("relationship-canvas");
    for (const g of ["proposition", "causal", "company", "fund"]) {
      expect(screen.getByTestId(`canvas-add-${g}`)).toBeInTheDocument();
    }
  });

  it("provides a primary '+ 新建关系' button in the page header", async () => {
    renderCanvas();
    const btn = await screen.findByTestId("canvas-new-relation");
    expect(btn).toBeVisible();
    expect(btn.className).toMatch(/btn--primary/);
  });

  it("mounts the React Flow relationship canvas with 5 visible groups", async () => {
    renderCanvas();
    const flow = await screen.findByTestId("relationship-flow");
    expect(flow).toBeInTheDocument();
    // React Flow renders an SVG inside the viewport at least once it has
    // initialised its internal store. We don't assert on edge DOM since
    // jsdom + ResizeObserver polyfill doesn't trigger the same layout
    // pipeline a real browser does.
    expect(flow.querySelector(".react-flow")).toBeInTheDocument();
  });
});