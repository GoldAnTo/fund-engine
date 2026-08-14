import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { ConflictError } from "../domain/researchPreparation";
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
    expect(within(screen.getByLabelText("当前人工任务")).getAllByText("等待上一步确认")).toHaveLength(4);
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

  it("automatically reloads preparation and activity after a command conflict", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const getPreparation = vi.spyOn(adapter, "getResearchPreparation");
    vi.spyOn(adapter, "confirmResearchPreparationClaims").mockRejectedValue(new ConflictError());
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    const task = await screen.findByLabelText("当前人工任务");
    await user.selectOptions(within(task).getByLabelText("候选陈述 1 的决定"), "confirmed");
    await user.type(within(task).getByLabelText("候选陈述 1 的核对说明"), "已核对冻结原文。");
    await user.click(within(task).getByRole("button", { name: "确认候选陈述" }));

    expect(await screen.findByText("此准备版本已更新，已显示最新内容")).toBeVisible();
    expect(getPreparation.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("button", { name: "重新加载" })).not.toBeInTheDocument();
  });

  it("keeps all existing drafts readable when preparation can be retried", async () => {
    renderPreparation("recoverable_failure");

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByDisplayValue(/订单增长可以转化为收入/)).toBeVisible();
    expect(within(task).getByDisplayValue(/事件是否改变关键因素/)).toBeVisible();
    expect(within(task).getByDisplayValue(/公司公告/)).toBeVisible();
    expect(within(task).getByRole("button", { name: "重新准备研究草案" })).toBeEnabled();
  });

  it("shows locked generated previews as disabled downstream forms", async () => {
    renderPreparation();

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByLabelText("研究协议草案预览")).toBeDisabled();
    expect(within(task).getByLabelText("补证计划草案预览")).toBeDisabled();
    expect(within(task).getAllByText("等待上一步确认")).toHaveLength(4);
  });

  it("persists the regeneration notice after successfully submitting a corrected claim", async () => {
    const user = userEvent.setup();
    renderPreparation();

    const task = await screen.findByLabelText("当前人工任务");
    await user.selectOptions(within(task).getByLabelText("候选陈述 1 的决定"), "modified");
    await user.type(within(task).getByLabelText("候选陈述 1 的核对说明"), "原文表述需要修正。");
    await user.type(within(task).getByLabelText("修正后陈述"), "订单增长可能转化为收入。");
    await user.click(within(task).getByRole("button", { name: "确认候选陈述" }));

    expect(await screen.findByText("协议草案和补证计划已因原文核验变更失效，系统将仅重新生成受影响步骤"))
      .toBeVisible();
  });

  it("loads all paginated activity records instead of stopping at the first 50", async () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listResearchPreparationEvents").mockImplementation(async (_caseId, cursor = {}) => {
      const start = cursor.afterSeq ?? 0;
      const items = Array.from({ length: start === 0 ? 50 : 5 }, (_, index) => {
        const seq = start + index + 1;
        return { seq, type: "draft_ready", step: "draft_protocol" as const, message: `活动 ${seq}`, detail: null, createdAt: "2026-08-15T09:00:00Z" };
      });
      return { items, nextAfterSeq: start === 0 ? 50 : null };
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/活动 55/)).toBeVisible();
  });

  it("polls from the latest event after an initially paginated activity load", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "preparing" });
    const calls: Array<number | undefined> = [];
    vi.spyOn(adapter, "listResearchPreparationEvents").mockImplementation(async (_caseId, cursor = {}) => {
      calls.push(cursor.afterSeq);
      const start = cursor.afterSeq ?? 0;
      const items = Array.from({ length: start === 0 ? 50 : start === 50 ? 5 : 0 }, (_, index) => {
        const seq = start + index + 1;
        return { seq, type: "draft_ready", step: "draft_protocol" as const, message: `活动 ${seq}`, detail: null, createdAt: "2026-08-15T09:00:00Z" };
      });
      return { items, nextAfterSeq: start === 0 ? 50 : start === 50 ? null : null };
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByText(/活动 55/);
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 2100));
    });

    expect(calls).toContain(55);
  });

  it("polls from the final event when the initial activity page has fewer than 50 records", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "preparing" });
    const calls: Array<number | undefined> = [];
    vi.spyOn(adapter, "listResearchPreparationEvents").mockImplementation(async (_caseId, cursor = {}) => {
      calls.push(cursor.afterSeq);
      const start = cursor.afterSeq ?? 0;
      const items = start === 0
        ? [1, 2, 3].map((seq) => ({ seq, type: "draft_ready", step: "draft_protocol" as const, message: `短页活动 ${seq}`, detail: null, createdAt: "2026-08-15T09:00:00Z" }))
        : [];
      return { items, nextAfterSeq: null };
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByText(/短页活动 3/);
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 2100));
    });

    expect(calls).toContain(3);
  });

  it("cleans up the two-second preparation poll when the page unmounts", async () => {
    const clearIntervalSpy = vi.spyOn(window, "clearInterval");
    const rendered = renderPreparation("preparing");

    await screen.findByRole("heading", { name: "系统正在准备" });
    rendered.unmount();

    expect(clearIntervalSpy).toHaveBeenCalled();
    clearIntervalSpy.mockRestore();
  });

  it("keeps the authorization idempotency key stable when a transient response failure is retried", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter({ preparationScenario: "review_plan" });
    const authorize = vi.spyOn(adapter, "authorizeResearchPreparation").mockRejectedValueOnce(new Error("响应暂时不可用"));
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    const button = await screen.findByRole("button", { name: "授权补证计划并启动正式研究" });
    await user.click(button);
    expect(await screen.findByText("响应暂时不可用")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "授权补证计划并启动正式研究" }));

    await screen.findByText("正式研究已启动");
    expect(authorize).toHaveBeenCalledTimes(2);
    expect(authorize.mock.calls[0][0].idempotencyKey).toBe(authorize.mock.calls[1][0].idempotencyKey);
  });

  it("keeps the preparation summary usable when loading its activity log fails", async () => {
    const adapter = new MockResearchAdapter();
    vi.spyOn(adapter, "listResearchPreparationEvents").mockRejectedValue(new Error("活动记录暂不可用"));
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "核对候选陈述" })).toBeVisible();
    expect(screen.getByText("无法读取活动记录，不影响准备状态。")).toBeVisible();
    expect(screen.getByRole("button", { name: "重新读取活动记录" })).toBeEnabled();
  });

  it("does not submit a protocol confirmation without a current protocol artifact", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "review_protocol" });
    const original = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await original(caseId);
      return { ...preparation, artifacts: { ...preparation.artifacts, protocol: null } };
    });
    const confirm = vi.spyOn(adapter, "confirmResearchPreparationProtocol");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("协议草案缺失，无法提交确认。请重新读取准备状态。"))
      .toBeVisible();
    expect(screen.getByRole("button", { name: "确认研究协议" })).toBeDisabled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("does not authorize a plan without a positive current plan sequence", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "review_plan" });
    const original = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await original(caseId);
      return {
        ...preparation,
        artifacts: {
          ...preparation.artifacts,
          evidencePlan: { ...preparation.artifacts.evidencePlan!, sequence: 0 },
        },
      };
    });
    const authorize = vi.spyOn(adapter, "authorizeResearchPreparation");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("补证计划草案缺失或版本无效，无法授权。请重新读取准备状态。"))
      .toBeVisible();
    expect(screen.getByRole("button", { name: "授权补证计划并启动正式研究" })).toBeDisabled();
    expect(authorize).not.toHaveBeenCalled();
  });

  it("ignores a stale case response after navigating to another preparation", async () => {
    const adapter = new MockResearchAdapter();
    const original = adapter.getResearchPreparation.bind(adapter);
    let resolveA: ((value: Awaited<ReturnType<typeof original>>) => void) | undefined;
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation((caseId) => {
      if (caseId === "case-a") return new Promise((resolve) => { resolveA = resolve; });
      return original("event-preparation").then((preparation) => ({ ...preparation, status: "awaiting_plan_authorization" }));
    });
    setResearchClient(adapter);
    function Switcher() {
      const navigate = useNavigate();
      return <><button type="button" onClick={() => navigate("/events/case-b/preparation")}>切换 Case</button><Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes></>;
    }
    render(<MemoryRouter initialEntries={["/events/case-a/preparation"]}><Switcher /></MemoryRouter>);

    await userEvent.setup().click(screen.getByRole("button", { name: "切换 Case" }));
    expect(await screen.findByRole("heading", { name: "授权补证计划" })).toBeVisible();
    await act(async () => { resolveA?.(await original("event-preparation")); });
    expect(screen.getByRole("heading", { name: "授权补证计划" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "核对候选陈述" })).not.toBeInTheDocument();
  });
});
