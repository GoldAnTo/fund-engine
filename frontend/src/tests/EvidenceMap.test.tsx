import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { GatewayEvidenceSummary } from "@/gateway/researchContent";
import { EvidenceMap } from "@/workbench/EvidenceMap";

function evidence(id: string, relationship: string): GatewayEvidenceSummary {
  return { evidenceLinkId: id, taskId: `task-${id}`, thesisId: "thesis", thesisStatement: "研究论点",
    statement: "材料内容仍待复核。", documentVersionId: `document-${id}`, title: `材料${id}`, sourceAuthority: "primary_disclosure",
    sourceUrl: null, publishedAt: null, observedPeriod: "2025年度", relationship, reviewState: "automatically_admitted" };
}

describe("EvidenceMap", () => {
  it("preserves actual inputs and separates retrieval directions from validated relationships", () => {
    render(<EvidenceMap evidence={[evidence("一", "supports"), evidence("二", "contradicts"), evidence("三", "alternative_explanation"), evidence("四", "unrecognized")]} onOpen={vi.fn()} />);
    for (const label of ["支持方向检索", "反证方向检索", "替代解释方向检索", "关系待核验"]) {
      expect(screen.getByRole("group", { name: label })).toBeVisible();
    }
    expect(screen.getByText(/检索方向不是已验证关系/)).toBeVisible();
    expect(screen.queryByText("已证实支持")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^查看引用原文：/ })).toHaveLength(4);
  });

  it("filters paths using keyboard controls and opens the exact original input", async () => {
    const support = evidence("一", "support"), counter = evidence("二", "counter_evidence"), onOpen = vi.fn();
    render(<EvidenceMap evidence={[support, counter]} onOpen={onOpen} selectedEvidenceId={counter.evidenceLinkId} />);
    const filter = screen.getByRole("button", { name: "仅看反证方向（1）" });
    filter.focus();
    const user = userEvent.setup();
    await user.keyboard(" ");
    expect(filter).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "查看引用原文：材料一" })).not.toBeInTheDocument();
    const node = screen.getByRole("button", { name: "查看引用原文：材料二" });
    expect(node).toHaveAttribute("aria-pressed", "true");
    node.focus();
    await user.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledExactlyOnceWith(counter);
    await user.click(screen.getByRole("button", { name: "全部方向（2）" }));
    expect(screen.getAllByRole("button", { name: /^查看引用原文：/ })).toHaveLength(2);
  });

  it("resets a direction filter when the authorized input set changes", async () => {
    const view = render(<EvidenceMap evidence={[evidence("一", "support"), evidence("二", "contradict")]} onOpen={vi.fn()} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "仅看反证方向（1）" }));
    view.rerender(<EvidenceMap evidence={[evidence("新授权", "support")]} onOpen={vi.fn()} />);
    expect(screen.getByRole("button", { name: "查看引用原文：材料新授权" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "查看引用原文：材料二" })).not.toBeInTheDocument();
  });

  it("keeps long Chinese text and markup literal, with a native structural list", () => {
    const entry = { ...evidence("一", "unknown"), title: "长中文标题".repeat(40) + '<img src=x onerror="alert(1)">' };
    const { container } = render(<EvidenceMap evidence={[entry]} onOpen={vi.fn()} />);
    const group = screen.getByRole("group", { name: "关系待核验" });
    expect(within(group).getByRole("list")).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: `查看引用原文：${entry.title}` })).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("does not imply evidence coverage when inputs are absent", () => {
    render(<EvidenceMap evidence={[]} onOpen={vi.fn()} />);
    expect(screen.getByText("当前没有可展示的评估输入。")).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
