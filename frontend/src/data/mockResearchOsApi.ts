import type { components } from "../contracts/v1";
import type { ResearchOsApi } from "../app/researchOsApi";

type Schemas = components["schemas"];
type Monitor = Schemas["CaseMonitorDTO"];
type MonitorDetail = Schemas["CaseMonitorDetailResponse"];
type AtomicClaim = Schemas["AtomicClaimCandidateDTO"];
type Protocol = Schemas["CaseMechanismProtocolDTO"];
type Rule = Schemas["VerificationRuleDTO"];

const now = "2026-08-09T09:30:00Z";
const source = {
  source_statement_id: "statement-demo-capex",
  document_version_id: "doc-demo-capex",
  document_title: "公司季度业绩说明",
  source_url: "https://disclosure.example/mock-capex",
  locator: { page: 2, paragraph: 3 },
  available_at: "2026-08-08T20:00:00Z",
  permission_status: "admitted",
};
const factors = [
  { id: "factor-capex", statement: "资本开支增速是否高于此前指引" },
  { id: "factor-margin", statement: "自由现金流压力是否由订单和毛利改善抵消" },
];
const template: Schemas["MechanismTemplateDTO"] = {
  id: "template-capex-v1",
  template_key: "capex-to-fundamental",
  version: 1,
  display_name: "资本开支到基本面验证",
  industry_scope: "AI 基础设施",
  approved_by: "human:methodology",
  reason: "演示用已审核机制模板",
  created_at: now,
  nodes: [
    {
      id: "node-capex",
      node_key: "capex",
      display_name: "资本开支",
      role: "driver",
    },
    {
      id: "node-orders",
      node_key: "orders",
      display_name: "订单与收入",
      role: "mediator",
    },
    {
      id: "node-margin",
      node_key: "margin",
      display_name: "自由现金流",
      role: "outcome",
    },
  ],
  edges: [
    {
      id: "edge-capex-orders",
      edge_key: "capex_to_orders",
      source_node_id: "node-capex",
      target_node_id: "node-orders",
    },
    {
      id: "edge-orders-margin",
      edge_key: "orders_to_margin",
      source_node_id: "node-orders",
      target_node_id: "node-margin",
    },
  ],
};
const metrics: Schemas["MetricDefinitionDTO"][] = [
  {
    id: "metric-capex",
    metric_id: "capex_growth",
    version: 1,
    display_name: "资本开支同比增速",
    entity_scope: "company",
    unit: "%",
    role_eligibility: ["driver"],
    approved_by: "human:methodology",
    reason: "公司一手披露可验证",
    created_at: now,
  },
  {
    id: "metric-fcf",
    metric_id: "free_cash_flow",
    version: 1,
    display_name: "自由现金流",
    entity_scope: "company",
    unit: "USD m",
    role_eligibility: ["outcome"],
    approved_by: "human:methodology",
    reason: "用于验证资金压力",
    created_at: now,
  },
];

export interface MockDocumentSupplementStore {
  createDocumentSupplement(input: {
    caseId: string;
    documentId: string;
    rawText: string;
    claimedPageReference: string;
    createdBy: string;
  }): Promise<{
    documentVersionId: string;
    originalDocumentVersionId: string;
    extractionAllowed: boolean;
  }>;
  getAtomicClaimCandidates(caseId: string): AtomicClaim[];
}

function monitorFor(
  caseId: string,
  version = 1,
  status = "active",
  reason = "建立可回放的演示监控范围",
): Monitor {
  return {
    id: `monitor-${caseId}-v${version}`,
    version,
    status,
    frequency: "weekday_08_30",
    factor_ids: factors.map((factor) => factor.id),
    allowed_source_types: ["licensed_provider", "company_disclosure"],
    next_verification_event: "下一次公司季报披露",
    budget: 20,
    changed_by: "human:researcher",
    change_reason: reason,
    created_at: now,
  };
}

function demoRule(caseId: string, supersedesId: string | null = null): Rule {
  return {
    id: `rule-${caseId}-${supersedesId ? "v2" : "v1"}`,
    research_case_id: caseId,
    mechanism_edge_id: "edge-capex-orders",
    metric_definition_id: "metric-capex",
    expected_direction: "increase",
    support_predicate: "公司一手披露的资本开支同比增长且项目投向明确",
    contradiction_predicate: "同口径资本开支下调或未投向目标业务",
    allowed_source_roles: ["primary_disclosure"],
    observed_period_start: "2026-07-01",
    observed_period_end: "2026-09-30",
    available_at_deadline: "2026-11-15",
    next_verification_event: "2026Q3 季报",
    supersedes_id: supersedesId,
    reviewer: "human:researcher",
    reason: "为演示 Case 固化支持与反证条件",
    created_at: now,
  };
}

function atomicClaim(caseId: string): AtomicClaim {
  return {
    id: `atomic-${caseId}-capex`,
    source_span_id: "sp-tsm-capex",
    document_version_id: "doc-event-tsm-q2",
    document_source_url: "https://disclosure.example/mock-capex",
    locator: { page: 2, paragraph: 3 },
    quote: "公司上调全年资本开支指引，同时市场关注自由现金流承压。",
    quote_start: 0,
    quote_end: 28,
    quote_sha256: "a".repeat(64),
    normalized_text: "公司上调全年资本开支指引。",
    claim_type: "disclosed_fact",
    assertion_actor: "公司",
    authority_level: "primary_disclosure",
    structured_fields: { extraction_run: "mock-extract-1" },
    validation_result: { quote_continuous: true },
    created_at: now,
    review_state: "awaiting_review",
    review_history: [],
    published_source_statement: null,
  };
}

/**
 * Explicit demo data for `?client=mock`. It is intentionally separate from
 * the HTTP client: every route remains clickable without a running ledger,
 * while normal traffic can only use V1 HTTP records.
 */
export class MockResearchOsApi implements ResearchOsApi {
  private monitors = new Map<string, Monitor>();
  private monitorHistory = new Map<string, Monitor[]>();
  private rules = new Map<string, Rule[]>();
  private claims = new Map<string, AtomicClaim[]>();
  private reviewedClaimIds = new Set<string>();
  private bindings = new Map<string, Schemas["OutcomeBindingDTO"]>();
  private manualRuns = new Map<string, { caseId: string; monitor: Monitor }>();
  private reportClaims = new Map<string, Schemas["ReportClaimDTO"][]>();
  private keyFactors = new Map<string, Schemas["KeyFactorDTO"][]>();
  private verifications = new Map<string, Schemas["ClaimVerificationDTO"]>();
  private marketBindings = new Map<
    string,
    Schemas["MarketInstrumentBindingDTO"][]
  >();
  private fundamentalImpacts = new Map<
    string,
    Schemas["FundamentalImpactDTO"][]
  >();
  private marketObservations = new Map<
    string,
    Schemas["MarketObservationDTO"][]
  >();

  constructor(private readonly documentStore?: MockDocumentSupplementStore) {}

  async monitor(caseId: string): ReturnType<ResearchOsApi["monitor"]> {
    const monitor = this.monitors.get(caseId) ?? monitorFor(caseId);
    this.monitors.set(caseId, monitor);
    const history = this.monitorHistory.get(caseId) ?? [monitor];
    this.monitorHistory.set(caseId, history);
    return {
      monitor,
      history,
      latest_run:
        caseId === "event-tsm"
          ? {
              id: "run-demo-1",
              status: "awaiting_review",
              stage: "review",
              updated_at: now,
            }
          : null,
      confirmed_factors: factors,
    };
  }

  async saveMonitor(
    caseId: string,
    input: Parameters<ResearchOsApi["saveMonitor"]>[1],
  ): ReturnType<ResearchOsApi["saveMonitor"]> {
    const previous = this.monitors.get(caseId) ?? monitorFor(caseId);
    const next: Monitor = {
      ...previous,
      id: `monitor-${caseId}-v${previous.version + 1}`,
      version: previous.version + 1,
      frequency: input.frequency,
      factor_ids: [...input.factor_ids],
      allowed_source_types: [...input.allowed_source_types],
      next_verification_event: input.next_verification_event,
      budget: input.budget,
      changed_by: input.actor,
      change_reason: input.change_reason,
      created_at: now,
    };
    this.monitors.set(caseId, next);
    this.monitorHistory.set(caseId, [
      next,
      ...(this.monitorHistory.get(caseId) ?? [previous]),
    ]);
    return next;
  }

  async setMonitorStatus(
    caseId: string,
    status: "active" | "paused",
    changeReason: string,
  ): ReturnType<ResearchOsApi["setMonitorStatus"]> {
    const previous = this.monitors.get(caseId) ?? monitorFor(caseId);
    const next = {
      ...previous,
      id: `monitor-${caseId}-v${previous.version + 1}`,
      version: previous.version + 1,
      status,
      changed_by: "human:researcher",
      change_reason: changeReason,
      created_at: now,
    };
    this.monitors.set(caseId, next);
    this.monitorHistory.set(caseId, [
      next,
      ...(this.monitorHistory.get(caseId) ?? [previous]),
    ]);
    return next;
  }

  async scopeHistory(
    caseId: string,
  ): ReturnType<ResearchOsApi["scopeHistory"]> {
    return {
      case_id: caseId,
      items: [
        {
          version: 1,
          factors: factors.map((factor) => ({
            statement: factor.statement,
            description: null,
          })),
          changed_by: "human:researcher",
          change_reason: "建立初始研究范围",
          created_at: now,
        },
      ],
    };
  }

  async startMonitorRun(
    caseId: string,
  ): ReturnType<ResearchOsApi["startMonitorRun"]> {
    const monitor = this.monitors.get(caseId) ?? monitorFor(caseId);
    this.monitors.set(caseId, monitor);
    const id = `run-${caseId}-manual-v${monitor.version}`;
    this.manualRuns.set(id, { caseId, monitor });
    return {
      id,
      case_id: caseId,
      status: "queued",
      stage: "queued",
      round: 0,
      max_rounds: 3,
      budget: monitor.budget,
      budget_used: 0,
      stop_reason: null,
      scope_thesis_ids: [...monitor.factor_ids],
      progress: {},
      evidence: {},
      by_thesis: {},
      gaps: [],
      gap_tasks: [],
      failed_tasks: [],
      assessments: [],
      pending_proposals: [],
      review_tasks: [],
      next_action: "查看运行详情",
      tasks: [],
    };
  }

  async startFactorMonitorRun(
    caseId: string,
    keyFactorId: string,
  ): ReturnType<ResearchOsApi["startFactorMonitorRun"]> {
    const monitor = this.monitors.get(caseId) ?? monitorFor(caseId);
    const factorId =
      keyFactorId === "factor-capex" ? "factor-capex" : keyFactorId;
    const scopedMonitor = {
      ...monitor,
      factor_ids: [factorId],
      allowed_source_types: monitor.allowed_source_types.filter(
        (source) => source === "company_disclosure",
      ),
    };
    const id = `run-${caseId}-factor-${keyFactorId}`;
    this.manualRuns.set(id, { caseId, monitor: scopedMonitor });
    return {
      id,
      case_id: caseId,
      status: "queued",
      stage: "queued",
      round: 0,
      max_rounds: 3,
      budget: scopedMonitor.budget,
      budget_used: 0,
      stop_reason: null,
      scope_thesis_ids: [factorId],
      progress: {},
      evidence: {},
      by_thesis: {},
      gaps: [],
      gap_tasks: [],
      failed_tasks: [],
      assessments: [],
      pending_proposals: [],
      review_tasks: [],
      next_action: "查看运行详情",
      tasks: [],
    };
  }

  async cancelRun(
    runId: string,
    _changeReason: string,
  ): ReturnType<ResearchOsApi["cancelRun"]> {
    const manualRun = this.manualRuns.get(runId);
    return {
      id: runId,
      status: "cancelled",
      stage: "stopped",
      round: 0,
      max_rounds: 3,
      budget: manualRun?.monitor.budget ?? 20,
      budget_used: 0,
      stop_reason: "cancelled",
      scope_thesis_ids:
        manualRun?.monitor.factor_ids ?? factors.map((factor) => factor.id),
      created_at: now,
      updated_at: now,
      next_action: "查看取消前进度",
    };
  }

  async runEvents(runId: string): ReturnType<ResearchOsApi["runEvents"]> {
    const manualRun = this.manualRuns.get(runId);
    if (manualRun) {
      const { monitor } = manualRun;
      return {
        run_id: runId,
        has_more: false,
        items: [
          {
            seq: 1,
            stage: "scope",
            status: "completed",
            message: "已冻结本次运行范围",
            details: {
              trigger: "manual",
              monitor_version_id: monitor.id,
              factor_ids: monitor.factor_ids,
              factor_statements: factors
                .filter((factor) => monitor.factor_ids.includes(factor.id))
                .map((factor) => factor.statement),
              allowed_source_types: monitor.allowed_source_types,
              budget: monitor.budget,
            },
            created_at: now,
          },
          {
            seq: 2,
            stage: "retrieve",
            status: "queued",
            message: "等待工作器按已冻结的来源许可补证",
            details: {},
            created_at: now,
          },
        ],
      };
    }
    return {
      run_id: runId,
      has_more: false,
      items: [
        {
          seq: 1,
          stage: "scope",
          status: "completed",
          message: "已冻结本次运行范围",
          details: {
            trigger: "schedule",
            monitor_version_id: "monitor-event-tsm-v1",
            factor_ids: factors.map((factor) => factor.id),
            factor_statements: factors.map((factor) => factor.statement),
            allowed_source_types: ["licensed_provider", "company_disclosure"],
            budget: 20,
          },
          created_at: now,
        },
        {
          seq: 2,
          stage: "retrieve",
          status: "completed",
          message: "已按许可读取候选资料",
          details: {
            accepted: 2,
            excluded: 1,
            exclusion_reason: "来源许可不足",
          },
          created_at: now,
        },
        {
          seq: 3,
          stage: "review",
          status: "awaiting_review",
          message: "候选证据等待人工审核，未写入结论",
          details: { pending_review: 1 },
          created_at: now,
        },
      ],
    };
  }

  async activeRuns(): ReturnType<ResearchOsApi["activeRuns"]> {
    return {
      has_more: false,
      items: [
        {
          run_id: "run-demo-1",
          case_id: "event-tsm",
          case_title: "TSM 资本开支与自由现金流验证",
          status: "awaiting_review",
          stage: "review",
          updated_at: now,
          processed_count: 2,
          next_action: "查看运行详情",
          scope: {
            trigger: "schedule",
            monitor_version_id: "monitor-event-tsm-v1",
            factor_ids: factors.map((factor) => factor.id),
            factor_statements: factors.map((factor) => factor.statement),
            allowed_source_types: ["licensed_provider", "company_disclosure"],
            budget: 20,
          },
        },
      ],
    };
  }

  async runs(): ReturnType<ResearchOsApi["runs"]> {
    const active = (await this.activeRuns()).items[0];
    return {
      has_more: false,
      items: [
        { ...active, created_at: "2026-08-09T08:30:00Z", stop_reason: null },
      ],
    };
  }

  async network(): ReturnType<ResearchOsApi["network"]> {
    return {
      reviewed_relations: [
        {
          id: "relation-demo",
          source_case: {
            case_id: "event-tsm",
            title: "TSM 资本开支与自由现金流验证",
            lifecycle_status: "awaiting_key_review",
          },
          target_case: {
            case_id: "event-ai-server",
            title: "AI 服务器订单验证",
            lifecycle_status: "continuing",
          },
          relation_type: "shared_driver",
          reason: "均需核验上游资本开支节奏",
          created_by: "human:researcher",
          review_state: "reviewed",
          created_at: now,
        },
      ],
      candidate_relations: [
        {
          id: "relation-candidate",
          source_case: {
            case_id: "event-tsm",
            title: "TSM 资本开支与自由现金流验证",
            lifecycle_status: "awaiting_key_review",
          },
          target_case: {
            case_id: "event-ai-server",
            title: "AI 服务器订单验证",
            lifecycle_status: "continuing",
          },
          relation_type: "potential_conflict",
          reason: "候选：需求节奏可能不同",
          created_by: "machine:relation",
          review_state: "machine_generated",
          created_at: now,
        },
      ],
    } as ReturnType<ResearchOsApi["network"]> extends Promise<infer T>
      ? T
      : never;
  }

  async caseRelations(
    caseId: string,
  ): ReturnType<ResearchOsApi["caseRelations"]> {
    const network = await this.network();
    return {
      ...network,
      reviewed_relations: network.reviewed_relations.filter(
        (relation) =>
          relation.source_case.case_id === caseId ||
          relation.target_case.case_id === caseId,
      ),
      candidate_relations: network.candidate_relations.filter(
        (relation) =>
          relation.source_case.case_id === caseId ||
          relation.target_case.case_id === caseId,
      ),
    };
  }

  async graph(caseId: string): ReturnType<ResearchOsApi["graph"]> {
    return {
      schema_version: "graph/v1",
      basis: {
        cutoff: now,
        is_historical: false,
        ledger_high_watermark: "mock-ledger-1",
        projection_built_at: now,
        projection_schema_version: "graph/v1",
      },
      nodes: [
        {
          id: "doc-event-tsm-q2",
          kind: "document",
          label: "冻结公司披露",
          properties: {
            verbatim_text:
              "公司上调全年资本开支指引，同时市场关注自由现金流承压。",
            permission_status: "admitted",
            source_visible_in_case: true,
            document_id: "doc-event-tsm-q2",
            locator: { page: 12, section: "资本开支" },
            available_at: "2026-08-08T20:00:00Z",
          },
        },
        {
          id: "claim-capex",
          kind: "claim",
          label: "资本开支指引上调",
          properties: {
            review_state: "reviewed",
            source_span_id: "sp-tsm-capex",
          },
        },
        {
          id: "factor-capex",
          kind: "factor",
          label: factors[0].statement,
          properties: { review_state: "reviewed" },
        },
        {
          id: "candidate-fcf",
          kind: "proposal",
          label: "自由现金流承压持续",
          properties: { review_state: "machine_generated" },
        },
        {
          id: "related-case",
          kind: "case",
          label: "AI 服务器订单验证",
          properties: { inherited: false },
        },
      ],
      edges: [
        {
          id: "edge-document-claim",
          semantic_kind: "quoted_by",
          source: "doc-event-tsm-q2",
          target: "claim-capex",
          review_state: "reviewed",
          available_at: "2026-08-08T20:00:00Z",
          properties: {
            reviewer: "human:reviewer",
            review_reason: "已逐字核对冻结原文与定位。",
            reviewed_at: "2026-08-08T20:10:00Z",
          },
        },
        {
          id: "edge-claim-factor",
          semantic_kind: "supports",
          source: "claim-capex",
          target: "factor-capex",
          review_state: "reviewed",
          available_at: "2026-08-08T20:00:00Z",
        },
        {
          id: "edge-factor-candidate",
          semantic_kind: "candidate_relation",
          source: "factor-capex",
          target: "candidate-fcf",
          review_state: "machine_generated",
        },
        {
          id: "edge-factor-related",
          semantic_kind: "related_case",
          source: "factor-capex",
          target: "related-case",
          review_state: "reviewed",
        },
      ],
      paths: [
        {
          node_ids: ["doc-event-tsm-q2", "claim-capex", "factor-capex"],
          edge_ids: ["edge-document-claim", "edge-claim-factor"],
          label: "冻结原文到已审核关键因素",
        },
      ],
      page: { has_more: false },
    } as ReturnType<ResearchOsApi["graph"]> extends Promise<infer T>
      ? T
      : never;
  }

  async exposure(caseId: string): ReturnType<ResearchOsApi["exposure"]> {
    return {
      case_id: caseId,
      as_of: "2026-06-30",
      funds: [
        {
          fund_id: "fund-demo",
          fund_code: "000001",
          fund_name: "演示成长基金",
          theme_exposure: 0.038,
          positions: [
            {
              stock_id: "stock-tsm",
              stock_code: "TSM",
              stock_name: "台积电",
              weight: 0.038,
              report_period: "2026-06-30",
              pe_ttm: null,
              pb: null,
            },
          ],
        },
      ],
    };
  }

  async marketExpression(
    caseId: string,
  ): ReturnType<ResearchOsApi["marketExpression"]> {
    const baseClaims: Schemas["ReportClaimDTO"][] = [
      {
        id: "report-claim-demo",
        text: "公司预计资本开支将高于此前指引。",
        claim_kind: "forecast",
        asserted_period: "2026H2",
        asserted_by: "研究报告",
        reviewed_by: "human:reviewer",
        review_reason: "原文定位及许可已核对",
        reviewed_at: now,
        source,
      },
    ];
    const baseFactors: Schemas["KeyFactorDTO"][] = [
      {
        id: "factor-capex",
        thesis_id: "factor-capex",
        report_claim_id: "report-claim-demo",
        name: factors[0].statement,
        expected_direction: "positive",
        metric_name: "资本开支同比增速",
        allowed_source_types: ["company_disclosure"],
        verification_window_start: "2026-07-01",
        verification_window_end: "2026-09-30",
        support_condition: "一手披露实际增长并明确投向",
        refutation_condition: "下调或投向不符",
        next_verification_event: "2026Q3 季报",
        reviewed_by: "human:reviewer",
        review_reason: "已定义可验证指标",
        reviewed_at: now,
        verification: {
          outcome: "not_due",
          rationale: "尚未到下一财报验证时点",
          reviewed_by: "human:reviewer",
          reviewed_at: now,
          source,
        },
      },
    ];
    const allFactors = [
      ...baseFactors,
      ...(this.keyFactors.get(caseId) ?? []),
    ].map((factor) => ({
      ...factor,
      verification:
        this.verifications.get(`${caseId}:${factor.id}`) ?? factor.verification,
    }));
    return {
      case_id: caseId,
      as_of: "2026-08-09",
      cutoff: now,
      claims: [...baseClaims, ...(this.reportClaims.get(caseId) ?? [])],
      factors: allFactors,
      fundamentals: [
        {
          id: "impact-demo",
          key_factor_id: "factor-capex",
          company_id: "company-tsm",
          company_name: "台积电",
          stock_id: "stock-tsm",
          stock_code: "TSM",
          stock_name: "台积电",
          metric_name: "资本开支",
          expected_direction: "positive",
          rationale: "仅记录待验证的基本面传导，不构成市场因果。",
          reviewed_by: "human:reviewer",
          review_reason: "研究员确认传导口径",
          reviewed_at: now,
          source,
        },
        ...(this.fundamentalImpacts.get(caseId) ?? []),
      ],
      market_observations: [
        {
          id: "market-demo",
          key_factor_id: "factor-capex",
          stock_id: "stock-tsm",
          stock_code: "TSM",
          stock_name: "台积电",
          event_at: "2026-08-08T20:00:00Z",
          available_at: "2026-08-09T00:00:00Z",
          window_label: "公告后 1D",
          benchmark: "SOX",
          price_source: "授权行情快照",
          after_hours_treatment: "事件发生在盘后，窗口从下一交易日开盘开始",
          relative_return: -0.012,
          reviewed_by: "human:reviewer",
          review_reason: "仅核验数据窗口",
          reviewed_at: now,
        },
        ...(this.marketObservations.get(caseId) ?? []),
      ],
      fund_exposure: [
        {
          fund_id: "fund-demo",
          fund_code: "000001",
          fund_name: "演示成长基金",
          disclosed_exposure: 0.038,
          positions: [
            {
              stock_id: "stock-tsm",
              stock_code: "TSM",
              stock_name: "台积电",
              weight: 0.038,
              report_period: "2026-06-30",
              published_at: "2026-07-20T00:00:00Z",
              acquired_at: "2026-07-21T00:00:00Z",
              source: "披露持仓快照",
              source_document_version_id: "doc-fund-holdings-2026q2",
              source_visible_in_case: true,
              source_locator: { table: "前十大持仓", row: 3 },
              provider_record_id: "provider-fund-2026q2",
              source_permission_status: "admitted",
              coverage_status: "complete",
              freshness_status: "historical_disclosure",
            },
          ],
        },
      ],
    };
  }

  async sourceStatements(
    _caseId: string,
  ): ReturnType<ResearchOsApi["sourceStatements"]> {
    return {
      items: [
        {
          id: source.source_statement_id!,
          kind: "disclosed_fact",
          text: "公司上调全年资本开支指引。",
          document_version_id: source.document_version_id!,
          document_title: source.document_title!,
          source_url: source.source_url,
          locator: source.locator!,
          available_at: source.available_at!,
          permission_status: "admitted",
        },
      ],
    };
  }
  async marketInstruments(
    caseId: string,
  ): ReturnType<ResearchOsApi["marketInstruments"]> {
    return {
      items: [
        {
          id: "binding-demo",
          company_id: "company-tsm",
          company_code: "TSM",
          company_name: "台积电",
          stock_id: "stock-tsm",
          stock_code: "TSM",
          stock_name: "台积电",
          relationship_role: "directly_affected",
          reviewed_by: "human:reviewer",
          review_reason: "研究员已核对该公司与本 Case 的适用范围。",
          reviewed_at: now,
          source,
        },
        ...(this.marketBindings.get(caseId) ?? []),
      ],
    };
  }
  async marketInstrumentCatalog(
    _query: string,
  ): ReturnType<ResearchOsApi["marketInstrumentCatalog"]> {
    return {
      items: [
        {
          company_id: "company-tsm",
          company_code: "TSM",
          company_name: "台积电",
          company_type: "listed",
          stocks: [
            { id: "stock-tsm", code: "TSM", name: "台积电", market: "NYSE" },
          ],
        },
      ],
    };
  }
  async createMarketInstrumentBinding(
    caseId: string,
    input: Parameters<ResearchOsApi["createMarketInstrumentBinding"]>[1],
  ): ReturnType<ResearchOsApi["createMarketInstrumentBinding"]> {
    const catalog = await this.marketInstrumentCatalog("");
    const company = catalog.items.find(
      (item) => item.company_id === input.company_id,
    );
    const stock =
      company?.stocks.find((item) => item.id === input.stock_id) ?? null;
    const created: Schemas["MarketInstrumentBindingDTO"] = {
      id: `binding-${Date.now()}`,
      company_id: input.company_id,
      company_code: company?.company_code ?? "已选公司",
      company_name: company?.company_name ?? "已选公司",
      stock_id: input.stock_id ?? null,
      stock_code: stock?.code ?? null,
      stock_name: stock?.name ?? null,
      relationship_role: input.relationship_role,
      reviewed_by: input.reviewed_by,
      review_reason: input.review_reason,
      reviewed_at: now,
      source,
    };
    this.marketBindings.set(caseId, [
      ...(this.marketBindings.get(caseId) ?? []),
      created,
    ]);
    return created;
  }
  async createFundamentalImpact(
    caseId: string,
    factorId: string,
    input: Parameters<ResearchOsApi["createFundamentalImpact"]>[2],
  ): ReturnType<ResearchOsApi["createFundamentalImpact"]> {
    const binding = (await this.marketInstruments(caseId)).items.find(
      (item) => item.id === input.market_instrument_binding_id,
    );
    if (!binding) throw new Error("market instrument binding is not reviewed");
    const created: Schemas["FundamentalImpactDTO"] = {
      id: `impact-${Date.now()}`,
      key_factor_id: factorId,
      company_id: binding.company_id,
      company_name: binding.company_name,
      stock_id: binding.stock_id,
      stock_code: binding.stock_code,
      stock_name: binding.stock_name,
      metric_name: input.metric_name,
      expected_direction: input.expected_direction,
      rationale: input.rationale,
      reviewed_by: input.reviewed_by,
      review_reason: input.review_reason,
      reviewed_at: now,
      source,
    };
    this.fundamentalImpacts.set(caseId, [
      ...(this.fundamentalImpacts.get(caseId) ?? []),
      created,
    ]);
    return created;
  }
  async createMarketObservation(
    caseId: string,
    factorId: string,
    input: Parameters<ResearchOsApi["createMarketObservation"]>[2],
  ): ReturnType<ResearchOsApi["createMarketObservation"]> {
    const binding = (await this.marketInstruments(caseId)).items.find(
      (item) => item.id === input.market_instrument_binding_id,
    );
    if (!binding?.stock_id || !binding.stock_code || !binding.stock_name)
      throw new Error("market observation requires a reviewed stock binding");
    const created: Schemas["MarketObservationDTO"] = {
      id: `observation-${Date.now()}`,
      key_factor_id: factorId,
      stock_id: binding.stock_id,
      stock_code: binding.stock_code,
      stock_name: binding.stock_name,
      event_at: input.event_at,
      available_at: input.available_at,
      window_label: input.window_label,
      benchmark: input.benchmark,
      price_source: input.price_source,
      after_hours_treatment: input.after_hours_treatment,
      relative_return: input.relative_return ?? null,
      reviewed_by: input.reviewed_by,
      review_reason: input.review_reason,
      reviewed_at: now,
    };
    this.marketObservations.set(caseId, [
      ...(this.marketObservations.get(caseId) ?? []),
      created,
    ]);
    return created;
  }
  async createReportClaim(
    caseId: string,
    input: Parameters<ResearchOsApi["createReportClaim"]>[1],
  ): ReturnType<ResearchOsApi["createReportClaim"]> {
    const statement = (await this.sourceStatements(caseId)).items.find(
      (item) => item.id === input.source_statement_id,
    );
    if (!statement) throw new Error("source statement is not admitted");
    const created: Schemas["ReportClaimDTO"] = {
      id: `report-claim-${Date.now()}`,
      text: input.text,
      claim_kind: input.claim_kind,
      asserted_period: input.asserted_period ?? null,
      asserted_by: input.asserted_by,
      reviewed_by: input.reviewed_by,
      review_reason: input.review_reason,
      reviewed_at: now,
      source: {
        source_statement_id: statement.id,
        document_version_id: statement.document_version_id,
        document_title: statement.document_title,
        source_url: statement.source_url,
        locator: statement.locator,
        available_at: statement.available_at,
        permission_status: statement.permission_status,
      },
    };
    this.reportClaims.set(caseId, [
      ...(this.reportClaims.get(caseId) ?? []),
      created,
    ]);
    return created;
  }
  async createKeyFactor(
    caseId: string,
    input: Parameters<ResearchOsApi["createKeyFactor"]>[1],
  ): ReturnType<ResearchOsApi["createKeyFactor"]> {
    const created: Schemas["KeyFactorDTO"] = {
      id: `key-factor-${Date.now()}`,
      thesis_id: input.thesis_id ?? null,
      report_claim_id: input.report_claim_id,
      name: input.name,
      expected_direction: input.expected_direction,
      metric_name: input.metric_name,
      allowed_source_types: input.allowed_source_types,
      verification_window_start: input.verification_window_start ?? null,
      verification_window_end: input.verification_window_end ?? null,
      support_condition: input.support_condition,
      refutation_condition: input.refutation_condition,
      next_verification_event: input.next_verification_event,
      reviewed_by: input.reviewed_by,
      review_reason: input.review_reason,
      reviewed_at: now,
      verification: null,
    };
    this.keyFactors.set(caseId, [
      ...(this.keyFactors.get(caseId) ?? []),
      created,
    ]);
    return created;
  }
  async createClaimVerification(
    caseId: string,
    factorId: string,
    input: Parameters<ResearchOsApi["createClaimVerification"]>[2],
  ): ReturnType<ResearchOsApi["createClaimVerification"]> {
    const statement = (await this.sourceStatements(caseId)).items.find(
      (item) => item.id === input.source_statement_id,
    );
    if (!statement) throw new Error("source statement is not admitted");
    const created: Schemas["ClaimVerificationDTO"] = {
      outcome: input.outcome,
      rationale: input.rationale,
      reviewed_by: input.reviewed_by,
      reviewed_at: now,
      source: {
        source_statement_id: statement.id,
        document_version_id: statement.document_version_id,
        document_title: statement.document_title,
        source_url: statement.source_url,
        locator: statement.locator,
        available_at: statement.available_at,
        permission_status: statement.permission_status,
      },
    };
    this.verifications.set(`${caseId}:${factorId}`, created);
    return created;
  }

  async metrics(): ReturnType<ResearchOsApi["metrics"]> {
    return metrics;
  }
  async createMetric(
    input: Parameters<ResearchOsApi["createMetric"]>[0],
  ): ReturnType<ResearchOsApi["createMetric"]> {
    const created = {
      id: `metric-${input.metric_id}`,
      metric_id: input.metric_id,
      version: 1,
      display_name: input.display_name,
      entity_scope: input.entity_scope,
      unit: input.unit,
      role_eligibility: input.role_eligibility,
      approved_by: input.approved_by,
      reason: input.reason,
      created_at: now,
    };
    metrics.push(created);
    return created;
  }
  async createOutcomeBinding(
    thesisId: string,
    input: Parameters<ResearchOsApi["createOutcomeBinding"]>[1],
  ): ReturnType<ResearchOsApi["createOutcomeBinding"]> {
    const binding = {
      id: `binding-${thesisId}`,
      thesis_id: thesisId,
      metric_definition_id: input.metric_definition_id,
      entity_scope: input.entity_scope,
      direction: input.direction,
      baseline: input.baseline,
      horizon_start: input.horizon_start,
      horizon_end: input.horizon_end,
      state: "pending_review",
      supersedes_id: null,
      reviewer: input.reviewer,
      reason: input.reason,
      created_at: now,
    };
    this.bindings.set(binding.id, binding);
    return binding;
  }
  async approveOutcomeBinding(
    bindingId: string,
    input: Parameters<ResearchOsApi["approveOutcomeBinding"]>[1],
  ): ReturnType<ResearchOsApi["approveOutcomeBinding"]> {
    const prior = this.bindings.get(bindingId);
    if (!prior) throw new Error("unknown mock binding");
    const approved = {
      ...prior,
      state: "approved",
      reviewer: input.reviewer,
      reason: input.reason,
    };
    this.bindings.set(bindingId, approved);
    return approved;
  }
  async researchability(
    thesisId: string,
  ): ReturnType<ResearchOsApi["researchability"]> {
    const binding = [...this.bindings.values()].find(
      (value) => value.thesis_id === thesisId,
    );
    return binding?.state === "approved" ||
      thesisId === "factor-capex" ||
      thesisId === "factor-margin"
      ? {
          status: "ready",
          reason_codes: [],
          effective_binding_id: binding?.id ?? `binding-demo-${thesisId}`,
          next_action: "可配置机制和验证规则",
        }
      : {
          status: "blocked",
          reason_codes: ["missing_outcome_binding"],
          effective_binding_id: null,
          next_action: "先建立并审核结果绑定",
        };
  }

  async mechanismTemplates(): ReturnType<ResearchOsApi["mechanismTemplates"]> {
    return [template];
  }
  async caseMechanismProtocol(
    caseId: string,
  ): ReturnType<ResearchOsApi["caseMechanismProtocol"]> {
    const rules = this.rules.get(caseId) ?? [];
    return {
      selection: {
        id: `selection-${caseId}`,
        research_case_id: caseId,
        template_version_id: template.id,
        supersedes_id: null,
        reviewer: "human:researcher",
        reason: "演示研究员确认机制范围",
        created_at: now,
      },
      template,
      rules,
      rule_history: rules,
    };
  }
  async selectMechanismTemplate(
    caseId: string,
    input: Parameters<ResearchOsApi["selectMechanismTemplate"]>[1],
  ): ReturnType<ResearchOsApi["selectMechanismTemplate"]> {
    return {
      id: `selection-${caseId}`,
      research_case_id: caseId,
      template_version_id: input.template_version_id,
      supersedes_id: null,
      reviewer: input.reviewer,
      reason: input.reason,
      created_at: now,
    };
  }
  async createVerificationRule(
    caseId: string,
    edgeId: string,
    input: Parameters<ResearchOsApi["createVerificationRule"]>[2],
  ): ReturnType<ResearchOsApi["createVerificationRule"]> {
    const previous =
      this.rules
        .get(caseId)
        ?.find((rule) => rule.mechanism_edge_id === edgeId) ?? null;
    const rule: Rule = {
      id: `rule-${caseId}-${Date.now()}`,
      research_case_id: caseId,
      mechanism_edge_id: edgeId,
      metric_definition_id: input.metric_definition_id,
      expected_direction: input.expected_direction,
      support_predicate: input.support_predicate,
      contradiction_predicate: input.contradiction_predicate,
      allowed_source_roles: input.allowed_source_roles,
      observed_period_start: input.observed_period_start,
      observed_period_end: input.observed_period_end,
      available_at_deadline: input.available_at_deadline,
      next_verification_event: input.next_verification_event,
      supersedes_id: previous?.id ?? null,
      reviewer: input.reviewer,
      reason: input.reason,
      created_at: now,
    };
    this.rules.set(caseId, [...(this.rules.get(caseId) ?? []), rule]);
    return rule;
  }

  async atomicClaims(
    caseId: string,
  ): ReturnType<ResearchOsApi["atomicClaims"]> {
    const stored = this.claims.get(caseId);
    const items = stored ??
      this.documentStore?.getAtomicClaimCandidates(caseId) ?? [
        atomicClaim(caseId),
      ];
    return {
      items: items.filter((claim) => !this.reviewedClaimIds.has(claim.id)),
    };
  }
  async reviewAtomicClaim(
    candidateId: string,
    input: Parameters<ResearchOsApi["reviewAtomicClaim"]>[1],
  ): ReturnType<ResearchOsApi["reviewAtomicClaim"]> {
    this.reviewedClaimIds.add(candidateId);
    for (const [caseId, claims] of this.claims)
      this.claims.set(
        caseId,
        claims.filter((claim) => claim.id !== candidateId),
      );
    return {
      id: `review-${candidateId}`,
      outcome: input.outcome,
      reviewer: input.reviewer,
      reason: input.reason,
      published_source_statement:
        input.outcome === "rejected"
          ? null
          : {
              id: `statement-${candidateId}`,
              normalized_text: input.normalized_text ?? "已审核原子陈述",
              kind: "disclosed_fact",
              observed_period: null,
              created_at: now,
            },
      created_at: now,
    };
  }
  async createDocumentSupplement(
    documentId: string,
    input: Parameters<ResearchOsApi["createDocumentSupplement"]>[1],
  ): ReturnType<ResearchOsApi["createDocumentSupplement"]> {
    if (!this.documentStore)
      return {
        document_version_id: `supplement-${documentId}`,
        original_document_version_id: documentId,
        claimed_page_reference: input.claimed_page_reference,
        extraction_allowed: true,
      };
    const result = await this.documentStore.createDocumentSupplement({
      caseId: input.case_id,
      documentId,
      rawText: input.raw_text,
      claimedPageReference: input.claimed_page_reference,
      createdBy: input.created_by,
    });
    return {
      document_version_id: result.documentVersionId,
      original_document_version_id: result.originalDocumentVersionId,
      claimed_page_reference: input.claimed_page_reference,
      extraction_allowed: result.extractionAllowed,
    };
  }
}
