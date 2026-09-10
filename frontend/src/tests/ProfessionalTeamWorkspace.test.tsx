import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { parseTeam } from "@/gateway/team";
import { ProfessionalTeamWorkspace, type ProfessionalTeamWorkspaceProps } from "@/workbench/ProfessionalTeamWorkspace";
import { teamAt, teamConversationId as conversationId, teamId, teamRunId as runSpecId, teamWire } from "./teamFixtures";

function setup(overrides: Partial<ProfessionalTeamWorkspaceProps> = {}, body = teamWire()) {
  const team = parseTeam(body, { conversationId, runSpecId });
  const mutation = { busy: false, notice: "", unknown: false, submit: vi.fn().mockResolvedValue(true), retry: vi.fn().mockResolvedValue(true) };
  const onOpenCitation = vi.fn(), onAskRole = vi.fn(), onShowRoleEvidence = vi.fn(), onSelectRevision = vi.fn(), onRefresh = vi.fn();
  const props: ProfessionalTeamWorkspaceProps = {
    state: { kind: "available", data: team, refreshing: false },
    selectedRevision: team.revision,
    onSelectRevision,
    mutation,
    onOpenCitation,
    onAskRole,
    onShowRoleEvidence,
    onRefresh,
    nativeStatus: "running",
    goal: { text: "核对半导体设备国产化机会。", createdAt: teamAt },
    scope: <div data-testid="scope-slot">固定范围</div>,
    composer: <form className="gateway-composer" aria-label="composer"><textarea aria-label="composer-input" /></form>,
    evidence: <aside tabIndex={-1}>证据侧栏<div className="research-evidence-original"><h3>引用原文标题</h3></div></aside>,
    nativeContent: <section>原生 Gateway 内容</section>,
    messageHistory: <section>已确认消息历史</section>,
    ...overrides,
  };
  return { ...render(<ProfessionalTeamWorkspace {...props} />), props, team, mutation, onOpenCitation, onAskRole, onShowRoleEvidence, onSelectRevision };
}

describe("ProfessionalTeamWorkspace", () => {
  it("retains cached work and selected tabs while blocking writes until a fresh team read", async () => {
    const user = userEvent.setup(), view = setup();
    await user.click(screen.getByRole("tab", { name: "审查状态" }));
    await user.type(screen.getByRole("textbox", { name: "人工复核评语" }), "保留复核草稿");
    const summary = screen.getByText("industry 已保存摘要");
    view.rerender(<ProfessionalTeamWorkspace {...view.props} state={{ kind: "available", data: view.team, refreshing: false, stale: true }} />);
    expect(screen.getByText(/团队状态待更新，当前显示上次成功读取的内容/)).toBeVisible();
    expect(screen.getByText("industry 已保存摘要")).toBe(summary);
    expect(screen.getByRole("tab", { name: "审查状态" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("textbox", { name: "人工复核评语" })).toHaveValue("保留复核草稿");
    expect(screen.getByRole("button", { name: "保存人工复核" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "暂停" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.queryByText(/正在查看历史版本/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "协作时间线" }));
    view.rerender(<ProfessionalTeamWorkspace {...view.props} state={{ kind: "available", data: view.team, refreshing: true, stale: true }} />);
    expect(screen.getByRole("tab", { name: "协作时间线" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("button", { name: "重新读取团队" }));
    expect(view.props.onRefresh).toHaveBeenCalledOnce();
    expect(view.mutation.submit).not.toHaveBeenCalled();
  });

  it("describes unread role states as unavailable instead of not started after a first read failure", () => {
    setup({ state: { kind: "error", schema: false } });
    expect(screen.queryByText("未开始")).not.toBeInTheDocument();
    expect(screen.queryByText("本角色尚未收到已保存指令。")).not.toBeInTheDocument();
    expect(screen.getAllByText("状态暂不可用")).toHaveLength(4);
    expect(screen.getAllByText("待恢复读取")).toHaveLength(4);
    expect(screen.getByRole("button", { name: "重新读取团队" })).toBeEnabled();
  });

  it("blocks an unknown mutation retry while the displayed team is stale", () => {
    const team = parseTeam(teamWire(), { conversationId, runSpecId });
    setup({ state: { kind: "available", data: team, refreshing: false, stale: true },
      mutation: { busy: false, notice: "结果未知", unknown: true, submit: vi.fn(), retry: vi.fn() } });
    expect(screen.getByRole("button", { name: "重试原请求" })).toBeDisabled();
  });

  it("supports keyboard navigation within each tab list", async () => {
    setup();
    const user = userEvent.setup();
    screen.getByRole("tab", { name: "研究对话" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "协作时间线" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "协作时间线" })).toHaveAttribute("aria-selected", "true");
    screen.getByRole("tab", { name: "证据与来源" }).focus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "审查状态" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "审查状态" })).toHaveAttribute("aria-selected", "true");
  });

  it("keeps composer input mounted when read state changes to available", async () => {
    const user = userEvent.setup();
    const team = parseTeam(teamWire(), { conversationId, runSpecId });
    const composer = () => <form className="gateway-composer" aria-label="composer"><textarea aria-label="composer-input" /></form>;
    const baseProps: ProfessionalTeamWorkspaceProps = {
      state: { kind: "loading" },
      composer: composer(),
      evidence: <section><div className="research-evidence-original"><h3>引用原文标题</h3></div></section>,
      nativeContent: <section>原生 Gateway 内容</section>,
      messageHistory: <section>已确认消息历史</section>,
    };
    const view = render(<ProfessionalTeamWorkspace {...baseProps} />);

    await user.type(screen.getByRole("textbox", { name: "composer-input" }), "专业团队指令最多 20,000 字");
    view.rerender(<ProfessionalTeamWorkspace {...baseProps} state={{ kind: "available", data: team, refreshing: false }} composer={composer()} />);

    expect(screen.getByRole("textbox", { name: "composer-input" })).toHaveValue("专业团队指令最多 20,000 字");
  });

  it("shows all four compact professional role rows by default", () => {
    setup();

    expect(screen.getByTestId("scope-slot")).toBeVisible();
    expect(screen.getByLabelText("产业分析师工作区")).toBeVisible();
    expect(screen.getByLabelText("财务分析师工作区")).toBeVisible();
    expect(screen.getByLabelText("策略分析师工作区")).toBeVisible();
    expect(screen.getByLabelText("AI 质控审核员工作区")).toBeVisible();
    expect(screen.getByRole("button", { name: "产业分析草案" })).toBeVisible();
    expect(screen.getByRole("button", { name: "财务分析草案" })).toBeVisible();
    expect(screen.getByRole("button", { name: "策略分析草案" })).toBeVisible();
    expect(screen.getByRole("button", { name: "质控草案" })).toBeVisible();
  });

  it("filters roles and expands a row into real task details", async () => {
    const user = userEvent.setup();
    const view = setup();

    await user.click(screen.getByRole("button", { name: "财务" }));
    expect(view.container.querySelectorAll(".team-workspace__role-row")).toHaveLength(1);
    const finance = screen.getByLabelText("财务分析师工作区");
    await user.click(within(finance).getByRole("button", { name: "展开财务分析师工作记录" }));

    expect(within(finance).getByText("任务 ID")).toBeVisible();
    expect(within(finance).getByText(teamId(11))).toBeVisible();
    expect(within(finance).getByText("模型调用与用量（1 次尝试）")).toBeVisible();
  });

  it("opens citations with actual evidence and output IDs while switching to the evidence pane", async () => {
    const user = userEvent.setup();
    const view = setup();
    const industry = screen.getByLabelText("产业分析师工作区");

    await user.click(screen.getByRole("tab", { name: "待解决问题" }));
    await user.click(within(industry).getByRole("button", { name: "展开产业分析师工作记录" }));
    const citationButton = within(industry).getByRole("button", { name: /引用 1/ });
    await user.click(citationButton);

    expect(screen.getByRole("tab", { name: "证据与来源" })).toHaveAttribute("aria-selected", "true");
    expect(view.onOpenCitation).toHaveBeenCalledWith(view.team.tasks[0]!.output!.content.findings[0]!.citations[0], view.team.tasks[0]!.output!, citationButton);
    await waitFor(() => expect(screen.getByText("引用原文标题")).toHaveFocus());
  });

  it("targets a role through onAskRole and returns mobile state to the work pane", async () => {
    const user = userEvent.setup();
    const view = setup();

    const finance = screen.getByLabelText("财务分析师工作区");
    await user.click(within(finance).getByRole("button", { name: "向此角色补充要求" }));

    expect(view.onAskRole).toHaveBeenCalledWith("finance");
    expect(view.container.querySelector(".team-workspace")).toHaveAttribute("data-mobile-pane", "work");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "composer-input" })).toHaveFocus());
  });

  it("submits review decisions with the four current output IDs and shows immutable history", async () => {
    const user = userEvent.setup();
    const body = teamWire();
    body.revision = 2;
    body.reviews = [{ id: teamId(60), revision: 1, decision: "changes_requested", comment: "第一版需要补充来源。", reviewed_by: "reviewer-a", output_ids: [20, 21, 22, 23].map(teamId), created_at: "2026-09-07T04:10:00Z" }];
    const view = setup({}, body);

    await user.click(screen.getByRole("tab", { name: "审查状态" }));
    expect(screen.getByText("不可变历史复核")).toBeVisible();
    expect(screen.getByText("第一版需要补充来源。")).toBeVisible();
    await user.selectOptions(screen.getByRole("combobox", { name: "人工复核决定" }), "changes_requested");
    await user.type(screen.getByRole("textbox", { name: "人工复核评语" }), "已核对期间，仍需补充独立来源。");
    await user.click(screen.getByRole("button", { name: "保存人工复核" }));

    expect(view.mutation.submit).toHaveBeenCalledWith({ kind: "review", body: {
      expected_revision: 2,
      output_ids: [20, 21, 22, 23].map(teamId),
      decision: "changes_requested",
      comment: "已核对期间，仍需补充独立来源。",
    } });
  });

  it("separates later reviews from the selected historical version and displays AI checks", async () => {
    const body = teamWire();
    body.revision = 2;
    body.reviews = [{ id: teamId(60), revision: 2, decision: "changes_requested", comment: "第二版的复核", reviewed_by: "reviewer-a", output_ids: [20, 21, 22, 23].map(teamId), created_at: "2026-09-07T04:10:00Z" }];
    setup({ selectedRevision: 1 }, body);
    expect(screen.getByText(/查看版本 1 · 最新版本 2/)).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "审查状态" }));
    expect(screen.getByRole("heading", { name: "AI 质控意见" })).toBeVisible();
    expect(screen.getByText("期间：提示")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "不可变历史复核" })).not.toBeInTheDocument();
    expect(screen.getByText("第二版的复核")).not.toBeVisible();
    await userEvent.click(screen.getByText("后续版本复核（不属于当前版本）"));
    expect(screen.getByText("第二版的复核")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存人工复核" })).toBeDisabled();
  });

  it("keeps unknown mutation retry available without resubmitting a new intent", async () => {
    const user = userEvent.setup();
    const retry = vi.fn().mockResolvedValue(true);
    setup({ mutation: { busy: false, notice: "无法确认本次提交是否已送达。", unknown: true, submit: vi.fn(), retry } });

    await user.click(screen.getByRole("button", { name: "重试原请求" }));

    expect(retry).toHaveBeenCalledOnce();
  });

  it("navigates exact dependency task history from the expanded row into outputs", async () => {
    const user = userEvent.setup();
    setup();

    const strategy = screen.getByLabelText("策略分析师工作区");
    await user.click(within(strategy).getByRole("button", { name: "展开策略分析师工作记录" }));
    await user.click(within(strategy).getByRole("button", { name: "查看前序产出：财务分析师 · 版本 1" }));

    expect(screen.getByRole("tab", { name: "研究产出" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("已定位任务：财务分析师 · 版本 1")).toBeVisible();
    expect(screen.getAllByText(teamId(11)).length).toBeGreaterThan(0);
  });

  it("switches to evidence and mobile evidence pane when an external evidence request key changes", async () => {
    const view = setup({ evidenceRequestKey: 1 });
    await userEvent.setup().click(screen.getByRole("tab", { name: "待解决问题" }));

    view.rerender(<ProfessionalTeamWorkspace {...view.props} evidenceRequestKey={2} />);

    expect(screen.getByRole("tab", { name: "证据与来源" })).toHaveAttribute("aria-selected", "true");
    expect(view.container.querySelector(".team-workspace")).toHaveAttribute("data-mobile-pane", "evidence");
    await waitFor(() => expect(screen.getByText("引用原文标题")).toHaveFocus());
  });

  it("does not treat initial evidenceRequestKey zero as an evidence request", () => {
    const view = setup({ evidenceRequestKey: 0 });

    expect(view.container.querySelector(".team-workspace")).toHaveAttribute("data-mobile-pane", "work");
    expect(screen.getByRole("tab", { name: "证据与来源" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches to the work pane and composer when an external composer request key changes", async () => {
    const view = setup({ composerRequestKey: 1 });
    view.rerender(<ProfessionalTeamWorkspace {...view.props} evidenceRequestKey={1} composerRequestKey={1} />);
    await waitFor(() => expect(view.container.querySelector(".team-workspace")).toHaveAttribute("data-mobile-pane", "evidence"));

    view.rerender(<ProfessionalTeamWorkspace {...view.props} evidenceRequestKey={1} composerRequestKey={2} />);

    expect(view.container.querySelector(".team-workspace")).toHaveAttribute("data-mobile-pane", "work");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "composer-input" })).toHaveFocus());
  });

  it("routes compact evidence chips through the actual role task", async () => {
    const user = userEvent.setup();
    const view = setup();
    const finance = screen.getByLabelText("财务分析师工作区");

    await user.click(within(finance).getByRole("button", { name: "关联 1 份证据 ↗" }));

    expect(view.onShowRoleEvidence).toHaveBeenCalledWith(view.team.tasks[1]);
    expect(screen.getByRole("tab", { name: "证据与来源" })).toHaveAttribute("aria-selected", "true");
  });

  it("allows retry for blocked current tasks", async () => {
    const user = userEvent.setup();
    const body = teamWire();
    Object.assign(body.tasks[0]!, { status: "blocked", reason_code: "evidence_gap" });
    const view = setup({}, body);

    const industry = screen.getByLabelText("产业分析师工作区");
    await user.click(within(industry).getByRole("button", { name: "展开产业分析师工作记录" }));
    await user.click(within(industry).getByRole("button", { name: "重试产业分析师任务" }));

    expect(view.mutation.submit).toHaveBeenCalledWith({ kind: "command", body: { kind: "retry", task_id: teamId(10), expected_revision: 1 } });
  });

  it("does not leak a selected later task into an earlier visible revision", async () => {
    const user = userEvent.setup();
    const body = teamWire();
    body.revision = 2;
    body.tasks.push({ ...body.tasks[1]!, id: teamId(50), revision: 2, instruction: "追加财务核对",
      output: { ...body.tasks[1]!.output, id: teamId(51), content: { ...body.tasks[1]!.output.content, summary: "finance 第二版" } } });
    const view = setup({ selectedRevision: 2 }, body);

    await user.click(screen.getByRole("button", { name: "财务分析草案" }));
    expect(screen.getByText("已定位任务：财务分析师 · 版本 2")).toBeVisible();

    view.rerender(<ProfessionalTeamWorkspace {...view.props} selectedRevision={1} />);

    expect(screen.queryByText("已定位任务：财务分析师 · 版本 2")).not.toBeInTheDocument();
    expect(screen.queryByText("finance 第二版")).not.toBeInTheDocument();
    expect(screen.getByText("finance 已保存摘要")).toBeVisible();
  });
});
