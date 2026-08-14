import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { ResearchPreparationPage } from "../features/events/ResearchPreparationPage";

type PreparationScenario = "preparing" | "review_claims" | "review_protocol" | "review_plan" | "recoverable_failure" | "authorized";

function renderPreparation(scenario: PreparationScenario = "review_claims") {
  setResearchClient(new MockResearchAdapter({ preparationScenario: scenario }));
  return render(
    <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
      <Routes>
        <Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ResearchPreparationPage", () => {
  beforeEach(() => {
    setResearchClient(new MockResearchAdapter());
  });

  afterEach(() => {
    resetResearchClient();
  });

  it("shows the review-gated preparation timeline and keeps formal research unstarted", async () => {
    renderPreparation();

    expect(await screen.findByRole("heading", { name: "研究准备" })).toBeVisible();
    expect(screen.getByText("正式研究尚未启动")).toBeVisible();
    expect(screen.getByLabelText("系统准备活动")).toHaveTextContent("解析冻结原文");
    expect(screen.getByLabelText("系统准备活动")).toHaveTextContent("生成研究协议草案");
    expect(screen.getByLabelText("系统准备活动")).toHaveTextContent("生成补证计划草案");
    expect(screen.getByRole("heading", { name: "核对候选陈述" })).toBeVisible();
    expect(screen.getByRole("button", { name: "确认候选陈述" })).toBeDisabled();
    expect(screen.getAllByText("等待上一步确认").length).toBeGreaterThanOrEqual(2);
    expect(within(screen.getByLabelText("当前人工任务")).getAllByText("等待上一步确认")).toHaveLength(2);
  });

  it("only advances to protocol confirmation after the researcher explicitly confirms claims", async () => {
    const user = userEvent.setup();
    renderPreparation();

    const task = await screen.findByLabelText("当前人工任务");
    await user.selectOptions(
      within(task).getByLabelText("候选陈述 1 的决定"),
      "confirmed",
    );
    await user.type(
      within(task).getByLabelText("候选陈述 1 的核对说明"),
      "已核对冻结原文。",
    );
    await user.click(within(task).getByRole("button", { name: "确认候选陈述" }));

    expect(await within(screen.getByLabelText("当前人工任务")).findByRole("heading", { name: "确认研究协议" })).toBeVisible();
    expect(screen.getByText("正式研究尚未启动")).toBeVisible();
  });

  it("does not show a human confirmation action while the system is preparing drafts", async () => {
    renderPreparation("preparing");

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByRole("heading", { name: "系统正在准备" })).toBeVisible();
    expect(within(task).queryByRole("button", { name: /确认|授权/ })).not.toBeInTheDocument();
    expect(screen.getByText("正式研究尚未启动")).toBeVisible();
  });

  it("makes downstream drafts visibly stale before a researcher submits a claim correction", async () => {
    const user = userEvent.setup();
    renderPreparation();

    const task = await screen.findByLabelText("当前人工任务");
    await user.selectOptions(
      within(task).getByLabelText("候选陈述 1 的决定"),
      "modified",
    );

    expect(within(task).getByText(/协议草案与补证计划会标记为过期并由系统重生成/)).toBeVisible();
  });

  it("keeps plan authorization as the sole action before it creates a formal run", async () => {
    renderPreparation("review_plan");

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByRole("heading", { name: "授权补证计划" })).toBeVisible();
    expect(within(task).getByRole("button", { name: "授权补证计划并启动正式研究" })).toBeEnabled();
    expect(screen.getByText("正式研究尚未启动")).toBeVisible();
  });
});
