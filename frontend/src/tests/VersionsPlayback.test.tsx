import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { setResearchClient } from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { VersionsScreen } from "../pages/prototype/VersionsScreen";

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/versions"]}>
      <VersionsScreen />
    </MemoryRouter>,
  );
}

describe("VersionsScreen playback mode", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the playback section when the mock fixture exposes eventSummary", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    // Wait for the playback section to appear.
    const section = await screen.findByTestId("playback-mode", undefined, {
      timeout: 3000,
    });
    expect(section).toBeInTheDocument();
  });

  it("shows the playback progress bar and event card with link-delta", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const card = await screen.findByTestId("playback-event-card");
    // The mock fixture's first event step is the 2025-04-15 snapshot with
    // linkDelta=5. The card should show "5" somewhere in its rendered text.
    expect(within(card).getByText(/\+5/)).toBeInTheDocument();
    // Progress bar element exists.
    expect(screen.getByTestId("playback-progress")).toBeInTheDocument();
  });

  it("advances the cursor when the user clicks 'next'", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    await screen.findByTestId("playback-event-card");
    const next = await screen.findByTestId("playback-next");
    await act(async () => {
      fireEvent.click(next);
    });
    // After one forward click, the index counter shows "第 2 / 4 步".
    expect(screen.getByText(/第\s*2\s*\/\s*4\s*步/)).toBeInTheDocument();
  });

  it("disables the prev button at the first step", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    await screen.findByTestId("playback-event-card");
    const prev = await screen.findByTestId("playback-prev");
    expect(prev).toBeDisabled();
  });

  it("toggles the play label between ▶ 播放 and ⏸ 暂停", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const toggle = await screen.findByTestId("playback-toggle");
    expect(toggle.textContent).toMatch(/播放/);
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(toggle.textContent).toMatch(/暂停/);
  });

  it("auto-advances while playing with fake timers", async () => {
    setResearchClient(new MockResearchAdapter());
    vi.useFakeTimers();
    renderScreen();
    await screen.findByTestId("playback-event-card");
    const toggle = await screen.findByTestId("playback-toggle");
    await act(async () => {
      fireEvent.click(toggle);
    });
    // The interval is 1500ms (1x speed). Advance by 2 intervals.
    await act(async () => {
      vi.advanceTimersByTime(3100);
    });
    // Should now be on step 3.
    expect(screen.getByText(/第\s*3\s*\/\s*4\s*步/)).toBeInTheDocument();
  });
});
