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
    expect(dossier.assessment.provisional).toBe(true);
    expect(dossier.assessment.major_gap).toBeTruthy();
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
    expect(dossier.assessment.major_gap).toBeTruthy();
  });

  it("insufficient scenario returns insufficient_evidence with gaps", async () => {
    const adapter = new MockResearchAdapter({ scenario: "insufficient" });
    const dossier = await adapter.getCaseDossier("ai-compute");
    expect(dossier.assessment.conclusion).toBe("insufficient_evidence");
    expect(dossier.assessment.major_gap).toBeTruthy();
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
});