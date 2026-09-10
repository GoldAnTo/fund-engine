import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { CompanyStudyWorkspace } from "@/workbench/CompanyStudyWorkspace";
import { GatewayHttpError } from "@/gateway/HttpGatewayClient";
import type { CompanyStudyClient } from "@/gateway/companyStudy";
import type { GatewayConversationClient } from "@/gateway/contracts";

const id = "22222222-2222-4222-8222-222222222222";
const study = {
  id,
  name: "研究公司",
  symbol: null,
  market: "CN" as const,
  focus: "核验收入与现金流",
  revision: 0,
  created_at: "2026-09-09T02:00:00Z",
  updated_at: "2026-09-09T02:00:00Z",
};
function client() {
  return {
    getSession: vi.fn(async () => ({ tenantId: "a", subjectId: "alice", roles: [] })),
    listStudies: vi.fn(async () => [study]),
    listConversations: vi.fn(async () => ({ conversations: [] })),
    getStudy: vi.fn(async () => ({ study, activities: [], revisions: [], monitor: null })),
    createStudy: vi.fn(async () => study),
    createStudyActivity: vi.fn(async () => ({
      id,
      study_id: id,
      kind: "event",
      title: "新财报",
      text: "核验新财报",
      status: "queued",
      conversation_id: null,
      run_spec_id: null,
      error_code: null,
      created_at: study.created_at,
      updated_at: study.updated_at,
    })),
    configureStudyMonitor: vi.fn(async () => ({
      version: 1,
      status: "active",
      frequency: "daily",
      focus: "核验现金流",
      next_due_at: "2026-09-09T12:00:00Z",
    })),
    linkStudyConversation: vi.fn(),
    adoptStudyRevision: vi.fn(),
    retryStudyActivity: vi.fn(),
  } as unknown as GatewayConversationClient & CompanyStudyClient;
}
describe("continuous company workspace", () => {
  it("aborts an old identity submission and ignores its late successful response", async () => {
    const alice = client(),
      bob = client();
    vi.mocked(bob.getSession).mockResolvedValue({ tenantId: "a", subjectId: "bob", roles: [] });
    let resolve!: (value: typeof study) => void;
    vi.mocked(alice.createStudy).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    function Path() {
      return <output data-testid="route">{useLocation().pathname}</output>;
    }
    const view = render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={alice} />
        <Path />
      </MemoryRouter>,
    );
    fireEvent.change(await screen.findByLabelText("公司名称"), { target: { value: "旧主体草稿" } });
    fireEvent.change(screen.getByLabelText("长期研究重点"), { target: { value: "核验" } });
    fireEvent.click(screen.getByRole("button", { name: "创建公司档案" }));
    await waitFor(() => expect(alice.createStudy).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(alice.createStudy).mock.calls[0]?.[2];
    view.rerender(
      <MemoryRouter>
        <CompanyStudyWorkspace client={bob} />
        <Path />
      </MemoryRouter>,
    );
    await screen.findByText("研究员：bob");
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      resolve(study);
    });
    expect(screen.getByTestId("route")).toHaveTextContent(/^\/$/);
    expect(screen.getByLabelText("公司名称")).toHaveValue("");
  });
  it.each(["confirmed", "response_lost"])("does not resume monitoring after a pause (%s)", async (outcome) => {
    const c = client();
    let monitor = {
      version: 1,
      status: "active" as "active" | "paused",
      frequency: "daily" as const,
      focus: "原重点",
      next_due_at: "2026-09-09T12:00:00Z" as string | null,
    };
    vi.mocked(c.getStudy).mockImplementation(async () => ({ study, activities: [], revisions: [], monitor }));
    let writes = 0;
    vi.mocked(c.configureStudyMonitor).mockImplementation(async (_id, body) => {
      monitor = { ...monitor, ...body, frequency: "daily", version: monitor.version + 1, next_due_at: null };
      if (++writes === 1 && outcome === "response_lost") throw new Error("response lost");
      return monitor;
    });
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} studyId={id} />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "监控与版本" }));
    fireEvent.click(screen.getByRole("button", { name: "调整监控" }));
    fireEvent.change(screen.getByLabelText("监控重点"), { target: { value: "补充现金流重点" } });
    fireEvent.click(screen.getByRole("button", { name: "暂停监控" }));
    if (outcome === "confirmed") await screen.findByText("监控已暂停");
    else await screen.findByText(/暂未确认是否保存成功/);
    fireEvent.click(screen.getByRole("button", { name: "保存监控配置" }));
    await waitFor(() => expect(c.configureStudyMonitor).toHaveBeenCalledTimes(2));
    expect(vi.mocked(c.configureStudyMonitor).mock.calls[1]?.[1]).toMatchObject({
      status: "paused",
      focus: "补充现金流重点",
    });
  });
  it("reuses the original adoption revision after a lost response and background update", async () => {
    const c = client();
    let currentRevision = 0;
    const activity = {
      id,
      study_id: id,
      kind: "event" as const,
      title: "完成研究",
      text: "核验",
      status: "completed" as const,
      conversation_id: id,
      run_spec_id: id,
      error_code: null,
      created_at: study.created_at,
      updated_at: study.updated_at,
    };
    vi.mocked(c.getStudy).mockImplementation(async () => ({
      study: { ...study, revision: currentRevision },
      activities: [activity],
      revisions: [],
      monitor: null,
    }));
    vi.mocked(c.adoptStudyRevision)
      .mockImplementationOnce(async () => {
        currentRevision = 1;
        throw new Error("response lost");
      })
      .mockResolvedValue({
        id,
        version: 1,
        activity_id: id,
        conversation_id: id,
        run_spec_id: id,
        team_revision: 1,
        note: "采用已核验版本",
        created_at: study.created_at,
      });
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} studyId={id} />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "监控与版本" }));
    fireEvent.change(screen.getByLabelText("完成的研究活动"), { target: { value: id } });
    fireEvent.change(screen.getByLabelText("采用理由"), { target: { value: "采用已核验版本" } });
    fireEvent.click(screen.getByRole("button", { name: "采用为当前版本" }));
    await screen.findByText(/暂未确认是否保存成功/);
    fireEvent.click(screen.getByRole("button", { name: "更新列表" }));
    await waitFor(() => expect(screen.getAllByText("版本 1").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "采用为当前版本" }));
    await waitFor(() => expect(c.adoptStudyRevision).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(c.adoptStudyRevision).mock.calls;
    expect(calls[1]?.[1]).toEqual(calls[0]?.[1]);
    expect(calls[1]?.[2]).toBe(calls[0]?.[2]);
  });
  it("allows a fresh adoption after a confirmed version conflict", async () => {
    const c = client();
    let currentRevision = 0;
    const activity = {
      id,
      study_id: id,
      kind: "event" as const,
      title: "完成研究",
      text: "核验",
      status: "completed" as const,
      conversation_id: id,
      run_spec_id: id,
      error_code: null,
      created_at: study.created_at,
      updated_at: study.updated_at,
    };
    vi.mocked(c.getStudy).mockImplementation(async () => ({
      study: { ...study, revision: currentRevision },
      activities: [activity],
      revisions: [],
      monitor: null,
    }));
    vi.mocked(c.adoptStudyRevision)
      .mockImplementationOnce(async () => {
        currentRevision = 1;
        throw Object.assign(new GatewayHttpError(409), { code: "company_study_revision_changed" });
      })
      .mockResolvedValue({
        id,
        version: 1,
        activity_id: id,
        conversation_id: id,
        run_spec_id: id,
        team_revision: 1,
        note: "采用已核验版本",
        created_at: study.created_at,
      });
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} studyId={id} />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "监控与版本" }));
    fireEvent.change(screen.getByLabelText("完成的研究活动"), { target: { value: id } });
    fireEvent.change(screen.getByLabelText("采用理由"), { target: { value: "采用已核验版本" } });
    fireEvent.click(screen.getByRole("button", { name: "采用为当前版本" }));
    await screen.findByText(/版本已变化/);
    fireEvent.click(screen.getByRole("button", { name: "更新列表" }));
    await waitFor(() => expect(screen.getAllByText("版本 1").length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "采用为当前版本" }));
    await waitFor(() => expect(c.adoptStudyRevision).toHaveBeenCalledTimes(2));
    const calls = vi.mocked(c.adoptStudyRevision).mock.calls;
    expect(calls[1]?.[1].expected_revision).toBe(1);
    expect(calls[1]?.[2]).not.toBe(calls[0]?.[2]);
  });
  it("creates a company dossier without pretending to start a model run", async () => {
    const c = client();
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} />
      </MemoryRouter>,
    );
    fireEvent.change(await screen.findByLabelText("公司名称"), { target: { value: "研究公司" } });
    fireEvent.change(screen.getByLabelText("长期研究重点"), { target: { value: "核验收入与现金流" } });
    fireEvent.click(screen.getByRole("button", { name: "创建公司档案" }));
    await waitFor(() => expect(c.createStudy).toHaveBeenCalledTimes(1));
    expect(c.createStudyActivity).not.toHaveBeenCalled();
  });
  it("queues an event under this company and fences double submit", async () => {
    const c = client();
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} studyId={id} />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "调查新事件" }));
    fireEvent.change(screen.getByLabelText("本次研究内容"), { target: { value: "核验新财报" } });
    const send = screen.getByRole("button", { name: "提交研究任务" });
    fireEvent.click(send);
    fireEvent.click(send);
    await waitFor(() => expect(c.createStudyActivity).toHaveBeenCalledTimes(1));
    expect(c.createStudyActivity).toHaveBeenCalledWith(
      id,
      { kind: "event", text: "核验新财报" },
      expect.any(String),
      expect.any(AbortSignal),
    );
  });
  it("clears private dossier on denied revalidation", async () => {
    const c = client();
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} studyId={id} />
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "研究公司", level: 1 });
    vi.mocked(c.getStudy).mockRejectedValue(new GatewayHttpError(403));
    fireEvent.click(screen.getByRole("button", { name: "更新列表" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "研究公司", level: 1 })).not.toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent("访问");
  });
  it("does not restore a late dossier read after a write revokes access", async () => {
    const c = client();
    render(
      <MemoryRouter>
        <CompanyStudyWorkspace client={c} studyId={id} />
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "研究公司", level: 1 });
    let resolve!: (value: Awaited<ReturnType<CompanyStudyClient["getStudy"]>>) => void;
    vi.mocked(c.getStudy).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "更新列表" }));
    fireEvent.click(screen.getByRole("button", { name: "调查新事件" }));
    fireEvent.change(screen.getByLabelText("本次研究内容"), { target: { value: "核验新财报" } });
    vi.mocked(c.createStudyActivity).mockRejectedValue(new GatewayHttpError(403));
    fireEvent.click(screen.getByRole("button", { name: "提交研究任务" }));
    await screen.findByRole("alert");
    await act(async () => {
      resolve({ study, activities: [], revisions: [], monitor: null });
    });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "研究公司", level: 1 })).not.toBeInTheDocument());
  });
});
