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
  CaseSummaryItem,
  CaseWorkbenchFactorRow,
  CaseWorkbenchFormalJudgment,
  CaseWorkbenchSourceRow,
  CaseWorkbenchThesisRow,
  CaseWorkbenchView,
  CreateCaseInput,
  CreateCaseResult,
  DataCenterView,
  DataMetricSelection,
  DataRevisionComparison,
  DataSeriesPoint,
  GraphLayer,
  GraphNodeView,
  LibraryView,
  LinkReviewPayload,
  NewResearchView,
  PlanAsset,
  ProposeEvidenceResult,
  RelationshipGraphView,
  ResearchPlanView,
  ReviewQueueView,
  ReviewQueueViewItem,
  ThemeClaim,
  ThemeFund,
  ThemeHypothesisLink,
  ThemeIndexEntry,
  ThemeIndexView,
  ThemeStatus,
  ThemeStock,
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

const EMPTY_METRIC_DETAIL: DataCenterView["selectedMetric"] = {
  id: "",
  name: "（暂无指标）",
  entity: "",
  value: "—",
  unit: "",
  period: "—",
  asOf: "—",
  publishedAt: "—",
  availableAt: "—",
  acquiredAt: "—",
  source: "—",
  methodology: "—",
  revision: "—",
  providerRunId: "—",
  failureMeaning:
    "刷新失败只表示本次未取得新版本，不撤销或推测替换已冻结观测。",
};

function buildRevisionComparison(
  series: DataSeriesPoint[],
): DataRevisionComparison {
  const latest = series[series.length - 1];
  const previous = series[series.length - 2];
  if (!latest || !previous) {
    return {
      oldValue: "—",
      oldSource: "—",
      oldCutoffMeaning: "观测点不足两个，暂无修订对照",
      newValue: latest?.value ?? "—",
      newSource: latest?.status ?? "—",
      newCutoffMeaning: "—",
      whyItMatters:
        "修订对照需要同一指标的至少两个冻结观测；新来源只能进入后续快照，不能回写历史截止日。",
    };
  }
  return {
    oldValue: previous.value,
    oldSource: previous.status,
    oldCutoffMeaning: `冻结于 ${previous.period}`,
    newValue: latest.value,
    newSource: latest.status,
    newCutoffMeaning: `冻结于 ${latest.period}`,
    whyItMatters:
      "新观测只能进入后续快照，不能回写历史截止日当时可知的信息。",
  };
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

  /** Prototype routes carry fixture ids (e.g. RC-AIC-2025-01); in live mode
   * fall back to the first real case unless the id is already a UUID. */
  private async resolveCaseId(caseId: string): Promise<string> {
    const UUID_RE =
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (UUID_RE.test(caseId)) return caseId;
    const cases = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    const first = cases.items[0];
    if (!first) {
      throw new PageStateError("parse_failed", "no research case exists yet");
    }
    return first.id;
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
    const cases = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    const first = cases.items[0];
    if (!first) {
      throw new PageStateError("parse_failed", "no research case exists yet");
    }
    const overview = await this.get<Schemas["OverviewResponse"]>(
      `/overview${this.buildQuery({ case_id: first.id })}`,
    );
    // 任务队列 / 证据变化 / 活动流: explicitly out of target scope (binding
    // doc 缺口清单 "明确不建"); the screen labels these blocks as 示例.
    const fixture = await (
      await import("./prototypeFixture")
    ).buildWorkspaceOverviewScreen();

    const assessment = overview.assessment;
    const CONCLUSION_LABEL: Record<string, string> = {
      supported: "支持（成立）",
      contradicted: "反驳（不成立）",
      insufficient_evidence: "证据不足",
    };
    const thesisStatement =
      typeof overview.thesis?.statement === "string"
        ? overview.thesis.statement
        : "";
    const bullets: string[] = [];
    if (thesisStatement) bullets.push(`焦点命题：${thesisStatement}`);
    if (assessment) {
      bullets.push(
        `${CONCLUSION_LABEL[assessment.conclusion] ?? assessment.conclusion}：${assessment.rationale}`,
      );
      for (const gap of assessment.gaps) bullets.push(`缺口：${gap}`);
    }
    if (bullets.length === 0) bullets.push("（暂无评估结论）");

    return {
      caseId: first.id,
      caseTitle: first.title,
      caseTopic: first.topic,
      caseTopicTags: [first.topic],
      lastUpdatedAt: first.updated_at.slice(0, 10),
      caseCountLabel: `${cases.items.length} 个研究案例`,
      tabs: [
        { id: "summary", label: "结论", count: bullets.length },
        { id: "evidence", label: "证据", count: overview.totals.evidence_total },
        { id: "pending", label: "待审核", count: overview.totals.pending_review },
        { id: "gaps", label: "缺口", count: overview.totals.major_gaps },
      ],
      bullets,
      keyChanges: overview.key_changes.map((kc) => ({
        id: kc.id,
        // View union has no 缺口 variant; fold it into 风险.
        tag: kc.tag === "缺口" ? "风险" : kc.tag,
        text: kc.text,
        detail:
          kc.review_state === "reviewed"
            ? "已人工复核"
            : kc.review_state === "rejected"
              ? "已驳回"
              : "AI 提议 · 未经人工复核",
        occurredAt: kc.occurred_at.slice(0, 10),
        sourceLabel: kc.source_label || "—",
      })),
      framework: overview.framework.map((node, i) => {
        const description =
          typeof node.description === "string" ? node.description : "";
        return {
          id: String(node.id ?? `step-${i}`),
          sequence: String(node.sequence ?? i + 1),
          title:
            description.length > 40
              ? `${description.slice(0, 40)}…`
              : description,
          description,
          expanded: false,
          children: [],
        };
      }),
      totals: {
        evidenceTotal: overview.totals.evidence_total,
        reliablePct: null,
        pendingReview: overview.totals.pending_review,
        majorBlockers: overview.totals.major_gaps,
      },
      taskQueue: fixture.taskQueue,
      evidenceChanges: fixture.evidenceChanges,
      activity: fixture.activity,
    };
  }

  async getThemeIndexView(): Promise<ThemeIndexView> {
    const cases = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    const entries = await Promise.all(
      cases.items.map(async (c) => {
        const dossier = await this.get<Schemas["DossierResponse"]>(
          `/research-cases/${c.id}/dossier${this.buildQuery({
            research_mode: "true",
          })}`,
        );
        const records = Object.values(dossier.evidence).flat();
        const contradicts = records.filter(
          (r) => r.role === "contradicts",
        ).length;
        const focus = dossier.theses.find(
          (t) => t.id === dossier.focus_thesis_id,
        );
        const status: ThemeIndexEntry["status"] = dossier.assessment?.review
          ? "frozen"
          : dossier.assessment
            ? "validating"
            : dossier.theses.length > 0
              ? "monitoring"
              : "draft";
        const STATUS_LABEL: Record<ThemeIndexEntry["status"], string> = {
          monitoring: "监测中",
          validating: "持续验证",
          frozen: "已冻结",
          draft: "草稿",
        };
        return {
          id: c.id,
          name: c.title,
          industry: c.topic,
          hypothesis: focus?.title ?? focus?.statement ?? "",
          status,
          statusLabel: STATUS_LABEL[status],
          claimCount: records.length,
          conflictCount: contradicts,
          lastUpdatedAt: c.updated_at.slice(0, 10),
        } satisfies ThemeIndexEntry;
      }),
    );
    return {
      themes: entries,
      totals: {
        themes: entries.length,
        validating: entries.filter((e) => e.status === "validating").length,
        frozen: entries.filter((e) => e.status === "frozen").length,
        conflictPairs: entries.reduce((sum, e) => sum + e.conflictCount, 0),
      },
      filters: {
        industries: [...new Set(entries.map((e) => e.industry))],
        statuses: ["monitoring", "validating", "frozen", "draft"],
      },
    };
  }

  async getThemeWorkbenchView(themeId: string): Promise<ThemeWorkbenchView> {
    const resolvedCaseId = await this.resolveCaseId(themeId);
    const dossier = await this.get<Schemas["DossierResponse"]>(
      `/research-cases/${resolvedCaseId}/dossier${this.buildQuery({
        research_mode: "true",
      })}`,
    );
    // Per-thesis evidence: the dossier only expands the focus thesis, so the
    // remaining theses are fetched in parallel (same pattern as screen 4).
    const perThesisRecords = new Map<string, Schemas["EvidenceRecordDTO"][]>();
    perThesisRecords.set(
      dossier.focus_thesis_id,
      Object.values(dossier.evidence).flat(),
    );
    await Promise.all(
      dossier.theses
        .filter((t) => !perThesisRecords.has(t.id))
        .map(async (t) => {
          const sub = await this.get<Schemas["DossierResponse"]>(
            `/research-cases/${resolvedCaseId}/dossier${this.buildQuery({
              thesis_id: t.id,
              research_mode: "true",
            })}`,
          );
          perThesisRecords.set(t.id, Object.values(sub.evidence).flat());
        }),
    );

    const SENTIMENT_OF: Record<string, ThemeClaim["sentiment"]> = {
      supports: "positive",
      contradicts: "negative",
      contextualizes: "neutral",
    };
    const DOC_TYPE_OF: Record<string, ThemeClaim["documentType"]> = {
      disclosed_fact: "公告",
      management_attribution: "财报",
      forecast: "研报",
      research_opinion: "研报",
    };
    const toClaim = (
      r: Schemas["EvidenceRecordDTO"],
      thesisId: string,
    ): ThemeClaim => ({
      id: r.link_id,
      content: r.statement_text ?? "（陈述内容缺失）",
      sentiment: SENTIMENT_OF[r.role] ?? "neutral",
      // Backend keeps no confidence score; 0 renders as "—" in the UI.
      confidence: 0,
      sourceLabel: r.statement_kind ?? "—",
      documentTitle: "—",
      documentType: DOC_TYPE_OF[r.statement_kind ?? ""] ?? "新闻",
      publishedAt: r.available_at.slice(0, 10),
      snippet: r.verbatim_text ?? r.statement_text ?? "—",
      span: r.span_id ?? "—",
      hypothesisIds: [thesisId],
      isAiProposed: r.review_state !== "reviewed" && r.review_state !== "confirmed",
    });

    const claims: ThemeClaim[] = [];
    const hypothesisLinks: ThemeHypothesisLink[] = dossier.theses.map((t) => {
      const records = perThesisRecords.get(t.id) ?? [];
      const thesisClaims = records.map((r) => toClaim(r, t.id));
      // Cross-link support/contradict claims of the same thesis as conflicts.
      const supports = thesisClaims.filter((c) => c.sentiment === "positive");
      const contradicts = thesisClaims.filter((c) => c.sentiment === "negative");
      for (const s of supports) {
        s.conflictsWith = contradicts.map((c) => c.id);
      }
      for (const c of contradicts) {
        c.conflictsWith = supports.map((s) => s.id);
      }
      claims.push(...thesisClaims);
      const isFocusAssessed =
        t.id === dossier.focus_thesis_id && dossier.assessment;
      return {
        hypothesis: {
          id: t.id,
          label: t.title ?? t.statement,
          supportCount: supports.length,
          contradictCount: contradicts.length,
          status:
            contradicts.length > 0
              ? "contested"
              : isFocusAssessed && dossier.assessment?.review
                ? "validated"
                : supports.length > 0
                  ? "validated"
                  : "unverified",
        },
        claims: thesisClaims,
      } satisfies ThemeHypothesisLink;
    });

    const exposure = await this.get<Schemas["FundExposureResponse"]>(
      `/research-cases/${resolvedCaseId}/fund-exposure`,
    );
    const stockMap = new Map<string, ThemeStock>();
    const funds: ThemeFund[] = exposure.funds.map((f) => {
      const topHoldings = f.positions
        .slice()
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 5)
        .map((p) => ({ code: p.stock_code, name: p.stock_name, weight: p.weight }));
      for (const p of f.positions) {
        const existing = stockMap.get(p.stock_id);
        if (existing) {
          existing.exposure += p.weight;
        } else {
          stockMap.set(p.stock_id, {
            code: p.stock_code,
            name: p.stock_name,
            industry: "—",
            pe: p.pe_ttm ?? 0,
            pb: p.pb ?? 0,
            roe: 0,
            marketCap: "—",
            valuationUpdatedAt: p.report_period,
            exposure: p.weight,
          });
        }
      }
      return {
        code: f.fund_code,
        name: f.fund_name,
        scale: "—",
        themeExposure: f.theme_exposure,
        topHoldings,
      } satisfies ThemeFund;
    });

    const assessment = dossier.assessment;
    const status: ThemeStatus = assessment?.review
      ? "frozen"
      : assessment
        ? "validating"
        : dossier.theses.length > 0
          ? "monitoring"
          : "draft";
    const STATUS_LABEL: Record<ThemeStatus, string> = {
      monitoring: "监测中",
      validating: "持续验证",
      frozen: "已冻结",
      draft: "草稿",
    };
    const focus = dossier.theses.find((t) => t.id === dossier.focus_thesis_id);
    return {
      id: dossier.case.id,
      name: dossier.case.title,
      industry: dossier.case.topic,
      hypothesis: focus?.title ?? focus?.statement ?? "",
      cutoff: dossier.basis.cutoff,
      snapshotId: "—",
      status,
      statusLabel: STATUS_LABEL[status],
      hypothesisLinks,
      claims,
      stocks: [...stockMap.values()],
      funds,
      chain: [],
      conflictCount: claims.filter((c) => c.sentiment === "negative").length,
    };
  }

  async getNewResearchView(): Promise<NewResearchView> {
    // A new case does not exist yet; only the asset summary and the (out of
    // scope) plan preview are data-driven. The thesis list starts blank.
    const [documents, catalog, reviewed, cases] = await Promise.all([
      this.get<Schemas["DocumentListResponse"]>(`/documents`),
      this.get<Schemas["MetricCatalogResponse"]>(`/metrics/catalog`),
      this.get<Schemas["KnowledgeResponse"]>(
        `/knowledge${this.buildQuery({ review_state: "reviewed", limit: "500" })}`,
      ),
      this.get<Schemas["CaseListResponse"]>(`/research-cases`),
    ]);
    // Provider 查询计划 / 证据检索计划: no backend entity (binding doc 缺口
    // 清单 "明确不建"); keep the fixture plan block labeled 非目标范围.
    const fixture = await (
      await import("./prototypeFixture")
    ).buildNewResearchView();
    return {
      caseId: "",
      caseTitle: "新建研究",
      caseQuestion: "",
      researchObject: "",
      phenomenon: "",
      researchPeriod: { start: "", end: "" },
      studyRange: "",
      cutoff: "",
      snapshotId: "",
      theses: [
        {
          id: "TH-DRAFT-1",
          origin: "human",
          lastEditedBy: "human",
          title: "",
          statement: "",
          observationStart: "",
          observationEnd: "",
          supportCondition: "",
          falsifier: "",
          nextValidationEvent: "",
        },
      ],
      confirmedTheses: [],
      activeStep: 2,
      stageStatus: "草稿",
      assets: {
        documentCount: documents.items.length,
        statementCount: documents.items.reduce(
          (sum, d) => sum + d.statement_count,
          0,
        ),
        metricCount: catalog.entries.length,
        reviewedLinkCount: reviewed.items.length,
        relatedCaseIds: cases.items.map((c) => c.id),
      },
      plan: fixture.plan,
    };
  }

  async createCase(input: CreateCaseInput): Promise<CreateCaseResult> {
    const dto = await this.post<Schemas["CreateCaseResponse"]>(
      `/research-cases`,
      {
        title: input.title,
        industry_topic: input.industryTopic,
        created_by: input.createdBy,
        research_object: input.researchObject || null,
        phenomenon: input.phenomenon || null,
        core_question: input.coreQuestion || null,
        period_start: input.periodStart || null,
        period_end: input.periodEnd || null,
        initial_theses: input.theses.map((t) => ({
          statement: t.statement,
          title: t.title || null,
          observation_start: t.observationStart || null,
          observation_end: t.observationEnd || null,
          support_condition: t.supportCondition || null,
          falsification_condition: t.falsificationCondition || null,
          next_verification_event: t.nextVerificationEvent || null,
          creator_type: t.creatorType,
        })),
      },
    );
    return {
      caseId: dto.case_id,
      thesisIds: dto.theses.map((t) => t.id),
    };
  }

  async listCaseSummaries(): Promise<CaseSummaryItem[]> {
    const cases = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    return cases.items.map((c) => ({
      id: c.id,
      title: c.title,
      topic: c.topic,
      updatedAt: c.updated_at,
    }));
  }

  async getResearchPlanView(): Promise<ResearchPlanView> {
    const cases = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    const first = cases.items[0];
    if (!first) {
      throw new PageStateError("parse_failed", "no research case exists yet");
    }
    const [gapsDto, documents, queue, runs] = await Promise.all([
      this.get<Schemas["CaseGapsResponse"]>(
        `/research-cases/${first.id}/gaps`,
      ),
      this.get<Schemas["DocumentListResponse"]>(`/documents`),
      this.get<Schemas["ReviewQueueResponse"]>(
        `/review-queue${this.buildQuery({ case_id: first.id, limit: "50" })}`,
      ),
      this.get<Schemas["ProviderRunsResponse"]>(
        `/provider-runs${this.buildQuery({ limit: "20" })}`,
      ),
    ]);
    // Provider 查询计划 / 采集编排 / 计划指标: no backend entity (binding
    // doc "明确不建"); keep the fixture blocks labeled 非目标范围.
    const fixture = await (
      await import("./prototypeFixture")
    ).buildResearchPlanView();

    const assets: PlanAsset[] = documents.items.map((d) => ({
      id: d.id,
      kind: "document",
      label: d.source_url,
      sourceVersion: d.parser_version,
      sourceSpan: `${d.span_count} spans`,
      reviewState: "reviewed",
      reviewCount: d.statement_count,
      selected: false,
    }));

    return {
      case: {
        id: first.id,
        researchPeriod: "—",
        cutoff: gapsDto.cutoff,
        revision: "—",
      },
      existingAssets: assets,
      orderedAssets: assets,
      assetPageSize: assets.length || 1,
      providerQueries: fixture.providerQueries,
      collection: fixture.collection,
      pendingResults: queue.items.map((item) => ({
        id: item.link_id,
        targetLabel: item.thesis_statement,
        task: `确认关系 ${item.ai_role}：${item.ai_reason}`,
        sourceId: item.statement_id,
        sourceVersion: item.document_version_id,
        reviewLabel: "待人工复核",
      })),
      gaps: gapsDto.gaps.map((g) => ({
        id: g.assessment_id,
        label: g.gap,
        scope: g.thesis_statement,
        type: "factor",
      })),
      resultMetrics: fixture.resultMetrics,
      failures: runs.runs
        .filter((r) => r.status !== "success")
        .map((r) => ({
          id: r.id.slice(0, 8),
          provider: r.model_version,
          outcome: r.status,
          observedAt: r.started_at,
          detail: r.error ?? "（无错误详情）",
        })),
      permissionGaps: [],
      manualUploads: [],
    };
  }

  async getCaseWorkbenchView(
    caseId: string,
  ): Promise<CaseWorkbenchView> {
    const resolvedCaseId = await this.resolveCaseId(caseId);
    // research_mode reveals AI-proposed links; the view labels each record's
    // review state so unreviewed evidence is never presented as confirmed.
    const dto = await this.get<Schemas["DossierResponse"]>(
      `/research-cases/${resolvedCaseId}/dossier${this.buildQuery({
        research_mode: "true",
      })}`,
    );
    const focus =
      dto.theses.find((t) => t.id === dto.focus_thesis_id) ?? dto.theses[0];
    // The dossier evidence map is grouped by role and only covers the focus
    // thesis; per-thesis rows below fetch their own dossier for counts.
    const focusEvidence = Object.values(dto.evidence).flat();
    const perThesisEvidence = new Map<string, Schemas["EvidenceRecordDTO"][]>();
    if (focus) perThesisEvidence.set(focus.id, focusEvidence);
    await Promise.all(
      dto.theses
        .filter((t) => !perThesisEvidence.has(t.id))
        .map(async (t) => {
          const sub = await this.get<Schemas["DossierResponse"]>(
            `/research-cases/${resolvedCaseId}/dossier${this.buildQuery({
              thesis_id: t.id,
              research_mode: "true",
            })}`,
          );
          perThesisEvidence.set(t.id, Object.values(sub.evidence).flat());
        }),
    );

    const ROLE_NORMALIZE: Record<string, string> = {
      supports: "support",
      contradicts: "contradict",
      contextualizes: "context",
    };
    const REVIEW_LABEL = (state: string) =>
      state === "reviewed" || state === "confirmed"
        ? "已人工复核"
        : "AI 提议 · 未经人工复核";
    const CONCLUSION_LABEL: Record<string, string> = {
      supported: "支持（成立）",
      contradicted: "反驳（不成立）",
      insufficient_evidence: "证据不足",
    };

    const allRecords = Object.values(dto.evidence).flat();
    const firstContradict = allRecords.find((r) => r.role === "contradicts");

    const sources: CaseWorkbenchSourceRow[] = focusEvidence.map((r) => ({
      id: r.link_id,
      relation: ROLE_NORMALIZE[r.role] ?? r.role,
      relationLabel:
        r.role === "supports" ? "支持" : r.role === "contradicts" ? "反驳" : "佐证",
      statement: r.statement_text ?? "（陈述内容缺失）",
      documentId: r.span_id ?? "—",
      sourceVersion: "—",
      publishedDate: r.available_at.slice(0, 10),
      sourceSpan: r.verbatim_text ?? "—",
      reviewState: r.review_state,
      reviewLabel: REVIEW_LABEL(r.review_state),
      snapshotMembership: "—",
      frozenEligibility:
        r.review_state === "reviewed" || r.review_state === "confirmed"
          ? "reviewed"
          : "excluded",
    }));

    const thesisRows: CaseWorkbenchThesisRow[] = dto.theses.map((t) => {
      const records = perThesisEvidence.get(t.id) ?? [];
      const reviewedCount = records.filter(
        (r) => r.review_state === "reviewed" || r.review_state === "confirmed",
      ).length;
      return {
        id: t.id,
        title: t.title ?? t.statement,
        supportCondition: t.support_condition ?? "—",
        evidenceState:
          records.length === 0
            ? "尚无证据关系"
            : reviewedCount === records.length
              ? "已有已审核关系"
              : "已有已审核关系 · 另有待审核关系",
        relationLabels: records
          .map((r) =>
            r.role === "supports" ? "支持" : r.role === "contradicts" ? "反驳" : "佐证",
          )
          .join(" · "),
        scope:
          t.observation_start && t.observation_end
            ? `${t.observation_start} — ${t.observation_end}`
            : "—",
        falsifier: t.falsification_condition ?? "—",
        reviewState: t.review_state,
        evidenceReviewState:
          records.length === 0
            ? "no_links"
            : reviewedCount < records.length
              ? "pending_relationship_review"
              : "reviewed_links_present",
        frozenEligibility:
          records.length > 0 && reviewedCount === records.length
            ? "reviewed"
            : "excluded",
        selected: t.id === (focus?.id ?? ""),
      };
    });

    const factorRows: CaseWorkbenchFactorRow[] = dto.causal_chain.map((step) => ({
      factorId: step.id,
      groupLabel: "因果链",
      roleLabel: `步骤 ${step.sequence}`,
      statusLabel: "—",
      label: step.description,
      timeOrder: String(step.sequence),
      mechanism: step.description,
      directEvidence: "—",
      alternatives: dto.competitive_explanations.join("；") || "—",
      differenceExplanation: "—",
      scope: "—",
      falsifier: "—",
      counterexample: "—",
      impactObject: "—",
    }));
    const EMPTY_FACTOR: CaseWorkbenchFactorRow = {
      factorId: "—",
      groupLabel: "—",
      roleLabel: "—",
      statusLabel: "—",
      label: "（暂无因果链步骤）",
      timeOrder: "—",
      mechanism: "—",
      directEvidence: "—",
      alternatives: "—",
      differenceExplanation: "—",
      scope: "—",
      falsifier: "—",
      counterexample: "—",
      impactObject: "—",
    };

    const assessment = dto.assessment;
    const formalJudgment: CaseWorkbenchFormalJudgment = assessment
      ? {
          text: `${CONCLUSION_LABEL[assessment.conclusion] ?? assessment.conclusion}：${assessment.rationale}`,
          rationale: assessment.gaps.length
            ? `待补缺口：${assessment.gaps.join("；")}`
            : "当前评估未标注额外缺口。",
          reviewState: assessment.review ? "reviewed" : "pending",
          snapshotId: "—",
          reviewedAt: assessment.review?.reviewed_at ?? "",
        }
      : dto.assess_failure
        ? {
            text: `AI 评估未完成：${dto.assess_failure.error}`,
            rationale: `模型 ${dto.assess_failure.model_version} 于 ${dto.assess_failure.failed_at} 评估失败；失败不撤销已有冻结证据。`,
            reviewState: "failed",
            snapshotId: "—",
            reviewedAt: "",
          }
        : {
            text: "尚无 AI 评估结果。",
            rationale: "该案例尚未运行评估，或评估不在当前证据截止日可见范围内。",
            reviewState: "pending",
            snapshotId: "—",
            reviewedAt: "",
          };

    return {
      case: {
        id: dto.case.id,
        title: dto.case.title,
        question: "",
        researchObject: dto.case.topic,
        researchPeriod:
          focus?.observation_start && focus?.observation_end
            ? `${focus.observation_start} — ${focus.observation_end}`
            : "—",
        cutoff: dto.basis.cutoff,
        snapshotId: "—",
        aiState: assessment ? "AI 已评估" : dto.assess_failure ? "评估失败" : "未评估",
        humanReviewState: assessment?.review
          ? `已人工复核 · ${assessment.review.reviewer}`
          : "未人工复核",
      },
      tabs: ["研究摘要", "关键图表", "核心观点", "风险与假设", "相关公司", "研究日志"],
      formalJudgment,
      aiDraft: assessment?.rationale ?? "",
      contradiction: firstContradict
        ? {
            id: firstContradict.link_id,
            label: firstContradict.statement_text ?? "（反驳陈述缺失）",
          }
        : { id: "—", label: "（暂无反驳证据）" },
      gap: dto.gaps.length
        ? { id: "gap-0", label: dto.gaps[0], explanation: dto.gaps.join("；") }
        : { id: "—", label: "（暂无已识别缺口）", explanation: "" },
      nextValidation: {
        thesisId: focus?.id ?? "—",
        event: focus?.next_verification_event ?? "—",
      },
      thesisRows,
      rebuttal: firstContradict
        ? {
            id: firstContradict.link_id,
            statement: firstContradict.statement_text ?? "（反驳陈述缺失）",
            documentId: firstContradict.span_id ?? "—",
            documentTitle: "—",
            sourceVersion: "—",
            publishedDate: firstContradict.available_at.slice(0, 10),
            sourceSpan: firstContradict.verbatim_text ?? "—",
            reviewLabel: REVIEW_LABEL(firstContradict.review_state),
            reviewState: firstContradict.review_state,
            relation: "contradict",
            snapshotMembership: "—",
            frozenEligibility:
              firstContradict.review_state === "reviewed" ? "已冻结可用" : "未复核 · 不入快照",
          }
        : {
            id: "—",
            statement: "（暂无反驳证据）",
            documentId: "—",
            documentTitle: "—",
            sourceVersion: "—",
            publishedDate: "—",
            sourceSpan: "—",
            reviewLabel: "—",
            reviewState: "—",
            relation: "contradict",
            snapshotMembership: "—",
            frozenEligibility: "—",
          },
      factorRows,
      selectedFactor: factorRows[0] ?? EMPTY_FACTOR,
      sources,
    };
  }

  async getRelationshipGraphView(
    caseId: string,
  ): Promise<RelationshipGraphView> {
    const resolvedCaseId = await this.resolveCaseId(caseId);
    const dto = await this.get<Schemas["GraphResponse"]>(
      `/research-cases/${resolvedCaseId}/graph${this.buildQuery({
        research_mode: "true",
      })}`,
    );
    const caseDto = await this.get<Schemas["CaseListResponse"]>(
      `/research-cases`,
    );
    const caseSummary = caseDto.items.find((c) => c.id === resolvedCaseId);

    // review_state arrives on evidence edges (thesis -> statement).
    const reviewByStatement = new Map<string, string>();
    for (const edge of dto.edges) {
      if (edge.semantic_kind === "evidence" && edge.review_state) {
        reviewByStatement.set(edge.target, edge.review_state);
      }
    }

    const KIND_LABEL: Record<string, string> = {
      case: "研究案例",
      thesis: "命题",
      statement: "来源事实",
      step: "因果步骤",
      company: "公司",
      stock: "股票",
      fund: "基金",
      valuation: "估值",
    };
    const LAYER_OF: Record<string, GraphLayer["key"]> = {
      statement: "evidence",
      thesis: "thesis",
      step: "causal",
      company: "company",
      stock: "company",
      fund: "fund",
      valuation: "fund",
    };
    const LAYER_LABEL: Record<GraphLayer["key"], string> = {
      evidence: "证据",
      thesis: "命题",
      causal: "因果链",
      company: "公司",
      fund: "基金",
    };

    const toNodeView = (node: Schemas["GraphNodeDTO"]): GraphNodeView => {
      const review = reviewByStatement.get(node.id);
      return {
        id: node.id,
        layer: node.kind,
        title: node.label,
        meta: String(node.properties?.statement_kind ?? node.kind),
        kind: node.kind,
        kindLabel: KIND_LABEL[node.kind] ?? node.kind,
        relation: String(node.properties?.reason ?? ""),
        review: review
          ? review === "reviewed"
            ? "已人工复核"
            : "AI 提议 · 未经人工复核"
          : "—",
        sourceName: "—",
        sourceSpan: "—",
        sourceHref: "",
        attachment: "—",
        publicationDate: "—",
        asOf: "—",
        scope: String(node.properties?.code ?? ""),
        citations: [],
      };
    };

    const layerKeys: GraphLayer["key"][] = [
      "evidence",
      "thesis",
      "causal",
      "company",
      "fund",
    ];
    const nodes = dto.nodes.filter((n) => n.kind !== "case").map(toNodeView);
    const layers: GraphLayer[] = layerKeys.map((key) => ({
      key,
      label: LAYER_LABEL[key],
      nodes: dto.nodes
        .filter((n) => LAYER_OF[n.kind] === key)
        .map(toNodeView),
    }));

    return {
      case: {
        id: resolvedCaseId,
        title: caseSummary?.title ?? "",
        question: "",
        cutoff: dto.basis?.cutoff ?? "",
        snapshotId: "",
      },
      layers,
      nodes,
      selectedNodeId: nodes[0]?.id ?? "",
    };
  }

  async getLibraryView(): Promise<LibraryView> {
    const documents = await this.get<Schemas["DocumentListResponse"]>(
      `/documents`,
    );
    if (documents.items.length === 0) {
      throw new PageStateError("parse_failed", "no documents exist yet");
    }
    const [details, knowledge, queue] = await Promise.all([
      // Fetch every document's spans so the reading pane excerpt/exact-span
      // follows the selected document instead of sticking to the first one.
      Promise.all(
        documents.items.map((d) =>
          this.get<Schemas["DocumentDetailResponse"]>(`/documents/${d.id}`),
        ),
      ),
      this.get<Schemas["KnowledgeResponse"]>(
        `/knowledge${this.buildQuery({ review_state: "reviewed", limit: "1" })}`,
      ),
      this.get<Schemas["ReviewQueueResponse"]>(
        `/review-queue${this.buildQuery({ limit: "1" })}`,
      ),
    ]);
    const firstSpanByDoc = new Map(
      details.map((det) => [det.document.id, det.spans[0]]),
    );
    const EXCERPT_LIMIT = 800;
    const excerptOf = (text: string | undefined): string => {
      if (!text) return "（暂无原文区段）";
      return text.length > EXCERPT_LIMIT
        ? `${text.slice(0, EXCERPT_LIMIT)}…`
        : text;
    };
    // Thesis titles are not part of the knowledge payload; resolve them from
    // the first case's dossier when available (best effort).
    const thesisTitles = new Map<string, string>();
    try {
      const cases = await this.get<Schemas["CaseListResponse"]>(
        `/research-cases`,
      );
      const first = cases.items[0];
      if (first) {
        const dossier = await this.get<Schemas["DossierResponse"]>(
          `/research-cases/${first.id}/dossier`,
        );
        for (const t of dossier.theses) {
          thesisTitles.set(t.id, t.title ?? t.statement);
        }
      }
    } catch {
      // Title lookup is best-effort; fall back to "—".
    }

    const dateLabel = (v: string | null) => (v ? v.slice(0, 10) : "—");
    const DOC_KIND_LABEL: Record<string, string> = {
      research_report: "研报",
      announcement: "公告",
      news: "资讯",
    };
    const toDocument = (
      d: Schemas["DocumentSummaryDTO"],
    ): LibraryView["documents"][number] => {
      const span = firstSpanByDoc.get(d.id);
      return {
        id: d.id,
        // Prefer ingest-time locator metadata (research report title, issuer
        // org, document kind); fall back to the raw source URL when absent.
        title: d.title ?? d.source_url,
        sourceName: d.org ?? d.source_url,
        sourceVersion: d.parser_version,
        documentType: (d.doc_kind && DOC_KIND_LABEL[d.doc_kind]) ?? "未分类",
        entity: d.entity ?? "—",
        reuseCount: d.statement_count,
        reviewState: d.parse_state === "parsed" ? "reviewed" : "pending_review",
        publishedLabel: dateLabel(d.published_at),
        availableLabel: dateLabel(d.available_at),
        acquiredLabel: dateLabel(d.acquired_at),
        previousVersion: d.supersedes_id ?? "—",
        linkedCaseIds: [],
        reuseHistory: [],
        sourceExcerpt: excerptOf(span?.verbatim_text),
        exactSpan: span ? JSON.stringify(span.locator) : "—",
      };
    };
    const docs = documents.items.map(toDocument);

    const selected: LibraryView["selected"] = docs[0];

    const ROLE_LABEL: Record<string, string> = {
      supports: "支持",
      contradicts: "反驳",
      contextualizes: "佐证",
    };
    const knowledgeItem = knowledge.items[0];
    const knowledgeLink = knowledgeItem?.links[0];
    const queueItem = queue.items[0];

    return {
      cutoff: documents.basis.cutoff,
      snapshotId: "—",
      documents: docs,
      selected,
      knowledge:
        knowledgeItem && knowledgeLink
          ? {
              statement: {
                id: knowledgeItem.statement_id,
                text: knowledgeItem.statement_text,
              },
              link: {
                id: knowledgeLink.link_id,
                role: knowledgeLink.role,
                reviewedBy: knowledgeLink.latest_reviewer ?? undefined,
                reviewedAt: knowledgeLink.latest_reviewed_at ?? undefined,
              },
              roleLabel: ROLE_LABEL[knowledgeLink.role] ?? knowledgeLink.role,
              thesis: {
                id: knowledgeLink.thesis_id,
                title: thesisTitles.get(knowledgeLink.thesis_id) ?? "—",
              },
              factor: null,
              reviewedBy: knowledgeLink.latest_reviewer ?? "—",
              reviewedAt: knowledgeLink.latest_reviewed_at?.slice(0, 10) ?? "—",
            }
          : null,
      proposal: queueItem
        ? {
            statement: {
              id: queueItem.statement_id,
              text: queueItem.statement_text,
            },
            link: { id: queueItem.link_id, role: queueItem.ai_role },
            roleLabel: `AI 提议 · ${ROLE_LABEL[queueItem.ai_role] ?? queueItem.ai_role}`,
          }
        : null,
    };
  }

  async getDataCenterView(): Promise<DataCenterView> {
    const catalog = await this.get<Schemas["MetricCatalogResponse"]>(
      `/metrics/catalog`,
    );
    const entries = catalog.entries;
    const first = entries[0];
    const selection = first
      ? await this.getDataCenterMetric(first.stock_id, first.metric_name)
      : { selectedMetric: EMPTY_METRIC_DETAIL, series: [] };

    const runs = await this.get<Schemas["ProviderRunsResponse"]>(
      `/provider-runs${this.buildQuery({ limit: "8" })}`,
    );

    return {
      cutoff: first?.latest_as_of ?? "",
      snapshotId: "",
      catalog: entries.map((e) => ({
        id: `${e.stock_id}::${e.metric_name}`,
        label: e.metric_name,
        entity: `${e.stock_name} (${e.stock_code})`,
        cadence: "—",
        state: "截止日可用",
        stockId: e.stock_id,
        metricName: e.metric_name,
      })),
      selectedMetricId: first ? `${first.stock_id}::${first.metric_name}` : "",
      selectedMetric: selection.selectedMetric,
      series: selection.series,
      revisionComparison: buildRevisionComparison(selection.series),
      plannedAttempt: {
        id: "PQ-MOCK",
        label: "计划中能力探测（示例，非目标范围）",
        state: "计划中 · 尚未执行",
        meaning: "任务队列不属于当前目标范围，此块为占位展示。",
      },
      historicalRuns: runs.runs.map((r) => ({
        id: r.id.slice(0, 8),
        providerLabel: r.model_version,
        outcome: r.status === "success" ? "success" : "quota_failure",
        outcomeLabel: r.status === "success" ? "成功" : "失败",
        detailLabel:
          r.status === "success"
            ? r.output_summary
            : (r.error ?? "（无错误详情）"),
        observedAt: r.started_at,
      })),
    };
  }

  async getDataCenterMetric(
    stockId: string,
    metricName: string,
  ): Promise<DataMetricSelection> {
    const dto = await this.get<Schemas["MetricSeriesResponse"]>(
      `/metrics/series${this.buildQuery({
        stock_id: stockId,
        metric_name: metricName,
      })}`,
    );
    const points = dto.points;
    const latest = points[points.length - 1];
    const series: DataSeriesPoint[] = points.map((p) => ({
      period: p.as_of_date,
      value: String(p.value),
      numericValue: p.value,
      acquiredAt: p.as_of_date,
      cutoffUsable: true,
      status: `冻结观测 · ${p.source}`,
    }));
    return {
      selectedMetric: {
        id: `${dto.stock_id}::${dto.metric_name}`,
        name: dto.metric_name,
        entity: dto.stock_id,
        value: latest ? String(latest.value) : "—",
        unit: "",
        period: latest?.as_of_date ?? "—",
        asOf: latest?.as_of_date ?? "—",
        publishedAt: "—",
        availableAt: "—",
        acquiredAt: "—",
        source: latest?.source ?? "—",
        methodology: latest?.definition ?? "—",
        revision: "—",
        providerRunId: "—",
        failureMeaning:
          "刷新失败只表示本次未取得新版本，不撤销或推测替换已冻结观测。",
      },
      series,
    };
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

  async proposeEvidence(thesisId: string): Promise<ProposeEvidenceResult> {
    const dto = await this.post<Schemas["ProposeResponse"]>(
      `/theses/${thesisId}/propose`,
      {},
    );
    return {
      thesisId: dto.thesis_id,
      mode: dto.mode,
      linkCount: dto.link_count,
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