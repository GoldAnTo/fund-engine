import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { VersionsScreen } from "../pages/prototype/VersionsScreen";
import { setResearchClient } from "../data/researchClient";
import { MockResearchAdapter } from "../data/mockResearchAdapter";

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/versions"]}>
      <VersionsScreen />
    </MemoryRouter>,
  );

describe("VersionsScreen playback mode", () => {
  it("renders the playback section when snapshotPoints have eventSummaries", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    // Wait for the view to load (mock returns synchronously but the page
    // mounts after one microtask).
    const heading = await screen.findByText(/快照时间轴/);
    expect(heading).toBeTruthy();
    const playback = await screen.findByTestId("playback-mode");
    expect(playback).toBeTruthy();
    // The mock fixture has 3 events (4 cutoffs - 1 seed = 3 with summary).
    expect(within(playback).getByText(/第 1 \/ 3 步/)).toBeTruthy();
  });

  it("advances one step when the next button is clicked", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const next = within(playback).getByTestId("playback-next");
    fireEvent.click(next);
    expect(within(playback).getByText(/第 2 \/ 3 步/)).toBeTruthy();
  });

  it("rewinds one step when the previous button is clicked", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    fireEvent.click(within(playback).getByTestId("playback-next"));
    fireEvent.click(within(playback).getByTestId("playback-prev"));
    expect(within(playback).getByText(/第 1 \/ 3 步/)).toBeTruthy();
  });

  it("shows conclusion flips when the playhead lands on an event with one", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    // Step 2 in the mock fixture has a flip from insufficient_evidence -> supported
    fireEvent.click(within(playback).getByTestId("playback-next"));
    const flips = within(playback).getAllByTestId("playback-flip");
    expect(flips.length).toBeGreaterThan(0);
  });

  it("disables the previous button at the first step", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const prev = within(playback).getByTestId("playback-prev") as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
  });

  it("disables the next button at the last step", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const next = within(playback).getByTestId("playback-next") as HTMLButtonElement;
    // 3 steps; click twice to land on step 3
    fireEvent.click(next);
    fireEvent.click(next);
    expect(next.disabled).toBe(true);
  });
});
