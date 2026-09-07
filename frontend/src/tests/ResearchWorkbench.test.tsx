import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ResearchWorkbench } from "@/workbench/ResearchWorkbench";

describe("ResearchWorkbench", () => {
  it("renders the active semiconductor research thread and its evidence", () => {
    render(<ResearchWorkbench />);

    expect(screen.getByRole("heading", { name: "科创板半导体设备国产化机会研究" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "证据与来源" })).toBeInTheDocument();
    expect(screen.getByText(/中微公司：刻蚀设备在客户端验证进展/)).toBeInTheDocument();
  });

  it("filters the research feed to evidence items", async () => {
    const user = userEvent.setup();
    render(<ResearchWorkbench />);

    await user.click(screen.getByRole("button", { name: /证据 3/ }));

    expect(screen.getByText("分析师-产业")).toBeInTheDocument();
    expect(screen.queryByText("质控审核员")).not.toBeInTheDocument();
  });

  it("switches the evidence panel to unresolved questions", async () => {
    const user = userEvent.setup();
    render(<ResearchWorkbench />);

    await user.click(screen.getByRole("tab", { name: /待解决问题/ }));

    expect(screen.getByText(/先进制程设备验证周期/)).toBeInTheDocument();
  });

  it("appends a researcher message from the composer", async () => {
    const user = userEvent.setup();
    render(<ResearchWorkbench />);

    await user.type(screen.getByLabelText("向研究团队发送消息"), "补充北方华创订单核验");
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.getByText("补充北方华创订单核验")).toBeInTheDocument();
  });
});

describe("workbench demo boundaries", () => {
  it("clearly marks the demo and disables unsupported external actions", () => {
    render(<ResearchWorkbench />);
    expect(screen.getByText(/演示模式：未连接 API/)).toBeVisible();
    for (const name of [/分享/, /导出/, /新建研究/]) expect(screen.getByRole("button", { name })).toBeDisabled();
  });
  it("isolates content, local messages and drafts by thread", async () => {
    const user = userEvent.setup();
    render(<ResearchWorkbench />);
    await user.type(screen.getByLabelText("向研究团队发送消息"), "仅半导体消息");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await user.type(screen.getByLabelText("向研究团队发送消息"), "半导体草稿");
    await user.click(screen.getByRole("button", { name: /先进封装产业链梳理/ }));
    expect(screen.queryByText("仅半导体消息")).not.toBeInTheDocument();
    expect(screen.getByLabelText("向研究团队发送消息")).toHaveValue("");
    expect(screen.queryByText("分析师-产业")).not.toBeInTheDocument();
    expect(screen.queryByText(/中微公司：刻蚀设备在客户端验证进展/)).not.toBeInTheDocument();
    expect(screen.getByText("暂无该线程的演示对话。")).toBeVisible();
    expect(screen.getByText("● 待启动")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /科创板半导体设备国产化机会研究/ }));
    expect(screen.getByText("仅半导体消息")).toBeVisible();
    expect(screen.getByLabelText("向研究团队发送消息")).toHaveValue("半导体草稿");
  });
  it("searches thread titles and gives an empty result", async () => {
    const user = userEvent.setup(); render(<ResearchWorkbench />);
    await user.type(screen.getByRole("searchbox"), "先进封装");
    expect(screen.getByRole("button", { name: /先进封装产业链梳理/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /海外半导体设备龙头跟踪/ })).not.toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), "不存在的线程");
    expect(screen.getByText("没有匹配的研究线程。")).toBeVisible();
  });
  it("derives counts and filters pending feed items", async () => {
    const user = userEvent.setup(); render(<ResearchWorkbench />);
    expect(screen.getByRole("button", { name: "全部 5" })).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: "仅看待办" }));
    expect(screen.queryByText("研究目标")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部 4" })).toBeVisible();
  });
  it("supports arrow, Home and End keys in evidence tabs", async () => {
    const user = userEvent.setup(); render(<ResearchWorkbench />);
    const evidence = screen.getByRole("tab", { name: "证据与来源" });
    evidence.focus(); await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /待解决问题/ })).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: /待解决问题/ })).toBeVisible();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "审查状态" })).toHaveFocus();
    await user.keyboard("{Home}"); expect(evidence).toHaveFocus();
  });
});
