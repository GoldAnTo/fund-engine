import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { EventDeskPage } from "../features/events/EventDeskPage";
import { CaseReviewPage } from "../features/case/CasePages";
import { CaseEvidencePage } from "../features/case/CasePages";
import { AppShell } from "../app/AppShell";
import { ResearchOsRoutes } from "../app/routes";

describe("Research OS event entry", () => {
  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => { resetResearchClient(); vi.unstubAllGlobals(); });

  it("puts the next human action ahead of automatic event work", async () => {
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes><Route path="/events" element={<EventDeskPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "今天，先推进哪一个判断？" })).toBeVisible();
    expect(await screen.findByText("当前优先")).toBeVisible();
    expect(await screen.findByText("研究网络")).toBeVisible();
    expect(screen.getAllByRole("link", { name: /Alphabet 财报超预期后股价下跌/ }).length).toBeGreaterThan(0);
  });

  it("keeps the research question and three factors editable before a Case is created", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/new"]}>
        <Routes><Route path="/events/new" element={<EventCreatePage />} /></Routes>
      </MemoryRouter>,
    );

    await user.type(
      screen.getByLabelText("事件原始输入"),
      "Alphabet 公布财报后上调资本开支指引，盘后股价下跌。",
    );
    await user.click(screen.getByRole("button", { name: "识别事件与研究问题" }));

    expect(await screen.findByLabelText("研究问题")).toBeVisible();
    expect(screen.getAllByLabelText(/关键因素/)).toHaveLength(3);
    expect(screen.getByRole("button", { name: "创建事件 Case" })).toBeEnabled();
  });

  it("requires a reason before a reviewer can confirm a candidate and then advances the queue", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
        <Routes><Route path="/events/:caseId/review" element={<CaseReviewPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: /条待审核关系/ });
    expect(screen.getByRole("button", { name: "确认采纳" })).toBeDisabled();
    await user.type(screen.getByLabelText("审核理由"), "原文来自冻结的一手公司披露，支持当前因素。");
    await user.click(screen.getByRole("button", { name: "确认采纳" }));

    expect(await screen.findByText("当前没有待审核候选。")) .toBeVisible();
  });

  it("lets a reviewer request more evidence without accepting the candidate", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/review"]}>
        <Routes><Route path="/events/:caseId/review" element={<CaseReviewPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: /条待审核关系/ });
    expect(screen.getByRole("button", { name: "要求补充证据" })).toBeDisabled();
    await user.type(screen.getByLabelText("审核理由"), "还需要同一期间的反证和实际经营数据。");
    await user.click(screen.getByRole("button", { name: "要求补充证据" }));

    expect(await screen.findByText("当前没有待审核候选。")).toBeVisible();
  });

  it("keeps a frozen active-run scope visible above every workbench route", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [{ run_id: "run-1", case_id: "event-tsm", case_title: "台积电上调 CoWoS 指引后下跌", status: "running", stage: "retrieve", updated_at: "2026-08-09T00:00:00Z", processed_count: 3, next_action: "查看本次运行", scope: { trigger: "manual", monitor_version_id: "monitor-1", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], budget: 12 } }],
      next_cursor: null, has_more: false,
    }), { status: 200, headers: { "content-type": "application/json" } })));
    render(
      <MemoryRouter initialEntries={["/events"]}>
        <Routes><Route element={<AppShell />}><Route path="/events" element={<p>工作台内容</p>} /></Route></Routes>
      </MemoryRouter>,
    );

    const strip = await screen.findByRole("region", { name: "系统正在运行" });
    expect(strip).toHaveTextContent("台积电上调 CoWoS 指引后下跌");
    expect(strip).toHaveTextContent("company_disclosure");
    expect(strip).toHaveTextContent("已处理 3");
  });

  it("keeps Case evidence separate from the current conclusion and exposes its frozen locator", async () => {
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/evidence"]}>
        <Routes><Route path="/events/:caseId/evidence" element={<CaseEvidencePage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "每一条关系都保留原文、时点与审核边界" })).toBeVisible();
    expect(screen.getAllByText("命题与证据").length).toBeGreaterThan(0);
    expect(screen.getByText("精确定位")).toBeVisible();
    expect(screen.getAllByRole("link", { name: "原文资料" }).some((link) => link.getAttribute("href") === "/events/event-tsm/documents")).toBe(true);
  });

  it("opens a Case-scoped frozen source snapshot with its exact locator", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/events/event-tsm/documents"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "原文资料" })).toBeVisible();
    await user.click(await screen.findByRole("button", { name: /台积电 2026 年第二季度法说会摘要/ }));
    expect(await screen.findByText("内容快照（当前 V1 未提供原件文件）")).toBeVisible();
    expect(screen.getByText(/资本开支指引/)).toBeVisible();
    expect(screen.getByText('{"page":12,"section":"资本开支"}')).toBeVisible();
  });

  it("renders reviewed Case relations separately from AI candidates in the global network", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(new Response(JSON.stringify(
      String(input).endsWith("/event-research/network")
        ? { reviewed_relations: [{ id: "relation-1", source_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, target_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, relation_type: "shared_driver", reason: "共同验证资本开支预期差", created_by: "human:researcher", review_state: "reviewed", created_at: "2026-08-09T00:00:00Z" }], candidate_relations: [{ id: "relation-2", source_case: { case_id: "event-alphabet", title: "Alphabet Case", lifecycle_status: "published" }, target_case: { case_id: "event-tsm", title: "台积电 Case", lifecycle_status: "researching" }, relation_type: "potential_conflict", reason: "候选解释可能冲突", created_by: "ai:relation-proposal", review_state: "machine_generated", created_at: "2026-08-09T00:00:00Z" }] }
        : { items: [], next_cursor: null, has_more: false },
    ), { status: 200, headers: { "content-type": "application/json" } }))));
    render(
      <MemoryRouter initialEntries={["/network"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "跨 Case 研究网络" })).toBeVisible();
    expect(screen.getByText("共同验证资本开支预期差")).toBeVisible();
    expect(screen.getAllByText("AI 候选，未经人工复核").length).toBeGreaterThan(0);
    expect(screen.getByText(/不继承证据、结论或审核状态/)).toBeVisible();
  });

  it("shows every active run and its frozen scope from the global monitoring page", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(new Response(JSON.stringify(
      String(input).endsWith("/research-runs/active")
        ? { items: [{ run_id: "run-1", case_id: "event-tsm", case_title: "台积电 Case", status: "running", stage: "retrieve", updated_at: "2026-08-09T00:00:00Z", processed_count: 3, next_action: "查看本次运行", scope: { trigger: "schedule", monitor_version_id: "monitor-v2", factor_ids: ["factor-1"], allowed_source_types: ["company_disclosure"], budget: 12 } }], next_cursor: null, has_more: false }
        : { items: [], next_cursor: null, has_more: false },
    ), { status: 200, headers: { "content-type": "application/json" } }))));
    render(
      <MemoryRouter initialEntries={["/monitoring"]}>
        <ResearchOsRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "全局运行与监控" })).toBeVisible();
    expect(screen.getByText("台积电 Case")).toBeVisible();
    expect(screen.getByText("company_disclosure")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看本次运行" })).toHaveAttribute("href", "/events/event-tsm/monitor");
  });
});
