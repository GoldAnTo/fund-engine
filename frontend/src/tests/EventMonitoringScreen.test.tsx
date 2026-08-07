import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventMonitoringScreen } from "../pages/prototype/EventMonitoringScreen";

describe("EventMonitoringScreen", () => {
  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => resetResearchClient());

  it("keeps monitoring attached to an event and its one current human action", async () => {
    render(<MemoryRouter initialEntries={["/versions?caseId=event-tsm"]}><Routes><Route path="/versions" element={<EventMonitoringScreen />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "研究状态与后续变化" })).toBeVisible();
    expect(await screen.findByDisplayValue("台积电上调 CoWoS 指引后下跌")).toBeVisible();
    expect(await screen.findByRole("link", { name: "处理关键证据" })).toHaveAttribute("href", "/events/event-tsm/review");
  });
});
