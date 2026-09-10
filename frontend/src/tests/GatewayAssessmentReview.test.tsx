import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HttpGatewayClient } from "@/gateway/HttpGatewayClient";
import { parseResearchContent } from "@/gateway/researchContent";
import { GatewayResearchContent } from "@/workbench/GatewayResearchContent";

const uid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const cid = uid(1), rid = uid(2), eid = uid(3), thesis = uid(4);
const at = "2026-09-05T05:00:00Z";
function fixture() {
  return {
    conversation_id: cid, run_spec_id: rid, state: "available", reason_code: null, draft_id: uid(5),
    result: { label: "系统生成，未经人工审核", human_reviewed: false, conclusion: "原始报告保持不变。Link 9 不是引用映射。",
      key_findings: ["原始发现"], counter_evidence: [], limitations: ["原始局限"], sources: [] },
    evidence: [{ evidence_link_id: eid, task_id: uid(6), thesis_id: thesis, thesis_statement: "现金流核验",
      statement: "2024 年现金流数据。", document_version_id: uid(7), title: "用户材料甲", source_authority: "user_supplied",
      source_url: null, published_at: at, observed_period: "2024-12-31", relationship: "supports", review_state: "automatically_admitted" }],
    total_evidence: 1, truncated: false, warnings: [],
    assessment_review: { method_version: "gateway-assessment-review/v1", state: "available", reason_code: null,
      items: [{ assessment_id: uid(8), snapshot_id: uid(9), task_id: uid(10), thesis_id: thesis, thesis_statement: "现金流核验",
        conclusion: "supported", rationale: "原始 AI 理由，不等于人工审核。", gaps: ["仍缺独立原始披露"], evidence_link_ids: [eid],
        quality_flags: ["user_material_only", "no_primary_disclosure", "source_independence_unverified", "retrieval_direction_unverified"] }],
      quality_flags: ["user_material_only", "no_primary_disclosure", "source_independence_unverified", "retrieval_direction_unverified"] },
  };
}
const parse = (body: unknown) => parseResearchContent(body, { conversationId: cid, runSpecId: rid });
function mount(body: unknown = fixture(), detailStatus = 200) {
  const evidence = fixture().evidence[0]!;
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/research")
    ? new Response(JSON.stringify(body))
    : new Response(JSON.stringify({ ...evidence, conversation_id: cid, run_spec_id: rid,
      quote: "冻结原文：现金流金额。", source_span_id: uid(11), locator: { page: 1, paragraph: 0 },
      content_sha256: "a".repeat(64), acquired_at: at }), { status: detailStatus }));
  const props = { client: new HttpGatewayClient({ fetch }), conversationId: cid,
    run: { runSpecId: rid, nativeCaseId: uid(12), nativeRunId: uid(13), status: "succeeded" as const,
      parentRunSpecId: null, createdAt: at }, connection: "live" as const, refreshSequence: 1,
    authorizedArtifactsKey: "authorized-v1", onAuthorizationLost: vi.fn() };
  return { ...render(<GatewayResearchContent {...props} />), props, fetch };
}

describe("assessment review contract", () => {
  it("parses the actual snapshot inputs without interpreting report Link numbers", () => {
    const content = parse(fixture());
    expect(content).toHaveProperty("assessmentReview.items.0.evidenceLinkIds", [eid]);
    expect(content.result?.conclusion).toBe(fixture().result.conclusion);
  });
  it("accepts an omitted field from older servers and explicit null", () => {
    const { assessment_review: _review, ...legacy } = fixture();
    expect(parse(legacy)).toHaveProperty("assessmentReview", null);
    expect(parse({ ...legacy, assessment_review: null })).toHaveProperty("assessmentReview", null);
  });
  it.each(["foreign evidence", "different thesis", "duplicate input", "duplicate item", "missing input", "unknown flag", "unknown version", "empty items", "partial union", "extra field", "wrong statement"])("rejects %s instead of inventing provenance", (variant) => {
    const body = fixture(), item = body.assessment_review.items[0]!;
    if (variant === "foreign evidence") item.evidence_link_ids = [uid(99)];
    if (variant === "different thesis") item.thesis_id = uid(99);
    if (variant === "duplicate input") item.evidence_link_ids.push(eid);
    if (variant === "duplicate item") body.assessment_review.items.push({ ...item });
    if (variant === "missing input") item.evidence_link_ids = [];
    if (variant === "unknown flag") item.quality_flags.push("verified_true");
    if (variant === "unknown version") body.assessment_review.method_version = "v2";
    if (variant === "empty items") body.assessment_review.items = [];
    if (variant === "partial union") { body.evidence.push({ ...body.evidence[0]!, evidence_link_id: uid(99) }); body.total_evidence = 2; }
    if (variant === "extra field") Object.assign(item, { prompt: "private" });
    if (variant === "wrong statement") item.thesis_statement = "另一个判断";
    expect(() => parse(body)).toThrow();
  });
  it("rejects available checks without an authorized report or with truncated evidence", () => {
    expect(() => parse({ ...fixture(), state: "withheld", reason_code: "result_not_authorized", result: null, draft_id: null })).toThrow();
    expect(() => parse({ ...fixture(), total_evidence: 201, truncated: true })).toThrow();
  });
  it("accepts unavailable review only with no partial items or flags", () => {
    const body = fixture();
    Object.assign(body.assessment_review, { state: "unavailable", reason_code: "lineage_unavailable", items: [], quality_flags: [] });
    expect(parse(body)).toHaveProperty("assessmentReview.state", "unavailable");
    body.assessment_review.items = fixture().assessment_review.items;
    expect(() => parse(body)).toThrow();
  });
  it("accepts contextual per-item warnings not present in whole-report flags for mixed authority", () => {
    const body = fixture(), otherThesis = uid(30), otherEvidence = uid(31);
    body.evidence.push({ ...body.evidence[0]!, evidence_link_id: otherEvidence, thesis_id: otherThesis,
      thesis_statement: "收入核验", source_authority: "primary_disclosure" });
    body.total_evidence = 2;
    body.assessment_review.items.push({ ...body.assessment_review.items[0]!, assessment_id: uid(32), snapshot_id: uid(33),
      task_id: uid(34), thesis_id: otherThesis, thesis_statement: "收入核验", evidence_link_ids: [otherEvidence],
      quality_flags: ["source_independence_unverified", "retrieval_direction_unverified"] });
    body.assessment_review.quality_flags = ["source_independence_unverified", "retrieval_direction_unverified"];
    expect(parse(body).assessmentReview?.items[0]?.qualityFlags).toContain("user_material_only");
    expect(parse(body).assessmentReview?.qualityFlags).not.toContain("user_material_only");
  });
});

describe("assessment review reading flow", () => {
  it("opens an evidence path with the keyboard and restores focus to its node", async () => {
    const view = mount();
    const map = await screen.findByRole("region", { name: "判断与证据关系" });
    expect(within(map).getByText("支持方向检索")).toBeVisible();
    const node = within(map).getByRole("button", { name: "查看引用原文：用户材料甲" });
    node.focus();
    const user = userEvent.setup();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("冻结原文：现金流金额。")).toBeVisible();
    expect(node).toHaveAttribute("aria-pressed", "true");
    expect(String(view.fetch.mock.calls.at(-1)?.[0]).endsWith(`/evidence/${eid}`)).toBe(true);
    await user.click(screen.getByRole("button", { name: "关闭原文" }));
    expect(node).toHaveFocus();
    expect(node).toHaveAttribute("aria-pressed", "false");
  });
  it("keeps shared quality explanations once and preserves item-specific checks and original gaps", async () => {
    const body = fixture();
    body.assessment_review.quality_flags = ["source_independence_unverified", "retrieval_direction_unverified"];
    mount(body);
    await screen.findByRole("region", { name: "结果摘要" });
    expect(screen.getAllByText("来源独立性未核验，多份材料不等于多个独立来源。")).toHaveLength(1);
    const item = screen.getByRole("region", { name: /^分项判断：/ });
    await userEvent.setup().click(within(item).getByText("本项独有质量提示（2 项）"));
    expect(within(item).getByText("仅有用户材料，尚未独立核验。")).toBeVisible();
    expect(within(item).getByText("仍缺独立原始披露")).toBeVisible();
  });
  it("keeps the complete server warnings available in one reading-boundary disclosure", async () => {
    const body = { ...fixture(), warnings: ["本轮来源授权范围有限，需单独核对材料使用范围。", "来源没有提供最新的数据期间。"] };
    mount(body);
    const rules = await screen.findByText("阅读边界与限制（2 项来源提示）");
    for (const warning of body.warnings) {
      expect(screen.getAllByText(warning)).toHaveLength(1);
      expect(screen.getByText(warning)).not.toBeVisible();
    }
    await userEvent.setup().click(rules);
    for (const warning of body.warnings) expect(screen.getByText(warning)).toBeVisible();
    expect(screen.getByText("系统生成，未经人工审核")).toBeVisible();
    expect(screen.getByText("仍缺独立原始披露")).toBeVisible();
  });
  it("withdraws map nodes immediately on authorization change and ignores a late original", async () => {
    const body = fixture();
    let finishDetail!: (response: Response) => void;
    let researchReads = 0;
    const fetch = vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith("/research")) {
        if (++researchReads === 1) return Promise.resolve(new Response(JSON.stringify(body)));
        return new Promise<Response>(() => undefined);
      }
      return new Promise<Response>((resolve) => { finishDetail = resolve; });
    });
    const props = { client: new HttpGatewayClient({ fetch }), conversationId: cid,
      run: { runSpecId: rid, nativeCaseId: uid(12), nativeRunId: uid(13), status: "succeeded" as const,
        parentRunSpecId: null, createdAt: at }, connection: "live" as const, refreshSequence: 1,
      authorizedArtifactsKey: "authorized-v1", onAuthorizationLost: vi.fn() };
    const view = render(<GatewayResearchContent {...props} />);
    const map = await screen.findByRole("region", { name: "判断与证据关系" });
    await userEvent.setup().click(within(map).getByRole("button", { name: "查看引用原文：用户材料甲" }));
    await waitFor(() => expect(finishDetail).toBeDefined());
    view.rerender(<GatewayResearchContent {...props} authorizedArtifactsKey="authorized-v2" />);
    expect(screen.queryByRole("region", { name: "判断与证据关系" })).not.toBeInTheDocument();
    expect(screen.queryByText("用户材料甲")).not.toBeInTheDocument();
    await act(async () => finishDetail(new Response(JSON.stringify({ ...body.evidence[0], conversation_id: cid,
      run_spec_id: rid, quote: "迟到的私有原文", source_span_id: uid(11), locator: { page: 1, paragraph: 0 },
      content_sha256: "a".repeat(64), acquired_at: at }))));
    expect(screen.queryByText("迟到的私有原文")).not.toBeInTheDocument();
  });
  it("keeps applicable item quality labels visible beside the original verdict", async () => {
    mount();
    await screen.findByRole("region", { name: "结果摘要" });
    const item = screen.getByRole("region", { name: /^分项判断：/ });
    expect(within(item).getByText("仅用户材料")).toBeVisible();
    expect(within(item).getByText("来源独立性待核验")).toBeVisible();
  });
  it("clears an earlier task filter when opening another task's assessment input and restores focus on close", async () => {
    const body = fixture(), taskA = uid(21), evidenceA = uid(22);
    body.evidence.push({ ...body.evidence[0]!, task_id: taskA, evidence_link_id: evidenceA, title: "另一任务材料" });
    body.total_evidence = 2;
    body.assessment_review.items[0]!.evidence_link_ids.push(evidenceA);
    const view = mount(body);
    view.rerender(<GatewayResearchContent {...view.props} run={{ ...view.props.run, execution: {
      projectionState: "available", stage: "complete", updatedAt: at, reasonCode: null, nextAction: "review_result",
      workerState: "online", workerLastSeenAt: at, workerScope: "acquisition_service",
      tasks: [{ taskId: taskA, taskType: "contradict", status: "partial", stage: "partial", sourceKind: "intake_material",
        sourceName: "用户提供材料", providers: [], attempt: 1, updatedAt: at, retryAt: null,
        counts: { discovered: 1, fetched: 1, frozen: 1, admitted: 1, exceptions: 0 } }],
    } }} />);
    await screen.findByRole("region", { name: "结果摘要" });
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "执行过程" }));
    await user.click(screen.getByRole("button", { name: "查看本任务证据（1）" }));
    expect(screen.getByText(/仅显示所选任务关联证据/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "研究结果" }));
    await user.click(screen.getByRole("button", { name: "查看引用原文：用户材料甲" }));
    await screen.findByText("冻结原文：现金流金额。");
    expect(screen.queryByText(/仅显示所选任务关联证据/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关闭原文" }));
    expect(screen.getByRole("button", { name: "查看引用原文：用户材料甲" })).toHaveFocus();
  });
  it("shows original verdicts separately from checks and opens the exact original evidence", async () => {
    const view = mount();
    const summary = await screen.findByRole("region", { name: "结果摘要" });
    expect(within(summary).getByText(/支持判断 1 项/)).toBeInTheDocument();
    expect(screen.getByText("原 AI 判断：支持")).toBeInTheDocument();
    expect(screen.getAllByText(/仅有用户材料，尚未独立核验/).length).toBeGreaterThan(0);
    expect(screen.queryByText("质量检查通过")).not.toBeInTheDocument();
    expect(screen.getByText("仍缺独立原始披露")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "查看引用原文：用户材料甲" }));
    const original = await screen.findByRole("region", { name: "证据原文" });
    expect(await within(original).findByText("冻结原文：现金流金额。")).toBeInTheDocument();
    expect(String(view.fetch.mock.calls.at(-1)?.[0]).endsWith(`/evidence/${eid}`)).toBe(true);
  });
  it("keeps the byte-for-byte historical report in an expandable section", async () => {
    mount();
    await screen.findByRole("region", { name: "结果摘要" });
    const text = screen.getByText(fixture().result.conclusion);
    expect(text).not.toBeVisible();
    await userEvent.setup().click(screen.getByText("查看原始报告"));
    expect(text).toBeVisible();
    expect(screen.getByText("原始发现")).toBeVisible();
  });
  it("keeps the original readable when lineage cannot be displayed", async () => {
    const body = fixture();
    Object.assign(body.assessment_review, { state: "unavailable", reason_code: "lineage_unavailable", items: [], quality_flags: [] });
    mount(body);
    expect(await screen.findByText(/暂无法建立完整的结论与证据关联/)).toBeInTheDocument();
    expect(screen.getByText(body.result.conclusion)).toBeVisible();
    expect(screen.queryByRole("region", { name: "结果摘要" })).not.toBeInTheDocument();
  });
  it("shows a truncation-specific limitation, not an empty successful review", async () => {
    const body = fixture();
    body.total_evidence = 201; body.truncated = true;
    Object.assign(body.assessment_review, { state: "unavailable", reason_code: "evidence_list_truncated", items: [], quality_flags: [] });
    mount(body);
    expect(await screen.findByText(/证据列表已截断，暂不展示不完整的结论关联/)).toBeInTheDocument();
  });
  it("does not render assessment markup as HTML and clears checks on access loss", async () => {
    const body = fixture(); body.assessment_review.items[0]!.rationale = '<img src=x onerror="alert(1)">';
    const view = mount(body);
    await screen.findByRole("region", { name: "结果摘要" });
    await userEvent.setup().click(screen.getByText("查看原 AI 判断理由"));
    expect(screen.getByText(body.assessment_review.items[0]!.rationale)).toBeVisible();
    expect(view.container.querySelector("img")).toBeNull();
    view.rerender(<GatewayResearchContent {...view.props} connection="access_revoked" />);
    expect(screen.queryByRole("region", { name: "结果摘要" })).not.toBeInTheDocument();
    expect(screen.getByText(/访问权限已失效/)).toBeInTheDocument();
  });
});
