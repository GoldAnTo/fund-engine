import type { components } from "../contracts/v1";
import type {
  Conclusion,
  DocumentsQuery,
  DossierQuery,
  EdgeKind,
  EvidenceRole,
  GraphEdge,
  GraphNode,
  EvidenceRecord,
  NodeKind,
  OverviewQuery,
  RelationshipGraph,
  RelationshipQuery,
  ResearchCaseDossier,
  ResearchCaseSummary,
  ReviewOutcome,
  ReviewQueueItem,
  ReviewState,
  SearchHit,
  SourceDocumentView,
  StatementKind,
  ThesisAssessment,
  DocumentSpan,
  CausalStepView,
  WorkspaceOverview,
  ResearchClient,
} from "../domain/types";
import { PageStateError } from "../domain/types";

type Schemas = components["schemas"];

// The v1 error envelope is produced by the backend exception handler, not the
// OpenAPI schema, so it is declared here manually.
interface V1ErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string;
    details: Record<string, unknown>;
  };
}

const VALID_NODE_KINDS: readonly string[] = [
  "case",
  "thesis",
  "statement",
  "step",
  "company",
  "stock",
  "fund",
  "valuation",
];

const VALID_EDGE_KINDS: readonly string[] = [
  "evidence",
  "causal",
  "theme_role",
  "holding",
  "contains_thesis",
  "company_stock",
  "contains_step",
  "valuation",
];

const VALID_GROUPS: readonly string[] = [
  "evidence",
  "proposition",
  "causal",
  "company",
  "fund",
];

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
      const payload = (await response.json().catch(() => null)) as V1ErrorEnvelope | null;
      if (payload?.error.code === "permission_denied") {
        throw new PageStateError("permission_denied", payload.error.message);
      }
      throw new PageStateError("backend_unavailable", payload?.error.message);
    }
    return (await response.json()) as T;
  }

  private buildQuery(params: Record<string, string | undefined>): string {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined) sp.set(k, v);
    }
    const qs = sp.toString();
    return qs ? `?${qs}` : "";
  }

  // ── Mappers ──────────────────────────────────────────────────────────────

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

  private mapThesisSummary(dto: Schemas["ThesisSummaryDTO"]): ResearchCaseSummary {
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

  private mapStatementKind(value: string | null): StatementKind {
    if (
      value === "disclosed_fact" ||
      value === "management_attribution" ||
      value === "forecast" ||
      value === "research_opinion"
    ) {
      return value;
    }
    return "disclosed_fact";
  }

  private mapReviewState(value: string): ReviewState {
    if (value === "machine_generated" || value === "reviewed" || value === "rejected") {
      return value;
    }
    return "machine_generated";
  }

  private mapEvidence(dto: Schemas["EvidenceRecordDTO"]): EvidenceRecord {
    return {
      link_id: dto.link_id,
      statement_id: dto.statement_id,
      statement_text: dto.statement_text ?? "",
      statement_kind: this.mapStatementKind(dto.statement_kind),
      span_id: dto.span_id,
      verbatim_text: dto.verbatim_text,
      locator: dto.locator,
      reason: dto.reason,
      role: dto.role,
      scope: dto.scope,
      period: dto.observed_period,
      available_at: dto.available_at,
      review_state: this.mapReviewState(dto.review_state),
      source_label: "",
      reliability: 0,
    };
  }

  private mapAssessment(dto: Schemas["AssessmentDTO"]): ThesisAssessment {
    return {
      id: dto.id,
      thesis_id: dto.thesis_id,
      conclusion: dto.conclusion,
      rationale: dto.rationale,
      bullets: [],
      gaps: dto.gaps,
      provisional: dto.provisional,
      review: null,
      major_gap: null,
      status_label: "",
      supply_chain_level: "",
      updated_at: "",
      confidence_label: "",
      focus_axes: [],
    };
  }

  private mapCausalStep(dto: Schemas["CausalStepDTO"]): CausalStepView {
    return {
      id: dto.id,
      sequence: dto.sequence,
      title: "",
      description: dto.description,
      status: "ai_pending_review",
    };
  }

  private mapNodeKind(value: string): NodeKind {
    return VALID_NODE_KINDS.includes(value) ? (value as NodeKind) : "statement";
  }

  private mapEdgeKind(value: string): EdgeKind {
    return VALID_EDGE_KINDS.includes(value) ? (value as EdgeKind) : "causal";
  }

  private mapNode(dto: Schemas["GraphNodeDTO"]): GraphNode {
    const props = (dto.properties ?? {}) as Record<string, unknown>;
    const node: GraphNode = {
      id: dto.id,
      kind: this.mapNodeKind(dto.kind),
      label: dto.label,
    };
    if (typeof props.group === "string" && VALID_GROUPS.includes(props.group)) {
      node.group = props.group as GraphNode["group"];
    }
    if (typeof props.sequence === "number") node.sequence = props.sequence;
    if (typeof props.description === "string") node.description = props.description;
    if (typeof props.chip === "string") node.chip = props.chip;
    if (typeof props.publisher === "string") node.publisher = props.publisher;
    if (typeof props.publish_date === "string") node.publish_date = props.publish_date;
    if (typeof props.reliability_bar === "number") node.reliability_bar = props.reliability_bar;
    if (typeof props.chapter === "string") node.chapter = props.chapter;
    if (typeof props.code === "string") node.code = props.code;
    if (typeof props.sector === "string") node.sector = props.sector;
    if (typeof props.relevance === "number") node.relevance = props.relevance;
    if (typeof props.weight === "string") node.weight = props.weight;
    if (typeof props.report_period === "string") node.report_period = props.report_period;
    if (typeof props.relevance_score === "number")
      node.relevance_score = props.relevance_score;
    return node;
  }

  private mapEdge(dto: Schemas["GraphEdgeDTO"]): GraphEdge {
    const props = (dto.properties ?? {}) as Record<string, unknown>;
    const edge: GraphEdge = {
      id: dto.id,
      kind: this.mapEdgeKind(dto.semantic_kind),
      source: dto.source,
      target: dto.target,
    };
    if (typeof dto.review_state === "string") {
      edge.review_state = this.mapReviewState(dto.review_state);
    }
    if (
      props.role === "supports" ||
      props.role === "contradicts" ||
      props.role === "contextualizes"
    ) {
      edge.role = props.role as EvidenceRole;
    }
    if (typeof props.reason === "string") edge.reason = props.reason;
    if (typeof props.weight === "string") edge.weight = props.weight;
    if (typeof props.report_period === "string") edge.report_period = props.report_period;
    return edge;
  }

  private mapDocument(dto: Schemas["DocumentSummaryDTO"]): SourceDocumentView {
    return {
      id: dto.id,
      title: null,
      publisher: null,
      document_type: null,
      publish_date: dto.published_at ?? "",
      available_at: dto.available_at,
      acquired_at: dto.acquired_at,
      parser_version: dto.parser_version,
      parse_quality: dto.parse_state === "parsed" ? "ok" : "partial",
      linked_cases: [],
      span_count: dto.span_count,
      statement_count: dto.statement_count,
      version_label: "",
    };
  }

  private mapSpan(dto: Schemas["SourceSpanDTO"]): DocumentSpan {
    return {
      id: dto.id,
      document_id: dto.document_version_id,
      locator: dto.locator,
      verbatim_text: dto.verbatim_text,
      cited_by: [],
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
        return "案例";
    }
  }

  // ── ResearchClient implementation ────────────────────────────────────────

  async getOverview(query?: OverviewQuery): Promise<WorkspaceOverview> {
    // The overview landing page has no case_id in its route; use the most
    // recent case from the ledger as the focus case (backend /overview
    // requires case_id).
    const list = await this.get<Schemas["CaseListResponse"]>(
      "/research-cases"
    );
    const focusCase = list.items[0];
    if (!focusCase) {
      throw new PageStateError(
        "backend_unavailable",
        "no research cases available"
      );
    }
    const dto = await this.get<Schemas["OverviewResponse"]>(
      `/overview${this.buildQuery({
        case_id: focusCase.id,
        cutoff: query?.cutoff,
      })}`
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
      })),
      framework: [],
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
    query?: DossierQuery
  ): Promise<ResearchCaseDossier> {
    const dto = await this.get<Schemas["DossierResponse"]>(
      `/research-cases/${encodeURIComponent(caseId)}/dossier${this.buildQuery({
        thesis_id: query?.thesisId,
        cutoff: query?.cutoff,
      })}`
    );
    const evidence = dto.evidence as Record<string, Schemas["EvidenceRecordDTO"][]>;
    const assessment: ThesisAssessment = dto.assessment
      ? this.mapAssessment(dto.assessment)
      : this.mapAssessment({
          id: "",
          thesis_id: dto.focus_thesis_id,
          conclusion: "insufficient_evidence",
          rationale: "",
          gaps: [],
          provisional: true,
          review: null,
        });
    return {
      case: this.mapCaseSummary(dto.case),
      theses: dto.theses.map((t) => this.mapThesisSummary(t)),
      focus_thesis_id: dto.focus_thesis_id,
      tabs: [],
      assessment,
      causal_chain: dto.causal_chain.map((c) => this.mapCausalStep(c)),
      evidence: {
        supports: (evidence.supports ?? []).map((e) => this.mapEvidence(e)),
        contradicts: (evidence.contradicts ?? []).map((e) => this.mapEvidence(e)),
        contextualizes: (evidence.contextualizes ?? []).map((e) => this.mapEvidence(e)),
      },
      competitive_explanations: dto.competitive_explanations,
      gaps: dto.gaps,
      log: [],
    };
  }

  async getRelationshipGraph(
    caseId: string,
    query?: RelationshipQuery
  ): Promise<RelationshipGraph> {
    const dto = await this.get<Schemas["GraphResponse"]>(
      `/research-cases/${encodeURIComponent(caseId)}/graph${this.buildQuery({
        cutoff: query?.cutoff,
      })}`
    );
    const nodes = dto.nodes.map((n) => this.mapNode(n));
    const edges = dto.edges.map((e) => this.mapEdge(e));
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
      nodes,
      edges,
      legend: [],
    };
  }

  async getDocuments(query?: DocumentsQuery): Promise<SourceDocumentView[]> {
    const dto = await this.get<Schemas["DocumentListResponse"]>(
      `/documents${this.buildQuery({
        q: query?.query,
        cutoff: query?.cutoff,
      })}`
    );
    return dto.items.map((d) => this.mapDocument(d));
  }

  async getDocumentDetail(documentId: string): Promise<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  }> {
    const dto = await this.get<Schemas["DocumentDetailResponse"]>(
      `/documents/${encodeURIComponent(documentId)}`
    );
    return {
      document: this.mapDocument(dto.document),
      spans: dto.spans.map((s) => this.mapSpan(s)),
    };
  }

  async search(query: string): Promise<SearchHit[]> {
    const dto = await this.get<Schemas["SearchResponse"]>(
      `/search${this.buildQuery({ q: query })}`
    );
    const hits: SearchHit[] = [];
    for (const group of dto.groups) {
      for (const hit of group.hits) {
        hits.push({
          group: this.mapSearchGroup(hit.object_type),
          id: hit.object_id,
          title: hit.title,
          hint: hit.snippet,
          navigate_to: hit.deep_link,
        });
      }
    }
    return hits;
  }

  async getCaseSummaries(): Promise<ResearchCaseSummary[]> {
    const dto = await this.get<Schemas["CaseListResponse"]>(`/research-cases`);
    return dto.items.map((c) => this.mapCaseSummary(c));
  }

  async getReviewQueue(): Promise<ReviewQueueItem[]> {
    throw new PageStateError(
      "backend_unavailable",
      "review API is not available in live-read delivery"
    );
  }

  async submitReviewDecision(
    _itemId: string,
    _decision: {
      outcome: ReviewOutcome;
      conclusion: Conclusion | null;
      reason: string;
    }
  ): Promise<void> {
    throw new PageStateError(
      "backend_unavailable",
      "review API is not available in live-read delivery"
    );
  }
}
