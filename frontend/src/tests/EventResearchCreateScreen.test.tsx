import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { resetResearchClient, setResearchClient } from "../data/researchClient";
import { EventResearchCreateScreen } from "../pages/prototype/EventResearchCreateScreen";

describe("EventResearchCreateScreen", () => {
  beforeEach(() => setResearchClient(new MockResearchAdapter()));
  afterEach(() => resetResearchClient());

  it("extracts pasted news, keeps the question editable, then starts automatic research", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><EventResearchCreateScreen /></MemoryRouter>);

    await user.type(screen.getByLabelText("新闻或研究信息"), "公司宣布上调资本开支，盘后下跌。");
    await user.click(screen.getByRole("button", { name: "AI 提取关键信息" }));
    const question = await screen.findByLabelText("研究问题");
    await user.clear(question);
    await user.type(question, "资本开支是否是下跌的主要因素？");
    await user.click(screen.getByRole("button", { name: "创建并开始自动研究" }));

    expect(await screen.findByText("系统正在自动研究")).toBeVisible();
  });
});
