import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  sourceTypeLabel,
  sourceTypeListLabel,
} from "../../domain/sourcePresentation";
import {
  researchOsApi,
  type MarketExpression,
  type MarketInstrumentBindings,
  type ForecastVerdictHistory,
  type ActiveResearchRun,
  type Researchability,
  type SourceStatementOptions,
} from "../../app/researchOsApi";
import { FundDisclosureSyncTask } from "./FundDisclosureSyncTask";

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

function publicWebUrl(sourceUrl?: string | null): string | null {
  return sourceUrl?.match(/^https?:\/\//i) ? sourceUrl : null;
}

type ThesisOption = { id: string; statement: string };
type KeyFactorCandidateRun = Awaited<
  ReturnType<typeof researchOsApi.keyFactorCandidateRuns>
>["items"][number];
type KeyFactorCandidateDraft = {
  sourceStatementId: string;
  candidate: KeyFactorCandidateRun["candidates"][number];
};

export function MarketExpressionContent({
  caseId,
  theses = [],
}: {
  caseId: string;
  theses?: ThesisOption[];
}) {
  const [expression, setExpression] = useState<MarketExpression | null>(null);
  const [forecastHistory, setForecastHistory] =
    useState<ForecastVerdictHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFactorId, setSelectedFactorId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [startedRunId, setStartedRunId] = useState<string | null>(null);
  const [fundDisclosureScopeRevision, setFundDisclosureScopeRevision] = useState(0);
  const [factorResearchability, setFactorResearchability] =
    useState<Researchability | null>(null);
  const [factorResearchabilityLoading, setFactorResearchabilityLoading] =
    useState(false);
  const [factorResearchabilityError, setFactorResearchabilityError] =
    useState(false);
  const [activeFactorRun, setActiveFactorRun] =
    useState<ActiveResearchRun | null>(null);
  const [activeFactorRunLoading, setActiveFactorRunLoading] = useState(false);
  const [factorRunRevision, setFactorRunRevision] = useState(0);
  const [candidateForRegistration, setCandidateForRegistration] =
    useState<KeyFactorCandidateDraft | null>(null);
  const selectedFactor =
    expression?.factors.find((factor) => factor.id === selectedFactorId) ??
    expression?.factors[0] ??
    null;

  async function reloadExpression() {
    setError(null);
    try {
      const [value, verdicts] = await Promise.all([
        researchOsApi.marketExpression(caseId),
        researchOsApi.forecastVerdicts(caseId),
      ]);
      setExpression(value);
      setForecastHistory(verdicts);
      setSelectedFactorId(value.factors[0]?.id ?? null);
      setFundDisclosureScopeRevision((revision) => revision + 1);
    } catch {
      setError(
        "无法读取已审核的市场表达；系统不会以 Case 摘要或未审核候选替代该层记录。",
      );
    }
  }
  useEffect(() => {
    void reloadExpression();
  }, [caseId]);
  useEffect(() => {
    let active = true;
    if (!selectedFactor?.thesis_id) {
      setFactorResearchability(null);
      setFactorResearchabilityLoading(false);
      setFactorResearchabilityError(false);
      return () => {
        active = false;
      };
    }
    setFactorResearchability(null);
    setFactorResearchabilityLoading(true);
    setFactorResearchabilityError(false);
    researchOsApi
      .researchability(selectedFactor.thesis_id)
      .then((value) => {
        if (active) setFactorResearchability(value);
      })
      .catch(() => {
        if (active) setFactorResearchabilityError(true);
      })
      .finally(() => {
        if (active) setFactorResearchabilityLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedFactor?.thesis_id]);
  useEffect(() => {
    let active = true;
    if (!selectedFactor?.thesis_id) {
      setActiveFactorRun(null);
      setActiveFactorRunLoading(false);
      return () => {
        active = false;
      };
    }
    setActiveFactorRun(null);
    setActiveFactorRunLoading(true);
    researchOsApi
      .activeRuns()
      .then((response) => {
        const matchingRun = response.items.find(
          (run) =>
            run.case_id === caseId &&
            run.scope.trigger === "factor_manual" &&
            run.scope.factor_ids?.length === 1 &&
            run.scope.factor_ids[0] === selectedFactor.thesis_id,
        );
        if (active) setActiveFactorRun(matchingRun ?? null);
      })
      .catch(() => {
        if (active) setActiveFactorRun(null);
      })
      .finally(() => {
        if (active) setActiveFactorRunLoading(false);
      });
    return () => {
      active = false;
    };
  }, [caseId, selectedFactor?.thesis_id, factorRunRevision]);

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
  const selectedForecastVerdicts = selectedFactor
    ? (forecastHistory?.items ?? []).filter(
        (verdict) => verdict.target.key_factor_id === selectedFactor.id,
      )
    : [];
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
      const run = await researchOsApi.startFactorMonitorRun(caseId, selectedFactor.id);
      setStartedRunId(run.id);
      setActionNotice(
        `已创建单因素补证运行 ${run.id.slice(0, 8)}；范围、配置版本和允许来源已冻结，等待研究 worker 领取后继续执行。`,
      );
      setFactorRunRevision((revision) => revision + 1);
    } catch {
      setRunError(
        "无法按该因素的已审核关联与许可范围创建补证运行；没有创建部分运行。",
      );
    } finally {
      setStarting(false);
    }
  }
  const sourceMetadata = selectedClaim?.source;
  const sourceWebUrl = publicWebUrl(sourceMetadata?.source_url);
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
      <KeyFactorCandidateParser
        caseId={caseId}
        onUseCandidate={setCandidateForRegistration}
      />
      <MarketExpressionRegistration
        caseId={caseId}
        theses={theses}
        claims={expression.claims}
        candidate={candidateForRegistration}
        onRegistered={() => {
          setCandidateForRegistration(null);
          void reloadExpression();
        }}
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
          {startedRunId && (
            <>
              {" "}
              <Link to={`/events/${caseId}/monitor`}>查看运行记录</Link>
            </>
          )}
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
                    {sourceWebUrl ? (
                      <>
                        {" "}
                        ·{" "}
                        <a
                          className="ros-source-link"
                          href={sourceWebUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看来源地址
                        </a>
                      </>
                    ) : sourceMetadata.source_url ? (
                      <> · 来源地址不是可打开的网页链接</>
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
                      <small>
                        {observation.source?.document_version_id ? (
                          <Link
                            className="ros-source-link"
                            to={`/events/${caseId}/documents?document=${observation.source.document_version_id}`}
                          >
                            定位到冻结行情原文
                          </Link>
                        ) : "历史记录未保存冻结行情原文"}
                      </small>
                      <p className="ros-note">
                        这是市场观测，不自动表述为研报或因素造成。
                      </p>
                    </article>
                  ))
                ) : (
                  <Empty text="尚未登记带冻结行情原文的市场观测；系统不会把研报或公告期间的价格变化写成影响结论。" />
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
                disabled={
                  !selectedFactor.thesis_id ||
                  !selectedFactor.verification_window_start ||
                  !selectedFactor.verification_window_end ||
                  factorResearchabilityLoading ||
                  factorResearchabilityError ||
                  !factorResearchability ||
                  factorResearchability.status === "blocked" ||
                  activeFactorRunLoading ||
                  Boolean(activeFactorRun) ||
                  starting
                }
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
              {(!selectedFactor.verification_window_start ||
                !selectedFactor.verification_window_end) && (
                <p className="ros-note">
                  此历史关键因素未记录验证观察窗口，不能启动补证；请新建一条带明确窗口的审核因素，不会改写旧记录。
                </p>
              )}
              {selectedFactor.thesis_id && factorResearchabilityLoading && (
                <p className="ros-note">正在读取此因素关联命题的研究协议状态…</p>
              )}
              {selectedFactor.thesis_id && factorResearchabilityError && (
                <p className="ros-error">
                  研究协议状态暂不可读取，不能创建补证运行；请恢复读取后重试。
                </p>
              )}
              {factorResearchability?.status === "blocked" && (
                <p className="ros-note">
                  研究协议尚未通过，不能启动补证。下一步：
                  {factorResearchability.next_action}
                </p>
              )}
              {selectedFactor.thesis_id && activeFactorRunLoading && (
                <p className="ros-note">正在核对该因素是否已有未结束的补证运行…</p>
              )}
              {activeFactorRun && (
                <p className="ros-note">
                  该关键因素已有未结束的补证运行。{" "}
                  <Link to={`/events/${caseId}/monitor`}>查看运行记录</Link>
                  {`（${activeFactorRun.run_id.slice(0, 8)} · ${activeFactorRun.status}）`}
                </p>
              )}
              {runError && <p className="ros-error">{runError}</p>}
            </>
          )}
          <ForecastVerdictPanel
            caseId={caseId}
            verdicts={selectedForecastVerdicts}
          />
          <ForecastVerificationWorkflow
            caseId={caseId}
            factor={selectedFactor}
            claim={selectedClaim}
            onPublished={() => void reloadExpression()}
          />
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
          <FundDisclosureSyncTask
            caseId={caseId}
            scopeRevision={fundDisclosureScopeRevision}
            onCompleted={() => void reloadExpression()}
          />
        </aside>
      </div>
    </section>
  );
}

function ForecastVerdictPanel({
  caseId,
  verdicts,
}: {
  caseId: string;
  verdicts: ForecastVerdictHistory["items"];
}) {
  return (
    <section className="ros-forecast-verdicts" aria-live="polite">
      <p className="ros-eyebrow">冻结预测 · 人工发布</p>
      <h3>历史预测验证</h3>
      <p>
        只展示已由人工确认或修订的数值裁决；机器候选、被否决结论和未经审核推断不会进入这里。
      </p>
      {!verdicts.length ? (
        <Empty text="当前关键因素尚无已发布的历史预测裁决。" />
      ) : (
        verdicts.map((verdict) => {
          const outcome = verificationLabels[verdict.outcome] ?? verdict.outcome;
          const forecastSource = verdict.forecast_source;
          const actualSource = verdict.actual_source;
          return (
            <article className="ros-forecast-verdict" key={verdict.id}>
              <header>
                <strong>{outcome}</strong>
                <span>{verdict.target.metric_name} · {verdict.target.entity_key}</span>
              </header>
              <dl>
                <div>
                  <dt>冻结预测</dt>
                  <dd>{formatForecastValue(verdict.target.expected_value, verdict.target.unit)}</dd>
                </div>
                <div>
                  <dt>后续实际</dt>
                  <dd>{formatForecastValue(verdict.actual.observed_value, verdict.actual.unit)}</dd>
                </div>
                <div>
                  <dt>适用期间</dt>
                  <dd>{verdict.target.forecast_period_start} 至 {verdict.target.forecast_period_end}</dd>
                </div>
              </dl>
              <p>{verdict.reason}</p>
              <small>
                人工 {verdict.reviewed_by} · {new Date(verdict.reviewed_at).toLocaleString("zh-CN")}
              </small>
              <details>
                <summary>查看规则、冻结来源与审核轨迹</summary>
                <p>规则 {verdict.rule_version} · {verdict.candidate_rationale}</p>
                <p>输入 {Object.entries(verdict.inputs).map(([key, value]) => `${key}=${value}`).join("；")}</p>
                <SourceReference label="预测原文" source={forecastSource} caseId={caseId} />
                <SourceReference label="实际原文" source={actualSource} caseId={caseId} />
              </details>
            </article>
          );
        })
      )}
    </section>
  );
}

function ForecastVerificationWorkflow({
  caseId,
  factor,
  claim,
  onPublished,
}: {
  caseId: string;
  factor: MarketExpression["factors"][number] | null;
  claim: MarketExpression["claims"][number] | null;
  onPublished: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [targetId, setTargetId] = useState<string | null>(null);
  const [actualId, setActualId] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<Awaited<ReturnType<typeof researchOsApi.evaluateForecastTarget>> | null>(null);
  const [expectedValue, setExpectedValue] = useState("");
  const [baselineValue, setBaselineValue] = useState("");
  const [entityKey, setEntityKey] = useState("");
  const [unit, setUnit] = useState("");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [comparator, setComparator] = useState<"at_least" | "at_most" | "within_tolerance">("within_tolerance");
  const [tolerance, setTolerance] = useState("0.1");
  const [actualSourceId, setActualSourceId] = useState("");
  const [actualValue, setActualValue] = useState("");
  const [decision, setDecision] = useState<"confirmed" | "modified" | "rejected">("confirmed");
  const [modifiedOutcome, setModifiedOutcome] = useState<
    "supported" | "contradicted" | "insufficient_evidence" | "not_due"
  >("insufficient_evidence");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function begin() {
    setOpen(true);
    setMessage(null);
    if (!factor || !claim) return;
    setExpectedValue("");
    setBaselineValue("");
    setEntityKey("");
    setUnit("");
    setPeriodStart(factor.verification_window_start ?? "");
    setPeriodEnd(factor.verification_window_end ?? "");
    try {
      const options = await researchOsApi.sourceStatements(caseId);
      setSources(options.items);
      setActualSourceId(options.items.find((item) => item.id !== claim.source.source_statement_id)?.id ?? options.items[0]?.id ?? "");
    } catch {
      setMessage("无法读取当前 Case 已准入的冻结原文，不能用摘要或行情替代实际值来源。");
    }
  }

  async function saveTarget() {
    if (!factor || !claim || !claim.source.source_statement_id || !expectedValue || !entityKey || !unit || !periodStart || !periodEnd || !reason.trim()) return;
    setBusy(true); setMessage(null);
    try {
      const target = await researchOsApi.createForecastTarget(caseId, {
        key_factor_id: factor.id, report_claim_id: claim.id,
        forecast_source_statement_id: claim.source.source_statement_id,
        metric_name: factor.metric_name, entity_key: entityKey.trim(),
        baseline_value: baselineValue ? Number(baselineValue) : null,
        expected_value: Number(expectedValue), unit: unit.trim(),
        forecast_period_start: periodStart, forecast_period_end: periodEnd,
        comparator, relative_tolerance: comparator === "within_tolerance" ? Number(tolerance) : null,
        reviewed_by: "human:researcher", review_reason: reason.trim(),
      });
      setTargetId(target.id); setReason("");
      setMessage("已冻结预测目标。接下来需要录入同实体、同单位、同期间的后续实际值。");
    } catch { setMessage("预测目标未登记。请核对主张、因素、原文、期间、数值和审核理由。"); }
    finally { setBusy(false); }
  }

  async function saveActual() {
    const actualSource = sources.find((item) => item.id === actualSourceId);
    if (!targetId || !actualSource || !actualValue || !entityKey || !unit || !periodStart || !periodEnd || !reason.trim()) return;
    setBusy(true); setMessage(null);
    try {
      const actual = await researchOsApi.recordActualMetricObservation(caseId, {
        forecast_target_id: targetId, source_statement_id: actualSource.id,
        entity_key: entityKey.trim(), observed_value: Number(actualValue), unit: unit.trim(),
        observed_period_start: periodStart, observed_period_end: periodEnd,
        available_at: actualSource.available_at, recorded_by: "human:researcher", record_reason: reason.trim(),
      });
      setActualId(actual.id); setReason("");
      setMessage("已冻结后续实际值。它尚未是研究结论，需要先生成机器候选。");
    } catch { setMessage("实际值未登记。它必须与冻结预测的实体、单位、期间和可得时间精确匹配。"); }
    finally { setBusy(false); }
  }

  async function evaluate() {
    if (!targetId || !actualId) return;
    setBusy(true); setMessage(null);
    try {
      const next = await researchOsApi.evaluateForecastTarget(targetId, { actual_observation_id: actualId, cutoff: new Date().toISOString() });
      setCandidate(next); setMessage(`已生成${verificationLabels[next.outcome] ?? next.outcome}候选；仍须人工发布。`);
    } catch { setMessage("无法生成候选。系统没有改写任何已冻结记录。"); }
    finally { setBusy(false); }
  }

  async function publish() {
    if (!candidate || !reason.trim()) return;
    setBusy(true); setMessage(null);
    try {
      await researchOsApi.createForecastVerdict(candidate.id, {
        decision,
        outcome: decision === "modified"
          ? modifiedOutcome
          : null,
        reason: reason.trim(), reviewed_by: "human:researcher",
      });
      setMessage(decision === "rejected" ? "已记录否决，候选不会作为正式结论展示。" : "已追加人工发布裁决；历史预测验证将更新。 ");
      onPublished();
    } catch { setMessage("裁决未发布。请记录明确的人工作出理由。 "); }
    finally { setBusy(false); }
  }

  if (!factor || !claim) return null;
  const isFiniteInput = (value: string) =>
    value.trim() !== "" && Number.isFinite(Number(value));
  const targetRequirements = [
    !claim.source.source_statement_id ? "选择冻结预测原文" : null,
    !isFiniteInput(expectedValue) ? "填写有效的冻结预测值" : null,
    baselineValue.trim() && !isFiniteInput(baselineValue)
      ? "修正预测基线数值"
      : null,
    !entityKey.trim() ? "填写实体标识" : null,
    !unit.trim() ? "填写单位" : null,
    !periodStart ? "填写预测期间开始" : null,
    !periodEnd ? "填写预测期间结束" : null,
    comparator === "within_tolerance" &&
    (!isFiniteInput(tolerance) || Number(tolerance) < 0)
      ? "填写非负的相对容差"
      : null,
    !reason.trim() ? "填写本阶段审核理由" : null,
  ].filter((value): value is string => Boolean(value));
  const actualRequirements = [
    !actualSourceId ? "选择后续实际值来源" : null,
    !isFiniteInput(actualValue) ? "填写有效的后续实际值" : null,
    !entityKey.trim() ? "填写实体标识" : null,
    !unit.trim() ? "填写单位" : null,
    !periodStart ? "填写预测期间开始" : null,
    !periodEnd ? "填写预测期间结束" : null,
    !reason.trim() ? "填写本阶段审核理由" : null,
  ].filter((value): value is string => Boolean(value));
  const publishRequirements = !reason.trim() ? ["填写人工裁决理由"] : [];
  const activeRequirements = !targetId
    ? targetRequirements
    : !actualId
      ? actualRequirements
      : candidate
        ? publishRequirements
        : [];
  const actionReady = !busy && activeRequirements.length === 0;
  return (
    <section className="ros-forecast-workflow">
      <p className="ros-eyebrow">人工操作 · 追加式账本</p>
      <h3>登记历史预测验证</h3>
      <p>机器只会生成候选；只有人工确认或修订才会进入上方的历史预测验证。</p>
      {!open ? <button className="ros-button ros-button--secondary" type="button" onClick={() => void begin()}>登记历史预测验证</button> : (
        <div className="ros-forecast-workflow__body">
          <p>当前因素：{factor.metric_name} · 上游主张已审核。每一阶段都写入独立不可变记录。</p>
          {!targetId && <>
            <label>冻结预测值<input aria-label="冻结预测值" inputMode="decimal" value={expectedValue} onChange={(event) => setExpectedValue(event.target.value)} /></label>
            <label>预测基线（可选）<input inputMode="decimal" value={baselineValue} onChange={(event) => setBaselineValue(event.target.value)} /></label>
            <label>实体标识<input value={entityKey} onChange={(event) => setEntityKey(event.target.value)} placeholder="例如 300894.SZ" /></label>
            <label>单位<input value={unit} onChange={(event) => setUnit(event.target.value)} placeholder="例如 CNY、%" /></label>
            <label>预测期间开始<input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} /></label>
            <label>预测期间结束<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} /></label>
            <label>比较规则<select value={comparator} onChange={(event) => setComparator(event.target.value as typeof comparator)}><option value="within_tolerance">容差内</option><option value="at_least">不低于预测</option><option value="at_most">不高于预测</option></select></label>
            {comparator === "within_tolerance" && <label>相对容差<input inputMode="decimal" value={tolerance} onChange={(event) => setTolerance(event.target.value)} /></label>}
          </>}
          {targetId && !actualId && <>
            <label>后续实际值来源<select value={actualSourceId} onChange={(event) => setActualSourceId(event.target.value)}>{sources.map((item) => <option value={item.id} key={item.id}>{item.document_title} · {JSON.stringify(item.locator)}</option>)}</select></label>
            <label>后续实际值<input inputMode="decimal" value={actualValue} onChange={(event) => setActualValue(event.target.value)} /></label>
          </>}
          {candidate && <p className="ros-market-warning">机器候选：{verificationLabels[candidate.outcome] ?? candidate.outcome} · {candidate.rationale}</p>}
          {candidate && <label>发布方式<select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}><option value="confirmed">确认候选</option><option value="modified">修订结果</option><option value="rejected">否决候选</option></select></label>}
          {candidate && decision === "modified" && <label>修订后的结果<select value={modifiedOutcome} onChange={(event) => setModifiedOutcome(event.target.value as typeof modifiedOutcome)}><option value="supported">得到支持</option><option value="contradicted">出现反证</option><option value="insufficient_evidence">证据不足</option><option value="not_due">尚未到验证时点</option></select></label>}
          <label>{candidate ? "人工裁决理由" : "本阶段审核理由"}<textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明数值、口径、来源定位或人工判断" /></label>
          {!actionReady && <p className="ros-note" role="status">{!targetId ? "冻结预测前还需填写：" : !actualId ? "冻结实际值前还需填写：" : "发布裁决前还需填写："}{activeRequirements.join("、")}。系统不会以空白或摘要补写冻结记录。</p>}
          {!targetId ? <button className="ros-button ros-button--secondary" type="button" disabled={!actionReady} onClick={() => void saveTarget()}>冻结预测目标</button> : !actualId ? <button className="ros-button ros-button--secondary" type="button" disabled={!actionReady} onClick={() => void saveActual()}>冻结后续实际值</button> : !candidate ? <button className="ros-button ros-button--secondary" type="button" disabled={busy} onClick={() => void evaluate()}>生成机器候选</button> : <button className="ros-button ros-button--primary" type="button" disabled={!actionReady} onClick={() => void publish()}>发布人工裁决</button>}
          {message && <p className="ros-note" role="status">{message}</p>}
        </div>
      )}
    </section>
  );
}

function formatForecastValue(value: number, unit: string): string {
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 6 })}${unit ? ` ${unit}` : ""}`;
}

function SourceReference({
  label,
  source,
  caseId,
}: {
  label: string;
  source: ForecastVerdictHistory["items"][number]["forecast_source"];
  caseId: string;
}) {
  return (
    <p>
      {label}：{source.document_version_id ? (
        <Link className="ros-source-link" to={`/events/${caseId}/documents?document=${source.document_version_id}`}>
          定位到冻结原文
        </Link>
      ) : "冻结版本未记录"}
      {source.document_title ? `（${source.document_title}）` : ""}
      {source.locator ? ` · 定位 ${JSON.stringify(source.locator)}` : ""}
      {source.available_at ? ` · 可得 ${new Date(source.available_at).toLocaleString("zh-CN")}` : ""}
    </p>
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
  const bindingMissingRequirements = [
    !companyId ? "选择本地账本标的" : null,
    !sourceId ? "选择冻结原文" : null,
    !reviewer.trim() ? "填写审核人" : null,
    !reason.trim() ? "填写审核理由" : null,
  ].filter((requirement): requirement is string => Boolean(requirement));
  const bindingSaveReady = !busy && bindingMissingRequirements.length === 0;

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
    if (!bindingSaveReady) return;
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
            disabled={!bindingSaveReady}
            onClick={() => void saveBinding()}
          >
            {busy ? "正在追加标的关联…" : "保存已审核标的关联"}
          </button>
          {bindingMissingRequirements.length > 0 && (
            <p className="ros-note" role="status">
              保存标的关联前还需填写：{bindingMissingRequirements.join("、")}。系统不会从事件标题推断公司或股票。
            </p>
          )}
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
  const impactMissingRequirements = [
    !factor ? "选择关键因素" : null,
    !bindingId ? "选择已审核传导标的" : null,
    !sourceId ? "选择传导来源" : null,
    !metric.trim() ? "填写传导指标" : null,
    !rationale.trim() ? "填写传导机制" : null,
    !reviewer.trim() ? "填写审核人" : null,
    !reason.trim() ? "填写审核理由" : null,
  ].filter((requirement): requirement is string => Boolean(requirement));
  const impactSaveReady = !busy && impactMissingRequirements.length === 0;
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
    if (!impactSaveReady || !factor) return;
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
            disabled={!impactSaveReady}
            onClick={() => void save()}
          >
            {busy ? "正在追加传导…" : "保存已审核基本面传导"}
          </button>
          {impactMissingRequirements.length > 0 && (
            <p className="ros-note" role="status">
              保存基本面传导前还需填写：{impactMissingRequirements.join("、")}。系统不会把因素方向自动写成公司传导。
            </p>
          )}
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
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [sourceId, setSourceId] = useState("");
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
  const observationMissingRequirements = [
    !factor ? "选择关键因素" : null,
    !bindingId ? "选择包含股票的已审核标的" : null,
    !sourceId ? "选择冻结行情原文" : null,
    !eventAt ? "填写事件时间" : null,
    !availableAt ? "填写资料可得时间" : null,
    !windowLabel.trim() ? "填写观测窗口" : null,
    !benchmark.trim() ? "填写比较基准" : null,
    !priceSource.trim() ? "填写价格来源" : null,
    !afterHoursTreatment.trim() ? "填写盘后处理" : null,
    !reviewer.trim() ? "填写审核人" : null,
    !reason.trim() ? "填写审核理由" : null,
  ].filter((requirement): requirement is string => Boolean(requirement));
  const observationSaveReady =
    !busy && observationMissingRequirements.length === 0;
  async function begin() {
    if (!factor) return;
    setOpen(true);
    setMessage(null);
    setBindingId(stockBindings[0]?.id ?? "");
    setBusy(true);
    try {
      const options = await researchOsApi.sourceStatements(caseId);
      setSources(options.items);
      setSourceId(
        factor.verification?.source.source_statement_id
          ?? options.items[0]?.id
          ?? "",
      );
    } catch {
      setMessage("无法读取当前 Case 已准入的冻结行情原文；系统不会仅凭价格来源文字登记市场观测。");
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!observationSaveReady || !factor) return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createMarketObservation(caseId, factor.id, {
        market_instrument_binding_id: bindingId,
        source_statement_id: sourceId,
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
          onClick={() => void begin()}
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
            冻结行情原文
            <select
              aria-label="冻结行情原文"
              value={sourceId}
              onChange={(event) => setSourceId(event.target.value)}
            >
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.document_title} · {JSON.stringify(source.locator)}
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
            disabled={!observationSaveReady}
            onClick={() => void save()}
          >
            {busy ? "正在追加市场观测…" : "保存已审核市场观测"}
          </button>
          {observationMissingRequirements.length > 0 && (
            <p className="ros-note" role="status">
              保存市场观测前还需填写：{observationMissingRequirements.join("、")}。窗口表现只作为观测，不自动形成因果结论。
            </p>
          )}
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

function KeyFactorCandidateParser({
  caseId,
  onUseCandidate,
}: {
  caseId: string;
  onUseCandidate: (draft: KeyFactorCandidateDraft) => void;
}) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<SourceStatementOptions["items"]>([]);
  const [runs, setRuns] = useState<KeyFactorCandidateRun[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function begin() {
    setOpen(true);
    setBusy(true);
    setMessage(null);
    try {
      const [sourceResult, runResult] = await Promise.all([
        researchOsApi.sourceStatements(caseId),
        researchOsApi.keyFactorCandidateRuns(caseId),
      ]);
      setSources(sourceResult.items);
      setRuns(runResult.items);
      setSourceId(sourceResult.items[0]?.id ?? "");
    } catch {
      setMessage("无法读取可解析的冻结原文或既往解析记录；系统没有改用摘要或跨 Case 资料。");
    } finally {
      setBusy(false);
    }
  }

  async function parse() {
    if (!sourceId || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await researchOsApi.startKeyFactorCandidateRun(caseId, {
        source_statement_id: sourceId,
        requested_by: "human:researcher",
      });
      setRuns((current) => [result, ...current.filter((run) => run.id !== result.id)]);
      setMessage(
        result.candidate_count
          ? "解析已完成：候选已保留原文、规则版本和默认核验口径，等待人工确认。"
          : result.skipped_reason ?? "解析完成，但没有生成可验证候选。",
      );
    } catch {
      setMessage("解析未执行。请确认原文属于当前 Case，且已获处理与展示许可。");
    } finally {
      setBusy(false);
    }
  }

  const selectedSource = sources.find((item) => item.id === sourceId);
  const latest = runs[0] ?? null;
  return (
    <section className="ros-market-register">
      <div>
        <p className="ros-eyebrow">可复现解析 · 候选，不是结论</p>
        <h3>从冻结原文解析关键因素</h3>
        <p>只解析当前 Case 已准入原文；每次运行会保留来源、规则版本和未生成候选的原因。</p>
      </div>
      {!open ? (
        <button className="ros-button ros-button--secondary" type="button" onClick={() => void begin()}>
          从冻结原文解析关键因素
        </button>
      ) : (
        <div className="ros-market-register__body">
          <label>
            解析来源原文
            <select aria-label="解析来源原文" value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
              <option value="">请选择已准入原文</option>
              {sources.map((item) => <option key={item.id} value={item.id}>{item.document_title} · {item.text.slice(0, 42)}</option>)}
            </select>
          </label>
          {selectedSource && <p className="ros-note">定位 {JSON.stringify(selectedSource.locator)} · 可得 {new Date(selectedSource.available_at).toLocaleString("zh-CN")} · 已准入</p>}
          <button className="ros-button ros-button--primary" type="button" disabled={!sourceId || busy} onClick={() => void parse()}>
            {busy ? "正在按冻结原文解析…" : "生成关键因素候选"}
          </button>
          {!sourceId && <p className="ros-note" role="status">请先选择当前 Case 已准入的冻结原文；系统不会从摘要生成候选。</p>}
          {message && <p className={message.startsWith("解析已完成") ? "ros-success" : "ros-note"} role="status">{message}</p>}
          {latest && (
            <section className="ros-market-register__factor">
              <p className="ros-eyebrow">解析记录 · {new Date(latest.created_at).toLocaleString("zh-CN")}</p>
              <h4>关键因素候选解析</h4>
              <p><span>规则 {latest.parser_version}</span> · 发起人 {latest.requested_by} · 来源定位 {latest.source.locator ? JSON.stringify(latest.source.locator) : "未记录"}</p>
              {latest.candidates.length ? latest.candidates.map((candidate) => (
                <article className="ros-market-register__source" key={candidate.id}>
                  <strong>{candidate.name}</strong>
                  <small>{candidate.metric_name} · {expectedDirectionLabels[candidate.expected_direction] ?? candidate.expected_direction} · 规则 {candidate.rule_id}</small>
                  <blockquote>{candidate.evidence_excerpt}</blockquote>
                  <small>验证窗口 {candidate.verification_window_start} 至 {candidate.verification_window_end} · 下一事件：{candidate.next_verification_event}</small>
                  <p>候选尚未成为正式关键因素；研究员必须在下方登记主张、补齐或确认核验口径并填写审核理由。</p>
                  {latest.source.source_statement_id && (
                    <button
                      className="ros-button ros-button--secondary"
                      type="button"
                      onClick={() => onUseCandidate({
                        sourceStatementId: latest.source.source_statement_id!,
                        candidate,
                      })}
                    >
                      带入人工登记
                    </button>
                  )}
                </article>
              )) : <p className="ros-note">{latest.skipped_reason ?? "该次解析没有生成候选。"}</p>}
            </section>
          )}
        </div>
      )}
    </section>
  );
}

function MarketExpressionRegistration({
  caseId,
  theses,
  claims,
  candidate,
  onRegistered,
}: {
  caseId: string;
  theses: ThesisOption[];
  claims: MarketExpression["claims"];
  candidate: KeyFactorCandidateDraft | null;
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
  const [savedClaim, setSavedClaim] = useState<
    MarketExpression["claims"][number] | null
  >(null);
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
  const allowedSourceTypes = sourceTypes
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const claimMissingRequirements = [
    !sourceId ? "选择已准入冻结原文" : null,
    !claimText.trim() ? "填写主张表述" : null,
    !assertedBy.trim() ? "填写主张归属" : null,
    !claimReviewer.trim() ? "填写审核人" : null,
    !claimReason.trim() ? "填写审核理由" : null,
  ].filter((requirement): requirement is string => Boolean(requirement));
  const factorMissingRequirements = [
    !claimId ? "选择已审核主张" : null,
    !factorName.trim() ? "填写关键因素名称" : null,
    !metricName.trim() ? "填写验证指标" : null,
    !allowedSourceTypes.length ? "填写允许来源" : null,
    !windowStart ? "填写验证开始日期" : null,
    !windowEnd ? "填写验证结束日期" : null,
    !support.trim() ? "填写支持条件" : null,
    !refutation.trim() ? "填写反证条件" : null,
    !nextEvent.trim() ? "填写下一验证事件" : null,
    !factorReviewer.trim() ? "填写审核人" : null,
    !factorReason.trim() ? "填写审核理由" : null,
  ].filter((requirement): requirement is string => Boolean(requirement));
  const claimSaveReady = !busy && claimMissingRequirements.length === 0;
  const factorSaveReady = !busy && factorMissingRequirements.length === 0;
  const availableClaims = savedClaim
    ? [
        ...claims.filter((claim) => claim.id !== savedClaim.id),
        savedClaim,
      ]
    : claims;

  useEffect(() => {
    if (!candidate) return;
    let active = true;
    setOpen(true);
    setLoading(true);
    setMessage(null);
    researchOsApi
      .sourceStatements(caseId)
      .then((response) => {
        if (!active) return;
        const source = response.items.find(
          (item) => item.id === candidate.sourceStatementId,
        );
        setSources(response.items);
        if (!source) {
          setMessage("候选来源已不在当前 Case 的可用冻结原文中，不能带入登记。");
          return;
        }
        setSourceId(source.id);
        setClaimText(candidate.candidate.evidence_excerpt);
        setClaimKind("forecast");
        setFactorName(candidate.candidate.name);
        setDirection(candidate.candidate.expected_direction as typeof direction);
        setMetricName(candidate.candidate.metric_name);
        setSourceTypes("company_disclosure");
        setWindowStart(candidate.candidate.verification_window_start);
        setWindowEnd(candidate.candidate.verification_window_end);
        setSupport(candidate.candidate.support_condition);
        setRefutation(candidate.candidate.refutation_condition);
        setNextEvent(candidate.candidate.next_verification_event);
        setMessage("候选已带入登记草稿；请补充主张归属、审核理由，并核对后再追加正式记录。");
      })
      .catch(() => {
        if (active) setMessage("候选未带入。无法读取当前 Case 的冻结原文权限。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [caseId, candidate?.candidate.id]);

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
    if (!claimSaveReady) return;
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
      setSavedClaim(claim);
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

  function selectClaim(id: string) {
    const claim = availableClaims.find((item) => item.id === id);
    setClaimId(claim?.id ?? null);
    if (claim) setFactorName(claim.text);
  }

  async function saveFactor() {
    if (!factorSaveReady || !claimId) return;
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createKeyFactor(caseId, {
        report_claim_id: claimId,
        thesis_id: thesisId || null,
        name: factorName.trim(),
        expected_direction: direction,
        metric_name: metricName.trim(),
        allowed_source_types: allowedSourceTypes,
        verification_window_start: windowStart,
        verification_window_end: windowEnd,
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
                  {publicWebUrl(selectedSource.source_url) ? (
                    <a
                      className="ros-source-link"
                      href={publicWebUrl(selectedSource.source_url) ?? undefined}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开来源地址
                    </a>
                  ) : selectedSource.source_url ? (
                    <small>来源记录不是可打开的网页链接</small>
                  ) : null}
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
                disabled={!claimSaveReady}
                onClick={() => void saveClaim()}
              >
                {busy
                  ? "正在追加主张…"
                  : claimId
                    ? "已登记主张"
                    : "登记已审核主张"}
              </button>
              {claimMissingRequirements.length > 0 && (
                <p className="ros-note" role="status">
                  登记主张前还需填写：{claimMissingRequirements.join("、")}。系统不会从摘要或事件标题补写主张。
                </p>
              )}
              {availableClaims.length > 0 && (
                <label>
                  已审核主张
                  <select
                    aria-label="已审核主张"
                    value={claimId ?? ""}
                    onChange={(event) => selectClaim(event.target.value)}
                  >
                    <option value="">请选择要拆解为因素的已审核主张</option>
                    {availableClaims.map((claim) => (
                      <option value={claim.id} key={claim.id}>
                        {claim.text}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {(claimId || candidate) && (
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
                    disabled={!factorSaveReady}
                    onClick={() => void saveFactor()}
                  >
                    {busy ? "正在追加关键因素…" : "登记已审核关键因素"}
                  </button>
                  {factorMissingRequirements.length > 0 && (
                    <p className="ros-note" role="status">
                      登记关键因素前还需填写：{factorMissingRequirements.join("、")}。系统不会默认补写验证口径。
                    </p>
                  )}
                </section>
              )}
            </>
          )}
          {message && (
            <p
              className={
                message.startsWith("已登记") || message.startsWith("候选已带入")
                  ? "ros-success"
                  : "ros-error"
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
  const verificationMissingRequirements = [
    !sourceId ? "选择冻结原文" : null,
    !rationale.trim() ? "填写核验说明" : null,
    !reviewer.trim() ? "填写审核人" : null,
    !reason.trim() ? "填写审核理由" : null,
  ].filter((requirement): requirement is string => Boolean(requirement));
  const verificationSaveReady =
    !busy && verificationMissingRequirements.length === 0;
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
    if (!verificationSaveReady) return;
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
            disabled={!verificationSaveReady}
            onClick={() => void save()}
          >
            {busy ? "正在追加验证…" : "保存审核验证"}
          </button>
          {verificationMissingRequirements.length > 0 && (
            <p className="ros-note" role="status">
              保存审核验证前还需填写：{verificationMissingRequirements.join("、")}。系统不会把运行结果自动写成支持或反证。
            </p>
          )}
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
