import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import ResearchHomePage from "./ResearchHomePage";

const uid = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const schema = { schema_version: "underwriting.v1" } as const;
const company = { ...schema, object_id: uid(1), identity_version_id: uid(2), kind: "company", external_key: "CN:CATL:COMPANY", canonical_name: "宁德时代", symbol: null, exchange: null, share_class: null, trading_currency: null };
function project(id: number, name: string, created: string) {
  return { ...schema, id: uid(id), primary_company_id: uid(1), target_security_ids: [uid(3)], company_identity: { ...schema, object_id: uid(1), identity_version_id: uid(2), canonical_name: name }, security_identities: [{ ...schema, object_id: uid(3), identity_version_id: uid(4), canonical_name: name, symbol: "300750", exchange: "SZSE", share_class: "A", trading_currency: "CNY" }], content_hash: "a".repeat(64), created_at: created };
}
function json(body: object, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
function progressResponse(projectId: string) {
  return { ...schema, project_id: projectId, company_id: uid(1), preparation: { ...schema, id: uid(20), project_id: projectId, request_hash: "a".repeat(64), strategy_version: "company-research-default.v1", status: "awaiting_evidence_review", current_step: "research_gaps", progress: 25, attempt: 1, next_attempt_at: null, last_error_code: null }, product_progress: { ...schema, run_id: uid(20), project_id: projectId, company_id: uid(1), status: "needs_input", current_step: "research_gaps", progress_percent: 25, user_focus: "现金流", cutoff_at: "2026-08-25T00:00:00Z", retryable: false, error_code: null } };
}
function Destination() {
  const location = useLocation();
  return <output aria-label="下一页面">{JSON.stringify({ path: location.pathname, state: location.state })}</output>;
}
function show() {
  render(<MemoryRouter initialEntries={["/research"]}><Routes><Route path="/research" element={<ResearchHomePage />} /><Route path="*" element={<Destination />} /></Routes></MemoryRouter>);
}

describe("company research library", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("opens persisted projects with the newest research first", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ ...schema, items: [project(5, "较早研究", "2026-08-24T00:00:00Z"), project(6, "最近研究", "2026-08-25T00:00:00Z")] })));
    const user = userEvent.setup();
    show();
    expect(screen.getByRole("heading", { name: "研究库", level: 1 })).toBeVisible();
    const list = await screen.findByRole("list", { name: "已有公司研究" });
    const links = within(list).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([`/research/projects/${uid(6)}`, `/research/projects/${uid(5)}`]);
    await user.click(links[0]!);
    expect(screen.getByLabelText("下一页面")).toHaveTextContent(uid(6));
  });

  it("searches actual identities and carries the selected company into the entry", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => String(input).includes("/objects?") ? json({ ...schema, items: [company] }) : json({ ...schema, items: [] }));
    vi.stubGlobal("fetch", fetch);
    const user = userEvent.setup();
    show();
    await user.type(screen.getByLabelText("公司名称或证券代码"), "宁德时代");
    await user.click(screen.getByRole("button", { name: "搜索公司" }));
    await user.click(await screen.findByRole("link", { name: "研究 宁德时代" }));
    expect(screen.getByLabelText("下一页面")).toHaveTextContent('"seedObject"');
    expect(screen.getByLabelText("下一页面")).toHaveTextContent(uid(1));
    expect(fetch.mock.calls.some(([url]) => String(url).includes(encodeURIComponent("宁德时代")))).toBe(true);
  });

  it("shows server-owned pending status while one unavailable status leaves its research accessible", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(`/company-research/projects/${uid(5)}`)) return json(progressResponse(uid(5)));
      if (url.includes(`/company-research/projects/${uid(6)}`)) throw new TypeError("offline");
      return json({ ...schema, items: [project(5, "待确认研究", "2026-08-24T00:00:00Z"), project(6, "另一份研究", "2026-08-25T00:00:00Z")] });
    }));
    show();
    expect(await screen.findByLabelText("待确认研究的研究状态")).toHaveTextContent("待处理");
    await waitFor(() => expect(screen.getByLabelText("另一份研究的研究状态")).toHaveTextContent("状态暂不可用"));
    expect(screen.getByRole("link", { name: /另一份研究/ })).toHaveAttribute("href", `/research/projects/${uid(6)}`);
  });

  it("discards a search response after the user changes the company query", async () => {
    let finish!: (value: Response) => void;
    const response = new Promise<Response>((resolve) => { finish = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("/objects?") ? response : json({ ...schema, items: [] })));
    const user = userEvent.setup();
    show();
    const input = screen.getByLabelText("公司名称或证券代码");
    await user.type(input, "宁德时代");
    await user.click(screen.getByRole("button", { name: "搜索公司" }));
    await user.clear(input);
    await user.type(input, "Alphabet");
    await act(async () => finish(json({ ...schema, items: [company] })));
    expect(screen.queryByRole("link", { name: "研究 宁德时代" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "搜索公司" })).toBeEnabled();
  });

  it("recovers library loading without representing a failed request as an empty library", async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(json({ ...schema, items: [] }));
    vi.stubGlobal("fetch", fetch);
    const user = userEvent.setup();
    show();
    await screen.findByRole("alert");
    expect(screen.queryByText("开始第一份公司研究")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重新加载研究库" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(await screen.findByText("开始第一份公司研究")).toBeVisible();
  });
});
