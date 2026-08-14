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

  it("submits the backend candidate UUID from a real claim artifact", async () => {
    const user = userEvent.setup();
    const candidateId = "8a23ef12-9b37-4f54-8f2d-b938605a1d8d";
    const adapter = new MockResearchAdapter();
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await originalPreparation(caseId);
      return {
        ...preparation,
        artifacts: {
          ...preparation.artifacts,
          candidateClaims: {
            ...preparation.artifacts.candidateClaims!,
            payload: {
              candidates: [{
                candidate_id: candidateId,
                normalized_text: "公司披露订单同比增长 20%",
                quote: "订单同比增长20%",
              }],
            },
          },
        },
      };
    });
    const confirm = vi.spyOn(adapter, "confirmResearchPreparationClaims");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByText("公司披露订单同比增长 20%")).toBeVisible();
    await user.selectOptions(within(task).getByLabelText("候选陈述 1 的决定"), "confirmed");
    await user.type(within(task).getByLabelText("候选陈述 1 的核对说明"), "已与冻结原文核对一致。");
    await user.click(within(task).getByRole("button", { name: "确认候选陈述" }));

    expect(await within(screen.getByLabelText("当前人工任务")).findByRole("heading", { name: "确认研究协议" })).toBeVisible();
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      decisions: [{ candidateId, outcome: "confirmed", reason: "已与冻结原文核对一致。", normalizedText: undefined }],
    }));
    expect(confirm).not.toHaveBeenCalledWith(expect.objectContaining({
      decisions: [expect.objectContaining({ candidateId: "candidate-1" })],
    }));
  });

  it("does not adopt quote-only candidates or submit a non-normalized claim", async () => {
    const candidateId = "58e12e87-8c38-4b5a-9c36-7e5069429289";
    const adapter = new MockResearchAdapter();
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await originalPreparation(caseId);
      return {
        ...preparation,
        artifacts: {
          ...preparation.artifacts,
          candidateClaims: {
            ...preparation.artifacts.candidateClaims!,
            payload: { candidates: [{ candidate_id: candidateId, quote: "原文摘录不能替代规范化陈述" }] },
          },
        },
      };
    });
    const confirm = vi.spyOn(adapter, "confirmResearchPreparationClaims");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByText("候选陈述受来源展示许可限制，无法在此页显示。请在已授权材料中核对后重试。")).toBeVisible();
    expect(within(task).queryByText("原文摘录不能替代规范化陈述")).not.toBeInTheDocument();
    expect(within(task).getByRole("button", { name: "确认候选陈述" })).toBeDisabled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("does not offer unsupported protocol notes or submit unexpected protocol edits", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter({ preparationScenario: "review_protocol" });
    const confirm = vi.spyOn(adapter, "confirmResearchPreparationProtocol");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).queryByLabelText("研究员备注（可选）")).not.toBeInTheDocument();
    await user.click(within(task).getByRole("button", { name: "确认研究协议" }));

    expect(await within(screen.getByLabelText("当前人工任务")).findByRole("heading", { name: "授权补证计划" })).toBeVisible();
    expect(confirm).toHaveBeenCalledWith({
      caseId: "event-preparation",
      revision: 1,
      actor: "human:researcher",
      draftSequence: 2,
    });
  });

  it("does not show a human confirmation action while the system is preparing drafts", async () => {
    renderPreparation("preparing");

    const task = await screen.findByLabelText("当前人工任务");
    expect(within(task).getByRole("heading", { name: "系统正在准备" })).toBeVisible();
    expect(within(task).queryByRole("button", { name: /确认|授权/ })).not.toBeInTheDocument();
    expect(screen.getByText("正式研究尚未启动")).toBeVisible();
  });

  it("marks only the running preparation step as current while downstream work remains queued", async () => {
    renderPreparation("preparing");

    const timeline = await screen.findByLabelText("系统准备活动");
    const steps = timeline.querySelector<HTMLElement>(".ros-preparation-timeline");
    expect(steps).not.toBeNull();
    expect(within(steps!).getByText("解析冻结原文", { selector: "strong" }).closest("li")).toHaveClass("is-current");
    expect(within(steps!).getByText("生成研究协议草案", { selector: "strong" }).closest("li")).toHaveClass("is-waiting");
    expect(within(steps!).getByText("生成补证计划草案", { selector: "strong" }).closest("li")).toHaveClass("is-waiting");
  });

  it("shows the first queued preparation step as next work rather than active work", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "preparing" });
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await originalPreparation(caseId);
      return {
        ...preparation,
        system: { ...preparation.system, candidateClaims: { state: "queued", artifactSequence: null } },
        progress: { completedSteps: 0, totalSteps: 3, currentStep: null, failedStep: null },
      };
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByText("研究准备");
    expect(document.querySelector(".ros-preparation-progress")).toHaveTextContent("下一步：解析冻结原文（已排队）");
    const timeline = screen.getByLabelText("系统准备活动");
    expect(within(timeline.querySelector<HTMLElement>(".ros-preparation-timeline")!).getByText("解析冻结原文", { selector: "strong" }).closest("li")).toHaveClass("is-waiting");
  });

  it("shows the case, frozen material, progress and only allowlisted activity detail", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "preparing" });
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => ({
      ...await originalPreparation(caseId),
      caseTitle: "AI 服务器需求研究",
      initialMaterial: {
        documentVersionId: "91c8e13c-f649-4f6b-9330-0c9ae7cb6641",
        title: "公司公告（冻结原文）",
        parseState: "success",
      },
      progress: { completedSteps: 0, totalSteps: 3, currentStep: "parse_claims", failedStep: null },
    }));
    vi.spyOn(adapter, "listResearchPreparationEvents").mockResolvedValue({
      items: [{
        seq: 1,
        type: "preparation_step_started",
        step: "parse_claims",
        message: "provider token should never appear",
        detail: { candidate_count: 3, artifact_sequence: 2, attempt: 2, duration_ms: 1200, input_scope: "frozen_original", source_url: "https://secret.example" },
        createdAt: "2026-08-15T09:00:00Z",
      }],
      nextAfterSeq: null,
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("AI 服务器需求研究")).toBeVisible();
    expect(screen.getByText("公司公告（冻结原文）")).toBeVisible();
    expect(screen.getByText(/材料版本：91c8e13c/)).toBeVisible();
    expect(document.querySelector(".ros-preparation-progress")).toHaveTextContent("准备进度：0 / 3");
    const activity = screen.getByLabelText("准备活动记录");
    expect(within(activity).getByText("输入范围：冻结原文")).toBeVisible();
    expect(within(activity).getByText("候选陈述：3")).toBeVisible();
    expect(within(activity).getByText("草案版本：2")).toBeVisible();
    expect(within(activity).getByText("第 2 次尝试")).toBeVisible();
    expect(within(activity).getByText("耗时：1.2 秒")).toBeVisible();
    expect(within(activity).queryByText(/provider token|secret\.example/)).not.toBeInTheDocument();
  });

  it("identifies the failed preparation step and its safe next retry time", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "recoverable_failure" });
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => ({
      ...await originalPreparation(caseId),
      progress: { completedSteps: 0, totalSteps: 3, currentStep: null, failedStep: "parse_claims" },
    }));
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("失败步骤：解析冻结原文")).toBeVisible();
    expect(screen.getByText(/下次重试：/)).toBeVisible();
  });

  it("keeps a newly polled claim-review status visible when polling activity records fails", async () => {
    const adapter = new MockResearchAdapter();
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    const originalEvents = adapter.listResearchPreparationEvents.bind(adapter);
    let preparationCalls = 0;
    let eventCalls = 0;
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await originalPreparation(caseId);
      return preparationCalls++ === 0 ? { ...preparation, status: "preparing" } : preparation;
    });
    vi.spyOn(adapter, "listResearchPreparationEvents").mockImplementation((caseId, cursor) => {
      if (eventCalls++ === 0) return originalEvents(caseId, cursor);
      return Promise.reject(new Error("活动记录暂不可用"));
    });
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "系统正在准备" });
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 2100)); });

    expect(await screen.findByRole("heading", { name: "核对候选陈述" })).toBeVisible();
    expect(screen.getByText("无法读取活动记录，不影响准备状态。")).toBeVisible();
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

    expect((await screen.findByLabelText("准备活动记录")).querySelectorAll("ol > li")).toHaveLength(55);
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

    expect((await screen.findByLabelText("准备活动记录")).querySelectorAll("ol > li")).toHaveLength(55);
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

    expect((await screen.findByLabelText("准备活动记录")).querySelectorAll("ol > li")).toHaveLength(3);
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

  it("does not submit a protocol confirmation with a fractional artifact sequence", async () => {
    const adapter = new MockResearchAdapter({ preparationScenario: "review_protocol" });
    const original = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(async (caseId) => {
      const preparation = await original(caseId);
      return { ...preparation, artifacts: { ...preparation.artifacts, protocol: { ...preparation.artifacts.protocol!, sequence: 1.5 } } };
    });
    const confirm = vi.spyOn(adapter, "confirmResearchPreparationProtocol");
    setResearchClient(adapter);
    render(
      <MemoryRouter initialEntries={["/events/event-preparation/preparation"]}>
        <Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("协议草案缺失，无法提交确认。请重新读取准备状态。")).toBeVisible();
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

  it("does not show a stale regeneration notice after switching Cases while a corrected-claim activity refresh is pending", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const originalEvents = adapter.listResearchPreparationEvents.bind(adapter);
    let resolveRefresh: ((value: Awaited<ReturnType<typeof originalEvents>>) => void) | undefined;
    let correctedClaimsSubmitted = false;
    let refreshPending = false;
    vi.spyOn(adapter, "listResearchPreparationEvents").mockImplementation((caseId, cursor) => {
      if (caseId === "event-preparation" && correctedClaimsSubmitted && !refreshPending) {
        refreshPending = true;
        return new Promise((resolve) => { resolveRefresh = resolve; });
      }
      return originalEvents(caseId === "case-b" ? "event-preparation" : caseId, cursor);
    });
    const originalConfirm = adapter.confirmResearchPreparationClaims.bind(adapter);
    vi.spyOn(adapter, "confirmResearchPreparationClaims").mockImplementation(async (input) => {
      correctedClaimsSubmitted = true;
      return originalConfirm(input);
    });
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(() => originalPreparation("event-preparation"));
    setResearchClient(adapter);
    function Switcher() {
      const navigate = useNavigate();
      return <><button type="button" onClick={() => navigate("/events/case-b/preparation")}>切换 Case</button><Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes></>;
    }
    render(<MemoryRouter initialEntries={["/events/event-preparation/preparation"]}><Switcher /></MemoryRouter>);

    const task = await screen.findByLabelText("当前人工任务", {}, { timeout: 3000 });
    await user.selectOptions(within(task).getByLabelText("候选陈述 1 的决定"), "modified");
    await user.type(within(task).getByLabelText("候选陈述 1 的核对说明"), "需按原文修正。");
    await user.type(within(task).getByLabelText("修正后陈述"), "修正后的候选陈述。");
    await user.click(within(task).getByRole("button", { name: "确认候选陈述" }));
    await user.click(screen.getByRole("button", { name: "切换 Case" }));
    await screen.findByLabelText("当前人工任务");
    await act(async () => { resolveRefresh?.(await originalEvents("event-preparation")); });

    expect(screen.queryByText("协议草案和补证计划已因原文核验变更失效，系统将仅重新生成受影响步骤")).not.toBeInTheDocument();
  });

  it("does not clear the new Case activity error when an old Case retry resolves", async () => {
    const user = userEvent.setup();
    const adapter = new MockResearchAdapter();
    const originalEvents = adapter.listResearchPreparationEvents.bind(adapter);
    let resolveRetry: ((value: Awaited<ReturnType<typeof originalEvents>>) => void) | undefined;
    let sourceCaseEventCalls = 0;
    vi.spyOn(adapter, "listResearchPreparationEvents").mockImplementation((caseId, cursor) => {
      if (caseId === "case-b") return Promise.reject(new Error("活动记录暂不可用"));
      if (++sourceCaseEventCalls === 1) return Promise.reject(new Error("活动记录暂不可用"));
      return new Promise((resolve) => { resolveRetry = resolve; });
    });
    const originalPreparation = adapter.getResearchPreparation.bind(adapter);
    vi.spyOn(adapter, "getResearchPreparation").mockImplementation(() => originalPreparation("event-preparation"));
    setResearchClient(adapter);
    function Switcher() {
      const navigate = useNavigate();
      return <><button type="button" onClick={() => navigate("/events/case-b/preparation")}>切换 Case</button><Routes><Route path="/events/:caseId/preparation" element={<ResearchPreparationPage />} /></Routes></>;
    }
    render(<MemoryRouter initialEntries={["/events/event-preparation/preparation"]}><Switcher /></MemoryRouter>);

    await screen.findByRole("button", { name: "重新读取活动记录" });
    await user.click(screen.getByRole("button", { name: "重新读取活动记录" }));
    await user.click(screen.getByRole("button", { name: "切换 Case" }));
    expect(await screen.findByText("无法读取活动记录，不影响准备状态。")).toBeVisible();
    await act(async () => { resolveRetry?.(await originalEvents("event-preparation")); });

    expect(screen.getByText("无法读取活动记录，不影响准备状态。")).toBeVisible();
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
