import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { WorkspaceOverviewPage } from "../pages/WorkspaceOverviewPage";
import { renderWithAppShell } from "./renderWithAppShell";

function renderOverview() {
  return renderWithAppShell(<WorkspaceOverviewPage />, {
    initialEntries: ["/"],
  });
}

describe("WorkspaceOverviewPage", () => {
  it("renders research summary, task queue, evidence changes and activity", async () => {
    renderOverview();
    expect(await screen.findByTestId("workspace-overview")).toBeVisible();
    // 中央研究正文
    expect(await screen.findByText("核心结论")).toBeVisible();
    // 右侧三列：任务队列 / 证据变化 / 活动
    expect(screen.getByText("任务队列")).toBeVisible();
    expect(screen.getByText("证据变化")).toBeVisible();
    expect(screen.getByText("活动")).toBeVisible();
    // 主要阻塞任务项必须可见，证明"边研究、边处理任务"结构没被简化
    expect(
      await screen.findByText(/高端 GPU 出口管制影响评估/)
    ).toBeVisible();
  });

  it("keeps the AI provisional banner when an AI assessment is unreviewed", async () => {
    renderOverview();
    expect(
      (await screen.findAllByText(/AI 临时判断/)).length
    ).toBeGreaterThan(0);
  });
});

describe("AppShell navigation", () => {
  it("lists the four implemented entry points and labels later entries", async () => {
    renderWithAppShell(<div />, { initialEntries: ["/"] });
    expect(screen.getByText("研究总览")).toBeVisible();
    expect(screen.getByText("行业研究")).toBeVisible();
    expect(screen.getByText("关系模式")).toBeVisible();
    expect(screen.getByText("证据库")).toBeVisible();
    expect(screen.getByText("审核队列")).toBeVisible();
    // 后续入口需要明示
    expect(screen.getAllByText(/后续/).length).toBeGreaterThan(0);
  });
});