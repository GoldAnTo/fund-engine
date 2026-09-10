import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { resetResearchOsApi } from "../app/researchOsApi";
import { ResearchOsRoutes } from "../app/routes";
import { resetResearchClient } from "../data/researchClient";

let fetchMock: ReturnType<typeof vi.fn>;

const caseRoutes: Array<[string, RegExp]> = [
  ["/events/case-route", /统一研究流程无法读取/],
  ["/events/case-route/history", /无法读取这个 Case/],
  ["/events/case-route/scope", /无法读取这个 Case/],
  ["/events/case-route/evidence", /无法读取这个 Case/],
  ["/events/case-route/documents", /无法读取这个 Case/],
  ["/events/case-route/review", /无法读取这个 Case/],
  ["/events/case-route/wiki", /无法读取这个 Case/],
  ["/events/case-route/protocol", /无法读取这个 Case/],
  ["/events/case-route/market", /无法读取这个 Case/],
  ["/events/case-route/stocks/stock-route", /无法读取这个 Case/],
  ["/events/case-route/funds/fund-route", /无法读取这个 Case/],
  ["/events/case-route/monitor", /无法读取这个 Case/],
  ["/events/case-route/monitor/config", /无法读取这个 Case/],
  ["/events/case-route/relations", /无法读取这个 Case/],
];

describe("Research OS route inventory", () => {
  beforeEach(() => {
    resetResearchClient();
    resetResearchOsApi();
    fetchMock = vi.fn().mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    resetResearchClient();
    resetResearchOsApi();
    vi.unstubAllGlobals();
  });

  it.each([
    ["/", /暂时无法读取事件研究/],
    ["/events", /暂时无法读取事件研究/],
    ["/network", /无法读取跨 Case 关联/],
    ["/monitoring", /无法读取实际运行记录/],
    ["/governance/case-admissions", /当前身份不能读取历史 Case 准入队列/],
    ["/events/new", /无法读取可归入 Case 清单/],
    ["/retired-prototype-route", /暂时无法读取事件研究/],
  ])("renders an explicit live-data error for %s", async (path, message) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(fetchMock).toHaveBeenCalled();
  });

  it.each(caseRoutes)(
    "renders a Case-specific live-data error for %s",
    async (path, expectedMessage) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <ResearchOsRoutes />
        </MemoryRouter>,
      );

      expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
      expect(fetchMock).toHaveBeenCalled();
    },
  );
});
