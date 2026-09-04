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

    await user.click(screen.getByRole("button", { name: /证据 6/ }));

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
