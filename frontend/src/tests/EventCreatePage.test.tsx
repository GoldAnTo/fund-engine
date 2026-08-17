import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventCreatePage } from "../features/events/EventCreatePage";

function renderCreatePage() {
  return render(
    <MemoryRouter initialEntries={["/events/new"]}>
      <Routes>
        <Route path="/events/new" element={<EventCreatePage />} />
        <Route
          path="/events/:caseId/automatic-research"
          element={<p>自动研究过程</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("EventCreatePage automatic research entry", () => {
  let adapter: MockResearchAdapter;

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    setResearchClient(adapter);
  });

  afterEach(() => {
    resetResearchClient();
  });

  it("shows only one research input and one primary action", () => {
    renderCreatePage();

    expect(
      screen.getByRole("heading", { name: "告诉系统你想研究什么" }),
    ).toBeVisible();
    expect(screen.getByLabelText("研究问题")).toBeVisible();
    expect(
      screen.getAllByRole("button", { name: "开始自动研究" }),
    ).toHaveLength(1);
    expect(screen.queryByText(/资料使用许可声明/)).not.toBeInTheDocument();
    expect(screen.queryByText(/关键因素/)).not.toBeInTheDocument();
    expect(screen.queryByText(/严格研究协议/)).not.toBeInTheDocument();
    expect(screen.queryByText(/决定材料归属/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/来源接入方式/)).not.toBeInTheDocument();
  });

  it("starts automatic research from the trimmed input and opens its process page", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(adapter, "startAutomaticResearch");
    renderCreatePage();

    await user.type(
      screen.getByLabelText("研究问题"),
      "  英伟达新产品会如何影响供应链？  ",
    );
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith("英伟达新产品会如何影响供应链？"),
    );
    expect(start).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("自动研究过程")).toBeVisible();
  });

  it("does not submit an empty input", async () => {
    const user = userEvent.setup();
    const start = vi.spyOn(adapter, "startAutomaticResearch");
    renderCreatePage();

    const submit = screen.getByRole("button", { name: "开始自动研究" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("研究问题"), "   ");
    expect(submit).toBeDisabled();
    expect(start).not.toHaveBeenCalled();
  });

  it("disables the form while the research is starting", async () => {
    const user = userEvent.setup();
    let resolveStart!: (value: {
      caseId: string;
      runId: string;
      status: "queued";
    }) => void;
    vi.spyOn(adapter, "startAutomaticResearch").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve;
        }),
    );
    renderCreatePage();

    const input = screen.getByLabelText("研究问题");
    await user.type(input, "研究一个事件");
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    expect(screen.getByRole("button", { name: "正在启动…" })).toBeDisabled();
    expect(input).toBeDisabled();
    resolveStart({ caseId: "case-loading", runId: "run-loading", status: "queued" });
    expect(await screen.findByText("自动研究过程")).toBeVisible();
  });

  it("announces a start failure inline and allows retry", async () => {
    const user = userEvent.setup();
    vi.spyOn(adapter, "startAutomaticResearch").mockRejectedValue(
      new Error("offline"),
    );
    renderCreatePage();

    await user.type(screen.getByLabelText("研究问题"), "研究一个事件");
    await user.click(screen.getByRole("button", { name: "开始自动研究" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "自动研究未能启动，请稍后重试。",
    );
    expect(screen.getByRole("button", { name: "开始自动研究" })).toBeEnabled();
    expect(screen.getByLabelText("研究问题")).toHaveFocus();
  });
});
