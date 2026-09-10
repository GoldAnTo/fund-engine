import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CaseRuntimeStatus } from "../domain/eventWorkflow";
import { RuntimeStatusNotice } from "../features/system/RuntimeStatusNotice";


const unavailable: CaseRuntimeStatus = {
  runtimeStatus: "unavailable",
  requiredServices: [
    { name: "scheduler", status: "healthy", state: "polling", lastSeenAt: "2026-08-17T08:00:00Z" },
    { name: "acquisition-worker", status: "unavailable", state: null, lastSeenAt: null },
  ],
  durableCheckpoint: {
    workflowState: "planning_acquisition",
    userStage: "acquisition",
    version: 3,
    systemAction: "正在生成资料获取计划",
    savedAt: "2026-08-17T08:00:00Z",
    lastTransition: "scope_confirmed",
    lastTransitionAt: "2026-08-17T07:59:00Z",
  },
  recovery: {
    automatic: true,
    status: "healthy",
    message: "服务恢复后会从耐久检查点继续，不会从头重做。",
  },
  message: "运行健康与 Case 进度分别读取；不会用运行健康推断 Case 阶段。",
};


describe("RuntimeStatusNotice", () => {
  it("shows the unavailable service, durable checkpoint, recovery behavior, and retry", () => {
    const onRetry = vi.fn();

    render(
      <RuntimeStatusNotice
        status={unavailable}
        error={null}
        retrying={false}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("资料获取服务暂不可用");
    expect(screen.getByText(/最后耐久检查点/)).toHaveTextContent("确认研究范围");
    expect(screen.getByText(/自动恢复/)).toHaveTextContent("服务恢复后会从耐久检查点继续");
    expect(screen.getByText(/不会用运行健康推断 Case 阶段/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新检查运行状态" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("keeps a durable workflow checkpoint visible when runtime health cannot be read", () => {
    const onRetry = vi.fn();

    render(
      <RuntimeStatusNotice
        status={null}
        error="运行状态接口暂时无法读取"
        retrying={false}
        fallbackCheckpoint={{
          version: 7,
          savedAt: "2026-08-17T08:03:00Z",
        }}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("运行健康暂不可确认");
    expect(screen.getByText(/已保存流程记录/)).toHaveTextContent("v7");
    expect(screen.getByText(/Case 进度仍以已保存的流程记录为准/)).toBeInTheDocument();
    expect(screen.queryByText(/研究失败/)).not.toBeInTheDocument();
  });

  it("renders a compact confirmation when the required service is healthy", () => {
    render(
      <RuntimeStatusNotice
        status={{
          ...unavailable,
          runtimeStatus: "healthy",
          requiredServices: [
            { name: "scheduler", status: "healthy", state: "polling", lastSeenAt: "2026-08-17T08:04:00Z" },
            { name: "acquisition-worker", status: "healthy", state: "polling", lastSeenAt: "2026-08-17T08:02:00Z" },
          ],
        }}
        error={null}
        retrying={false}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("流程调度服务、资料获取服务运行正常");
    expect(screen.getByRole("status")).toHaveTextContent(/流程调度服务.*16:04:00/);
    expect(screen.getByRole("status")).toHaveTextContent(/资料获取服务.*16:02:00/);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
