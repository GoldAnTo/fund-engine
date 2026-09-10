import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  CompanyResearchCriticalInput,
  CompanyResearchCriticalInputSet,
  CriticalInputDecisionRequest,
} from "../../data/investmentResearchApi";
import CriticalInputDrawer from "./CriticalInputDrawer";

const hash = "a".repeat(64);
const artifactId = "20000000-0000-4000-8000-000000000070";

function sourceInput(overrides: Partial<CompanyResearchCriticalInput> = {}): CompanyResearchCriticalInput {
  return {
    key: "fact:fy2025_revenue",
    kind: "source_fact",
    value: "350018",
    value_type: "decimal",
    period: "FY2025",
    unit: "USD million",
    currency: "USD",
    source_ref: {
      fact_key: "fy2025_revenue",
      raw_hash: hash,
      source_locator: "2025 10-K, p. 32",
      source_role: "filing",
      source_url: "https://abc.xyz/investor/10-k",
    },
    provider: null,
    available_at: null,
    coverage: null,
    rationale: null,
    assumption_key: null,
    equation_id: null,
    parent_input_keys: [],
    unknown_reason: null,
    gap_key: null,
    impact: {
      surfaces: ["revenue", "security_value"],
      dependency_paths: [
        ["fact:fy2025_revenue", "surface:revenue"],
        ["fact:fy2025_revenue", "calculation:dcf", "surface:security_value"],
      ],
    },
    decision: "pending",
    replacement: null,
    input_fingerprint: hash,
    ...overrides,
  };
}

function inputSet(inputs: CompanyResearchCriticalInput[]): CompanyResearchCriticalInputSet {
  return {
    schema_version: "underwriting.v1",
    artifact_id: artifactId,
    version: 1,
    input_hash: hash,
    content_hash: hash,
    inputs,
  };
}

describe("CriticalInputDrawer", () => {
  beforeEach(() => {
    Object.defineProperties(HTMLDialogElement.prototype, {
      showModal: { configurable: true, value(this: HTMLDialogElement) { this.setAttribute("open", ""); } },
      close: { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute("open"); } },
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows the current governed input, impact, progress, source, and supplemental-source guidance", async () => {
    const first = sourceInput();
    const second = sourceInput({ key: "assumption:required_return", kind: "ai_assumption", value: "0.12", unit: "ratio", currency: "N/A", source_ref: null, rationale: "长期资本成本假设", assumption_key: "company-research-mainline.v1:required_return" });
    const onDecide = vi.fn<(request: CriticalInputDecisionRequest) => void>();
    const onClose = vi.fn();
    const returnFocus = document.createElement("button");
    returnFocus.textContent = "保存研究版本";
    document.body.append(returnFocus);
    const { container, unmount } = render(<CriticalInputDrawer
      busy={false}
      criticalInputs={inputSet([{ ...first, decision: "confirmed" }, second])}
      error={null}
      onClose={onClose}
      onDecide={onDecide}
      returnFocusTo={returnFocus}
    />);

    const dialog = await screen.findByRole("dialog", { name: "确认关键输入" });
    expect(dialog).toHaveAttribute("aria-describedby");
    expect(dialog).toHaveTextContent("已处理 1 / 2，剩余 1");
    expect(dialog).toHaveTextContent("AI 假设");
    expect(dialog).toHaveTextContent("0.12");
    expect(dialog).toHaveTextContent("ratio");
    expect(dialog).toHaveTextContent("长期资本成本假设");
    expect(dialog).toHaveTextContent("收入预测");
    expect(dialog).toHaveTextContent("证券价值");
    expect(dialog).toHaveTextContent("补充新的授权资料");
    expect(container).toBeEmptyDOMElement();
    expect(container).toHaveAttribute("aria-hidden", "true");
    expect((container as HTMLElement & { inert?: boolean }).inert).toBe(true);
    expect(within(dialog).getByRole("heading", { name: "assumption:required_return" })).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(within(dialog).getByRole("button", { name: "关闭关键输入确认" })).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(within(dialog).getByRole("button", { name: "稍后处理" })).toHaveFocus();

    const user = userEvent.setup();
    await user.click(within(dialog).getByRole("radio", { name: "确认当前输入" }));
    await user.click(within(dialog).getByRole("button", { name: "提交本项决定" }));
    expect(onDecide).toHaveBeenCalledWith({
      schema_version: "underwriting.v1",
      critical_input_key: "assumption:required_return",
      expected_artifact_id: artifactId,
      expected_input_fingerprint: hash,
      decision: "confirmed",
    });

    fireEvent(dialog, new Event("cancel", { bubbles: false, cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    expect(container).not.toHaveAttribute("aria-hidden");
    expect((container as HTMLElement & { inert?: boolean }).inert).toBe(false);
    expect(returnFocus).toHaveFocus();
    returnFocus.remove();
  });

  it("requires a complete replacement and explains that a source disclosure remains unchanged", async () => {
    const onDecide = vi.fn<(request: CriticalInputDecisionRequest) => void>();
    render(<CriticalInputDrawer
      busy={false}
      criticalInputs={inputSet([sourceInput()])}
      error={null}
      onClose={vi.fn()}
      onDecide={onDecide}
      returnFocusTo={null}
    />);
    const user = userEvent.setup();
    const dialog = await screen.findByRole("dialog", { name: "确认关键输入" });
    await user.click(within(dialog).getByRole("radio", { name: "改为用户假设" }));
    expect(dialog).toHaveTextContent("原始披露会保留，新值将另存为用户假设");
    const submit = within(dialog).getByRole("button", { name: "提交本项决定" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox", { name: "替代值" }), "360000");
    await user.type(within(dialog).getByRole("textbox", { name: "单位" }), "USD million");
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox", { name: "修改理由" }), "采用已授权补充材料中的口径");
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(onDecide).toHaveBeenCalledWith({
      schema_version: "underwriting.v1",
      critical_input_key: "fact:fy2025_revenue",
      expected_artifact_id: artifactId,
      expected_input_fingerprint: hash,
      decision: "replaced_with_user_assumption",
      replacement_value: "360000",
      replacement_unit: "USD million",
      replacement_rationale: "采用已授权补充材料中的口径",
    });
  });

  it("offers only governed terminal decisions for concrete and unknown inputs", async () => {
    const onDecide = vi.fn<(request: CriticalInputDecisionRequest) => void>();
    const { rerender } = render(<CriticalInputDrawer
      busy={false}
      criticalInputs={inputSet([sourceInput()])}
      error={null}
      onClose={vi.fn()}
      onDecide={onDecide}
      returnFocusTo={null}
    />);
    const user = userEvent.setup();
    let dialog = await screen.findByRole("dialog", { name: "确认关键输入" });
    expect(within(dialog).getByRole("radio", { name: "标记为未知" })).toBeVisible();
    expect(within(dialog).queryByRole("radio", { name: "接受为研究缺口" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: "标记为未知" }));
    await user.click(within(dialog).getByRole("button", { name: "提交本项决定" }));
    expect(onDecide).toHaveBeenLastCalledWith(expect.objectContaining({ decision: "marked_unknown" }));

    const unknown = sourceInput({
      key: "unknown:segment_margin",
      kind: "unknown",
      value: null,
      value_type: "none",
      period: null,
      unit: null,
      currency: null,
      source_ref: null,
      unknown_reason: "公司未单独披露分部利润率",
      gap_key: "segment_margin_gap",
      impact: { surfaces: ["answerability"], dependency_paths: [["unknown:segment_margin", "surface:answerability"]] },
      input_fingerprint: "b".repeat(64),
    });
    rerender(<CriticalInputDrawer
      busy={false}
      criticalInputs={{ ...inputSet([unknown]), artifact_id: "20000000-0000-4000-8000-000000000071", version: 2 }}
      error={null}
      onClose={vi.fn()}
      onDecide={onDecide}
      returnFocusTo={null}
    />);
    dialog = await screen.findByRole("dialog", { name: "确认关键输入" });
    expect(dialog).toHaveTextContent("公司未单独披露分部利润率");
    expect(within(dialog).getByRole("radio", { name: "接受为研究缺口" })).toBeVisible();
    expect(within(dialog).queryByRole("radio", { name: "标记为未知" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: "接受为研究缺口" }));
    await user.click(within(dialog).getByRole("button", { name: "提交本项决定" }));
    expect(onDecide).toHaveBeenLastCalledWith(expect.objectContaining({
      critical_input_key: "unknown:segment_margin",
      decision: "accepted_gap",
    }));
  });
});
