import type { components } from "../contracts/v1";
import type {
  Conclusion,
  DocumentSpan,
  DocumentsQuery,
  DossierQuery,
  EdgeKind,
  EvidenceRecord,
  EvidenceRole,
  NodeKind,
  OverviewQuery,
  PageStateErrorKind,
  RelationshipGraph,
  RelationshipQuery,
  ResearchCaseDossier,
  ResearchCaseSummary,
  ReviewDecisionView,
  ReviewOutcome,
  SearchHit,
  SourceDocumentView,
  StatementKind,
  ThesisAssessment,
  WorkspaceOverview,
} from "../domain/types";
import { PageStateError } from "../domain/types";
import type { ResearchClient } from "../domain/prototypeTypes";
import type {
  LinkReviewPayload,
  ReviewQueueView,
  ReviewQueueViewItem,
  ThemeIndexView,
  ThemeWorkbenchView,
  ThesisRerunResult,
  VersionsView,
  WorkspaceOverviewScreen,
} from "../domain/prototypeTypes";

type Schemas = components["schemas"];

// Backend does not prefix /api/v1 in the OpenAPI spec; the adapter always
// runs against the configured base URL which already includes the version.
type ErrorEnvelopeDTO = components["schemas"]["ErrorEnvelope"];

const VALID_NODE_KINDS: readonly NodeKind[] = [
  "case",
  "thesis",
  "statement",
  "step",
  "company",
  "stock",
  "fund",
  "valuation",
];
const VALID_EDGE_KINDS: readonly EdgeKind[] = [
  "evidence",
  "causal",
  "theme_role",
  "holding",
  "contains_thesis",
  "company_stock",
  "contains_step",
  "valuation",
];
const VALID_STATEMENT_KINDS: readonly StatementKind[] = [
  "disclosed_fact",
  "management_attribution",
  "forecast",
  "research_opinion",
];
const VALID_EVIDENCE_ROLES: readonly EvidenceRole[] = [
  "supports",
  "contradicts",
  "contextualizes",
];

// Backend search deep_link paths are prefixed with /research-cases/... but
// the React routes are /cases/... and /relationships/...; rewrite to the
// real frontend routes so clicks do not hit the wildcard redirect.
// Exact-match patterns only: case-level, dossier, graph.
function rewriteDeepLink(deepLink: string): string {
  if (deepLink.startsWith("/research-cases/")) {
    if (deepLink.endsWith("/dossier")) {
      // /research-cases/{id}/dossier -> /cases/{id}
      return deepLink
        .replace(/^\/research-cases\//, "/cases/")
        .replace(/\/dossier$/, "");
    }
    if (deepLink.endsWith("/graph")) {
      // /research-cases/{id}/graph -> /relationships/{id}
      return deepLink
        .replace(/^\/research-cases\//, "/relationships/")
        .replace(/\/graph$/, "");
    }
    // /research-cases/{id} -> /cases/{id}
    return deepLink.replace(/^\/research-cases\//, "/cases/");
  }
  return deepLink;
}

function asPageStateErrorKind(
  code: string | undefined,
): PageStateErrorKind {
  if (code === "permission_denied") return "permission_denied";
  if (code === "parse_failed") return "parse_failed";
  if (code === "stale") return "stale";
  return "backend_unavailable";
}

export class HttpResearchAdapter implements ResearchClient {
  constructor(private readonly options: { baseUrl: string }) {}

  private async get<T>(path: string): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.options.baseUrl}${path}`, {
        headers: { Accept: "application/json" },
      });
    } catch {
      throw new PageStateError("backend_unavailable");
    }
    if (!response.ok) {
      const payload = (await response.json().catch(
        () => null,
      )) as ErrorEnvelopeDTO | null;
      const code = payload?.error?.code;
      throw new PageStateError(
        asPageStateErrorKind(code),
        payload?.error?.message,
      );
    }
    return (await response.json()) as T;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.options.baseUrl}${path}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
    } catch {
      throw new PageStateError("backend_unavailable");
    }
    if (!response.ok) {
      const payload = (await response.json().catch(
        () => null,
      )) as ErrorEnvelopeDTO | null;
      const code = payload?.error?.code;
      throw new PageStateError(
        asPageStateErrorKind(code),
        payload?.error?.message,
      );
    }
    return (await response.json()) as T;
  }

  private buildQuery(params: Record<string, string | undefined>): string {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") sp.set(k, v);
    }
    const qs = sp.toString();
    return qs ? `?${qs}` : "";
  }

  // ── Mappers (honest pass-through; unknown enums throw to surface drift) ─

  private mapCaseSummary(dto: Schemas["CaseSummaryDTO"]): ResearchCaseSummary {
    return {
      id: dto.id,
      title: dto.title,
      topic: dto.topic,
      author: dto.created_by,
      created_at: dto.created_at,
      updated_at: dto.updated_at,
      has_markdown: false,
    };
  }

  private mapThesisSummary(
    dto: Schemas["ThesisSummaryDTO"],
  ): ResearchCaseSummary {
    return {
      id: dto.id,
      title: dto.statement,
      topic: "",
      author: dto.created_by,
      created_at: dto.created_at,
      updated_at: dto.created_at,
      has_markdown: false,
    };
  }

  private mapStatementKind(value: string): StatementKind {
    if ((VALID_STATEMENT_KINDS as readonly string[]).includes(value)) {
      return value as StatementKind;
    }
    // Unknown kind is a contract drift, not something to silently rewrite
    // into disclosed_fact. Surface it.
    throw new PageStateError(
      "backend_unavailable",
      `unknown statement_kind: ${value}`,
    );
  }

  private mapReviewState(value: string): EvidenceRecord["review_state"] {
    if (
      value === "machine_generated" ||
      value === "reviewed" ||
      value === "rejected"
    ) {
      return value;
    }
    throw new PageStateError(
      "backend_unavailable",
      `unknown review_state: ${value}`,
    );
  }

  private mapEvidenceRole(value: string): EvidenceRole {
    if ((VALID_EVIDENCE_ROLES as readonly string[]).includes(value)) {
      return value as EvidenceRole;
    }
    throw new PageStateError(
      "backend_unavailable",
      `unknown evidence role: ${value}`,
    );
  }

  private mapConclusion(value: string): Conclusion {
    if (
      value === "supported" ||
      value === "contradicted" ||
      value === "insufficient_evidence"
    ) {
      return value;
    }
    throw new PageStateError(
      "backend_unavailable",
      `unknown conclusion: ${value}`,
    );
  }

  private mapReviewOutcome(value: string): ReviewOutcome {
    if (value === "confirmed" || value === "modified" || value === "rejected") {
      return value;
    }
    throw new PageStateError(
      "backend_unavailable",
      `unknown review outcome: ${value}`,
    );
  }

  private mapNodeKind(value: string): NodeKind {
    if ((VALID_NODE_KINDS as readonly string[]).includes(value)) {
      return value as NodeKind;
    }
    throw new PageStateError(
      "backend_unavailable",
      `unknown graph node kind: ${value}`,
    );
  }

  private mapEdgeKind(value: string): EdgeKind {
    if ((VALID_EDGE_KINDS as readonly string[]).includes(value)) {
      return value as EdgeKind;
    }
    throw new PageStateError(
      "backend_unavailable",
      `unknown graph edge semantic_kind: ${value}`,
    );
  }

  private mapEvidence(dto: Schemas["EvidenceRecordDTO"]): EvidenceRecord {
    return {
      link_id: dto.link_id,
      statement_id: dto.statement_id,
      // statement_text is nullable in the backend (Statement missing -> null);
      // propagate null instead of fabricating "".
      statement_text: dto.statement_text ?? null,
      // statement_kind is nullable in the backend (Statement missing -> null).
      statement_kind: dto.statement_kind
        ? this.mapStatementKind(dto.statement_kind)
        : null,
      span_id: dto.span_id,
      verbatim_text: dto.verbatim_text,
      locator: dto.locator,
      reason: dto.reason,
      role: this.mapEvidenceRole(dto.role),
      scope: dto.scope,
      period: dto.observed_period,
      available_at: dto.available_at,
      review_state: this.mapReviewState(dto.review_state),
      // Backend has no source_label / reliability / preview metadata in this
      // delivery; expose null so the UI renders an honest placeholder.
      source_label: null,
      reliability: null,
    };
  }

  private mapAssessment(
    dto: Schemas["AssessmentDTO"] | undefined,
    _focusThesisId: string,
  ): ThesisAssessment | null {
    if (!dto) return null;
    let review: ReviewDecisionView | null = null;
    if (dto.review) {
      // conclusion is a free-form string in the ReviewDecision model;
      // accept any non-empty value to preserve ledger honesty without
      // inventing a typed enum that does not exist on the backend.
      review = {
        outcome: this.mapReviewOutcome(dto.review.outcome),
        conclusion: dto.review.conclusion ?? null,
        reason: dto.review.reason,
        reviewer: dto.review.reviewer,
        reviewed_at: dto.review.reviewed_at,
      };
    }
    return {
      id: dto.id,
      thesis_id: dto.thesis_id,
      conclusion: this.mapConclusion(dto.conclusion),
      rationale: dto.rationale,
      bullets: [],
      gaps: dto.gaps ?? [],
      provisional: dto.provisional,
      review,
      major_gap: null,
      status_label: "",
      supply_chain_level: "",
      updated_at: "",
      confidence_label: "",
      focus_axes: [],
    };
  }

  private mapCausalStep(dto: Schemas["CausalStepDTO"]): {
    id: string;
    sequence: number;
    title: string;
    description: string;
    status: null;
  } {
    // Backend CausalStepDTO carries no review status; do not invent one.
    return {
      id: dto.id,
      sequence: dto.sequence,
      title: dto.description,
      description: dto.description,
      status: null,
    };
  }

  private mapNode(dto: Schemas["GraphNodeDTO"]): {
    id: string;
    kind: NodeKind;
    label: string;
  } {
    return {
      id: dto.id,
      kind: this.mapNodeKind(dto.kind),
      label: dto.label,
    };
  }

  private mapEdge(dto: Schemas["GraphEdgeDTO"]): {
    id: string;
    kind: EdgeKind;
    source: string;
    target: string;
    review_state?: EvidenceRecord["review_state"];
    role?: EvidenceRole;
  } {
    const edge: {
      id: string;
      kind: EdgeKind;
      source: string;
      target: string;
      review_state?: EvidenceRecord["review_state"];
      role?: EvidenceRole;
    } = {
      id: dto.id,
      kind: this.mapEdgeKind(dto.semantic_kind),
      source: dto.source,
      target: dto.target,
    };
    if (dto.review_state) {
      edge.review_state = this.mapReviewState(dto.review_state);
    }
    const props = (dto.properties ?? {}) as Record<string, unknown>;
    if (typeof props.role === "string") {
      edge.role = this.mapEvidenceRole(props.role);
    }
    return edge;
  }

  private mapDocument(
    dto: Schemas["DocumentSummaryDTO"],
  ): SourceDocumentView {
    return {
      id: dto.id,
      title: null,
      publisher: null,
      document_type: null,
      publish_date: dto.published_at ?? null,
      available_at: dto.available_at,
      acquired_at: dto.acquired_at,
      parser_version: dto.parser_version,
      parse_quality: dto.parse_state === "parsed" ? "ok" : "partial",
      linked_cases: [],
      span_count: dto.span_count,
      statement_count: dto.statement_count,
      version_label: null,
    };
  }

  private mapSpan(dto: Schemas["SourceSpanDTO"]): DocumentSpan {
    const citations = (dto.citations ?? []) as Array<{
      link_id?: string;
      thesis_id?: string;
      role?: string;
    }>;
    return {
      id: dto.id,
      document_id: dto.document_version_id,
      locator: dto.locator,
      verbatim_text: dto.verbatim_text,
      cited_by: citations
        .filter(
          (c): c is { link_id: string; thesis_id: string; role: string } =>
            typeof c.link_id === "string" &&
            typeof c.thesis_id === "string" &&
            typeof c.role === "string",
        )
        .map((c) => ({
          evidence_id: c.link_id,
          thesis_id: c.thesis_id,
          role: this.mapEvidenceRole(c.role),
        })),
    };
  }

  private mapSearchGroup(objectType: string): SearchHit["group"] {
    switch (objectType) {
      case "case":
        return "案例";
      case "thesis":
        return "命题";
      case "evidence":
        return "证据";
      case "company":
        return "公司";
      case "stock":
        return "股票";
      case "fund":
        return "基金";
      default:
        // Unknown object_type is contract drift; surface it.
        throw new PageStateError(
          "backend_unavailable",
          `unknown search object_type: ${objectType}`,
        );
    }
  }

  // ── ResearchClient implementation ────────────────────────────────────────

  async getOverview(query?: OverviewQuery): Promise<WorkspaceOverview> {
    const list = await this.get<Schemas["CaseListResponse"]>(
      "/research-cases",
    );
    const focusCase = list.items[0];
    if (!focusCase) {
      throw new PageStateError(
        "backend_unavailable",
        "no research cases available",
      );
    }
    const dto = await this.get<Schemas["OverviewResponse"]>(
      `/overview${this.buildQuery({
        case_id: focusCase.id,
        cutoff: query?.cutoff,
      })}`,
    );
    const caseSummary = this.mapCaseSummary(dto.case);
    return {
      case_id: caseSummary.id,
      case_title: caseSummary.title,
      case_topic: caseSummary.topic,
      last_updated_at: caseSummary.updated_at,
      case_count_label: "",
      case_topic_tags: [],
      bullets: [],
      key_changes: dto.key_changes.map((kc) => ({
        id: kc.id,
        tag: kc.tag,
        text: kc.text,
        detail: "",
        occurred_at: kc.occurred_at,
        source_label: kc.source_label,
        review_state: kc.review_state
          ? this.mapReviewState(kc.review_state)
          : null,
      })),
      framework: dto.framework
        .filter(
          (f): f is Schemas["CausalStepDTO"] =>
            typeof f.id === "string" &&
            typeof f.sequence === "number" &&
            typeof f.description === "string",
        )
        .map((s) => ({
          id: s.id,
          sequence: String(s.sequence),
          title: s.description,
          children: [],
        })),
      totals: {
        evidence_total: dto.totals.evidence_total,
        reliable_pct: null,
        pending_review: dto.totals.pending_review,
        major_blockers: dto.totals.major_gaps,
      },
      task_queue: [],
      evidence_changes: [],
      activity: [],
    };
  }

  async getCaseDossier(
    caseId: string,
    query?: DossierQuery,
  ): Promise<ResearchCaseDossier> {
    const dto = await this.get<Schemas["DossierResponse"]>(
      `/research-cases/${encodeURIComponent(caseId)}/dossier${this.buildQuery({
        thesis_id: query?.thesisId,
        cutoff: query?.cutoff,
      })}`,
    );
    const evidence = dto.evidence as Record<
      string,
      Schemas["EvidenceRecordDTO"][]
    >;
    const assessment = this.mapAssessment(
      dto.assessment ?? undefined,
      dto.focus_thesis_id,
    );
    return {
      case: this.mapCaseSummary(dto.case),
      theses: dto.theses.map((t) => this.mapThesisSummary(t)),
      focus_thesis_id: dto.focus_thesis_id,
      tabs: [],
      // assessment can legitimately be null (no AI snapshot for the focus
      // thesis yet); expose null so the UI renders an honest placeholder.
      assessment,
      causal_chain: dto.causal_chain.map((c) => this.mapCausalStep(c)),
      evidence: {
        supports: (evidence.supports ?? []).map((e) => this.mapEvidence(e)),
        contradicts: (evidence.contradicts ?? []).map((e) =>
          this.mapEvidence(e),
        ),
        contextualizes: (evidence.contextualizes ?? []).map((e) =>
          this.mapEvidence(e),
        ),
      },
      competitive_explanations: dto.competitive_explanations,
      gaps: dto.gaps,
      log: [],
    };
  }

  async getRelationshipGraph(
    caseId: string,
    query?: RelationshipQuery,
  ): Promise<RelationshipGraph> {
    const dto = await this.get<Schemas["GraphResponse"]>(
      `/research-cases/${encodeURIComponent(caseId)}/graph${this.buildQuery({
        cutoff: query?.cutoff,
      })}`,
    );
    return {
      case: {
        id: caseId,
        title: "",
        topic: "",
        author: "",
        created_at: "",
        updated_at: "",
        has_markdown: false,
      },
      nodes: dto.nodes.map((n) => this.mapNode(n)),
      edges: dto.edges.map((e) => this.mapEdge(e)),
      legend: [],
    };
  }

  async getDocuments(query?: DocumentsQuery): Promise<SourceDocumentView[]> {
    const dto = await this.get<Schemas["DocumentListResponse"]>(
      `/documents${this.buildQuery({
        q: query?.query,
        cutoff: query?.cutoff,
      })}`,
    );
    return dto.items.map((d) => this.mapDocument(d));
  }

  async getDocumentDetail(documentId: string): Promise<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  }> {
    const dto = await this.get<Schemas["DocumentDetailResponse"]>(
      `/documents/${encodeURIComponent(documentId)}`,
    );
    return {
      document: this.mapDocument(dto.document),
      spans: dto.spans.map((s) => this.mapSpan(s)),
    };
  }

  async search(query: string): Promise<SearchHit[]> {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      // Backend rejects q < 2 with 422; return empty hits instead of
      // surfacing an error so the UI search box does not flash a banner on
      // every keystroke.
      return [];
    }
    const dto = await this.get<Schemas["SearchResponse"]>(
      `/search${this.buildQuery({ q: trimmed })}`,
    );
    const hits: SearchHit[] = [];
    for (const group of dto.groups) {
      for (const hit of group.hits) {
        hits.push({
          group: this.mapSearchGroup(hit.object_type),
          id: hit.object_id,
          title: hit.title,
          hint: hit.snippet,
          navigate_to: rewriteDeepLink(hit.deep_link),
        });
      }
    }
    return hits;
  }

  async getCaseSummaries(): Promise<ResearchCaseSummary[]> {
    const dto = await this.get<Schemas["CaseListResponse"]>(
      `/research-cases`,
    );
    return dto.items.map((c) => this.mapCaseSummary(c));
  }

  async getReviewQueue(): Promise<never[]> {
    throw new PageStateError(
      "backend_unavailable",
      "review API is not available in live-read delivery",
    );
  }

  async submitReviewDecision(
    _itemId: string,
    _decision: {
      outcome: import("../domain/types").ReviewOutcome;
      conclusion: Conclusion | null;
      reason: string;
    },
  ): Promise<void> {
    throw new PageStateError(
      "backend_unavailable",
      "review API is not available in live-read delivery",
    );
  }

  // ── Prototype screens (live backend does not yet expose these endpoints,
  //    so they fall back to the same frozen fixture as the mock adapter. The
  //    frontend only depends on the prototype view-model shape; switching
  //    to a live endpoint is a one-line change once the backend ships it.)

  async getWorkspaceOverviewView() {
    return (await import("./prototypeFixture")).buildWorkspaceOverview();
  }

  async getWorkspaceOverviewScreen(): Promise<WorkspaceOverviewScreen> {
    return (await import("./prototypeFixture")).buildWorkspaceOverviewScreen();
  }

  async getThemeIndexView(): Promise<ThemeIndexView> {
    return (await import("./prototypeFixture")).buildThemeIndexView();
  }

  async getThemeWorkbenchView(themeId: string): Promise<ThemeWorkbenchView> {
    return (await import("./prototypeFixture")).buildThemeWorkbenchView(themeId);
  }

  async getNewResearchView() {
    return (await import("./prototypeFixture")).buildNewResearchView();
  }

  async getResearchPlanView() {
    return (await import("./prototypeFixture")).buildResearchPlanView();
  }

  async getCaseWorkbenchView(_caseId: string) {
    return (await import("./prototypeFixture")).buildCaseWorkbenchView();
  }

  async getRelationshipGraphView(_caseId: string) {
    return (await import("./prototypeFixture")).buildRelationshipGraphView();
  }

  async getLibraryView() {
    return (await import("./prototypeFixture")).buildLibraryView();
  }

  async getDataCenterView() {
    return (await import("./prototypeFixture")).buildDataCenterView();
  }

  async getVersionsView(): Promise<VersionsView> {
    const cases = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    const firstCase = cases.items[0];
    if (!firstCase) {
      throw new PageStateError("parse_failed", "no research case exists yet");
    }
    const snapshotsDto = await this.get<Schemas["CaseSnapshotsResponse"]>(
      `/research-cases/${firstCase.id}/snapshots`,
    );
    // Latest two distinct cutoffs define base/compare; with fewer than two
    // the compare degenerates to "from the beginning" (epoch -> latest),
    // showing every visible link as newly added.
    const cutoffs = [
      ...new Set(snapshotsDto.snapshots.map((s) => s.cutoff)),
    ].sort();
    const compareCutoff = cutoffs[cutoffs.length - 1] ?? new Date().toISOString();
    const baseCutoff =
      cutoffs.length > 1
        ? cutoffs[cutoffs.length - 2]
        : "1970-01-01T00:00:00Z";

    const compare = await this.get<Schemas["CaseCompareResponse"]>(
      `/research-cases/${firstCase.id}/compare${this.buildQuery({
        base: baseCutoff,
        compare: compareCutoff,
      })}`,
    );
    const runs = await this.get<Schemas["ProviderRunsResponse"]>(
      `/provider-runs${this.buildQuery({ kind: "assess", limit: "1" })}`,
    );

    const thesis = compare.theses[0];
    const lastAssessRun = runs.runs[0];
    const conclusionLabel = (c: string | null) =>
      c === "supported"
        ? "支持（成立）"
        : c === "contradicted"
          ? "被反驳"
          : c === "insufficient_evidence"
            ? "证据不足 · 继续验证"
            : "开放判断";
    const linkRow = (l: Schemas["CompareLinkDTO"], idx: number) => ({
      id: l.link_id ?? `link-${idx}`,
      label: l.statement_text ?? l.reason,
      role: l.role,
      reviewState: l.review_state,
    });

    const snapshotMeta = (cutoff: string) => {
      const s = snapshotsDto.snapshots.find((x) => x.cutoff === cutoff);
      return {
        id: s ? s.snapshot_id.slice(0, 8) : cutoff.slice(0, 10),
        cutoff: cutoff.slice(0, 10),
        freezeTime: s ? s.created_at : "",
      };
    };

    const addedLinks = thesis?.added_links ?? [];
    const removedLinks = thesis?.removed_links ?? [];
    return {
      case: { id: firstCase.id, title: firstCase.title },
      focusThesisId: thesis?.thesis_id ?? "",
      beforeSnapshot: snapshotMeta(baseCutoff),
      afterSnapshot: snapshotMeta(compareCutoff),
      before: {
        formalConclusion: {
          state: conclusionLabel(thesis?.conclusion_before ?? null),
          text: thesis?.statement ?? "",
        },
        inputs: [],
        relationships: removedLinks.map(linkRow),
        factors: [],
        gaps: (thesis?.gaps_before ?? []).map((g, i) => ({
          id: `gap-b-${i}`,
          label: g,
          state: "未解决",
        })),
      },
      after: {
        formalConclusion: {
          state: conclusionLabel(thesis?.conclusion_after ?? null),
          text: thesis?.statement ?? "",
        },
        inputs: compare.documents_added.map((d) => ({
          id: d.document_version_id,
          kind: "DocumentVersion",
          label: d.source_url,
          version: d.published_at ?? "",
        })),
        relationships: addedLinks.map(linkRow),
        factors: [],
        gaps: (thesis?.gaps_after ?? []).map((g, i) => ({
          id: `gap-a-${i}`,
          label: g,
          state: "未解决",
        })),
      },
      changeRail: {
        inputSummary: `新增 ${compare.documents_added.length} 个文档版本`,
        relationshipSummary: `新增 ${addedLinks.length} 条关系 · 移除 ${removedLinks.length} 条`,
        factorSummary: "因素角色暂无变化记录",
        conclusionSummary: thesis?.conclusion_changed
          ? `结论变化：${conclusionLabel(thesis.conclusion_before)} → ${conclusionLabel(thesis.conclusion_after)}`
          : "结论未变化",
        gapSummary: `缺口 ${thesis?.gaps_before.length ?? 0} → ${thesis?.gaps_after.length ?? 0}`,
        rationale: "",
        reviewedBy: "",
        reviewedAt: "",
      },
      aiProposal: {
        runId: lastAssessRun ? lastAssessRun.id.slice(0, 8) : "—",
        observedAt: lastAssessRun?.started_at ?? "",
        label: "AI RERUN",
        text: lastAssessRun
          ? lastAssessRun.output_summary
          : "（暂无 AI 评估运行记录）",
        boundary: "AI 生成 · 未经人工复核",
      },
    };
  }

  async rerunThesis(thesisId: string): Promise<ThesisRerunResult> {
    const dto = await this.post<Schemas["RerunResponse"]>(
      `/theses/${thesisId}/rerun`,
      {},
    );
    return {
      thesisId: dto.thesis_id,
      mode: dto.mode,
      assessmentId: dto.assessment.id,
      snapshotId: dto.assessment.snapshot_id,
      conclusion: dto.assessment.conclusion,
      rationale: dto.assessment.rationale,
      gaps: dto.assessment.gaps,
      createdAt: dto.assessment.created_at,
    };
  }

  // ── Review queue (screen 6 · live) ─────────────────────────────────────

  private mapReviewQueueItem(
    dto: Schemas["ReviewQueueItemDTO"],
  ): ReviewQueueViewItem {
    return {
      linkId: dto.link_id,
      thesisId: dto.thesis_id,
      caseId: dto.case_id,
      thesisStatement: dto.thesis_statement,
      aiRole: dto.ai_role,
      aiReason: dto.ai_reason,
      aiScope: dto.ai_scope,
      statementId: dto.statement_id,
      statementText: dto.statement_text,
      statementKind: dto.statement_kind,
      verbatimText: dto.verbatim_text,
      documentVersionId: dto.document_version_id,
      documentSourceUrl: dto.document_source_url,
      documentPublishedAt: dto.document_published_at ?? null,
      availableAt: dto.available_at,
    };
  }

  async getReviewQueueView(): Promise<ReviewQueueView> {
    const dto = await this.get<Schemas["ReviewQueueResponse"]>(`/review-queue`);
    return { items: dto.items.map((i) => this.mapReviewQueueItem(i)) };
  }

  async submitLinkReview(
    linkId: string,
    payload: LinkReviewPayload,
  ): Promise<void> {
    await this.post<Schemas["LinkReviewResponse"]>(
      `/evidence-links/${linkId}/reviews`,
      payload,
    );
  }
}