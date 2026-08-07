import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventEvidenceLibraryScreen } from "../pages/prototype/EventEvidenceLibraryScreen";

describe("EventEvidenceLibraryScreen", () => {
  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => resetResearchClient());

  it("keeps the library explicitly scoped to the selected event", async () => {
    render(<MemoryRouter initialEntries={["/library?caseId=event-tsm"]}><Routes><Route path="/library" element={<EventEvidenceLibraryScreen />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "事件证据库" })).toBeVisible();
    expect(await screen.findByDisplayValue("台积电上调 CoWoS 指引后下跌")).toBeVisible();
    expect(await screen.findByText("公司上调全年资本开支指引，同时市场关注自由现金流承压。")).toBeVisible();
  });
});
