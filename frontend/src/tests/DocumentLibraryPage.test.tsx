import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentLibraryPage } from "../pages/DocumentLibraryPage";
import {
  resetResearchClient,
  setResearchClient,
} from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { renderWithAppShell } from "./renderWithAppShell";

function renderLibrary(scenario?: "parse_failed" | "offline") {
  if (scenario) {
    setResearchClient(new MockResearchAdapter({ scenario }));
  } else {
    resetResearchClient();
  }
  return renderWithAppShell(<DocumentLibraryPage />, {
    initialEntries: ["/documents"],
  });
}

describe("DocumentLibraryPage", () => {
  it("lists documents in a high-density table", async () => {
    renderLibrary();
    expect(await screen.findByTestId("library-table")).toBeVisible();
    expect(await screen.findByTestId("library-inspector")).toBeVisible();
  });

  it("shows the empty-state copy when query returns no results", async () => {
    renderLibrary();
    const search = await screen.findByTestId("library-search");
    await userEvent.type(search, "不存在的资料");
    await waitFor(() => {
      expect(screen.getByTestId("library-empty")).toBeVisible();
    });
  });

  it("parse_failed scenario shows the failure pane inside the inspector", async () => {
    renderLibrary("parse_failed");
    const row = await screen.findByTestId("library-row-doc-1");
    await userEvent.click(row);
    await waitFor(() => {
      expect(screen.getByTestId("library-failed")).toBeVisible();
    });
  });

  it("offline scenario shows the backend-unavailable banner", async () => {
    renderLibrary("offline");
    expect(await screen.findByTestId("banner-offline")).toBeVisible();
  });
});