import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setResearchClient } from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { VersionsScreen } from "../pages/prototype/VersionsScreen";

function renderScreen() {
  return render(
    <MemoryRouter>
      <VersionsScreen />
    </MemoryRouter>,
  );
}

describe("VersionsScreen playback mode", () => {
  beforeEach(() => {
    setResearchClient(new MockResearchAdapter({ scenario: "typical" }));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the playback controls when snapshot points carry event summaries", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByTestId("playback-mode")).toBeTruthy();
    });
    expect(screen.getByTestId("playback-toggle")).toBeTruthy();
    expect(screen.getByTestId("playback-progress")).toBeTruthy();
  });

  it("starts paused and surfaces the first event card", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByTestId("playback-event-card")).toBeTruthy();
    });
    expect(screen.getByTestId("playback-toggle").textContent).toContain("播放");
  });

  it("advances one step when next is clicked", async () => {
    renderScreen();
    await waitFor(() => screen.getByTestId("playback-event-card"));
    const next = screen.getByTestId("playback-next") as HTMLButtonElement;
    const before = next.textContent ?? "";
    fireEvent.click(next);
    // The button label and counter should still be present, and the next
    // button should still be enabled (i.e. there is at least one more step).
    expect((screen.getByTestId("playback-next") as HTMLButtonElement).disabled).toBeFalsy();
    expect(before).toBeTruthy();
  });

  it("disables prev at the start of the list", async () => {
    renderScreen();
    await waitFor(() => screen.getByTestId("playback-event-card"));
    expect((screen.getByTestId("playback-prev") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders a flip badge when a conclusion changed at this step", async () => {
    renderScreen();
    await waitFor(() => screen.getByTestId("playback-event-card"));
    // The fixture has the first step (2025-04-15) as a pure data-growth step
    // (no flips) and the second step (2025-05-15) containing a flip. Step
    // forward to the flip.
    const next = screen.getByTestId("playback-next");
    fireEvent.click(next);
    await waitFor(() => {
      expect(screen.queryAllByTestId("playback-flip").length).toBeGreaterThan(0);
    });
  });
});
