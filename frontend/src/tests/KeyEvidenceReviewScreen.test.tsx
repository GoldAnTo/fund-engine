import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { hasOwnKey, KeyEvidenceReviewScreen } from "../pages/prototype/KeyEvidenceReviewScreen";

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
    expect(screen.getAllByText("AI 关系：支持")[0]).toBeVisible();
    expect(screen.getByText("支持的因素")).toBeVisible();
    expect(screen.getByText("发布日期")).toBeVisible();
    const rawUrl = screen.getByRole("link", { name: "https://investor.tsmc.com/english/quarterly-results/2026/q2" });
    expect(rawUrl).toHaveAttribute("target", "_blank");
    expect(rawUrl).toHaveAttribute("rel", "noopener noreferrer");
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

  it("replaces a test-domain source title while preserving the invalid source audit link", async () => {
    const user = userEvent.setup();
    const originalGetQueue = adapter.getEventReviewQueue.bind(adapter);
    vi.spyOn(adapter, "getEventReviewQueue").mockImplementation(async (caseId) => {
      const queue = await originalGetQueue(caseId);
      return {
        ...queue,
        items: queue.items.map((item, index) => index === 2 ? {
          ...item,
          sourceTitle: "Market report from example.org",
          documentSourceUrl: "https://example.org/unverified",
        } : item),
      };
    });
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /来源待核验/ }));
    expect(screen.queryByText("Market report from example.org")).not.toBeInTheDocument();
    expect(screen.getAllByText("来源待核验").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "https://example.org/unverified" })).toHaveAttribute("href", "https://example.org/unverified");
    expect(screen.getAllByText("无效来源")[0]).toBeVisible();
  });

  it("describes the selected factor with the queue item's AI relationship", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.click(await screen.findByRole("button", { name: /用户粘贴的市场报道/ }));

    expect(screen.getByText("补充背景的因素")).toBeVisible();
    expect(screen.queryByText("支持的因素")).not.toBeInTheDocument();
  });

  it("renders a contradictory relationship without treating it as support", async () => {
    const originalGetQueue = adapter.getEventReviewQueue.bind(adapter);
    vi.spyOn(adapter, "getEventReviewQueue").mockImplementation(async (caseId) => {
      const queue = await originalGetQueue(caseId);
      return {
        ...queue,
        items: queue.items.map((item, index) => index === 0 ? { ...item, aiRole: "contradicts" } : item),
      };
    });
    renderScreen();

    expect(await screen.findByText("反驳的因素")).toBeVisible();
    expect(screen.getByText("AI 关系：反驳")).toBeVisible();
    expect(screen.queryByText("支持的因素")).not.toBeInTheDocument();
  });

  it("falls back safely when Object.hasOwn is unavailable and the AI relationship is unknown", async () => {
    const originalGetQueue = adapter.getEventReviewQueue.bind(adapter);
    vi.spyOn(adapter, "getEventReviewQueue").mockImplementation(async (caseId) => {
      const queue = await originalGetQueue(caseId);
      return {
        ...queue,
        items: queue.items.map((item, index) => index === 0 ? { ...item, aiRole: "toString" } : item),
      };
    });
    const objectConstructor = Object as typeof Object & { hasOwn?: typeof Object.prototype.hasOwnProperty };
    const originalObjectHasOwn = objectConstructor.hasOwn;
    objectConstructor.hasOwn = undefined;
    try {
      expect(hasOwnKey({ supports: "支持" }, "toString")).toBe(false);
    } finally {
      objectConstructor.hasOwn = originalObjectHasOwn;
    }

    renderScreen();
    expect(await screen.findByText("toString的因素")).toBeVisible();
    expect(screen.getByText("AI 关系：toString")).toBeVisible();
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
        items: [],
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
    expect(screen.getByRole("status")).toHaveTextContent("已采纳为“资本开支 / 自由现金流担忧”的正式证据");
    expect(screen.getByText("本轮没有待审证据")).toBeVisible();
  });
});
