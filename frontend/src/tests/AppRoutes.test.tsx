import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ApplicationRoutes } from "../appRoutes";

describe("ApplicationRoutes", () => {
  it("uses the current report intake as the root instead of the legacy event list", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ApplicationRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "接入研究资料" }),
    ).toBeVisible();
  });

  it("sends an unmatched legacy path to the current intake entry", async () => {
    render(
      <MemoryRouter initialEntries={["/removed-legacy-route"]}>
        <ApplicationRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "接入研究资料" }),
    ).toBeVisible();
  });
});
