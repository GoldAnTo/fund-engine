import { act, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HttpGatewayClient } from "@/gateway/HttpGatewayClient";
import type { GatewayConversationSnapshot } from "@/gateway/contracts";
import { GatewayConversationPanel } from "@/workbench/GatewayConversationPanel";
import { GatewayResearchContent } from "@/workbench/GatewayResearchContent";
import { teamWire } from "./teamFixtures";

const cid = "11111111-1111-4111-8111-111111111111";
const rid = "22222222-2222-4222-8222-222222222222";
const tid = "33333333-3333-4333-8333-333333333333";
const eid = "44444444-4444-4444-8444-444444444444";
const thesisId = "55555555-5555-4555-8555-555555555555";
const documentId = "66666666-6666-4666-8666-666666666666";
const at = "2026-09-05T05:00:04Z";
const summary = {
  evidence_link_id: eid, task_id: tid, thesis_id: thesisId,
  thesis_statement: "资本开支与现金流需要核验", statement: "已记录资本开支数据，但不能据此确认当期现金流。",
  document_version_id: documentId, title: "来源材料一", source_authority: "user_supplied",
  source_url: "https://example.com/report", published_at: at, observed_period: "2024 Q2",
  relationship: "supports", review_state: "automatically_admitted",
};
const research = {
  conversation_id: cid, run_spec_id: rid, state: "available", reason_code: null,
  draft_id: "77777777-7777-4777-8777-777777777777",
  result: { label: "系统生成，未经人工审核", human_reviewed: false,
    conclusion: "现有证据不足以确认该现金流结论。", key_findings: ["已找到可追溯的来源材料。"],
    counter_evidence: ["尚无已确认的相反证据。"], limitations: ["数据期间不同，不能直接比较。"],
    sources: [{ title: "来源材料一", url: "https://example.com/report", role: "support", review_state: "automatically_admitted" }],
  }, evidence: [summary], total_evidence: 1, truncated: false, warnings: [],
};
const detail = { ...summary, conversation_id: cid, run_spec_id: rid,
  quote: "原始材料：资本开支增加。", source_span_id: "88888888-8888-4888-8888-888888888888",
  locator: { page: 3, paragraph: 7 }, content_sha256: "a".repeat(64), acquired_at: at,
};
const trace = { conversation_id: cid, run_spec_id: rid, task_id: tid,
  events: [{ sequence: 1, stage: "fetching", status: "running", label: "来源获取阶段", occurred_at: at }],
  exceptions: [{ reason_code: "evidence_not_admitted", message: "部分材料未通过证据准入", next_action: "review_sources", count: 2 }],
  truncated: false,
};
const session = { tenantId: "team-a", subjectId: "alice", roles: ["researcher"] };

const snapshot: GatewayConversationSnapshot = {
  conversationId: cid, title: "财报核验", createdAt: at, updatedAt: at, latestSequence: 5,
  eventRetentionFloor: 1, messages: [], roles: [], events: [],
  runs: [{ runSpecId: rid, nativeCaseId: cid, nativeRunId: rid, status: "succeeded", parentRunSpecId: null, createdAt: at,
    execution: { projectionState: "available", stage: "complete", updatedAt: at, reasonCode: null,
      nextAction: "review_result", workerState: "online", workerLastSeenAt: at, workerScope: "acquisition_service",
      tasks: [{ taskId: tid, taskType: "contradict", status: "partial", stage: "partial", sourceKind: "intake_material",
        sourceName: "用户提供材料", providers: [], attempt: 1, updatedAt: at, retryAt: null,
        counts: { discovered: 1, fetched: 1, frozen: 1, admitted: 1, exceptions: 2 } }],
    } }],
};

type Handler = (path: string, init?: RequestInit) => unknown | Response | Promise<unknown | Response>;
function setup(handler?: Handler, initial = snapshot) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const body = handler ? await handler(path, init) : path.endsWith("/research") ? research : path.endsWith("/trace") ? trace : detail;
    return body instanceof Response ? body : new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  });
  const client = new HttpGatewayClient({ fetch });
  const onAuthorizationLost = vi.fn();
  const props = { client, snapshot: initial, connection: "live" as const, issue: "none" as const, session,
    onRefresh: vi.fn(), onConversationChanged: vi.fn(), onAuthorizationLost };
  const view = render(<GatewayConversationPanel {...props} />);
  return { ...view, fetch, props, onAuthorizationLost };
}

async function openEvidence() {
  await userEvent.setup().click(await screen.findByRole("button", { name: "查看原文：来源材料一" }));
}

beforeEach(() => {
  sessionStorage.clear();
});

it("invalidates cached team output after a source 404 even while team reads keep returning 503", async () => {
  let teamReads = 0;
  const view = setup((path) => {
    if (path.endsWith("/team")) return ++teamReads === 1
      ? { ...teamWire(), conversation_id: cid, run_spec_id: rid }
      : new Response("", { status: 503 });
    if (path.endsWith("/research")) return research;
    return new Response("", { status: 404 });
  });
  view.rerender(<GatewayConversationPanel {...view.props} teamProgressByRun={{}} />);
  await screen.findByText("industry 已保存摘要");
  await userEvent.setup().click(screen.getByRole("button", { name: "重新读取团队" }));
  await screen.findByText(/团队状态待更新，当前显示上次成功读取的内容/);
  await openEvidence();
  await waitFor(() => expect(screen.queryByText("industry 已保存摘要")).not.toBeInTheDocument());
  await waitFor(() => expect(teamReads).toBeGreaterThanOrEqual(3));
  expect(screen.queryByText(/团队状态待更新，当前显示上次成功读取的内容/)).not.toBeInTheDocument();
  expect(screen.getAllByText("状态暂不可用")).toHaveLength(4);
  expect(view.onAuthorizationLost).not.toHaveBeenCalled();
});

it("shares one authorized reader across prototype layout slots and keeps original citation focus", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
    String(input).endsWith("/research") ? research : detail,
  ), { headers: { "Content-Type": "application/json" } }));
  const client = new HttpGatewayClient({ fetch });
  render(<GatewayResearchContent client={client} conversationId={cid} run={snapshot.runs[0]!}
    connection="live" refreshSequence={0} authorizedArtifactsKey="scope-one" onAuthorizationLost={vi.fn()}
    evidenceRoles={{ [eid]: ["分析师-产业"] }}
    renderLayout={({ nativeContent, evidence }) => <div><div>{nativeContent}</div><div>{evidence}</div></div>} />);
  const trigger = await screen.findByRole("button", { name: "查看原文：来源材料一" });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByText("关联角色：分析师-产业")).toBeVisible();
  await userEvent.setup().click(trigger);
  expect(await screen.findByText(detail.quote)).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(2);
  await userEvent.setup().click(screen.getByRole("button", { name: "关闭原文" }));
  expect(trigger).toHaveFocus();
  expect(screen.getByText(research.result.conclusion)).toBeVisible();
});

it("filters role evidence against the current authorized list without loading a second reader", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ ...research, total_evidence: 2,
    evidence: [summary, { ...summary, evidence_link_id: "99999999-9999-4999-8999-999999999999", title: "另一份材料" }],
  }), { headers: { "Content-Type": "application/json" } }));
  const client = new HttpGatewayClient({ fetch });
  const props = { client, conversationId: cid, run: snapshot.runs[0]!, connection: "live" as const,
    refreshSequence: 0, authorizedArtifactsKey: "scope-one", onAuthorizationLost: vi.fn() };
  const layout = ({ evidence }: { evidence: ReactElement }) => <div>{evidence}</div>;
  const view = render(<GatewayResearchContent {...props} renderLayout={layout} />);
  await screen.findByRole("button", { name: "查看原文：另一份材料" });
  view.rerender(<GatewayResearchContent {...props} renderLayout={layout} roleEvidenceRequest={{ key: 1, label: "产业分析师", ids: [eid, "not-authorized"] }} />);
  await waitFor(() => expect(screen.queryByRole("button", { name: "查看原文：另一份材料" })).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "查看原文：来源材料一" })).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole("button", { name: "显示全部论点证据" }));
  expect(screen.getByRole("button", { name: "查看原文：另一份材料" })).toBeVisible();
});

describe("Gateway research reading loop", () => {
  it("allows expanding a medium length title that can wrap beyond two lines on mobile", async () => {
    const title = "研究中国新能源产业链各环节的竞争格局以及公司现金流与资本支出变化对未来业务发展的影响";
    setup(undefined, { ...snapshot, title });
    const toggle = screen.getByRole("button", { name: "展开完整请求" });
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
    await userEvent.setup().click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
  it("keeps the running process visible while reading evidence in the permanent aside and returns focus", async () => {
    const view = setup(undefined, { ...snapshot, runs: [{ ...snapshot.runs[0]!, status: "running" }] });
    const aside = screen.getByRole("complementary", { name: "证据与来源" });
    const trigger = await within(aside).findByRole("button", { name: "查看原文：来源材料一" });
    await userEvent.setup().click(trigger);
    expect(await within(aside).findByText(detail.quote)).toBeVisible();
    expect(screen.getByRole("heading", { name: "运行进度" })).toBeVisible();
    expect(screen.queryByRole("tab", { name: /证据与来源/ })).not.toBeInTheDocument();
    await userEvent.setup().click(trigger);
    expect(within(aside).getByRole("heading", { name: "证据原文" })).toHaveFocus();
    expect(view.fetch).toHaveBeenCalledTimes(2);
    await userEvent.setup().click(within(aside).getByRole("button", { name: "关闭原文" }));
    expect(trigger).toHaveFocus();
  });
  it("defaults an ended run to the actual unreviewed result with a direct evidence bridge", async () => {
    const view = setup();
    expect(await screen.findByText(research.result.conclusion)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "研究结果" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("系统生成，未经人工审核")).toBeInTheDocument();
    expect(screen.getByText(research.result.key_findings[0]!)).toBeInTheDocument();
    expect(screen.getByText(/检索方向不等于已证实的反证/)).toBeInTheDocument();
    expect(screen.getByText(/请复核来源、数据期与口径/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "查看证据 / 原文溯源" }));
    expect(screen.getByRole("heading", { name: summary.thesis_statement })).toBeInTheDocument();
    expect(screen.getByText(/不代表逐句引用映射/)).toBeInTheDocument();
    expect(view.fetch).toHaveBeenCalledTimes(1);
    expect(String(view.fetch.mock.calls[0]?.[0])).toBe(`/api/v1/research-conversations/${cid}/runs/${rid}/research`);
  });

  it("opens task evidence without leaving process and clears the explicit filter", async () => {
    setup();
    await screen.findByText(research.result.conclusion);
    await userEvent.setup().click(screen.getByRole("tab", { name: "执行过程" }));
    const trigger = screen.getByRole("button", { name: "查看本任务证据（1）" });
    const central = screen.getByRole("tab", { name: "执行过程" }).closest(".research-desk-central")!;
    central.scrollTop = 120;
    await userEvent.setup().click(trigger);
    expect(await screen.findByText(detail.quote)).toBeVisible();
    expect(screen.getByRole("heading", { name: "运行进度" })).toBeVisible();
    expect(central.scrollTop).toBe(120);
    expect(screen.getByText(/仅显示所选任务关联证据/)).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "关闭原文" }));
    expect(trigger).toHaveFocus();
    await userEvent.setup().click(screen.getByRole("button", { name: "显示全部论点证据" }));
    expect(screen.queryByText(/仅显示所选任务关联证据/)).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: "研究结果" }));
    expect(central.scrollTop).toBe(0);
  });

  it("labels evidence with no current task and explains an empty evidence list", async () => {
    setup((path) => path.endsWith("/research") ? research : detail, { ...snapshot, runs: [{ ...snapshot.runs[0]!, execution: undefined }] });
    expect(await screen.findByText("没有可定位的当前资料任务")).toBeVisible();
  });

  it("loads exact quotes lazily and shows authority, publication/observation dates and locators", async () => {
    const view = setup();
    await screen.findByText(research.result.conclusion);
    expect(view.fetch).toHaveBeenCalledTimes(1);
    await openEvidence();
    const panel = await screen.findByRole("region", { name: "证据原文" });
    expect(within(panel).getByText(detail.quote)).toBeInTheDocument();
    expect(within(panel).getByText(/用户材料.*待独立核验/)).toBeInTheDocument();
    expect(within(panel).getByText("2024 Q2")).toBeInTheDocument();
    expect(within(panel).getByText(/第 3 页/)).toBeInTheDocument();
    expect(within(panel).getByText(/解析器段落编号：7/)).toBeInTheDocument();
    expect(within(panel).getByText(detail.content_sha256)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "打开原始来源" })).toHaveAttribute("href", summary.source_url);
    expect(String(view.fetch.mock.calls[1]?.[0])).toBe(`/api/v1/research-conversations/${cid}/runs/${rid}/evidence/${eid}`);
  });

  it("expands task history on demand and links its own evidence without relabelling a search direction as fact", async () => {
    const view = setup();
    await userEvent.setup().click(await screen.findByRole("tab", { name: "执行过程" }));
    expect(screen.queryByText(trace.events[0]!.label)).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "展开任务历史" }));
    expect(await screen.findByText(trace.events[0]!.label)).toBeInTheDocument();
    expect(screen.getByText(trace.exceptions[0]!.message)).toBeInTheDocument();
    expect(screen.getByText("复核来源材料与研究范围，必要时补充材料并创建新一轮。")).toBeInTheDocument();
    expect(screen.getByText(/发生 2 次/)).toBeInTheDocument();
    expect(String(view.fetch.mock.calls.at(-1)?.[0])).toBe(`/api/v1/research-conversations/${cid}/runs/${rid}/tasks/${tid}/trace`);
    await userEvent.setup().click(screen.getByRole("button", { name: "查看本任务证据（1）" }));
    expect(screen.getByRole("heading", { name: summary.thesis_statement })).toBeInTheDocument();
    expect(screen.queryByText("已证实反证")).not.toBeInTheDocument();
  });

  it.each([
    ["pending", "result_pending", "研究结果尚未生成"],
    ["withheld", "result_not_authorized", "当前结果暂不可展示"],
    ["failed", "execution_failed", "本轮执行失败"],
    ["cancelled", "execution_cancelled", "本轮执行已取消"],
  ])("explains %s without inventing a conclusion and offers read-only retry", async (state, reason, copy) => {
    setup(() => ({ ...research, state, reason_code: reason, result: null, draft_id: null }));
    expect(await screen.findByText(copy)).toBeInTheDocument();
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取研究内容" })).toBeInTheDocument();
  });

  it("recovers from a disconnected read without starting another research run", async () => {
    let attempts = 0;
    const view = setup(() => { if (++attempts === 1) throw new TypeError("network down"); return research; });
    expect(await screen.findByText(/研究内容暂时无法读取/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新读取研究内容" }));
    expect(await screen.findByText(research.result.conclusion)).toBeInTheDocument();
    expect(view.fetch.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });

  it("contains a synchronous missing-client-method throw and permits retry instead of staying loading", async () => {
    const view = setup();
    const getResearch = view.props.client.getResearch;
    // A retained older client can lack the newly added method. This exercises
    // a synchronous throw at the real reader call site, not a rejected fetch.
    view.props.client.getResearch = undefined as unknown as typeof getResearch;
    expect(await screen.findByText(/研究内容暂时无法读取/)).toBeInTheDocument();
    expect(screen.queryByText("正在读取研究内容…")).not.toBeInTheDocument();
    expect(view.fetch).not.toHaveBeenCalled();
    view.props.client.getResearch = getResearch;
    await userEvent.setup().click(screen.getByRole("button", { name: "重新读取研究内容" }));
    expect(await screen.findByText(research.result.conclusion)).toBeInTheDocument();
    expect(view.fetch).toHaveBeenCalledOnce();
  });

  it.each(["conversation_id", "run_spec_id"])("rejects a research response for the wrong %s", async (field) => {
    setup(() => ({ ...research, [field]: documentId }));
    expect(await screen.findByText(/研究内容校验未通过/)).toBeInTheDocument();
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
  });

  it.each(["conversation_id", "run_spec_id", "evidence_link_id", "task_id"])("rejects detail with a mismatched %s", async (field) => {
    setup((path) => path.endsWith("/research") ? research : { ...detail, [field]: documentId });
    await openEvidence();
    expect(await screen.findByText(/证据原文校验未通过/)).toBeInTheDocument();
    expect(screen.queryByText(detail.quote)).not.toBeInTheDocument();
  });

  it("rejects task history from a different task", async () => {
    setup((path) => path.endsWith("/research") ? research : { ...trace, task_id: documentId });
    await userEvent.setup().click(await screen.findByRole("tab", { name: "执行过程" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "展开任务历史" }));
    expect(await screen.findByText(/任务历史校验未通过/)).toBeInTheDocument();
    expect(screen.queryByText(trace.events[0]!.label)).not.toBeInTheDocument();
  });

  it("aborts old detail on run changes and ignores its late response", async () => {
    let resolveDetail!: (value: unknown) => void;
    let detailSignal: AbortSignal | null | undefined;
    const nextRun = "99999999-9999-4999-8999-999999999999";
    const view = setup((path, init) => {
      if (path.includes(nextRun)) return { ...research, run_spec_id: nextRun, state: "pending", reason_code: "result_pending", result: null, draft_id: null, evidence: [], total_evidence: 0 };
      if (path.endsWith("/research")) return research;
      detailSignal = init?.signal;
      return new Promise((resolve) => { resolveDetail = resolve; });
    });
    await openEvidence();
    await waitFor(() => expect(resolveDetail).toBeDefined());
    view.rerender(<GatewayConversationPanel {...view.props} snapshot={{ ...snapshot, runs: [{ ...snapshot.runs[0]!, runSpecId: nextRun }] }} />);
    expect(screen.queryByText(summary.statement)).not.toBeInTheDocument();
    expect(detailSignal?.aborted).toBe(true);
    await act(async () => resolveDetail(detail));
    expect(screen.queryByText(detail.quote)).not.toBeInTheDocument();
    expect(await screen.findByText("研究结果尚未生成")).toBeInTheDocument();
  });

  it("clears private content on revocation and cannot be repopulated by an in-flight detail", async () => {
    let resolveDetail!: (value: unknown) => void;
    const view = setup((path) => path.endsWith("/research") ? research : new Promise((resolve) => { resolveDetail = resolve; }));
    await openEvidence();
    view.rerender(<GatewayConversationPanel {...view.props} snapshot={null} issue="access_revoked" connection="access_revoked" />);
    await act(async () => resolveDetail(detail));
    expect(screen.getByText(/已清除当前私有研究内容/)).toBeInTheDocument();
    expect(screen.queryByText(detail.quote)).not.toBeInTheDocument();
    expect(screen.queryByText(summary.thesis_statement)).not.toBeInTheDocument();
  });

  it("notifies the identity boundary and clears the reader on a detail 403", async () => {
    const view = setup((path) => path.endsWith("/research") ? research : new Response("", { status: 403 }));
    await openEvidence();
    await waitFor(() => expect(view.onAuthorizationLost).toHaveBeenCalledOnce());
    expect(screen.queryByText(summary.statement)).not.toBeInTheDocument();
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
  });

  it.each(["primary_disclosure", "official_disclosure"])("labels %s as official in both summary and original", async (sourceAuthority) => {
    setup((path) => path.endsWith("/research") ? { ...research, evidence: [{ ...summary, source_authority: sourceAuthority }] }
      : { ...detail, source_authority: sourceAuthority });
    expect(await screen.findByText("官方披露 · 仍需核对期间与口径")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "查看原文：来源材料一" }));
    expect(await screen.findByText(detail.quote)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "证据原文" })).getByText(/官方披露 · 仍需核对期间与口径/)).toBeInTheDocument();
    expect(screen.queryByText("未确认来源类别")).not.toBeInTheDocument();
  });

  it.each(["withheld", "remaining"])("withdraws stale content on a detail 404, then revalidates %s without signing out", async (outcome) => {
    let finishDetail!: (value: Response) => void;
    let revalidate!: (value: unknown) => void;
    let researchReads = 0;
    const view = setup((path) => {
      if (!path.endsWith("/research")) return new Promise<Response>((resolve) => { finishDetail = resolve; });
      if (++researchReads === 1) return research;
      return new Promise((resolve) => { revalidate = resolve; });
    });
    await openEvidence();
    await waitFor(() => expect(finishDetail).toBeDefined());
    expect(screen.getByText(summary.statement)).toBeInTheDocument();
    await act(async () => finishDetail(new Response("", { status: 404 })));
    expect(await screen.findByText(/来源内容不可用，已隐藏本轮旧内容并重新校验访问权限/)).toBeInTheDocument();
    expect(screen.queryByText(summary.statement)).not.toBeInTheDocument();
    expect(screen.queryByText(detail.quote)).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: "研究结果" }));
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: "执行过程" }));
    expect(screen.queryByRole("heading", { name: "运行进度" })).not.toBeInTheDocument();
    await waitFor(() => expect(revalidate).toBeDefined());
    const remaining = { ...summary, evidence_link_id: "99999999-9999-4999-8999-999999999999", title: "仍获授权的材料", statement: "重新授权读取后的剩余证据。" };
    await act(async () => revalidate(outcome === "withheld" ? { ...research, state: "withheld", reason_code: "result_not_authorized", draft_id: null, result: null, evidence: [], total_evidence: 0 }
      : { ...research, result: { ...research.result, conclusion: "重新校验后可展示的结果。" }, evidence: [remaining] }));
    await userEvent.setup().click(screen.getByRole("tab", { name: "研究结果" }));
    expect(await screen.findByText(outcome === "withheld" ? "当前结果暂不可展示" : "重新校验后可展示的结果。")).toBeInTheDocument();
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
    expect(screen.queryByText(summary.statement)).not.toBeInTheDocument();
    if (outcome === "remaining") expect(screen.getByText(remaining.statement)).toBeInTheDocument();
    expect(view.onAuthorizationLost).not.toHaveBeenCalled();
    expect(researchReads).toBe(2);
  });

  it("withdraws the entire reader on task history 404 and keeps old content hidden across tabs during slow revalidation", async () => {
    let finishTrace!: (value: Response) => void;
    let revalidate!: (value: unknown) => void;
    let researchReads = 0;
    const view = setup((path) => {
      if (path.endsWith("/trace")) return new Promise<Response>((resolve) => { finishTrace = resolve; });
      if (++researchReads === 1) return research;
      return new Promise((resolve) => { revalidate = resolve; });
    });
    expect(await screen.findByText(research.result.conclusion)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("tab", { name: "执行过程" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "展开任务历史" }));
    await waitFor(() => expect(finishTrace).toBeDefined());
    await act(async () => finishTrace(new Response("", { status: 404 })));
    expect(await screen.findByText(/来源内容不可用，已隐藏本轮旧内容并重新校验访问权限/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "运行进度" })).not.toBeInTheDocument();
    await waitFor(() => expect(revalidate).toBeDefined());
    for (const tab of ["研究结果", "执行过程", "研究结果"]) {
      await userEvent.setup().click(screen.getByRole("tab", { name: tab }));
      expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
      expect(screen.queryByText(summary.statement)).not.toBeInTheDocument();
      expect(screen.queryByText(trace.events[0]!.label)).not.toBeInTheDocument();
    }
    await act(async () => revalidate({ ...research, state: "withheld", reason_code: "result_not_authorized", draft_id: null, result: null, evidence: [], total_evidence: 0 }));
    expect(await screen.findByText("当前结果暂不可展示")).toBeInTheDocument();
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
    expect(view.onAuthorizationLost).not.toHaveBeenCalled();
    expect(researchReads).toBe(2);
  });

  it("does not abort a slow open detail during ordinary report refreshes under the same authorization", async () => {
    let finishDetail!: (value: unknown) => void;
    let detailSignal: AbortSignal | null | undefined;
    let detailReads = 0;
    let researchReads = 0;
    const view = setup((path, init) => {
      if (path.endsWith("/research")) { researchReads += 1; return research; }
      detailReads += 1;
      detailSignal = init?.signal;
      return new Promise((resolve) => { finishDetail = resolve; });
    });
    await openEvidence();
    await waitFor(() => expect(finishDetail).toBeDefined());
    const firstSignal = detailSignal;
    const run = snapshot.runs[0]!;
    for (let admitted = 2; admitted <= 4; admitted += 1) {
      view.rerender(<GatewayConversationPanel {...view.props} snapshot={{ ...snapshot, runs: [{ ...run,
        execution: { ...run.execution!, tasks: [{ ...run.execution!.tasks[0]!, counts: { ...run.execution!.tasks[0]!.counts, admitted } }] },
      }] }} />);
      await waitFor(() => expect(researchReads).toBe(admitted));
    }
    expect(firstSignal?.aborted).toBe(false);
    expect(detailReads).toBe(1);
    await act(async () => finishDetail(detail));
    expect(await screen.findByText(detail.quote)).toBeInTheDocument();
  });

  it("renders quote/report markup as text and does not link unsafe source URLs", async () => {
    const markup = '<img src=x onerror="alert(1)">';
    setup((path) => path.endsWith("/research") ? { ...research,
      result: { ...research.result, conclusion: markup, sources: [{ ...research.result.sources[0], url: "javascript:alert(1)" }] },
      evidence: [{ ...summary, source_url: "javascript:alert(1)" }],
    } : { ...detail, quote: markup, source_url: "javascript:alert(1)" });
    expect(await screen.findByText(markup)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    await openEvidence();
    expect(await screen.findByRole("region", { name: "证据原文" })).toHaveTextContent(markup);
    expect(screen.queryByRole("link", { name: "打开原始来源" })).not.toBeInTheDocument();
    expect(document.querySelector('[href^="javascript:"]')).toBeNull();
  });

  it("keeps active progress visible, skips heartbeat-only reads and coalesces substantive updates", async () => {
    const active = { ...snapshot, runs: [{ ...snapshot.runs[0]!, status: "running" as const }] };
    const view = setup(undefined, active);
    expect(screen.getByRole("tab", { name: "执行过程" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "运行进度" })).toBeInTheDocument();
    await screen.findByRole("button", { name: "查看原文：来源材料一" });
    const run = active.runs[0]!;
    view.rerender(<GatewayConversationPanel {...view.props} snapshot={{ ...active, runs: [{ ...run,
      execution: { ...run.execution!, workerLastSeenAt: "2026-09-05T05:00:05Z", updatedAt: "2026-09-05T05:00:05Z",
        tasks: [{ ...run.execution!.tasks[0]!, counts: { ...run.execution!.tasks[0]!.counts, discovered: 4, fetched: 4, frozen: 4 } }],
      },
    }] }} />);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(view.fetch).toHaveBeenCalledTimes(1);
    for (let admitted = 2; admitted <= 4; admitted += 1) {
      view.rerender(<GatewayConversationPanel {...view.props} snapshot={{ ...active, runs: [{ ...run,
        execution: { ...run.execution!, tasks: [{ ...run.execution!.tasks[0]!, counts: { ...run.execution!.tasks[0]!.counts, admitted } }] },
      }] }} />);
    }
    await waitFor(() => expect(view.fetch).toHaveBeenCalledTimes(2));
  });

  it("finishes a slow authorized read before coalescing incoming progress into one follow-up", async () => {
    let finish!: (value: unknown) => void;
    let firstSignal: AbortSignal | null | undefined;
    let calls = 0;
    const active = { ...snapshot, runs: [{ ...snapshot.runs[0]!, status: "running" as const }] };
    const view = setup((_path, init) => {
      if (++calls > 1) return research;
      firstSignal = init?.signal;
      return new Promise((resolve) => { finish = resolve; });
    }, active);
    await waitFor(() => expect(finish).toBeDefined());
    const run = active.runs[0]!;
    for (let admitted = 2; admitted <= 4; admitted += 1) {
      view.rerender(<GatewayConversationPanel {...view.props} snapshot={{ ...active, runs: [{ ...run,
        execution: { ...run.execution!, tasks: [{ ...run.execution!.tasks[0]!, counts: { ...run.execution!.tasks[0]!.counts, admitted } }] },
      }] }} />);
    }
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    expect(firstSignal?.aborted).toBe(false);
    expect(view.fetch).toHaveBeenCalledTimes(1);
    await act(async () => finish(research));
    expect(await screen.findByRole("button", { name: "查看原文：来源材料一" })).toBeInTheDocument();
    await waitFor(() => expect(view.fetch).toHaveBeenCalledTimes(2));
  });

  it("preserves parser paragraph zero without assuming an index base or upgrading a user-claimed page reference", async () => {
    setup((path) => path.endsWith("/research") ? research : { ...detail, locator: { page: 1, paragraph: 0 } });
    await openEvidence();
    expect(await screen.findByText(/冻结材料第 1 页/)).toBeInTheDocument();
    expect(screen.getByText(/解析器段落编号：0/)).toBeInTheDocument();
    expect(screen.queryByText(/0 起/)).not.toBeInTheDocument();
    expect(screen.getByText(/不是材料文字声称的报告页码/)).toBeInTheDocument();
  });

  it("clears a revealed quote immediately when authorized artifacts change without a sequence advance", async () => {
    const event = { sequence: 5, runSpecId: rid, role: "sources_evidence" as const, type: "evidence_available" as const,
      status: "completed" as const, summary: null, reasonCode: null, artifacts: [{ kind: "evidence_link" as const, id: eid }], occurredAt: at };
    const view = setup(undefined, { ...snapshot, events: [event] });
    await openEvidence();
    expect(await screen.findByText(detail.quote)).toBeInTheDocument();
    view.rerender(<GatewayConversationPanel {...view.props} snapshot={{ ...snapshot, events: [{ ...event, artifacts: [] }] }} />);
    expect(screen.queryByText(detail.quote)).not.toBeInTheDocument();
    expect(screen.queryByText(summary.statement)).not.toBeInTheDocument();
  });

  it("returns focus to the evidence trigger after closing the original", async () => {
    setup();
    await openEvidence();
    await screen.findByText(detail.quote);
    await userEvent.setup().click(screen.getByRole("button", { name: "关闭原文" }));
    expect(screen.getByRole("button", { name: "查看原文：来源材料一" })).toHaveFocus();
  });

  it.each([
    { ...research, draft_id: null },
    { ...research, state: "pending", reason_code: "result_pending", result: null },
    { ...research, result: { ...research.result, human_reviewed: true } },
    { ...research, evidence: [{ ...summary, raw_payload: "unapproved" }] },
    { ...research, total_evidence: 0 },
  ])("rejects inconsistent result authorization and unapproved DTO fields %#", async (wire) => {
    setup(() => wire);
    expect(await screen.findByText(/研究内容校验未通过/)).toBeInTheDocument();
    expect(screen.queryByText(research.result.conclusion)).not.toBeInTheDocument();
  });

  it.each([
    { ...trace, events: Array.from({ length: 101 }, (_, sequence) => ({ ...trace.events[0], sequence })) },
    { ...trace, exceptions: Array.from({ length: 7 }, () => trace.exceptions[0]) },
    { ...trace, exceptions: [{ ...trace.exceptions[0], reason_code: "provider_private_exception" }] },
    { ...trace, exceptions: [{ ...trace.exceptions[0], next_action: "provider_private_command" }] },
    { ...trace, events: [{ ...trace.events[0], label: "provider_private_text" }] },
    { ...trace, events: [{ ...trace.events[0], stage: "provider_private_stage" }] },
  ])("rejects oversized or non-contract task traces %#", async (wire) => {
    setup((path) => path.endsWith("/research") ? research : wire);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "执行过程" }));
    await userEvent.setup().click(screen.getByRole("button", { name: "展开任务历史" }));
    expect(await screen.findByText(/任务历史校验未通过/)).toBeInTheDocument();
  });
});
