import { describe, it, expect } from "vitest";
import { MockResearchAdapter } from "../data/mockResearchAdapter";
import { PageStateError } from "../domain/types";

describe("MockResearchAdapter scenarios", () => {
  const typical = new MockResearchAdapter();

  it("returns the workspace overview with task queue and activity groups", async () => {
    const overview = await typical.getOverview();
    expect(overview.case_id).toBe("ai-compute");
    expect(overview.task_queue.length).toBeGreaterThan(0);
    expect(overview.task_queue.some((t) => t.category === "主要阻塞")).toBe(true);
    expect(overview.activity.some((a) => a.group === "今天")).toBe(true);
    expect(overview.framework[0].children.length).toBeGreaterThan(0);
  });

  it("builds a case dossier with provisional AI assessment and explicit gaps", async () => {
    const dossier = await typical.getCaseDossier("ai-compute");
    expect(dossier.assessment?.provisional).toBe(true);
    expect(dossier.assessment?.major_gap).toBeTruthy();
    expect(dossier.gaps.length).toBeGreaterThan(0);
    expect(dossier.evidence.supports.length).toBeGreaterThan(0);
    expect(dossier.evidence.contradicts.length).toBeGreaterThan(0);
    expect(
      dossier.evidence.supports.length + dossier.evidence.contradicts.length
    ).toBeGreaterThan(1);
  });

  it("filters evidence by historical cutoff so post-cutoff material is hidden", async () => {
    const before = await typical.getCaseDossier("ai-compute");
    const after = await typical.getCaseDossier("ai-compute", {
      cutoff: "2024-04-15T00:00:00+08:00",
    });
    expect(after.evidence.supports.length).toBeLessThanOrEqual(
      before.evidence.supports.length
    );
    expect(
      after.evidence.contradicts.find((e) => e.period === "2024-05-25")
    ).toBeUndefined();
  });

  it("returns a relationship graph with five-column evidence-to-fund layout", async () => {
    const graph = await typical.getRelationshipGraph("ai-compute");
    const groups = new Set(graph.nodes.map((n) => n.group));
    expect(groups.has("evidence")).toBe(true);
    expect(groups.has("proposition")).toBe(true);
    expect(groups.has("causal")).toBe(true);
    expect(groups.has("company")).toBe(true);
    expect(groups.has("fund")).toBe(true);
    const kinds = new Set(graph.edges.map((e) => e.kind));
    expect(kinds.has("evidence")).toBe(true);
    expect(kinds.has("causal")).toBe(true);
    expect(kinds.has("theme_role")).toBe(true);
    expect(kinds.has("holding")).toBe(true);
  });

  it("returns documents including a parse-failed sample", async () => {
    const docs = await typical.getDocuments();
    expect(docs.some((d) => d.parse_quality === "failed")).toBe(true);
    expect(docs.some((d) => d.parse_quality === "ok")).toBe(true);
  });

  it("keeps a newly created strict Case in intake review without creating a run", async () => {
    const adapter = new MockResearchAdapter();

    const created = await adapter.createEventResearch({
      rawInput: "公司上调资本开支指引，盘后股价下跌。",
      eventTitle: "资本开支指引更新",
      companyName: "示例公司",
      ticker: "600000.SH",
      eventAt: "2026-08-09T00:00:00Z",
      marketReaction: "盘后下跌",
      summary: null,
      researchQuestion: "资本开支上调是否解释了市场反应？",
      candidateFactors: ["现金流", "盈利预期", "估值"],
      confirmationRequired: true,
      researchProtocolRequired: true,
      createdBy: "human:researcher",
    });

    expect(created.lifecycle).toMatchObject({
      status: "awaiting_key_review",
      activeRunId: null,
      currentRound: 0,
      nextHumanAction: "核验原文资料并完成研究协议",
    });

    const workbench = await adapter.getEventWorkbench(created.caseId);
    expect(workbench.event).toMatchObject({
      id: created.caseId,
      eventTitle: "资本开支指引更新",
      ticker: "600000.SH",
    });
    expect(workbench.nextAction).toMatchObject({
      kind: "review_intake",
      label: "核验原文资料并完成研究协议",
    });

    const documents = await adapter.getDocuments({ caseId: created.caseId });
    expect(documents).toHaveLength(1);
    expect(documents[0]).toMatchObject({
      linked_cases: [{ id: created.caseId, title: "资本开支指引更新" }],
      source_authority: "unknown",
      parse_quality: "partial",
    });

    const detail = await adapter.getDocumentDetail(documents[0].id);
    expect(detail.spans).toEqual([expect.objectContaining({
      document_id: documents[0].id,
      verbatim_text: "公司上调资本开支指引，盘后股价下跌。",
    })]);
  });

  it("freezes a document supplement in the same Case without changing the original", async () => {
    const adapter = new MockResearchAdapter();
    const created = await adapter.createEventResearch({
      rawInput: "公司上调资本开支指引。",
      eventTitle: "资本开支指引更新",
      companyName: "示例公司", ticker: "600000.SH", eventAt: "2026-08-09T00:00:00Z", marketReaction: null, summary: null,
      researchQuestion: "资本开支上调是否可验证？", candidateFactors: ["资本开支"], confirmationRequired: true, researchProtocolRequired: true, createdBy: "human:researcher",
    });
    const [original] = await adapter.getDocuments({ caseId: created.caseId });

    const supplement = await adapter.createDocumentSupplement({ caseId: created.caseId, documentId: original.id, rawText: "公司在第 3 页明确上调全年资本开支指引。", claimedPageReference: "第 3 页", createdBy: "human:researcher" });

    expect(supplement).toMatchObject({ originalDocumentVersionId: original.id, extractionAllowed: true });
    expect((await adapter.getDocuments({ caseId: created.caseId })).map((document) => document.id)).toEqual([original.id, supplement.documentVersionId]);
    expect((await adapter.getDocumentDetail(original.id)).spans[0].verbatim_text).toBe("公司上调资本开支指引。");
    expect((await adapter.getDocumentDetail(supplement.documentVersionId)).spans[0]).toMatchObject({ verbatim_text: "公司在第 3 页明确上调全年资本开支指引。", locator: { claimed_page_reference: "第 3 页" } });
  });

  it("does not let a restricted published snapshot start a successor run", async () => {
    const adapter = new MockResearchAdapter();
    const restricted = await adapter.decidePublishedMaterial({
      caseId: "event-published",
      rawInput: "这份资料禁止展示和 AI 处理。",
      sourceType: "pasted_snapshot",
      sourceMetadata: {
        permissions: { ai_processing: false, display: false },
      },
      decision: "no_change",
      reason: "仅保留审计元数据。",
      actor: "human:researcher",
    });

    await expect(
      adapter.continueEventResearch({
        caseId: "event-published",
        documentVersionId: restricted.documentVersionId,
        reason: "尝试继续研究。",
        triggeredBy: "human:researcher",
      }),
    ).rejects.toThrow("source contract does not permit research");
  });

  it("returns review queue items with AI provenance and dated scope", async () => {
    const queue = await typical.getReviewQueue();
    expect(queue.length).toBeGreaterThan(0);
    queue.forEach((item) => {
      expect(item.proposed_by).toBe("ai");
      expect(item.scope).toBeTruthy();
      expect(item.available_at).toBeTruthy();
    });
  });

  it("offline scenario throws PageStateError with kind=backend_unavailable", async () => {
    const offline = new MockResearchAdapter({ scenario: "offline" });
    await expect(offline.getOverview()).rejects.toBeInstanceOf(PageStateError);
    await expect(offline.getOverview()).rejects.toMatchObject({
      kind: "backend_unavailable",
    });
  });

  it("permission scenario blocks writes but reads succeed", async () => {
    const perm = new MockResearchAdapter({ scenario: "permission" });
    const queue = await perm.getReviewQueue();
    expect(queue.length).toBeGreaterThan(0);
    await expect(
      perm.submitReviewDecision("rq-1", {
        outcome: "confirmed",
        conclusion: null,
        reason: "test",
      })
    ).rejects.toMatchObject({ kind: "permission_denied" });
  });

  it("empty scenario surfaces first-use state without evidence", async () => {
    const empty = new MockResearchAdapter({ scenario: "empty" });
    const overview = await empty.getOverview();
    expect(overview.task_queue).toHaveLength(0);
    expect(overview.bullets).toHaveLength(0);
    const dossier = await empty.getCaseDossier("ai-compute");
    expect(dossier.evidence.supports).toHaveLength(0);
    expect(dossier.evidence.contradicts).toHaveLength(0);
    expect(dossier.causal_chain).toHaveLength(0);
    expect(dossier.assessment?.major_gap).toBeTruthy();
  });

  it("insufficient scenario returns insufficient_evidence with gaps", async () => {
    const adapter = new MockResearchAdapter({ scenario: "insufficient" });
    const dossier = await adapter.getCaseDossier("ai-compute");
    expect(dossier.assessment?.conclusion).toBe("insufficient_evidence");
    expect(dossier.assessment?.major_gap).toBeTruthy();
    expect(dossier.gaps.length).toBeGreaterThanOrEqual(3);
    expect(dossier.evidence.supports).toHaveLength(0);
    expect(dossier.evidence.contradicts).toHaveLength(0);
  });

  it("conflict scenario keeps both supports and contradicts visible", async () => {
    const adapter = new MockResearchAdapter({ scenario: "conflict" });
    const dossier = await adapter.getCaseDossier("ai-compute");
    expect(dossier.evidence.supports.length).toBeGreaterThan(0);
    expect(dossier.evidence.contradicts.length).toBeGreaterThan(0);
  });

  it("parse_failed scenario makes every document fail and yields no spans", async () => {
    const adapter = new MockResearchAdapter({ scenario: "parse_failed" });
    const docs = await adapter.getDocuments();
    expect(docs.every((d) => d.parse_quality === "failed")).toBe(true);
    const detail = await adapter.getDocumentDetail(docs[0].id);
    expect(detail.spans).toHaveLength(0);
  });

  it("large scenario returns a virtualisable graph (>200 nodes)", async () => {
    const adapter = new MockResearchAdapter({ scenario: "large" });
    const graph = await adapter.getRelationshipGraph("ai-compute");
    expect(graph.nodes.length).toBeGreaterThan(1000);
    expect(graph.edges.length).toBeGreaterThan(2000);
  });

  it("submitReviewDecision removes the item from the queue and records outcome", async () => {
    const adapter = new MockResearchAdapter();
    const before = await adapter.getReviewQueue();
    await adapter.submitReviewDecision(before[0].id, {
      outcome: "confirmed",
      conclusion: null,
      reason: "test",
    });
    const after = await adapter.getReviewQueue();
    expect(after.find((q) => q.id === before[0].id)).toBeUndefined();
    expect(adapter.getDecisions()).toHaveLength(1);
  });

  it("setScenario can be reused across reads to swap behavior", async () => {
    const adapter = new MockResearchAdapter();
    await expect(adapter.getOverview()).resolves.toBeTruthy();
    adapter.setScenario("offline");
    await expect(adapter.getOverview()).rejects.toMatchObject({
      kind: "backend_unavailable",
    });
    adapter.setScenario("typical");
    await expect(adapter.getOverview()).resolves.toBeTruthy();
  });

  // ── 公司研究（/companies）───────────────────────────────────────────────

  it("listCompanies returns seed companies under typical scenario", async () => {
    const adapter = new MockResearchAdapter();
    const view = await adapter.listCompanies();
    expect(view.items.length).toBeGreaterThan(0);
    expect(view.items[0]).toMatchObject({
      id: expect.any(String),
      code: expect.any(String),
      name: expect.any(String),
      type: expect.any(String),
    });
    expect(view.hasMore).toBe(false);
  });

  it("listCompanies filter narrows by code or name", async () => {
    const adapter = new MockResearchAdapter();
    const full = await adapter.listCompanies();
    const partial = await adapter.listCompanies(full.items[0].code);
    expect(partial.items.length).toBeGreaterThan(0);
    expect(partial.items.length).toBeLessThanOrEqual(full.items.length);
  });

  it("listCompanies returns empty items under empty scenario", async () => {
    const adapter = new MockResearchAdapter();
    adapter.setScenario("empty");
    const view = await adapter.listCompanies();
    expect(view.items).toEqual([]);
    expect(view.hasMore).toBe(false);
  });

  it("getCompanyDossier exposes identity, theme roles, theses, valuations, holders", async () => {
    const adapter = new MockResearchAdapter();
    const list = await adapter.listCompanies();
    const dossier = await adapter.getCompanyDossier(list.items[0].id);
    expect(dossier.company.id).toBe(list.items[0].id);
    expect(dossier.stocks.length).toBeGreaterThan(0);
    // 主题角色应包含来源回链字段
    expect(dossier.themeRoles.length).toBeGreaterThan(0);
    expect(dossier.themeRoles[0].statementId).toBeTruthy();
    // 关联命题必须分离承载 AI / 人工字段
    expect(dossier.relatedTheses.length).toBeGreaterThan(0);
    const t = dossier.relatedTheses[0];
    expect(t.aiConclusion === null || typeof t.aiConclusion === "string").toBe(
      true,
    );
    // cut 为基准的过滤不能破坏 dossier 结构
    expect(dossier.cutoff).toBeTruthy();
  });

  it("getCompanyDossier returns empty dossier for unknown company", async () => {
    const adapter = new MockResearchAdapter();
    const dossier = await adapter.getCompanyDossier("co-unknown");
    expect(dossier.company.id).toBe("co-unknown");
    expect(dossier.stocks).toEqual([]);
    expect(dossier.themeRoles).toEqual([]);
    expect(dossier.relatedTheses).toEqual([]);
  });

  it("getCompanyDossier honors historical cutoff: applicableTo past roles hidden", async () => {
    const adapter = new MockResearchAdapter();
    const list = await adapter.listCompanies();
    const before = await adapter.getCompanyDossier(list.items[0].id);
    const after = await adapter.getCompanyDossier(list.items[0].id, {
      cutoff: "2020-01-01T00:00:00+00:00",
    });
    // 历史 cutoff 早于 applicable_from / applicable_to 的角色应被过滤
    expect(after.themeRoles.length).toBeLessThanOrEqual(
      before.themeRoles.length,
    );
  });

  // ── 主题研究（/topics · 横切主题）───────────────────────────────────────

  it("listThemes returns at least one topic under typical scenario", async () => {
    const adapter = new MockResearchAdapter();
    const topics = await adapter.listThemes();
    expect(topics.length).toBeGreaterThan(0);
    for (const t of topics) {
      expect(t.tag).toBeTruthy();
      expect(t.caseCount).toBeGreaterThan(0);
    }
  });

  it("listThemes returns empty array under empty scenario", async () => {
    const adapter = new MockResearchAdapter();
    adapter.setScenario("empty");
    const topics = await adapter.listThemes();
    expect(topics).toEqual([]);
  });

  it("getThemeView assembles cases, company roles, fund exposure and derivedFrom", async () => {
    const adapter = new MockResearchAdapter();
    const topics = await adapter.listThemes();
    const view = await adapter.getThemeView(topics[0].tag);
    expect(view.tag).toBe(topics[0].tag);
    expect(view.cases.length).toBeGreaterThan(0);
    // 公司 × 角色表应回链 case
    expect(view.companyRoles.length).toBeGreaterThan(0);
    for (const r of view.companyRoles) {
      expect(r.companyId).toBeTruthy();
    }
    // derivedFrom 必须覆盖 case / thesis / role / disclosure 四个 ID 集合
    expect(view.derivedFrom.caseIds.length).toBe(view.cases.length);
    expect(view.derivedFrom.thesisIds.length).toBeGreaterThan(0);
    expect(view.derivedFrom.themeRoleIds.length).toBeGreaterThan(0);
  });

  it("getThemeView returns empty view for unknown tag with derivedFrom still well-formed", async () => {
    const adapter = new MockResearchAdapter();
    const view = await adapter.getThemeView("不存在的主题-xyz");
    expect(view.tag).toBe("不存在的主题-xyz");
    expect(view.cases).toEqual([]);
    expect(view.companyRoles).toEqual([]);
    expect(view.fundExposure).toEqual([]);
    expect(view.derivedFrom).toEqual({
      caseIds: [],
      thesisIds: [],
      themeRoleIds: [],
      disclosureIds: [],
    });
  });

  it("getThemeView under empty scenario returns empty regardless of tag", async () => {
    const adapter = new MockResearchAdapter();
    adapter.setScenario("empty");
    const view = await adapter.getThemeView("算力国产化");
    expect(view.cases).toEqual([]);
    expect(view.derivedFrom.thesisIds).toEqual([]);
  });
});
