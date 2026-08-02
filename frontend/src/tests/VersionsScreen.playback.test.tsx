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
    const playback = await screen.findByTestId("playback-mode");
    expect(playback).toBeTruthy();
    // The mock fixture has 3 events (4 cutoffs - 1 seed = 3 with summary).
    expect(within(playback).getByText(/第 1 \/ 3 步/)).toBeTruthy();
  });

  it("advances to the next playback step on click", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const next = within(playback).getByTestId("playback-next");
    fireEvent.click(next);
    expect(within(playback).getByText(/第 2 \/ 3 步/)).toBeTruthy();
  });

  it("reverts to the previous playback step on click", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    fireEvent.click(within(playback).getByTestId("playback-next"));
    fireEvent.click(within(playback).getByTestId("playback-prev"));
    expect(within(playback).getByText(/第 1 \/ 3 步/)).toBeTruthy();
  });

  it("displays a conclusion flip row at the step that flips", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    // Step 2 in the mock fixture has a flip from insufficient_evidence -> supported
    fireEvent.click(within(playback).getByTestId("playback-next"));
    const flips = within(playback).getAllByTestId("playback-flip");
    expect(flips.length).toBeGreaterThan(0);
  });

  it("disables prev at the first step and next at the last step", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const prev = within(playback).getByTestId("playback-prev") as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    const next = within(playback).getByTestId("playback-next") as HTMLButtonElement;
    // 3 steps; click twice to land on step 3
    fireEvent.click(next);
    fireEvent.click(next);
    expect(next.disabled).toBe(true);
  });

  it("toggles the play button label between 播放 and 暂停", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const toggle = within(playback).getByTestId("playback-toggle");
    expect(toggle.textContent).toContain("播放");
    expect(toggle.textContent).not.toContain("暂停");
    fireEvent.click(toggle);
    expect(toggle.textContent).toContain("暂停");
    fireEvent.click(toggle);
    expect(toggle.textContent).toContain("播放");
  });

  it("exposes a speed selector with 0.5x / 1x / 2x options", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const speed = within(playback).getByTestId(
      "playback-speed",
    ) as HTMLSelectElement;
    expect(speed.value).toBe("1");
    const options = Array.from(speed.options).map((o) => o.value);
    expect(options).toEqual(["0.5", "1", "2"]);
  });

  it("renders a progress bar whose width reflects the current step", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    const progress = within(playback).getByTestId(
      "playback-progress",
    ) as HTMLDivElement;
    // At step 1 of 3, width should be (1/3)*100% ~= 33.3%.
    expect(progress.style.width).toMatch(/33/);
    fireEvent.click(within(playback).getByTestId("playback-next"));
    // After stepping forward, width should be ~(2/3)*100% ~= 66.7%.
    expect(progress.style.width).toMatch(/66/);
  });

  it("renders the event card with the active step's link/review deltas and flip", async () => {
    setResearchClient(new MockResearchAdapter());
    renderScreen();
    const playback = await screen.findByTestId("playback-mode");
    // Step 1 mock: linkDelta=5, no flip
    const card1 = within(playback).getByTestId("playback-event-card");
    expect(card1.textContent).toMatch(/\+5/);
    // Step 2 mock: linkDelta=8 + a flip
    fireEvent.click(within(playback).getByTestId("playback-next"));
    const card2 = within(playback).getByTestId("playback-event-card");
    expect(card2.textContent).toMatch(/\+8/);
    const flip = within(card2).getByTestId("playback-flip");
    expect(flip.textContent).toMatch(/证据不足/);
    expect(flip.textContent).toMatch(/支持/);
  });
});
