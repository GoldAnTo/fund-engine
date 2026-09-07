import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ResearchActions } from "@/workbench/ResearchActions";
const preparation = (withheld = false) => ({ case_id: "a", status: "awaiting_plan_authorization", revision: 3, research_run_id: null, system: { claims: { state: "completed" } }, review: { claims: { state: "confirmed" }, protocol: { state: "confirmed" }, plan: { state: "awaiting_review" } }, progress: { completed_steps: 3, total_steps: 3 }, artifacts: { protocol: { sequence: 4, state: "current", display_withheld: false, payload: {} }, plan: { sequence: 7, state: "current", display_withheld: withheld, payload: { items: [{ factor: "需求", evidence_target: "原始订单", allowed_source_roles: ["official"], priority: "high", stop_condition: "找到公告", budget: 3 }] } } } });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });
it("keeps failed material input and confirms only material storage on success", async () => {
  let posts = 0;
  const fetcher = vi.fn((url: string, init: RequestInit) => Promise.resolve(init.method === "POST" ? ++posts === 1 ? response({ error: { request_id: "retry" } }, 503) : response({ document_version_id: "doc-1", source_type: "pasted_snapshot" }) : response(url.endsWith("/preparation") ? preparation() : { items: [], has_more: false, next_cursor: null })));
  vi.stubGlobal("fetch", fetcher);
  render(<ResearchActions caseId="a" published={false} hasPreparation onRefresh={() => {}} />);
  await userEvent.type(screen.getByLabelText("补充材料原文"), "真实原文");
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  await userEvent.click(screen.getByRole("button", { name: "保存补充材料" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时不可用");
  expect(screen.getByLabelText("补充材料原文")).toHaveValue("真实原文");
  await userEvent.click(screen.getByRole("button", { name: "保存补充材料" }));
  expect(await screen.findByText(/材料已保存/)).toHaveTextContent("尚未自动启动研究");
});
it("does not allow authorization of withheld evidence plans", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(response(url.endsWith("/preparation") ? preparation(true) : { items: [], has_more: false, next_cursor: null }))));
  render(<ResearchActions caseId="a" published={false} hasPreparation onRefresh={() => {}} />);
  expect(await screen.findByText(/计划内容暂不可展示/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "授权计划并启动研究" })).not.toBeInTheDocument();
});
it("authorizes the displayed revision and reuses the key after uncertain failure", async () => {
  const payloads: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
    if (init.method === "POST") { payloads.push(JSON.parse(init.body as string)); return Promise.resolve(response({}, 503)); }
    return Promise.resolve(response(url.endsWith("/preparation") ? preparation() : { items: [], has_more: false, next_cursor: null }));
  }));
  render(<ResearchActions caseId="a" published={false} hasPreparation onRefresh={() => {}} />);
  expect(await screen.findByText("原始订单")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  await userEvent.click(screen.getByLabelText("我已核对计划范围、来源和预算"));
  await userEvent.click(screen.getByRole("button", { name: "授权计划并启动研究" }));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "授权计划并启动研究" }));
  expect(payloads).toHaveLength(2);
  expect(payloads[0]).toMatchObject({ revision: 3, plan_sequence: 7, actor: "审核员" });
  expect(payloads[0]?.idempotency_key).toBe(payloads[1]?.idempotency_key);
});
it("disables intake for published research and reads recorded run status", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ items: [{ id: "run-1", status: "failed", stage: "retrieval", round: 1, max_rounds: 3, budget: 10, budget_used: 2, next_action: "查看失败原因" }], has_more: false, next_cursor: null })));
  render(<ResearchActions caseId="a" published hasPreparation={false} onRefresh={() => {}} />);
  expect(await screen.findByText("检索证据")).toBeInTheDocument();
  expect(screen.getByText("失败")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "保存补充材料" })).not.toBeInTheDocument();
});
it("does not authorize when the underlying reviewed protocol is withheld", async () => {
  const prep = { ...preparation(), artifacts: { ...preparation().artifacts, protocol: { sequence: 4, state: "current", display_withheld: true, payload: {} } } };
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(response(url.endsWith("/preparation") ? prep : { items: [], has_more: false, next_cursor: null }))));
  render(<ResearchActions caseId="a" published={false} hasPreparation onRefresh={() => {}} />);
  await screen.findByText("原始订单");
  expect(screen.queryByRole("button", { name: "授权计划并启动研究" })).not.toBeInTheDocument();
});
