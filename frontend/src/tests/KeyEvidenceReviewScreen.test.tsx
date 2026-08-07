import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { KeyEvidenceReviewScreen } from "../pages/prototype/KeyEvidenceReviewScreen";

describe("KeyEvidenceReviewScreen", () => {
  let adapter: MockResearchAdapter;

  const renderScreen = () => render(
    <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
      <Routes><Route path="/events/:caseId/review" element={<KeyEvidenceReviewScreen />} /></Routes>
    </MemoryRouter>,
  );

  beforeEach(() => {
    adapter = new MockResearchAdapter();
    setResearchClient(adapter);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    resetResearchClient();
  });

  it("uses the event review queue in mock mode to show progress and a traceable source link", async () => {
    const getEventQueue = vi.spyOn(adapter, "getEventReviewQueue");
    const getLegacyQueue = vi.spyOn(adapter, "getReviewQueueView");
    renderScreen();

    expect(await screen.findByText("总数 3")).toBeVisible();
    expect(screen.getByText("已审核 0")).toBeVisible();
    expect(screen.getByText("待审核 1")).toBeVisible();
    expect(screen.getByText("无效来源 1")).toBeVisible();
    expect(screen.getByText("当前第 1 条/第 1 轮")).toBeVisible();
    expect(screen.getByText("下一步：审核 1 条关键证据")).toBeVisible();

    const source = screen.getByRole("link", { name: "台积电季度财报与电话会" });
    expect(source).toHaveAttribute("href", "https://investor.tsmc.com/english/quarterly-results/2026/q2");
    expect(source).toHaveAttribute("target", "_blank");
    expect(source).toHaveAttribute("rel", "noopener noreferrer");
    expect(getEventQueue).toHaveBeenCalledWith("event-tsm");
    expect(getLegacyQueue).not.toHaveBeenCalled();
  });

  it("keeps an invalid source auditable while disabling acceptance and explaining why", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /未验证测试来源/ }));
    expect(screen.getByText("来源不可采纳：测试域名不能作为正式证据来源")).toBeVisible();
    expect(screen.getByRole("button", { name: "采纳为本因素的正式证据" })).toBeDisabled();
    expect(screen.getByText("此来源仅供审计，不能计入结论。")).toBeVisible();
    expect(screen.getByRole("button", { name: "不采纳，不计入结论" })).toBeVisible();
    expect(screen.getByRole("button", { name: "退回并继续找真实来源" })).toBeVisible();
  });

  it("refreshes the event queue after accepting and names the factor, remaining work, and next action", async () => {
    const user = userEvent.setup();
    let decided = false;
    const getQueue = adapter.getEventReviewQueue.bind(adapter);
    vi.spyOn(adapter, "getEventReviewQueue").mockImplementation(async (caseId) => {
      const queue = await getQueue(caseId);
      return decided ? {
        ...queue,
        summary: {
          ...queue.summary,
          reviewed: 1,
          pending: 0,
          nextAction: "没有可采纳证据，系统将继续寻找真实来源",
        },
      } : queue;
    });
    vi.spyOn(adapter, "reviewProposal").mockImplementation(async (proposalId, payload) => {
      await MockResearchAdapter.prototype.reviewProposal.call(adapter, proposalId, payload);
      decided = true;
    });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "采纳为本因素的正式证据" }));

    expect(await screen.findByText("已采纳为“资本开支 / 自由现金流担忧”的正式证据；剩余 0 条有效待审。下一步：没有可采纳证据，系统将继续寻找真实来源")).toBeVisible();
    expect(screen.getByText("下一步：没有可采纳证据，系统将继续寻找真实来源")).toBeVisible();
  });
});
