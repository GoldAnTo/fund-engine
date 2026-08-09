import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventCreatePage } from "../features/events/EventCreatePage";
import { EventDeskPage } from "../features/events/EventDeskPage";
import { CaseReviewPage } from "../features/case/CasePages";
import { AppShell } from "../app/AppShell";

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
});
