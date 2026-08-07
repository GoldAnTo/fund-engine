import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventResearchBasisScreen } from "../pages/prototype/EventResearchBasisScreen";

describe("EventResearchBasisScreen", () => {
  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => resetResearchClient());

  it("shows factor-to-evidence context rather than an unscoped standalone graph", async () => {
    render(<MemoryRouter initialEntries={["/events/event-tsm/basis"]}><Routes><Route path="/events/:caseId/basis" element={<EventResearchBasisScreen />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "研究依据" })).toBeVisible();
    expect(screen.getByText(/资本开支 \/ 自由现金流担忧/)).toBeVisible();
    expect(screen.getByRole("link", { name: "返回结论工作台" })).toHaveAttribute("href", "/events/event-tsm");
  });
});
