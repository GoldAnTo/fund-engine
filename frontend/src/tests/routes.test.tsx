import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { resetResearchOsApi } from "../app/researchOsApi";
import { ResearchOsRoutes } from "../app/routes";
import { resetResearchClient } from "../data/researchClient";

let fetchMock: ReturnType<typeof vi.fn>;

const caseRoutes = [
  "/events/case-route",
  "/events/case-route/history",
  "/events/case-route/scope",
  "/events/case-route/evidence",
  "/events/case-route/documents",
  "/events/case-route/review",
  "/events/case-route/wiki",
  "/events/case-route/protocol",
  "/events/case-route/market",
  "/events/case-route/stocks/stock-route",
  "/events/case-route/funds/fund-route",
  "/events/case-route/monitor",
  "/events/case-route/monitor/config",
  "/events/case-route/relations",
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

  it("renders the automatic research entry without loading an existing Case list", async () => {
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "告诉系统你想研究什么" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "开始自动研究" })).toBeDisabled();
    expect(screen.queryByText(/无法读取可归入 Case 清单/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /自动研究/ })).toHaveLength(2);
    expect(screen.queryByText("资料收件箱")).not.toBeInTheDocument();
    expect(screen.queryByText(/从事件开始/)).not.toBeInTheDocument();
  });

  it.each(caseRoutes)(
    "renders a Case-specific live-data error for %s",
    async (path) => {
      render(
        <MemoryRouter initialEntries={[path]}>
          <ResearchOsRoutes />
        </MemoryRouter>,
      );

      expect(await screen.findByRole("alert")).toHaveTextContent(
        /无法读取这个 Case/,
      );
      expect(fetchMock).toHaveBeenCalled();
    },
  );

  it("renders the preparation route and exposes its live-data failure", async () => {
    render(
      <MemoryRouter initialEntries={["/events/case-route/preparation"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("backend_unavailable");
    expect(screen.getByRole("heading", { name: "无法读取研究准备" })).toBeVisible();
    expect(fetchMock).toHaveBeenCalled();
  });
});
