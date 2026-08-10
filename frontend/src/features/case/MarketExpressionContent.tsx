import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  sourceTypeLabel,
  sourceTypeListLabel,
} from "../../domain/sourcePresentation";
import {
  researchOsApi,
  type MarketExpression,
  type MarketInstrumentBindings,
  type SourceStatementOptions,
} from "../../app/researchOsApi";

const claimKindLabels: Record<string, string> = {
  disclosed_fact: "已披露事实",
  forecast: "预测",
  research_opinion: "研究意见",
};

const verificationLabels: Record<string, string> = {
  supported: "得到支持",
  contradicted: "出现反证",
  insufficient_evidence: "证据不足",
  not_due: "尚未到验证时点",
};
const permissionLabels: Record<string, string> = {
  admitted: "已准入",
  restricted: "受限",
  not_recorded: "未记录",
};
const fundCoverageLabels: Record<string, string> = {
  complete: "完整",
  partial: "部分覆盖",
  not_recorded: "未记录",
};
const fundFreshnessLabels: Record<string, string> = {
  historical_disclosure: "报告期仍在有效期",
  stale_disclosure: "披露已过期",
  coverage_incomplete: "覆盖不足",
  source_unlinked: "来源受限",
};
const instrumentRoleLabels: Record<string, string> = {
  directly_affected: "直接受影响",
  supply_chain: "供应链传导",
  competitor: "竞争对手",
  beneficiary: "受益方",
  risk_exposure: "风险暴露",
};
const expectedDirectionLabels: Record<string, string> = {
  positive: "预期向上",
  negative: "预期向下",
  neutral: "方向中性",
};

type ThesisOption = { id: string; statement: string };

export function MarketExpressionContent({
  caseId,
  theses = [],
}: {
  caseId: string;
  theses?: ThesisOption[];
}) {
  const navigate = useNavigate();
  const [expression, setExpression] = useState<MarketExpression | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFactorId, setSelectedFactorId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  async function reloadExpression() {
    setError(null);
    try {
      const value = await researchOsApi.marketExpression(caseId);
      setExpression(value);
      setSelectedFactorId(value.factors[0]?.id ?? null);
    } catch {
      setError(
        "无法读取已审核的市场表达；系统不会以 Case 摘要或未审核候选替代该层记录。",
      );
    }
  }
  useEffect(() => {
    void reloadExpression();
  }, [caseId]);

  if (error)
    return (
      <section className="ros-empty ros-page-gap" role="alert">
        <strong>市场表达暂不可读取</strong>
        <p>{error}</p>
        <button
          className="ros-button ros-button--secondary"
          type="button"
          onClick={() => void reloadExpression()}
        >
          重试读取市场表达
        </button>
      </section>
    );
  if (!expression)
    return (
      <div className="ros-empty ros-page-gap">正在读取已审核的市场表达…</div>
    );
  const selectedFactor =
    expression.factors.find((factor) => factor.id === selectedFactorId) ??
    expression.factors[0] ??
    null;
  const selectedClaim = selectedFactor?.report_claim_id
    ? (expression.claims.find(
        (claim) => claim.id === selectedFactor.report_claim_id,
      ) ?? null)
    : null;
  const fundamentals = selectedFactor
    ? expression.fundamentals.filter(
        (impact) => impact.key_factor_id === selectedFactor.id,
      )
    : [];
  const observations = selectedFactor
    ? expression.market_observations.filter(
        (observation) => observation.key_factor_id === selectedFactor.id,
      )
    : [];
  const selectedStockIds = new Set(
    fundamentals.flatMap((impact) =>
      impact.stock_id ? [impact.stock_id] : [],
    ),
  );
  const relatedFunds = expression.fund_exposure
    .map((fund) => ({
      ...fund,
      positions: fund.positions.filter((position) =>
        selectedStockIds.has(position.stock_id),
      ),
    }))
    .filter((fund) => fund.positions.length > 0);
  const hasOperatingEvidence = fundamentals.length > 0;
  const hasMarketEvidence = observations.length > 0;
  const transmissionStatus = !selectedFactor
    ? "暂无可穿透的已审核因素"
    : hasOperatingEvidence && hasMarketEvidence
      ? "经营与市场验证均已可见"
      : hasOperatingEvidence
        ? "经营传导待市场验证"
        : hasMarketEvidence
          ? "市场反应已观察，待补经营传导"
          : "尚缺公司、股票或市场验证";
  async function startFactorRun() {
    if (!selectedFactor?.thesis_id) return;
    setStarting(true);
    setRunError(null);
    try {
      await researchOsApi.startFactorMonitorRun(caseId, selectedFactor.id);
      navigate(`/events/${caseId}/monitor`);
    } catch {
      setRunError(
        "无法按该因素的已审核关联与许可范围创建补证运行；没有创建部分运行。",
      );
    } finally {
      setStarting(false);
    }
  }
  const sourceMetadata = selectedClaim?.source;
  return (
    <section className="ros-market ros-market-expression">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">市场与表达 · 已审核读模型</p>
          <h2>研报主张、后续验证与基金披露分层呈现</h2>
          <p>
            截至 {expression.as_of} 的可见资料；查询截点{" "}
            {new Date(expression.cutoff).toLocaleString("zh-CN")}。
          </p>
        </div>
        <span className="ros-pill ros-pill--system">仅已审核记录</span>
      </header>
      <p className="ros-market-intro">
        研报观点不会自动变成事实；市场窗口只描述观测；基金数据只表示历史披露而非实时仓位。
      </p>
      <MarketExpressionRegistration
        caseId={caseId}
        theses={theses}
        onRegistered={() => void reloadExpression()}
      />
      <MarketInstrumentWorkspace
        caseId={caseId}
        factor={selectedFactor}
        onRegistered={(notice) => {
          setActionNotice(notice);
          void reloadExpression();
        }}
      />
      {actionNotice && (
        <p className="ros-success" role="status">
          {actionNotice}
        </p>
      )}
      <div className="ros-market-workbench">
        <aside className="ros-market-factors">
          <p className="ros-eyebrow">选择一个关键因素</p>
          <h3>影响穿透</h3>
          {expression.factors.length ? (
            expression.factors.map((factor) => (
              <button
                type="button"
                className={`ros-market-factor${factor.id === selectedFactor?.id ? " is-selected" : ""}`}
                key={factor.id}
                aria-pressed={factor.id === selectedFactor?.id}
                onClick={() => setSelectedFactorId(factor.id)}
              >
                <i
                  className={
                    factor.verification?.outcome === "contradicted"
                      ? "is-risk"
                      : factor.verification
                        ? ""
                        : "is-gap"
                  }
                />
                <span>
                  <strong>{factor.name}</strong>
                  <small>{factor.metric_name}</small>
                </span>
                <b>
                  {factor.verification
                    ? (verificationLabels[factor.verification.outcome] ??
                      factor.verification.outcome)
                    : "待验证"}
                </b>
              </button>
            ))
          ) : (
            <Empty text="当前没有可进入正式验证的关键因素。" />
          )}
        </aside>
        <section className="ros-market-chain" aria-live="polite">
          <p className="ros-eyebrow">当前因素的四段链路</p>
          <h3>{selectedFactor?.name ?? "尚未选择关键因素"}</h3>
          <p className="ros-market-status">{transmissionStatus}</p>
          <div className="ros-chain-row">
            <article>
              <span>01 · 研报主张</span>
              <b>
                {selectedClaim?.text ??
                  (selectedFactor?.report_claim_id
                    ? "上游主张未在当前截点可见"
                    : "独立关键因素")}
              </b>
              <small>
                {selectedClaim && (
                  <>
                    <span>
                      {claimKindLabels[selectedClaim.claim_kind] ??
                        selectedClaim.claim_kind}
                    </span>{" "}
                    · 审核 {selectedClaim.reviewed_by}
                  </>
                )}
              </small>
              {sourceMetadata && (
                <>
                  <small>
                    {sourceMetadata.document_version_id ? (
                      <Link
                        className="ros-source-link"
                        to={`/events/${caseId}/documents?document=${sourceMetadata.document_version_id}`}
                      >
                        定位到冻结原文
                      </Link>
                    ) : (
                      "冻结版本未记录"
                    )}
                    {sourceMetadata.source_url ? (
                      <>
                        {" "}
                        ·{" "}
                        <a
                          className="ros-source-link"
                          href={sourceMetadata.source_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看来源地址
                        </a>
                      </>
                    ) : (
                      ""
                    )}
                    {sourceMetadata.document_title
                      ? ` · ${sourceMetadata.document_title}`
                      : ""}
                  </small>
                  <small>
                    定位{" "}
                    {sourceMetadata.locator
                      ? JSON.stringify(sourceMetadata.locator)
                      : "未记录"}{" "}
                    · 可得{" "}
                    {sourceMetadata.available_at
                      ? new Date(sourceMetadata.available_at).toLocaleString(
                          "zh-CN",
                        )
                      : "未记录"}
                  </small>
                  <small>
                    许可：
                    {permissionLabels[sourceMetadata.permission_status] ??
                      sourceMetadata.permission_status}
                  </small>
                </>
              )}
              <small>
                {selectedFactor
                  ? `允许来源：${sourceTypeListLabel(selectedFactor.allowed_source_types)}`
                  : ""}
              </small>
            </article>
            <i>→</i>
            <article>
              <span>02 · 公司传导</span>
              <b>
                {fundamentals.length
                  ? `${fundamentals.length} 家已审核公司`
                  : "尚未建立已审核传导"}
              </b>
              <small>
                {fundamentals
                  .map(
                    (impact) =>
                      `${impact.company_name}${impact.stock_code ? ` · ${impact.stock_code}` : " · 未上市/未映射"}`,
                  )
                  .join("；") || "需补关系来源与机制"}
              </small>
            </article>
            <i>→</i>
            <article>
              <span>03 · 股票验证</span>
              <b>
                {hasMarketEvidence
                  ? `${observations.length} 条市场观测`
                  : "尚未取得市场验证"}
              </b>
              <small>
                {hasMarketEvidence
                  ? observations
                      .map((item) => `${item.stock_code} ${item.window_label}`)
                      .join("；")
                  : "经营数据与市场观测必须分开记录"}
              </small>
            </article>
            <i>→</i>
            <article>
              <span>04 · 基金披露</span>
              <b>
                {relatedFunds.length
                  ? `${relatedFunds.length} 只基金有历史披露`
                  : "暂无可关联披露"}
              </b>
              <small>
                {relatedFunds.length
                  ? "仅展示已披露仓位，不表示实时仓位"
                  : "未上市公司不进入基金暴露；披露不足不估算"}
              </small>
            </article>
          </div>
          <section className="ros-expression-lane">
            <div className="ros-expression-grid">
              <div>
                <h4>公司传导 · {fundamentals.length}</h4>
                {fundamentals.length ? (
                  fundamentals.map((impact) => (
                    <article className="ros-expression-card" key={impact.id}>
                      <strong>
                        {impact.stock_id ? (
                          <Link
                            to={`/events/${caseId}/stocks/${impact.stock_id}`}
                          >
                            {impact.company_name} · {impact.stock_code}
                          </Link>
                        ) : (
                          `${impact.company_name} · 未上市/未映射`
                        )}
                      </strong>
                      <small>
                        {impact.metric_name} ·{" "}
                        {impact.expected_direction === "positive"
                          ? "预期向上"
                          : impact.expected_direction === "negative"
                            ? "预期向下"
                            : "方向中性"}
                      </small>
                      <p>{impact.rationale}</p>
                      <small>
                        审核 {impact.reviewed_by} ·{" "}
                        {impact.source.locator
                          ? `定位 ${JSON.stringify(impact.source.locator)}`
                          : "原文定位未记录"}
                      </small>
                    </article>
                  ))
                ) : (
                  <Empty text="当前因素尚缺已审核的公司传导关系。" />
                )}
              </div>
              <div>
                <h4>事件窗口观测 · {observations.length}</h4>
                {observations.length ? (
                  observations.map((observation) => (
                    <article
                      className="ros-expression-card"
                      key={observation.id}
                    >
                      <strong>
                        <Link
                          to={`/events/${caseId}/stocks/${observation.stock_id}`}
                        >
                          {observation.stock_name}
                        </Link>{" "}
                        · {observation.window_label}
                      </strong>
                      <small>
                        事件{" "}
                        {new Date(observation.event_at).toLocaleString("zh-CN")}{" "}
                        · 可得{" "}
                        {new Date(observation.available_at).toLocaleString(
                          "zh-CN",
                        )}
                      </small>
                      <small>
                        相对 {observation.benchmark} · 价格源{" "}
                        {sourceTypeLabel(observation.price_source)}
                      </small>
                      <small>
                        盘后处理：
                        {observation.after_hours_treatment || "历史记录未保存"}
                      </small>
                      <b>
                        {observation.relative_return === null
                          ? "相对表现未记录"
                          : `相对表现 ${(observation.relative_return * 100).toFixed(2)}%`}
                      </b>
                      <small>
                        审核 {observation.reviewed_by} ·{" "}
                        {observation.review_reason}
                      </small>
                      <p className="ros-note">
                        这是市场观测，不自动表述为研报或因素造成。
                      </p>
                    </article>
                  ))
                ) : (
                  <Empty text="当前因素尚缺股票市场观测。" />
                )}
              </div>
            </div>
          </section>
        </section>
        <aside className="ros-market-rail">
          <p className="ros-eyebrow">核验口径与基金披露</p>
          <h3>可复现，不伪造因果</h3>
          {selectedFactor && (
            <>
              <dl className="ros-market-checks">
                <div>
                  <dt>验证指标</dt>
                  <dd>{selectedFactor.metric_name}</dd>
                </div>
                <div>
                  <dt>预期方向</dt>
                  <dd>
                    {expectedDirectionLabels[selectedFactor.expected_direction] ??
                      selectedFactor.expected_direction}
                  </dd>
                </div>
                <div>
                  <dt>验证窗口</dt>
                  <dd>
                    {selectedFactor.verification_window_start || "未记录"} 至{" "}
                    {selectedFactor.verification_window_end || "未记录"}
                  </dd>
                </div>
                <div>
                  <dt>允许来源</dt>
                  <dd>
                    {sourceTypeListLabel(selectedFactor.allowed_source_types)}
                  </dd>
                </div>
                <div>
                  <dt>支持条件</dt>
                  <dd>{selectedFactor.support_condition}</dd>
                </div>
                <div>
                  <dt>反证条件</dt>
                  <dd>{selectedFactor.refutation_condition}</dd>
                </div>
                <div>
                  <dt>下一验证</dt>
                  <dd>{selectedFactor.next_verification_event}</dd>
                </div>
                <div>
                  <dt>审核理由</dt>
                  <dd>{selectedFactor.review_reason}</dd>
                </div>
                <div>
                  <dt>因素审核</dt>
                  <dd>
                    {selectedFactor.reviewed_by} ·{" "}
                    {new Date(selectedFactor.reviewed_at).toLocaleString("zh-CN")}
                  </dd>
                </div>
              </dl>
              <p className="ros-market-warning">
                {selectedFactor.verification
                  ? `当前验证：${verificationLabels[selectedFactor.verification.outcome] ?? selectedFactor.verification.outcome}。${selectedFactor.verification.rationale}`
                  : "尚未形成审核验证；不会把候选关系写成正式影响。"}
              </p>
              <VerificationRegistration
                caseId={caseId}
                factor={selectedFactor}
                onSaved={() => void reloadExpression()}
              />
              <button
                className="ros-button ros-button--primary"
                type="button"
                disabled={!selectedFactor.thesis_id || starting}
                onClick={() => void startFactorRun()}
              >
                {starting ? "正在创建单因素补证…" : "立即补证此因素"}
              </button>
              {!selectedFactor.thesis_id && (
                <p className="ros-note">
                  尚未审核登记此关键因素与 Case
                  研究范围的关联，不能按名称猜测后启动运行。
                </p>
              )}
              {runError && <p className="ros-error">{runError}</p>}
            </>
          )}
          {relatedFunds.length ? (
            relatedFunds.map((fund) => {
              const disclosedExposure = fund.disclosed_exposure;
              return (
                <article className="ros-fund-row" key={fund.fund_id}>
                  <strong>
                    <Link to={`/events/${caseId}/funds/${fund.fund_id}`}>
                      {fund.fund_name}
                    </Link>{" "}
                    <small>{fund.fund_code}</small>
                  </strong>
                  <span>
                    {disclosedExposure !== null
                      ? `相关已披露仓位 ${(disclosedExposure * 100).toFixed(2)}%`
                      : "披露已过期、覆盖不足或来源受限，不能汇总精确暴露"}
                  </span>
                  {fund.positions.map((position) => (
                    <small key={position.stock_id}>
                      <Link
                        to={`/events/${caseId}/stocks/${position.stock_id}`}
                      >
                        {position.stock_name}
                      </Link>{" "}
                      · {(position.weight * 100).toFixed(2)}%<br />
                      报告期 {position.report_period} · 披露{" "}
                      {new Date(position.published_at).toLocaleDateString(
                        "zh-CN",
                      )}{" "}
                      · 采集{" "}
                      {new Date(position.acquired_at).toLocaleDateString(
                        "zh-CN",
                      )}
                      <br />
                      来源 {sourceTypeLabel(position.source)} · 覆盖：
                      {fundCoverageLabels[position.coverage_status] ??
                        "未记录"}{" "}
                      · 时效：
                      {fundFreshnessLabels[position.freshness_status] ??
                        "状态未记录"}
                      <br />
                      {position.source_visible_in_case &&
                      position.source_document_version_id ? (
                        <Link
                          className="ros-source-link"
                          to={`/events/${caseId}/documents?document=${position.source_document_version_id}`}
                        >
                          定位到冻结持仓来源（版本{" "}
                          {position.source_document_version_id}）
                        </Link>
                      ) : position.source_document_version_id ? (
                        `来源版本 ${position.source_document_version_id}（未关联当前 Case）`
                      ) : (
                        "来源版本未记录"
                      )}{" "}
                      · 许可：
                      {permissionLabels[position.source_permission_status] ??
                        "未记录"}
                      {position.source_locator
                        ? ` · 定位 ${JSON.stringify(position.source_locator)}`
                        : ""}
                      {position.provider_record_id
                        ? ` · 供应商记录 ${position.provider_record_id}`
                        : ""}
                    </small>
                  ))}
                </article>
              );
            })
          ) : (
            <Empty text="当前因素未关联到可用的基金历史披露。" />
          )}
        </aside>
      </div>
    </section>
  );
}

function MarketInstrumentWorkspace({
  caseId,
  factor,
  onRegistered,
}: {
  caseId: string;
  factor: MarketExpression["factors"][number] | null;
  onRegistered: (notice: string) => void;
}) {
  const [bindings, setBindings] = useState<MarketInstrumentBindings["items"]>(
    [],
  );
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<
    Awaited<ReturnType<typeof researchOsApi.marketInstrumentCatalog>>["items"]
  >([]);
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [companyId, setCompanyId] = useState("");
  const [stockId, setStockId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [role, setRole] = useState<
    | "directly_affected"
    | "supply_chain"
    | "competitor"
    | "beneficiary"
    | "risk_exposure"
  >("directly_affected");
  const [reviewer, setReviewer] = useState("human:researcher");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const selectedCompany =
    catalog.find((item) => item.company_id === companyId) ?? null;

  async function reload() {
    setLoading(true);
    try {
      setBindings((await researchOsApi.marketInstruments(caseId)).items);
    } catch {
      setMessage(
        "无法读取当前 Case 已审核标的关联；不会使用事件标题或旧主题标签补全。 ",
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void reload();
  }, [caseId]);
  async function beginBinding() {
    setOpen(true);
    setMessage(null);
    try {
      const [nextCatalog, nextSources] = await Promise.all([
        researchOsApi.marketInstrumentCatalog(""),
        researchOsApi.sourceStatements(caseId),
      ]);
      setCatalog(nextCatalog.items);
      setSources(nextSources.items);
      const first = nextCatalog.items[0];
      setCompanyId(first?.company_id ?? "");
      setStockId(first?.stocks[0]?.id ?? "");
      setSourceId(nextSources.items[0]?.id ?? "");
    } catch {
      setMessage(
        "无法加载本地标的目录或当前 Case 的已准入原文。没有创建部分关联。 ",
      );
    }
  }
  async function saveBinding() {
    if (!companyId || !sourceId || !reviewer.trim() || !reason.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createMarketInstrumentBinding(caseId, {
        company_id: companyId,
        stock_id: stockId || null,
        source_statement_id: sourceId,
        relationship_role: role,
        reviewed_by: reviewer.trim(),
        review_reason: reason.trim(),
      });
      await reload();
      setOpen(false);
      const notice = "已追加已审核标的关联；后续传导只能引用此清单。 ";
      setMessage(notice);
      onRegistered(notice);
    } catch {
      setMessage(
        "标的关联未登记。请核对当前 Case 的冻结原文、标的和审核理由。 ",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ros-market-instruments">
      <div>
        <p className="ros-eyebrow">人工确认 · 标的范围</p>
        <h3>关联公司与股票</h3>
        <p>
          不会从事件标题或代码自动推断。每一条关联都必须由本 Case
          已准入的冻结原文、审核人和理由支持。
        </p>
      </div>
      <div className="ros-market-instruments__list">
        {loading ? (
          <p className="ros-note">正在读取已审核标的关联…</p>
        ) : bindings.length ? (
          bindings.map((binding) => (
            <article key={binding.id}>
              <strong>
                {binding.stock_id ? (
                  <Link to={`/events/${caseId}/stocks/${binding.stock_id}`}>
                    {binding.company_name} · {binding.stock_code}
                  </Link>
                ) : (
                  `${binding.company_name} · 未上市/未映射`
                )}
              </strong>
              <small>
                {instrumentRoleLabels[binding.relationship_role] ??
                  binding.relationship_role}{" "}
                · 审核 {binding.reviewed_by}
              </small>
              <small>
                定位{" "}
                {binding.source.locator
                  ? JSON.stringify(binding.source.locator)
                  : "未记录"}{" "}
                · {binding.review_reason}
              </small>
            </article>
          ))
        ) : (
          <p className="ros-note">
            尚无已审核标的关联。先选择本地账本中的公司/股票，再以冻结原文确认其适用范围。
          </p>
        )}
      </div>
      <button
        className="ros-button ros-button--secondary"
        type="button"
        onClick={() => void beginBinding()}
      >
        关联公司与股票
      </button>
      {open && (
        <div className="ros-market-instruments__form">
          <label>
            新增关联标的
            <select
              aria-label="新增关联标的"
              value={companyId}
              onChange={(event) => {
                const next = catalog.find(
                  (item) => item.company_id === event.target.value,
                );
                setCompanyId(event.target.value);
                setStockId(next?.stocks[0]?.id ?? "");
              }}
            >
              <option value="">选择本地账本标的</option>
              {catalog.map((item) => (
                <option value={item.company_id} key={item.company_id}>
                  {item.company_name} · {item.company_code}
                </option>
              ))}
            </select>
          </label>
          <label>
            关联股票
            <select
              aria-label="关联股票"
              value={stockId}
              onChange={(event) => setStockId(event.target.value)}
            >
              <option value="">仅公司，不关联股票</option>
              {selectedCompany?.stocks.map((stock) => (
                <option value={stock.id} key={stock.id}>
                  {stock.name} · {stock.code} · {stock.market}
                </option>
              ))}
            </select>
          </label>
          <label>
            关联角色
            <select
              aria-label="关联角色"
              value={role}
              onChange={(event) => setRole(event.target.value as typeof role)}
            >
              <option value="directly_affected">直接受影响</option>
              <option value="supply_chain">供应链</option>
              <option value="competitor">竞争对手</option>
              <option value="beneficiary">受益方</option>
              <option value="risk_exposure">风险暴露</option>
            </select>
          </label>
          <label>
            关联原文
            <select
              aria-label="关联原文"
              value={sourceId}
              onChange={(event) => setSourceId(event.target.value)}
            >
              <option value="">选择冻结原文</option>
              {sources.map((source) => (
                <option value={source.id} key={source.id}>
                  {source.document_title} · {source.text.slice(0, 36)}
                </option>
              ))}
            </select>
          </label>
          <label>
            审核人
            <input
              aria-label="标的审核人"
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
            />
          </label>
          <label>
            审核理由
            <textarea
              aria-label="标的审核理由"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明这份原文为何支持该公司/股票进入本 Case…"
            />
          </label>
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={busy || !companyId || !sourceId || !reason.trim()}
            onClick={() => void saveBinding()}
          >
            {busy ? "正在追加标的关联…" : "保存已审核标的关联"}
          </button>
        </div>
      )}
      <FundamentalImpactRegistration
        caseId={caseId}
        factor={factor}
        bindings={bindings}
        onSaved={onRegistered}
      />
      <MarketObservationRegistration
        caseId={caseId}
        factor={factor}
        bindings={bindings}
        onSaved={onRegistered}
      />
      {message && (
        <p
          className={message.startsWith("已追加") ? "ros-success" : "ros-error"}
        >
          {message}
        </p>
      )}
    </section>
  );
}

function FundamentalImpactRegistration({
  caseId,
  factor,
  bindings,
  onSaved,
}: {
  caseId: string;
  factor: MarketExpression["factors"][number] | null;
  bindings: MarketInstrumentBindings["items"];
  onSaved: (notice: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [bindingId, setBindingId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [metric, setMetric] = useState("");
  const [direction, setDirection] = useState<
    "positive" | "negative" | "neutral"
  >("positive");
  const [rationale, setRationale] = useState("");
  const [reviewer, setReviewer] = useState("human:researcher");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function begin() {
    if (!factor) return;
    setOpen(true);
    setMessage(null);
    try {
      const response = await researchOsApi.sourceStatements(caseId);
      setSources(response.items);
      setBindingId(bindings[0]?.id ?? "");
      setSourceId(response.items[0]?.id ?? "");
      setMetric(factor.metric_name);
      setDirection(factor.expected_direction as typeof direction);
    } catch {
      setMessage("无法读取可用于传导审核的冻结原文。 ");
    }
  }
  async function save() {
    if (
      !factor ||
      !bindingId ||
      !sourceId ||
      !metric.trim() ||
      !rationale.trim() ||
      !reviewer.trim() ||
      !reason.trim()
    )
      return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createFundamentalImpact(caseId, factor.id, {
        market_instrument_binding_id: bindingId,
        source_statement_id: sourceId,
        metric_name: metric.trim(),
        expected_direction: direction,
        rationale: rationale.trim(),
        reviewed_by: reviewer.trim(),
        review_reason: reason.trim(),
      });
      const notice = "已追加已审核基本面传导；它不等同于市场因果。 ";
      setMessage(notice);
      onSaved(notice);
    } catch {
      setMessage("基本面传导未登记。请核对已审核标的、来源和理由。 ");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ros-fundamental-register">
      <p>公司传导必须选择上方已审核标的，并以独立冻结原文说明指标与机制。</p>
      {!open ? (
        <button
          className="ros-button ros-button--secondary"
          type="button"
          disabled={!factor || !bindings.length}
          onClick={() => void begin()}
        >
          登记基本面传导
        </button>
      ) : (
        <div>
          <label>
            传导标的
            <select
              aria-label="传导标的"
              value={bindingId}
              onChange={(event) => setBindingId(event.target.value)}
            >
              {bindings.map((binding) => (
                <option key={binding.id} value={binding.id}>
                  {binding.company_name}
                  {binding.stock_code ? ` · ${binding.stock_code}` : ""} ·{" "}
                  {instrumentRoleLabels[binding.relationship_role] ??
                    binding.relationship_role}
                </option>
              ))}
            </select>
          </label>
          <label>
            传导来源
            <select
              aria-label="传导来源"
              value={sourceId}
              onChange={(event) => setSourceId(event.target.value)}
            >
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.document_title} · {source.text.slice(0, 36)}
                </option>
              ))}
            </select>
          </label>
          <label>
            传导指标
            <input
              aria-label="传导指标"
              value={metric}
              onChange={(event) => setMetric(event.target.value)}
            />
          </label>
          <label>
            预期方向
            <select
              aria-label="传导预期方向"
              value={direction}
              onChange={(event) =>
                setDirection(event.target.value as typeof direction)
              }
            >
              <option value="positive">正向</option>
              <option value="negative">负向</option>
              <option value="neutral">中性</option>
            </select>
          </label>
          <label>
            传导机制
            <textarea
              aria-label="传导机制"
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </label>
          <label>
            审核人
            <input
              aria-label="传导审核人"
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
            />
          </label>
          <label>
            审核理由
            <textarea
              aria-label="传导审核理由"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={
              busy ||
              !bindingId ||
              !sourceId ||
              !metric.trim() ||
              !rationale.trim() ||
              !reason.trim()
            }
            onClick={() => void save()}
          >
            {busy ? "正在追加传导…" : "保存已审核基本面传导"}
          </button>
        </div>
      )}
      {!bindings.length && (
        <p className="ros-note">先建立已审核标的关联，才可登记基本面传导。</p>
      )}
      {message && (
        <p
          className={message.startsWith("已追加") ? "ros-success" : "ros-error"}
        >
          {message}
        </p>
      )}
    </section>
  );
}

function MarketObservationRegistration({
  caseId,
  factor,
  bindings,
  onSaved,
}: {
  caseId: string;
  factor: MarketExpression["factors"][number] | null;
  bindings: MarketInstrumentBindings["items"];
  onSaved: (notice: string) => void;
}) {
  const stockBindings = bindings.filter((binding) => binding.stock_id !== null);
  const [open, setOpen] = useState(false);
  const [bindingId, setBindingId] = useState("");
  const [eventAt, setEventAt] = useState("2026-08-08T20:00");
  const [availableAt, setAvailableAt] = useState("2026-08-09T00:00");
  const [windowLabel, setWindowLabel] = useState("公告后 1D");
  const [benchmark, setBenchmark] = useState("中证全指");
  const [priceSource, setPriceSource] = useState("授权行情快照");
  const [afterHoursTreatment, setAfterHoursTreatment] = useState(
    "事件发生在盘后，窗口从下一交易日开盘开始",
  );
  const [relativeReturn, setRelativeReturn] = useState("");
  const [reviewer, setReviewer] = useState("human:researcher");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  function begin() {
    if (!factor) return;
    setOpen(true);
    setMessage(null);
    setBindingId(stockBindings[0]?.id ?? "");
  }
  async function save() {
    if (
      !factor ||
      !bindingId ||
      !eventAt ||
      !availableAt ||
      !windowLabel.trim() ||
      !benchmark.trim() ||
      !priceSource.trim() ||
      !afterHoursTreatment.trim() ||
      !reviewer.trim() ||
      !reason.trim()
    )
      return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createMarketObservation(caseId, factor.id, {
        market_instrument_binding_id: bindingId,
        event_at: new Date(eventAt).toISOString(),
        available_at: new Date(availableAt).toISOString(),
        window_label: windowLabel.trim(),
        benchmark: benchmark.trim(),
        price_source: priceSource.trim(),
        after_hours_treatment: afterHoursTreatment.trim(),
        relative_return: relativeReturn.trim() ? Number(relativeReturn) : null,
        reviewed_by: reviewer.trim(),
        review_reason: reason.trim(),
      });
      const notice = "已追加已审核市场观测；它描述窗口表现，不构成因果结论。 ";
      setMessage(notice);
      onSaved(notice);
    } catch {
      setMessage("市场观测未登记。请核对股票绑定、时点、数据来源和审核理由。 ");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ros-fundamental-register ros-market-observation-register">
      <p>市场窗口只记录发生了什么，不表示研报、事件或因素造成价格变化。</p>
      {!open ? (
        <button
          className="ros-button ros-button--secondary"
          type="button"
          disabled={!factor || !stockBindings.length}
          onClick={begin}
        >
          登记市场观测
        </button>
      ) : (
        <div>
          <label>
            观测标的
            <select
              aria-label="观测标的"
              value={bindingId}
              onChange={(event) => setBindingId(event.target.value)}
            >
              {stockBindings.map((binding) => (
                <option key={binding.id} value={binding.id}>
                  {binding.company_name} · {binding.stock_code} ·{" "}
                  {instrumentRoleLabels[binding.relationship_role] ??
                    binding.relationship_role}
                </option>
              ))}
            </select>
          </label>
          <label>
            事件时间
            <input
              aria-label="事件时间"
              type="datetime-local"
              value={eventAt}
              onChange={(event) => setEventAt(event.target.value)}
            />
          </label>
          <label>
            资料可得时间
            <input
              aria-label="资料可得时间"
              type="datetime-local"
              value={availableAt}
              onChange={(event) => setAvailableAt(event.target.value)}
            />
          </label>
          <label>
            观测窗口
            <input
              aria-label="观测窗口"
              value={windowLabel}
              onChange={(event) => setWindowLabel(event.target.value)}
            />
          </label>
          <label>
            比较基准
            <input
              aria-label="比较基准"
              value={benchmark}
              onChange={(event) => setBenchmark(event.target.value)}
            />
          </label>
          <label>
            价格来源
            <input
              aria-label="价格来源"
              value={priceSource}
              onChange={(event) => setPriceSource(event.target.value)}
            />
          </label>
          <label>
            盘后处理
            <textarea
              aria-label="盘后处理"
              value={afterHoursTreatment}
              onChange={(event) => setAfterHoursTreatment(event.target.value)}
              placeholder="说明盘后发布、停牌或非交易日如何进入窗口…"
            />
          </label>
          <label>
            相对表现
            <input
              aria-label="相对表现"
              type="number"
              step="any"
              value={relativeReturn}
              onChange={(event) => setRelativeReturn(event.target.value)}
              placeholder="例如 -0.012"
            />
          </label>
          <label>
            审核人
            <input
              aria-label="市场观测审核人"
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
            />
          </label>
          <label>
            审核理由
            <textarea
              aria-label="市场观测审核理由"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明窗口、基准和价格来源为何适用于这条观察…"
            />
          </label>
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={
              busy ||
              !bindingId ||
              !afterHoursTreatment.trim() ||
              !reason.trim()
            }
            onClick={() => void save()}
          >
            {busy ? "正在追加市场观测…" : "保存已审核市场观测"}
          </button>
        </div>
      )}
      {!stockBindings.length && (
        <p className="ros-note">
          先建立包含股票的已审核标的关联，才可登记市场观测。
        </p>
      )}
      {message && (
        <p
          className={message.startsWith("已追加") ? "ros-success" : "ros-error"}
        >
          {message}
        </p>
      )}
    </section>
  );
}

function MarketExpressionRegistration({
  caseId,
  theses,
  onRegistered,
}: {
  caseId: string;
  theses: ThesisOption[];
  onRegistered: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [loading, setLoading] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [claimText, setClaimText] = useState("");
  const [claimKind, setClaimKind] = useState<
    "disclosed_fact" | "forecast" | "research_opinion"
  >("research_opinion");
  const [assertedBy, setAssertedBy] = useState("");
  const [claimReviewer, setClaimReviewer] = useState("human:researcher");
  const [claimReason, setClaimReason] = useState("");
  const [claimId, setClaimId] = useState<string | null>(null);
  const [factorName, setFactorName] = useState("");
  const [direction, setDirection] = useState<
    "positive" | "negative" | "neutral"
  >("positive");
  const [metricName, setMetricName] = useState("");
  const [sourceTypes, setSourceTypes] = useState("company_disclosure");
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [support, setSupport] = useState("");
  const [refutation, setRefutation] = useState("");
  const [nextEvent, setNextEvent] = useState("");
  const [thesisId, setThesisId] = useState("");
  const [factorReviewer, setFactorReviewer] = useState("human:researcher");
  const [factorReason, setFactorReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const selectedSource = sources.find((item) => item.id === sourceId) ?? null;

  async function begin() {
    setOpen(true);
    setLoading(true);
    setMessage(null);
    try {
      const response = await researchOsApi.sourceStatements(caseId);
      setSources(response.items);
      const first = response.items[0];
      if (first) {
        setSourceId(first.id);
        setClaimText(first.text);
        setClaimKind(
          first.kind === "disclosed_fact" || first.kind === "forecast"
            ? first.kind
            : "research_opinion",
        );
      }
    } catch {
      setMessage(
        "无法读取当前 Case 可用的冻结原文；没有提供跨 Case 或未准入来源的替代选项。",
      );
    } finally {
      setLoading(false);
    }
  }

  async function saveClaim() {
    if (
      !sourceId ||
      !claimText.trim() ||
      !assertedBy.trim() ||
      !claimReviewer.trim() ||
      !claimReason.trim()
    )
      return;
    setBusy(true);
    setMessage(null);
    try {
      const claim = await researchOsApi.createReportClaim(caseId, {
        source_statement_id: sourceId,
        text: claimText.trim(),
        claim_kind: claimKind,
        asserted_period: null,
        asserted_by: assertedBy.trim(),
        reviewed_by: claimReviewer.trim(),
        review_reason: claimReason.trim(),
      });
      setClaimId(claim.id);
      setFactorName(claim.text);
      setMessage(
        "已登记已审核主张；它仍是研究观点或预测，不会自动变成事实或结论。",
      );
    } catch {
      setMessage("主张未登记。请核对冻结原文、来源许可、审核人和理由后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function saveFactor() {
    const allowed = sourceTypes
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (
      !claimId ||
      !factorName.trim() ||
      !metricName.trim() ||
      !allowed.length ||
      !support.trim() ||
      !refutation.trim() ||
      !nextEvent.trim() ||
      !factorReviewer.trim() ||
      !factorReason.trim()
    )
      return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createKeyFactor(caseId, {
        report_claim_id: claimId,
        thesis_id: thesisId || null,
        name: factorName.trim(),
        expected_direction: direction,
        metric_name: metricName.trim(),
        allowed_source_types: allowed,
        verification_window_start: windowStart || null,
        verification_window_end: windowEnd || null,
        support_condition: support.trim(),
        refutation_condition: refutation.trim(),
        next_verification_event: nextEvent.trim(),
        reviewed_by: factorReviewer.trim(),
        review_reason: factorReason.trim(),
      });
      setMessage(
        "已登记已审核关键因素；支持、反证、来源范围和时间窗已固定，后续补证可按此复现。",
      );
      onRegistered();
    } catch {
      setMessage(
        "关键因素未登记。请确认它连接的是当前 Case 已审核主张与已确认命题。 ",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ros-market-register">
      <div>
        <p className="ros-eyebrow">人工登记 · 追加式记录</p>
        <h3>登记已审核主张与关键因素</h3>
        <p>
          只可选择本 Case
          内已准入、可定位的冻结原文；保存会追加审核记录，绝不覆盖既有主张、因素或结论。
        </p>
      </div>
      {!open ? (
        <button
          className="ros-button ros-button--secondary"
          type="button"
          onClick={() => void begin()}
        >
          选择冻结原文并登记主张
        </button>
      ) : (
        <div className="ros-market-register__body">
          {loading ? (
            <p className="ros-note">正在核对当前 Case 的原文权限与定位…</p>
          ) : (
            <>
              <label>
                冻结原文陈述
                <select
                  aria-label="冻结原文陈述"
                  value={sourceId}
                  onChange={(event) => {
                    const next = sources.find(
                      (item) => item.id === event.target.value,
                    );
                    setSourceId(event.target.value);
                    if (next) {
                      setClaimText(next.text);
                      setClaimKind(
                        next.kind === "disclosed_fact" ||
                          next.kind === "forecast"
                          ? next.kind
                          : "research_opinion",
                      );
                    }
                  }}
                >
                  <option value="">请选择已准入原文</option>
                  {sources.map((item) => (
                    <option value={item.id} key={item.id}>
                      {item.document_title} · {item.text.slice(0, 42)}
                    </option>
                  ))}
                </select>
              </label>
              {selectedSource ? (
                <article className="ros-market-register__source">
                  <strong>{selectedSource.document_title}</strong>
                  <small>
                    定位 {JSON.stringify(selectedSource.locator)} · 可得{" "}
                    {new Date(selectedSource.available_at).toLocaleString(
                      "zh-CN",
                    )}{" "}
                    · 许可：已准入
                  </small>
                  {selectedSource.source_url && (
                    <a
                      className="ros-source-link"
                      href={selectedSource.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开来源地址
                    </a>
                  )}
                </article>
              ) : (
                <p className="ros-note">
                  当前 Case 没有已准入的原文陈述。请先在“原文资料 /
                  证据审核”完成资料冻结与人工审核。
                </p>
              )}
              <div className="ros-market-register__grid">
                <label>
                  主张表述
                  <textarea
                    aria-label="主张表述"
                    value={claimText}
                    onChange={(event) => setClaimText(event.target.value)}
                  />
                </label>
                <label>
                  主张类型
                  <select
                    aria-label="主张类型"
                    value={claimKind}
                    onChange={(event) =>
                      setClaimKind(event.target.value as typeof claimKind)
                    }
                  >
                    <option value="disclosed_fact">已披露事实</option>
                    <option value="forecast">预测</option>
                    <option value="research_opinion">研究意见</option>
                  </select>
                </label>
                <label>
                  主张归属
                  <input
                    aria-label="主张归属"
                    value={assertedBy}
                    onChange={(event) => setAssertedBy(event.target.value)}
                    placeholder="例如：某券商 / 公司管理层"
                  />
                </label>
                <label>
                  审核人
                  <input
                    aria-label="主张审核人"
                    value={claimReviewer}
                    onChange={(event) => setClaimReviewer(event.target.value)}
                  />
                </label>
                <label className="is-wide">
                  审核理由
                  <textarea
                    aria-label="主张审核理由"
                    value={claimReason}
                    onChange={(event) => setClaimReason(event.target.value)}
                    placeholder="说明已核对的原文、定位、主体与表述边界…"
                  />
                </label>
              </div>
              <button
                className="ros-button ros-button--primary"
                type="button"
                disabled={
                  busy ||
                  !sourceId ||
                  !claimText.trim() ||
                  !assertedBy.trim() ||
                  !claimReason.trim()
                }
                onClick={() => void saveClaim()}
              >
                {busy
                  ? "正在追加主张…"
                  : claimId
                    ? "已登记主张"
                    : "登记已审核主张"}
              </button>
              {claimId && (
                <section className="ros-market-register__factor">
                  <p className="ros-eyebrow">
                    下一步 · 从已审核主张拆解可验证因素
                  </p>
                  <h4>固定关键因素的验证口径</h4>
                  <div className="ros-market-register__grid">
                    <label>
                      关键因素名称
                      <input
                        aria-label="关键因素名称"
                        value={factorName}
                        onChange={(event) => setFactorName(event.target.value)}
                      />
                    </label>
                    <label>
                      预期方向
                      <select
                        aria-label="预期方向"
                        value={direction}
                        onChange={(event) =>
                          setDirection(event.target.value as typeof direction)
                        }
                      >
                        <option value="positive">正向</option>
                        <option value="negative">负向</option>
                        <option value="neutral">中性</option>
                      </select>
                    </label>
                    <label>
                      验证指标
                      <input
                        aria-label="验证指标"
                        value={metricName}
                        onChange={(event) => setMetricName(event.target.value)}
                        placeholder="例如：订单同比增速"
                      />
                    </label>
                    <label>
                      允许来源（逗号分隔）
                      <input
                        aria-label="允许来源"
                        value={sourceTypes}
                        onChange={(event) => setSourceTypes(event.target.value)}
                      />
                    </label>
                    <label>
                      开始日期
                      <input
                        aria-label="验证开始日期"
                        type="date"
                        value={windowStart}
                        onChange={(event) => setWindowStart(event.target.value)}
                      />
                    </label>
                    <label>
                      结束日期
                      <input
                        aria-label="验证结束日期"
                        type="date"
                        value={windowEnd}
                        onChange={(event) => setWindowEnd(event.target.value)}
                      />
                    </label>
                    <label>
                      关联已确认命题
                      <select
                        aria-label="关联已确认命题"
                        value={thesisId}
                        onChange={(event) => setThesisId(event.target.value)}
                      >
                        <option value="">暂不关联（不能立即启动补证）</option>
                        {theses.map((thesis) => (
                          <option key={thesis.id} value={thesis.id}>
                            {thesis.statement}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      审核人
                      <input
                        aria-label="因素审核人"
                        value={factorReviewer}
                        onChange={(event) =>
                          setFactorReviewer(event.target.value)
                        }
                      />
                    </label>
                    <label className="is-wide">
                      支持条件
                      <textarea
                        aria-label="支持条件"
                        value={support}
                        onChange={(event) => setSupport(event.target.value)}
                      />
                    </label>
                    <label className="is-wide">
                      反证条件
                      <textarea
                        aria-label="反证条件"
                        value={refutation}
                        onChange={(event) => setRefutation(event.target.value)}
                      />
                    </label>
                    <label>
                      下一验证事件
                      <input
                        aria-label="下一验证事件"
                        value={nextEvent}
                        onChange={(event) => setNextEvent(event.target.value)}
                        placeholder="例如：2026 年三季报"
                      />
                    </label>
                    <label>
                      审核理由
                      <textarea
                        aria-label="因素审核理由"
                        value={factorReason}
                        onChange={(event) =>
                          setFactorReason(event.target.value)
                        }
                      />
                    </label>
                  </div>
                  <button
                    className="ros-button ros-button--primary"
                    type="button"
                    disabled={
                      busy ||
                      !factorName.trim() ||
                      !metricName.trim() ||
                      !support.trim() ||
                      !refutation.trim() ||
                      !nextEvent.trim() ||
                      !factorReason.trim()
                    }
                    onClick={() => void saveFactor()}
                  >
                    {busy ? "正在追加关键因素…" : "登记已审核关键因素"}
                  </button>
                </section>
              )}
            </>
          )}
          {message && (
            <p
              className={
                message.startsWith("已登记") ? "ros-success" : "ros-error"
              }
            >
              {message}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function VerificationRegistration({
  caseId,
  factor,
  onSaved,
}: {
  caseId: string;
  factor: MarketExpression["factors"][number];
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [sourceId, setSourceId] = useState("");
  const [outcome, setOutcome] = useState<
    "supported" | "contradicted" | "insufficient_evidence" | "not_due"
  >("supported");
  const [rationale, setRationale] = useState("");
  const [reviewer, setReviewer] = useState("human:researcher");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function openForm() {
    setOpen(true);
    setMessage(null);
    try {
      const response = await researchOsApi.sourceStatements(caseId);
      setSources(response.items);
      setSourceId(response.items[0]?.id ?? "");
    } catch {
      setMessage(
        "无法读取可用于审核验证的冻结原文；不会用运行摘要或市场表现替代来源。 ",
      );
    }
  }
  async function save() {
    if (!sourceId || !rationale.trim() || !reviewer.trim() || !reason.trim())
      return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createClaimVerification(caseId, factor.id, {
        source_statement_id: sourceId,
        outcome,
        rationale: rationale.trim(),
        reviewed_by: reviewer.trim(),
        review_reason: reason.trim(),
      });
      setMessage(
        "已追加审核验证；此前记录不被覆盖，当前读模型会展示最新审核结果。 ",
      );
      onSaved();
    } catch {
      setMessage("验证未登记。请核对来源、原文定位、审核人和理由。 ");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ros-factor-verification">
      <p>
        不会把运行结果自动写成支持或反证；每次验证必须选择当前 Case
        已准入的冻结原文并记录理由。
      </p>
      {!open ? (
        <button
          className="ros-button ros-button--secondary"
          type="button"
          onClick={() => void openForm()}
        >
          登记审核验证
        </button>
      ) : (
        <div>
          <label>
            验证来源
            <select
              aria-label="验证来源"
              value={sourceId}
              onChange={(event) => setSourceId(event.target.value)}
            >
              <option value="">请选择冻结原文</option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.document_title} · {source.text.slice(0, 36)}
                </option>
              ))}
            </select>
          </label>
          <label>
            验证结果
            <select
              aria-label="验证结果"
              value={outcome}
              onChange={(event) =>
                setOutcome(event.target.value as typeof outcome)
              }
            >
              <option value="supported">得到支持</option>
              <option value="contradicted">出现反证</option>
              <option value="insufficient_evidence">证据不足</option>
              <option value="not_due">尚未到验证时点</option>
            </select>
          </label>
          <label>
            核验说明
            <textarea
              aria-label="核验说明"
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="说明该原文如何满足或不满足支持/反证条件…"
            />
          </label>
          <label>
            审核人
            <input
              aria-label="验证审核人"
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
            />
          </label>
          <label>
            审核理由
            <textarea
              aria-label="验证审核理由"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            disabled={busy || !sourceId || !rationale.trim() || !reason.trim()}
            onClick={() => void save()}
          >
            {busy ? "正在追加验证…" : "保存审核验证"}
          </button>
        </div>
      )}
      {message && (
        <p
          className={message.startsWith("已追加") ? "ros-success" : "ros-error"}
        >
          {message}
        </p>
      )}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="ros-empty ros-empty--compact">{text}</p>;
}
