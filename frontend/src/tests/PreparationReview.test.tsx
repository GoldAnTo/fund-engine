import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ResearchActions } from "@/workbench/ResearchActions";

const outcome = { thesis_id: "10000000-0000-4000-8000-000000000001", metric: { metric_id: "sales", display_name: "销售额", canonical_definition: "季度销售额", entity_scope: "company", unit: "元", frequency: "quarterly", period_semantics: "季度", allowed_source_roles: ["official"], role_eligibility: ["outcome"] }, binding: { entity_scope: { company_id: "10000000-0000-4000-8000-000000000002", company: "测试公司" }, direction: "increase", baseline: { source_ref: "document:10000000-0000-4000-8000-000000000003", value: "100", unit: "元", observed_period: "2025-12-31", available_at: "2026-01-01T00:00:00Z" }, horizon_start: "2026-01-01", horizon_end: "2026-12-31" }, template_version_id: "10000000-0000-4000-8000-000000000004", verification_rules: [{ mechanism_edge_id: "10000000-0000-4000-8000-000000000005", expected_direction: "increase", support_predicate: "增长", contradiction_predicate: "下降", allowed_source_roles: ["official"], observed_period_start: "2026-01-01", observed_period_end: "2026-12-31", available_at_deadline: "2027-01-31", next_verification_event: "年报" }] };
const protocol = { outcomes: [outcome], baseline: { description: "去年基线" }, horizon: { start: "2026-01-01", end: "2026-12-31" }, mechanisms: [{ description: "需求传导" }], verification_rules: [{ description: "对照年报" }] };
const prep = (stage = "claims", withheld = false) => ({ case_id: "a", status: stage === "claims" ? "awaiting_claim_review" : "awaiting_protocol_confirmation", revision: 8, research_run_id: null, system: {}, review: { claims: { state: stage === "claims" ? "awaiting_review" : "confirmed" }, protocol: { state: stage === "protocol" ? "awaiting_review" : "locked" } }, progress: { completed_steps: 2, total_steps: 3 }, artifacts: { [stage]: { sequence: 12, state: "current", display_withheld: withheld, context_fingerprint: "source-context-123", payload: stage === "claims" ? { candidates: [{ candidate_id: "c1" }] } : protocol } } });
const candidate = { id: "c1", normalized_text: "收入增长", quote: "公告原文：收入增长10%", document_version_id: "doc1", source_span_id: "span1", document_source_url: "https://example.com/report", locator: { page: 3 }, claim_type: "fact", authority_level: "official", assertion_actor: "公司", structured_fields: {}, validation_result: {}, review_state: "awaiting_review", review_history: [] };
const reply = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status });
function setup(value = prep(), candidates = [candidate], postStatus = 200) {
  const posts: Array<{ url: string; body: Record<string, unknown> }> = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
    if (init.method === "POST") { posts.push({ url, body: JSON.parse(init.body as string) }); return Promise.resolve(reply(value, postStatus)); }
    return Promise.resolve(reply(url.endsWith("/preparation") ? value : url.includes("atomic-claims") ? { items: candidates } : { items: [], has_more: false, next_cursor: null }));
  }));
  render(<ResearchActions caseId="a" published={false} hasPreparation onRefresh={() => {}} />);
  return posts;
}
afterEach(() => vi.unstubAllGlobals());
it("requires an explicit sourced claim decision and reason, submits the displayed revision", async () => {
  const posts = setup();
  expect(await screen.findByText(candidate.quote)).toBeInTheDocument();
  expect(screen.getByText(/doc1/)).toBeInTheDocument();
  const submit = screen.getByRole("button", { name: "提交陈述审核" });
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  expect(submit).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText("陈述 1 审核决定"), "modified");
  await userEvent.type(screen.getByLabelText("陈述 1 审核理由"), "保留原文数值");
  await userEvent.clear(screen.getByLabelText("陈述 1 修订内容"));
  expect(submit).toBeDisabled();
  await userEvent.type(screen.getByLabelText("陈述 1 修订内容"), "收入增长10%");
  await userEvent.click(submit);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/v1/event-research/a/preparation/claims/confirm", body: { revision: 8, actor: "审核员", decisions: [{ candidate_id: "c1", outcome: "modified", reason: "保留原文数值", normalized_text: "收入增长10%" }] } });
});
it("blocks withheld claims without reading or showing their source contents", async () => {
  setup(prep("claims", true));
  expect(await screen.findByText(/陈述内容暂不可展示/)).toBeInTheDocument();
  expect(screen.queryByText(candidate.quote)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "提交陈述审核" })).not.toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("atomic-claims"))).toBe(false);
});
it("blocks an artifact whose source candidates cannot all be loaded", async () => {
  setup(prep(), []);
  expect(await screen.findByText(/候选陈述未完整加载/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "提交陈述审核" })).not.toBeInTheDocument();
});
it("edits a complete readable protocol with explicit acknowledgment and its source sequence", async () => {
  const posts = setup(prep("protocol"));
  const metric = await screen.findByLabelText("研究结果 1 指标 显示名称");
  expect(metric).toHaveValue("销售额");
  expect(screen.getByText(/source-context-123/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  const submit = screen.getByRole("button", { name: "确认研究协议" });
  expect(submit).toBeDisabled();
  await userEvent.clear(metric); await userEvent.type(metric, "营业收入");
  await userEvent.click(screen.getByLabelText("我已核对研究协议全部字段及来源范围"));
  await userEvent.click(submit);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({ url: "/api/v1/event-research/a/preparation/protocol/confirm", body: { actor: "审核员", revision: 8, draft_sequence: 12, edits: { outcomes: [{ ...outcome, metric: { ...outcome.metric, display_name: "营业收入" } }] } } });
});
it("retains protocol edits on conflict and never advances to authorization", async () => {
  setup(prep("protocol"), [], 409);
  const metric = await screen.findByLabelText("研究结果 1 指标 显示名称");
  await userEvent.clear(metric); await userEvent.type(metric, "修订名称");
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  await userEvent.click(screen.getByLabelText("我已核对研究协议全部字段及来源范围"));
  await userEvent.click(screen.getByRole("button", { name: "确认研究协议" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("研究状态已变化");
  expect(metric).toHaveValue("修订名称");
  expect(screen.queryByRole("button", { name: "授权计划并启动研究" })).not.toBeInTheDocument();
});
it("does not expose a withheld protocol editor", async () => {
  setup(prep("protocol", true));
  await screen.findByText(/已审协议暂不可展示/);
  expect(screen.queryByRole("button", { name: "确认研究协议" })).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue("销售额")).not.toBeInTheDocument();
});
it("blocks missing formal outcome fields instead of acknowledging an opaque draft", async () => {
  const value = prep("protocol");
  value.artifacts.protocol!.payload = { ...protocol, outcomes: [{ thesis_id: "thesis-1" }] } as typeof protocol;
  setup(value);
  expect(await screen.findByText(/协议草稿缺少必需内容/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认研究协议" })).toBeDisabled();
});
it("blocks an invalid protocol horizon and requires rechecking after an edit", async () => {
  setup(prep("protocol"));
  await screen.findByLabelText("研究期限 结束日期");
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  await userEvent.click(screen.getByLabelText("我已核对研究协议全部字段及来源范围"));
  const end = screen.getByLabelText("研究期限 结束日期");
  await userEvent.clear(end); await userEvent.type(end, "2025-01-01");
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).not.toBeChecked();
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).toBeDisabled();
  expect(screen.getByRole("button", { name: "确认研究协议" })).toBeDisabled();
});
it("preserves numeric baseline values after clearing and replacing them", async () => {
  const value = prep("protocol");
  const numericProtocol = { ...protocol, baseline: { description: "基线", value: 100 } };
  value.artifacts.protocol!.payload = numericProtocol;
  const posts = setup(value);
  const baseline = await screen.findByLabelText("基线 数值");
  await userEvent.clear(baseline); await userEvent.type(baseline, "120");
  await userEvent.type(screen.getByLabelText("操作人"), "审核员");
  await userEvent.click(screen.getByLabelText("我已核对研究协议全部字段及来源范围"));
  await userEvent.click(screen.getByRole("button", { name: "确认研究协议" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]?.body.edits).toEqual({ baseline: { description: "基线", value: 120 } });
});

it.each(["source_ref", "value", "unit", "observed_period", "available_at"])("blocks missing required baseline field %s", async (field) => {
  const value = prep("protocol");
  const invalid = structuredClone(protocol);
  delete (invalid.outcomes[0]!.binding.baseline as Record<string, unknown>)[field];
  value.artifacts.protocol!.payload = invalid;
  setup(value);
  await screen.findByRole("button", { name: "确认研究协议" });
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).toBeDisabled();
});
it.each(["company_id", "company"])("blocks a missing entity scope identifier %s", async (field) => {
  const value = prep("protocol");
  const invalid = structuredClone(protocol);
  delete (invalid.outcomes[0]!.binding.entity_scope as Record<string, unknown>)[field];
  value.artifacts.protocol!.payload = invalid;
  setup(value);
  await screen.findByRole("button", { name: "确认研究协议" });
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).toBeDisabled();
});
it("blocks mismatched baseline units", async () => {
  const value = prep("protocol");
  const invalid = structuredClone(protocol);
  invalid.outcomes[0]!.binding.baseline.unit = "美元";
  value.artifacts.protocol!.payload = invalid;
  setup(value);
  await screen.findByRole("button", { name: "确认研究协议" });
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).toBeDisabled();
});
it.each(["abc", "2026-01-01T00:00:00"])("blocks baseline availability without a valid timezone timestamp: %s", async (availableAt) => {
  const value = prep("protocol");
  const invalid = structuredClone(protocol);
  invalid.outcomes[0]!.binding.baseline.available_at = availableAt;
  value.artifacts.protocol!.payload = invalid;
  setup(value);
  await screen.findByRole("button", { name: "确认研究协议" });
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).toBeDisabled();
});
it.each([0, 120])("accepts a numeric outcome baseline value %s", async (baselineValue) => {
  const value = prep("protocol");
  const valid = structuredClone(protocol);
  (valid.outcomes[0]!.binding.baseline as Record<string, unknown>).value = baselineValue;
  value.artifacts.protocol!.payload = valid;
  setup(value);
  await screen.findByRole("button", { name: "确认研究协议" });
  expect(screen.getByLabelText("我已核对研究协议全部字段及来源范围")).toBeEnabled();
});
