import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProfessionalRoleFrame } from "@/workbench/ProfessionalRoleFrame";
import { parseTeam } from "@/gateway/team";
import { teamWire, teamConversationId as conversationId, teamRunId as runSpecId, teamId } from "./teamFixtures";

function setup(body = teamWire(), revision = body.revision) {
  const expectedConversationId = typeof body.conversation_id === "string" ? body.conversation_id : conversationId;
  const expectedRunSpecId = typeof body.run_spec_id === "string" ? body.run_spec_id : runSpecId;
  const team = parseTeam(body, { conversationId: expectedConversationId, runSpecId: expectedRunSpecId });
  const mutation = { busy: false, notice: "", unknown: false, submit: vi.fn().mockResolvedValue(true), retry: vi.fn().mockResolvedValue(true) };
  const onOpenCitation = vi.fn(), onSelectRevision = vi.fn(), onRefresh = vi.fn();
  const props = { state: { kind: "available" as const, data: team, refreshing: false }, selectedRevision: revision, onSelectRevision,
    mutation, onOpenCitation, onRefresh, nativeStatus: "running" as const };
  return { ...render(<ProfessionalRoleFrame {...props} />), props, team, mutation, onOpenCitation, onSelectRevision };
}
describe("real professional team", () => {
  it("keeps the selected cached role and review draft but disables stale writes", async () => {
    const view = setup(), user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /财务分析师/ }));
    await user.type(screen.getByRole("textbox", { name: "人工复核评语" }), "保留复核草稿");
    view.rerender(<ProfessionalRoleFrame {...view.props} state={{ ...view.props.state, stale: true }} />);
    expect(screen.getByText(/团队状态待更新，当前显示上次成功读取的内容/)).toBeVisible();
    expect(screen.getByText("finance 已保存摘要")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "人工复核评语" })).toHaveValue("保留复核草稿");
    expect(screen.getByRole("button", { name: "保存人工复核" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "暂停专业团队" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新读取团队" })).toBeEnabled();
  });
  it("renders live DTO attempts and only calls out repaired attempts when true", async () => {
    const body = teamWire();
    (body.tasks[0]!.attempts[0]! as { repaired?: boolean }).repaired = true;

    setup(body);
    await userEvent.setup().click(screen.getByText(/模型调用与用量/));

    expect(screen.getByText("本次调用经过结构修复后入库。")).toBeVisible();
  });

  it("opens an exact older same-role dependency version instead of the latest role task", async () => {
    const body = teamWire();
    body.revision = 2;
    const oldFinance = body.tasks[1]!;
    body.tasks.push({ ...oldFinance, id: teamId(50), revision: 2, instruction: "追加财务核对", dependency_ids: [oldFinance.id],
      output: { ...oldFinance.output, id: teamId(51), content: { ...oldFinance.output.content, summary: "finance 第二版" } } });

    setup(body, 2);
    await userEvent.setup().click(screen.getByRole("tab", { name: /财务分析师/ }));
    expect(screen.getByText("finance 第二版")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: /查看前序产出：财务分析师 · 版本 1/ }));

    expect(screen.getByText("finance 已保存摘要")).toBeVisible();
    expect(screen.queryByText("finance 第二版")).not.toBeInTheDocument();
  });

  it("shows saved human reviews by bound revision without treating old reviews as current", async () => {
    const body = teamWire();
    body.revision = 2;
    body.reviews = [{ id: teamId(60), revision: 1, decision: "changes_requested", comment: "第一版需要补充来源。", reviewed_by: "reviewer-a", output_ids: [20, 21, 22, 23].map(teamId), created_at: "2026-09-07T04:10:00Z" }];

    setup(body, 2);

    expect(screen.getByText("当前版本尚无已保存人工复核。")).toBeVisible();
    expect(screen.getByText("其他版本复核记录（1 条）")).toBeVisible();
    expect(screen.getByText(/版本 1 · 要求修改 · reviewer-a/)).toBeVisible();
    expect(screen.getByText("第一版需要补充来源。")).toBeVisible();
  });

  it("explains stable backend reason codes in Chinese and keeps the raw code in technical details", async () => {
    const body = teamWire();
    (body.tasks[0]! as { reason_code: string | null }).reason_code = "invalid_model_result";

    setup(body);

    expect(screen.getByText(/校验失败/)).toBeVisible();
    expect(screen.queryByText("原因：invalid_model_result")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("技术原因代码"));
    expect(screen.getByText("invalid_model_result")).toBeVisible();
  });

  it("explains waiting-for-native-completion and cancel scope", () => {
    const body = teamWire();
    (body.tasks[0]! as { reason_code: string | null }).reason_code = "waiting_for_native_completion";

    setup(body);

    expect(screen.getByText(/等待资料采集与原生研究完成/)).toBeVisible();
    expect(screen.getByText(/取消会同时取消仍在进行的原生 Gateway 研究/)).toBeVisible();
  });

  it("reads actual role outputs and dependencies and preserves unknown model usage", async () => {
    setup();
    expect(screen.getByText("industry 已保存摘要")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("tab", { name: /财务分析师/ }));
    expect(screen.getByText("finance 已保存摘要")).toBeVisible();
    await userEvent.setup().click(screen.getByText("模型调用与用量（1 次尝试）"));
    expect(screen.getByText("总 token：未知")).toBeVisible();
    expect(screen.queryByText("总 token：0")).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: /策略分析师/ }));
    expect(screen.getByRole("button", { name: /查看前序产出：财务分析师/ })).toBeVisible();
    expect(screen.getByText(/暂停仅影响专业团队/)).toBeVisible();
  });
  it("opens a citation with its actual evidence and owning output IDs", async () => {
    const view = setup();
    const citation = screen.getByRole("button", { name: "引用 1：查看冻结原文" });
    await userEvent.setup().click(citation);
    expect(view.onOpenCitation).toHaveBeenCalledWith(view.team.tasks[0]!.output!.content.findings[0]!.citations[0], view.team.tasks[0]!.output!, citation);
  });
  it("submits a human decision with the four currently displayed output IDs", async () => {
    const view = setup(), user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "人工复核评语" }), "已核对期间，仍需补充独立来源。");
    await user.selectOptions(screen.getByRole("combobox", { name: "人工复核决定" }), "changes_requested");
    await user.click(screen.getByRole("button", { name: "保存人工复核" }));
    expect(view.mutation.submit).toHaveBeenCalledWith({ kind: "review", body: { expected_revision: 1,
      output_ids: [20, 21, 22, 23].map(teamId), decision: "changes_requested", comment: "已核对期间，仍需补充独立来源。" } });
  });
  it("keeps historical revisions read-only and carries forward unchanged roles", async () => {
    const body = teamWire(); body.revision = 2;
    body.tasks.push({ ...body.tasks[1]!, id: teamId(50), revision: 2, instruction: "只追加财务核对",
      output: { ...body.tasks[1]!.output, id: teamId(51), content: { ...body.tasks[1]!.output.content, summary: "finance 第二版" } } });
    const view = setup(body, 1);
    await userEvent.setup().click(screen.getByRole("tab", { name: /财务分析师/ }));
    expect(screen.getByText("finance 已保存摘要")).toBeVisible();
    expect(screen.queryByText("finance 第二版")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存人工复核" })).toBeDisabled();
    view.rerender(<ProfessionalRoleFrame {...view.props} selectedRevision={2} />);
    expect(screen.getByText("finance 第二版")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("tab", { name: /产业分析师/ }));
    expect(screen.getByText("industry 已保存摘要")).toBeVisible();
  });
  it("does not expose withheld output text and only retries a real failed task", async () => {
    const body = teamWire();
    Object.assign(body.tasks[0]!, { status: "failed", output_state: "withheld", output: null });
    const view = setup(body);
    expect(screen.queryByText("industry 已保存摘要")).not.toBeInTheDocument();
    expect(screen.getByText("该产出未通过当前权限或完整性校验，正文与引用已隐藏。")).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "重试产业分析师任务" }));
    expect(view.mutation.submit).toHaveBeenCalledWith({ kind: "command", body: { kind: "retry", task_id: teamId(10), expected_revision: 1 } });
    expect(screen.getByRole("button", { name: "保存人工复核" })).toBeDisabled();
  });
});
