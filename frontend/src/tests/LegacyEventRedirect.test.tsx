import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LegacyEventRedirect } from "../components/LegacyEventRedirect";

function Destination() {
  const location = useLocation();
  return <p>{location.pathname}{location.search}</p>;
}

describe("LegacyEventRedirect", () => {
  it("keeps a legacy deep link inside its event context", async () => {
    render(<MemoryRouter initialEntries={["/relationships/event-tsm"]}><Routes><Route path="/relationships/:caseId" element={<LegacyEventRedirect />} /><Route path="/events/:caseId/basis" element={<Destination />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("/events/event-tsm/basis")).toBeVisible();
  });

  it("does not open an unscoped legacy research page", async () => {
    render(<MemoryRouter initialEntries={["/themes"]}><Routes><Route path="/themes" element={<LegacyEventRedirect />} /><Route path="/events" element={<Destination />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("/events")).toBeVisible();
  });
});
