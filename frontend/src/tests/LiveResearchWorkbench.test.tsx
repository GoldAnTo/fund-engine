import { webcrypto } from "node:crypto";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LiveResearchWorkbench } from "@/workbench/LiveResearchWorkbench";

const item = (id: string) => ({ case_id: id, event_title: `研究${id}`, lifecycle_status: "draft", status_summary: "等待材料核验", updated_at: "2026-09-06" });
const detail = (id: string) => ({ event: item(id), lifecycle: { status: "draft", status_summary: "等待材料核验", current_gap: "缺少原始公告", next_human_action: "补充材料" }, conclusion: { state: "draft", text: "尚未形成结论", confidence: "low", citations: [] }, factors: [], evidence: [], progress: { verified: 0, pending: 0, invalid_source: 0 }, scope: { factors: [] }, next_action: { kind: "attach_material", label: "补充材料" } });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => { window.history.replaceState({}, "", "/"); sessionStorage.clear(); vi.stubGlobal("crypto", webcrypto); });
afterEach(() => vi.unstubAllGlobals());
it("shows API errors without inserting demo studies", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ error: { code: "forbidden", message: "无权读取研究", request_id: "req-403", details: {} } }, 403)));
  render(<LiveResearchWorkbench />);
  expect(await screen.findByRole("alert")).toHaveTextContent("权限");
  expect(screen.queryByText("科创板半导体设备国产化机会研究")).not.toBeInTheDocument();
});
it("aborts old selections and ignores their late responses", async () => {
  let resolveOld!: (value: Response) => void;
  let oldSignal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
    if (url.endsWith("/runs")) return Promise.resolve(response({ items: [] }));
    if (url.endsWith("/event-research")) return Promise.resolve(response({ items: [item("a"), item("b")] }));
    if (url.includes("/a/")) { oldSignal = init.signal as AbortSignal; return new Promise<Response>((resolve) => { resolveOld = resolve; }); }
    return Promise.resolve(response(detail("b")));
  }));
  render(<LiveResearchWorkbench />);
  await userEvent.click(await screen.findByRole("button", { name: /研究a/ }));
  await waitFor(() => expect(resolveOld).toBeDefined());
  await userEvent.click(screen.getByRole("button", { name: /研究b/ }));
  expect(await screen.findByRole("heading", { name: "研究b" })).toBeInTheDocument();
  await act(async () => resolveOld(response(detail("a"))));
  expect(oldSignal?.aborted).toBe(true);
  expect(screen.queryByRole("heading", { name: "研究a" })).not.toBeInTheDocument();
  expect(window.location.search).toContain("caseId=b");
});
it("preserves failed creation input then reads back a successful case", async () => {
  let attempts = 0;
  const fetcher = vi.fn((url: string, init: RequestInit) => {
    if (url.endsWith("/runs")) return Promise.resolve(response({ items: [] }));
    if (init.method === "POST") return Promise.resolve(++attempts === 1 ? response({ error: { code: "unavailable", message: "暂时无法创建", request_id: "req-503", details: {} } }, 503) : response({ case_id: "new" }));
    return Promise.resolve(response(url.endsWith("/workbench") ? detail("new") : { items: attempts > 1 ? [item("new")] : [] }));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<LiveResearchWorkbench />);
  await userEvent.click(screen.getByRole("button", { name: "新建研究" }));
  for (const [label, value] of [["事件标题", "真实事件"], ["原始材料", "公告原文"], ["研究问题", "利润为何变化"], ["候选因素（每行一项，3–5项）", "需求\n价格\n成本"], ["创建人", "研究员"]] as const) await userEvent.type(screen.getByLabelText(label), value);
  await userEvent.click(screen.getByRole("button", { name: "创建研究" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("研究服务暂时不可用");
  expect(screen.getByLabelText("原始材料")).toHaveValue("公告原文");
  await userEvent.click(screen.getByRole("button", { name: "创建研究" }));
  expect(await screen.findByRole("heading", { name: "研究new" })).toBeInTheDocument();
  expect(window.location.search).toContain("caseId=new");
  const writes = fetcher.mock.calls.filter(([, init]) => init.method === "POST");
  expect(writes).toHaveLength(2);
  const headers = writes.map(([, init]) => new Headers(init.headers).get("Idempotency-Key"));
  expect(headers[0]).toBeTruthy();
  expect(headers[0]).toBe(headers[1]);
  expect(sessionStorage.getItem("fundclaw:create-submission")).toBeNull();
});
it("restores the selected case from the URL", async () => {
  window.history.replaceState({}, "", "/?caseId=saved");
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(response(url.endsWith("/runs") ? { items: [] } : url.endsWith("/workbench") ? detail("saved") : { items: [item("saved")] }))));
  render(<LiveResearchWorkbench />);
  expect(await screen.findByRole("heading", { name: "研究saved" })).toBeInTheDocument();
});
it("keeps the visible workbench when selecting the active study again", async () => {
  window.history.replaceState({}, "", "/?caseId=a");
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(response(url.endsWith("/runs") ? { items: [] } : url.endsWith("/workbench") ? detail("a") : { items: [item("a")] }))));
  render(<LiveResearchWorkbench />);
  expect(await screen.findByRole("heading", { name: "研究a" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /研究a/ }));
  expect(screen.getByRole("heading", { name: "研究a" })).toBeInTheDocument();
});
