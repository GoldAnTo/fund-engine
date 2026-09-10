import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { investmentResearchApi, InvestmentResearchRequestError } from "../../data/investmentResearchApi";
import { modelHash, modelIds, modelRecord, modelWorkspace } from "../../data/companyFinancialModel.test-fixtures";
import { CompanyFinancialModelPanel } from "./CompanyFinancialModelPanel";

function panel(projectId = modelIds.project, mode: "forecast" | "valuation" = "forecast") { return <MemoryRouter><CompanyFinancialModelPanel projectId={projectId} parentRevisionId={modelIds.revision} parentManifestHash={modelHash} mode={mode} /></MemoryRouter>; }
beforeEach(() => { vi.spyOn(investmentResearchApi, "companyFinancialModel").mockResolvedValue(modelWorkspace()); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("conditional financial model drafts", () => {
  it("shows attributed annual/H1 baselines, missing inputs and three editable scenarios without marking the parent reviewed", async () => {
    const user = userEvent.setup(); render(panel());
    expect(await screen.findByRole("heading", { name: "条件估值模型草稿" })).toBeVisible();
    expect(screen.getByText("年度集团收入")).not.toBeVisible();
    await user.click(screen.getByText("核对历史财务基线（2 项 / 1 份原文）"));
    expect(screen.getByText("年度集团收入")).toBeVisible(); expect(screen.getByText("上半年云收入")).toBeVisible();
    expect(screen.getByText("云分部现金流未披露")).toBeVisible();
    expect(screen.getByLabelText("基准 2026 收入")).toHaveValue("450000");
    await user.click(screen.getByRole("tab", { name: "乐观" }));
    expect(screen.getByLabelText("乐观 2030 收入")).toHaveValue("540000");
    expect(screen.getByText(/不改变已冻结研究的正式判断/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /确认|发布/ })).not.toBeInTheDocument();
  });
  it("saves all inputs using CAS and persists the returned unreviewed result", async () => {
    const user = userEvent.setup(); const save = vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel").mockImplementation(async (_id, body) => ({ ...modelRecord(), inputs: body.inputs }));
    render(panel()); const input = await screen.findByLabelText("基准 2026 收入"); fireEvent.change(input, { target: { value: "460000" } });
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0]?.[1]).toMatchObject({ parent_revision_id: modelIds.revision, expected_latest_id: null, baseline_content_hash: modelHash, inputs: { scenarios: [expect.objectContaining({ paths: expect.objectContaining({ revenue: ["460000", "480000", "500000", "520000", "540000"] }) }), expect.anything(), expect.anything()] } });
    expect(save.mock.calls[0]?.[2]).toEqual(expect.any(String));
    expect(await screen.findByText("模型草稿 1 已保存，尚未复核。")).toBeVisible();
  });
  it("preserves edits on conflict and offers safe reloading without silently replacing input", async () => {
    const user = userEvent.setup(); vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel").mockRejectedValue(new InvestmentResearchRequestError("版本冲突", 409, "conflict", null));
    render(panel()); fireEvent.change(await screen.findByLabelText("基准 2026 收入"), { target: { value: "470000" } });
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/输入已保留/);
    expect(screen.getByLabelText("基准 2026 收入")).toHaveValue("470000");
  });
  it("rejects invalid decimals locally and retains the string being edited", async () => {
    const user = userEvent.setup(); const save = vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel"); render(panel());
    fireEvent.change(await screen.findByLabelText("基准 2026 收入"), { target: { value: "not a number" } });
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/数值/); expect(save).not.toHaveBeenCalled();
  });
  it("loads a historical draft while saving against latest, never against the loaded history", async () => {
    const user = userEvent.setup(); const latest = modelRecord(2); const old = modelRecord();
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), latest, history: [latest, old] });
    vi.spyOn(investmentResearchApi, "companyFinancialModelDraft").mockResolvedValue(old);
    const save = vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel").mockResolvedValue(modelRecord(2)); render(panel());
    await user.click(await screen.findByRole("button", { name: "加载模型草稿 1" }));
    await screen.findByText("正在回放模型草稿 1");
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    await waitFor(() => expect(save).toHaveBeenCalled()); expect(save.mock.calls[0]?.[1].expected_latest_id).toBe(latest.id);
  });
  it("valuation shows saved results and a forecast link without editable valuation inputs", async () => {
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), latest: modelRecord(), history: [modelRecord()] }); render(panel(modelIds.project, "valuation"));
    expect(await screen.findByText("213.22")).toBeVisible();
    expect(screen.getByRole("link", { name: "编辑预测与估值假设" })).toHaveAttribute("href", `/research/projects/${modelIds.project}/forecast`);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
  it("refresh loads the saved inputs", async () => {
    const saved = modelRecord(); saved.inputs.scenarios[0]!.paths.revenue[0] = "490000";
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), latest: saved, history: [saved] }); render(panel());
    expect(await screen.findByLabelText("基准 2026 收入")).toHaveValue("490000");
  });
  it("discards late responses when the project changes", async () => {
    let resolve!: (value: ReturnType<typeof modelWorkspace>) => void;
    vi.mocked(investmentResearchApi.companyFinancialModel).mockReturnValueOnce(new Promise((r) => { resolve = r; }));
    const other = "20000000-0000-4000-8000-000000000099";
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValueOnce({ ...modelWorkspace(), project_id: other });
    const view = render(panel()); view.rerender(panel(other));
    await screen.findByLabelText("基准 2026 收入"); fireEvent.change(screen.getByLabelText("基准 2026 收入"), { target: { value: "500001" } });
    await act(async () => resolve(modelWorkspace())); expect(screen.getByLabelText("基准 2026 收入")).toHaveValue("500001");
  });
  it("reuses one idempotency key after an uncertain save and preserves inputs", async () => {
    const user = userEvent.setup(); const save = vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel").mockRejectedValueOnce(new Error("connection interrupted")).mockResolvedValueOnce(modelRecord());
    render(panel()); await screen.findByLabelText("基准 2026 收入");
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" })); await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    await screen.findByText("模型草稿 1 已保存，尚未复核。");
    expect(save.mock.calls[0]?.[2]).toBe(save.mock.calls[1]?.[2]);
  });
  it("exports the selected historical receipt as one download", async () => {
    const user = userEvent.setup(); const saved = modelRecord();
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), latest: saved, history: [saved] });
    const exported = vi.spyOn(investmentResearchApi, "exportCompanyFinancialModelDraft").mockResolvedValue({ filename: `alphabet-conditional-model-${saved.id}.md`, media_type: "text/markdown", content: "未复核模型草稿", content_hash: modelHash });
    const create = vi.fn(() => "blob:model-export"); const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: create, revokeObjectURL: revoke }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(panel(modelIds.project, "valuation")); await screen.findByText("213.22");
    await user.click(screen.getByRole("button", { name: "导出模型草稿 1" }));
    await waitFor(() => expect(exported).toHaveBeenCalledWith(modelIds.project, saved.id));
    expect(create).toHaveBeenCalledOnce(); expect(click).toHaveBeenCalledOnce(); expect(revoke).toHaveBeenCalledWith("blob:model-export");
  });
  it("discards a late save after switching to another project", async () => {
    const user = userEvent.setup(); let resolve!: (record: ReturnType<typeof modelRecord>) => void;
    vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel").mockReturnValue(new Promise((r) => { resolve = r; }));
    const view = render(panel()); await screen.findByLabelText("基准 2026 收入");
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    const other = "20000000-0000-4000-8000-000000000099";
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), project_id: other }); view.rerender(panel(other));
    await screen.findByLabelText("基准 2026 收入");
    await act(async () => resolve(modelRecord()));
    expect(screen.queryByText("模型草稿 1 已保存，尚未复核。")).not.toBeInTheDocument();
    expect(screen.getByText("输入草稿 · 尚未保存")).toBeVisible();
  });
  it("keeps enterable inputs while refreshing the conflict CAS anchor", async () => {
    const user = userEvent.setup(); const latest = modelRecord(2);
    const save = vi.spyOn(investmentResearchApi, "saveCompanyFinancialModel").mockRejectedValueOnce(new InvestmentResearchRequestError("conflict", 409, "conflict", null)).mockResolvedValueOnce(latest);
    render(panel()); fireEvent.change(await screen.findByLabelText("基准 2026 收入"), { target: { value: "480001" } });
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" })); await screen.findByRole("alert");
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), latest, history: [latest] });
    await user.click(screen.getByRole("button", { name: "更新版本信息，保留输入" }));
    await screen.findByText("版本信息已更新，当前输入已保留；请核对事实基线后再保存。");
    expect(screen.getByLabelText("基准 2026 收入")).toHaveValue("480001");
    await user.click(screen.getByRole("button", { name: "保存草稿并计算" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2)); expect(save.mock.calls[1]?.[1].expected_latest_id).toBe(latest.id);
    expect(save.mock.calls[1]?.[1].inputs.scenarios[0]?.paths.revenue[0]).toBe("480001");
  });
  it("rejects a mismatched parent manifest before displaying financial input", async () => {
    vi.mocked(investmentResearchApi.companyFinancialModel).mockResolvedValue({ ...modelWorkspace(), parent_manifest_hash: "b".repeat(64) }); render(panel());
    expect(await screen.findByRole("alert")).toHaveTextContent(/冻结版本/); expect(screen.queryByLabelText("基准 2026 收入")).not.toBeInTheDocument();
  });
});
