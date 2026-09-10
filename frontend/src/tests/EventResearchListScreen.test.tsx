import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventResearchListScreen } from "../pages/prototype/EventResearchListScreen";

describe("EventResearchListScreen", () => {
  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => resetResearchClient());

  it("lists separate events with one clear next action", async () => {
    render(<MemoryRouter><EventResearchListScreen /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "事件研究" })).toBeVisible();
    expect(await screen.findByRole("link", { name: /Alphabet 财报超预期后股价下跌/ })).toBeVisible();
    expect(screen.getByText("审核 2 条关键证据")).toBeVisible();
  });
});
