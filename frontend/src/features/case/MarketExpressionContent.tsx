import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { researchOsApi, type MarketExpression } from "../../app/researchOsApi";

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

export function MarketExpressionContent({ caseId }: { caseId: string }) {
  const navigate = useNavigate();
  const [expression, setExpression] = useState<MarketExpression | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFactorId, setSelectedFactorId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    setExpression(null);
    setError(null);
    researchOsApi.marketExpression(caseId).then((value) => {
      setExpression(value);
      setSelectedFactorId(value.factors[0]?.id ?? null);
    }).catch(() => {
      setError("无法读取已审核的市场表达；系统不会以 Case 摘要或未审核候选替代该层记录。");
    });
  }, [caseId]);

  if (error) return <p className="ros-error ros-page-gap" role="alert">{error}</p>;
  if (!expression) return <div className="ros-empty ros-page-gap">正在读取已审核的市场表达…</div>;
  const selectedFactor = expression.factors.find((factor) => factor.id === selectedFactorId) ?? expression.factors[0] ?? null;
  const selectedClaim = selectedFactor?.report_claim_id ? expression.claims.find((claim) => claim.id === selectedFactor.report_claim_id) ?? null : null;
  const fundamentals = selectedFactor ? expression.fundamentals.filter((impact) => impact.key_factor_id === selectedFactor.id) : [];
  const observations = selectedFactor ? expression.market_observations.filter((observation) => observation.key_factor_id === selectedFactor.id) : [];
  const selectedStockIds = new Set(fundamentals.flatMap((impact) => impact.stock_id ? [impact.stock_id] : []));
  const relatedFunds = expression.fund_exposure.map((fund) => ({ ...fund, positions: fund.positions.filter((position) => selectedStockIds.has(position.stock_id)) })).filter((fund) => fund.positions.length > 0);
  const hasOperatingEvidence = fundamentals.length > 0;
  const hasMarketEvidence = observations.length > 0;
  const transmissionStatus = !selectedFactor ? "暂无可穿透的已审核因素" : hasOperatingEvidence && hasMarketEvidence ? "经营与市场验证均已可见" : hasOperatingEvidence ? "经营传导待市场验证" : hasMarketEvidence ? "市场反应已观察，待补经营传导" : "尚缺公司、股票或市场验证";
  async function startFactorRun() { if (!selectedFactor?.thesis_id) return; setStarting(true); setRunError(null); try { await researchOsApi.startFactorMonitorRun(caseId, selectedFactor.id); navigate(`/events/${caseId}/monitor`); } catch { setRunError("无法按该因素的已审核关联与许可范围创建补证运行；没有创建部分运行。"); } finally { setStarting(false); } }
  return <section className="ros-market ros-market-expression">
    <header className="ros-section-heading"><div><p className="ros-eyebrow">市场与表达 · 已审核读模型</p><h2>研报主张、后续验证与基金披露分层呈现</h2><p>截至 {expression.as_of} 的可见资料；查询截点 {new Date(expression.cutoff).toLocaleString("zh-CN")}。</p></div><span className="ros-pill ros-pill--system">仅已审核记录</span></header>
    <p className="ros-market-intro">研报观点不会自动变成事实；市场窗口只描述观测；基金数据只表示历史披露而非实时仓位。</p>
    <div className="ros-market-workbench">
      <aside className="ros-market-factors"><p className="ros-eyebrow">选择一个关键因素</p><h3>影响穿透</h3>{expression.factors.length ? expression.factors.map((factor) => <button type="button" className={`ros-market-factor${factor.id === selectedFactor?.id ? " is-selected" : ""}`} key={factor.id} aria-pressed={factor.id === selectedFactor?.id} onClick={() => setSelectedFactorId(factor.id)}><i className={factor.verification?.outcome === "contradicted" ? "is-risk" : factor.verification ? "" : "is-gap"} /><span><strong>{factor.name}</strong><small>{factor.metric_name}</small></span><b>{factor.verification ? verificationLabels[factor.verification.outcome] ?? factor.verification.outcome : "待验证"}</b></button>) : <Empty text="当前没有可进入正式验证的关键因素。" />}</aside>
      <section className="ros-market-chain" aria-live="polite"><p className="ros-eyebrow">当前因素的四段链路</p><h3>{selectedFactor?.name ?? "尚未选择关键因素"}</h3><p className="ros-market-status">{transmissionStatus}</p><div className="ros-chain-row"><article><span>01 · 研报主张</span><b>{selectedClaim?.text ?? (selectedFactor?.report_claim_id ? "上游主张未在当前截点可见" : "独立关键因素")}</b><small>{selectedClaim && <><span>{claimKindLabels[selectedClaim.claim_kind] ?? selectedClaim.claim_kind}</span> · 审核 {selectedClaim.reviewed_by}</>}</small><small>{selectedFactor ? `允许来源：${selectedFactor.allowed_source_types.join("、") || "未记录"}` : ""}</small></article><i>→</i><article><span>02 · 公司传导</span><b>{fundamentals.length ? `${fundamentals.length} 家已审核公司` : "尚未建立已审核传导"}</b><small>{fundamentals.map((impact) => `${impact.company_name}${impact.stock_code ? ` · ${impact.stock_code}` : " · 未上市/未映射"}`).join("；") || "需补关系来源与机制"}</small></article><i>→</i><article><span>03 · 股票验证</span><b>{hasMarketEvidence ? `${observations.length} 条市场观测` : "尚未取得市场验证"}</b><small>{hasMarketEvidence ? observations.map((item) => `${item.stock_code} ${item.window_label}`).join("；") : "经营数据与市场观测必须分开记录"}</small></article><i>→</i><article><span>04 · 基金披露</span><b>{relatedFunds.length ? `${relatedFunds.length} 只基金有历史披露` : "暂无可关联披露"}</b><small>{relatedFunds.length ? "仅展示已披露仓位，不表示实时仓位" : "未上市公司不进入基金暴露；披露不足不估算"}</small></article></div>
        <section className="ros-expression-lane"><div className="ros-expression-grid"><div><h4>公司传导 · {fundamentals.length}</h4>{fundamentals.length ? fundamentals.map((impact) => <article className="ros-expression-card" key={impact.id}><strong>{impact.company_name}{impact.stock_code ? ` · ${impact.stock_code}` : " · 未上市/未映射"}</strong><small>{impact.metric_name} · {impact.expected_direction === "positive" ? "预期向上" : impact.expected_direction === "negative" ? "预期向下" : "方向中性"}</small><p>{impact.rationale}</p><small>审核 {impact.reviewed_by} · {impact.source.locator ? `定位 ${JSON.stringify(impact.source.locator)}` : "原文定位未记录"}</small></article>) : <Empty text="当前因素尚缺已审核的公司传导关系。" />}</div><div><h4>事件窗口观测 · {observations.length}</h4>{observations.length ? observations.map((observation) => <article className="ros-expression-card" key={observation.id}><strong>{observation.stock_name} · {observation.window_label}</strong><small>事件 {new Date(observation.event_at).toLocaleString("zh-CN")} · 可得 {new Date(observation.available_at).toLocaleString("zh-CN")}</small><small>相对 {observation.benchmark} · 价格源 {observation.price_source}</small><b>{observation.relative_return === null ? "相对表现未记录" : `相对表现 ${(observation.relative_return * 100).toFixed(2)}%`}</b><p className="ros-note">这是市场观测，不自动表述为研报或因素造成。</p></article>) : <Empty text="当前因素尚缺股票市场观测。" />}</div></div></section>
      </section>
      <aside className="ros-market-rail"><p className="ros-eyebrow">核验口径与基金披露</p><h3>可复现，不伪造因果</h3>{selectedFactor && <><dl className="ros-market-checks"><div><dt>支持条件</dt><dd>{selectedFactor.support_condition}</dd></div><div><dt>反证条件</dt><dd>{selectedFactor.refutation_condition}</dd></div><div><dt>下一验证</dt><dd>{selectedFactor.next_verification_event}</dd></div><div><dt>审核理由</dt><dd>{selectedFactor.review_reason}</dd></div></dl><p className="ros-market-warning">{selectedFactor.verification ? `当前验证：${verificationLabels[selectedFactor.verification.outcome] ?? selectedFactor.verification.outcome}。${selectedFactor.verification.rationale}` : "尚未形成审核验证；不会把候选关系写成正式影响。"}</p><button className="ros-button ros-button--primary" type="button" disabled={!selectedFactor.thesis_id || starting} onClick={() => void startFactorRun()}>{starting ? "正在创建单因素补证…" : "立即补证此因素"}</button>{!selectedFactor.thesis_id && <p className="ros-note">尚未审核登记此关键因素与 Case 研究范围的关联，不能按名称猜测后启动运行。</p>}{runError && <p className="ros-error">{runError}</p>}</>}{relatedFunds.length ? relatedFunds.map((fund) => { const completeCoverage = fund.positions.every((position) => position.coverage_status === "complete"); return <article className="ros-fund-row" key={fund.fund_id}><strong>{fund.fund_name} <small>{fund.fund_code}</small></strong><span>{completeCoverage ? `相关已披露仓位 ${(fund.positions.reduce((total, position) => total + position.weight, 0) * 100).toFixed(2)}%` : "披露覆盖未知，不能汇总精确暴露"}</span>{fund.positions.map((position) => <small key={position.stock_id}>{position.stock_name} · {(position.weight * 100).toFixed(2)}%<br />报告期 {position.report_period} · 披露 {new Date(position.published_at).toLocaleDateString("zh-CN")} · 采集 {new Date(position.acquired_at).toLocaleDateString("zh-CN")}<br />来源 {position.source} · 覆盖 {position.coverage_status} · 时效 {position.freshness_status}</small>)}</article>; }) : <Empty text="当前因素未关联到可用的基金历史披露。" />}</aside>
    </div>
  </section>;
}

function Empty({ text }: { text: string }) { return <p className="ros-empty ros-empty--compact">{text}</p>; }
