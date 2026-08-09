import { useEffect, useState } from "react";

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
  const [expression, setExpression] = useState<MarketExpression | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setExpression(null);
    setError(null);
    researchOsApi.marketExpression(caseId).then(setExpression).catch(() => {
      setError("无法读取已审核的市场表达；系统不会以 Case 摘要或未审核候选替代该层记录。");
    });
  }, [caseId]);

  if (error) return <p className="ros-error ros-page-gap" role="alert">{error}</p>;
  if (!expression) return <div className="ros-empty ros-page-gap">正在读取已审核的市场表达…</div>;
  return <section className="ros-market ros-market-expression">
    <header className="ros-section-heading"><div><p className="ros-eyebrow">市场与表达 · 已审核读模型</p><h2>研报主张、后续验证与基金披露分层呈现</h2><p>截至 {expression.as_of} 的可见资料；查询截点 {new Date(expression.cutoff).toLocaleString("zh-CN")}。</p></div><span className="ros-pill ros-pill--system">仅已审核记录</span></header>
    <p className="ros-market-intro">研报观点不会自动变成事实；市场窗口只描述观测；基金数据只表示历史披露而非实时仓位。</p>
    <section className="ros-expression-lane"><header><p className="ros-eyebrow">研报主张 → 关键因素 → 验证</p><h3>每一步都带有原文、审核与反证边界</h3></header><div className="ros-expression-grid">
      <div><h4>研报主张 · {expression.claims.length}</h4>{expression.claims.length ? expression.claims.map((claim) => <article className="ros-expression-card" key={claim.id}><span className="ros-pill ros-pill--system">{claimKindLabels[claim.claim_kind] ?? claim.claim_kind}</span><strong>{claim.text}</strong><small>{claim.asserted_by} · 审核 {claim.reviewed_by}</small><small>{claim.source.locator ? `定位 ${JSON.stringify(claim.source.locator)}` : "原文定位未记录"} · 许可 {claim.source.permission_status}</small></article>) : <Empty text="当前没有已审核研报主张。" />}</div>
      <div><h4>关键因素 · {expression.factors.length}</h4>{expression.factors.length ? expression.factors.map((factor) => <article className="ros-expression-card" key={factor.id}><strong>{factor.name}</strong><small>{factor.metric_name} · {factor.expected_direction === "positive" ? "预期向上" : factor.expected_direction === "negative" ? "预期向下" : "方向中性"}</small><small>支持：{factor.support_condition}；反证：{factor.refutation_condition}</small><b className={`ros-expression-status is-${factor.verification?.outcome ?? "not_due"}`}>{factor.verification ? verificationLabels[factor.verification.outcome] ?? factor.verification.outcome : "尚未形成审核验证"}</b></article>) : <Empty text="当前没有可进入正式验证的关键因素。" />}</div>
    </div></section>
    <div className="ros-market-workbench">
      <section className="ros-market-chain"><p className="ros-eyebrow">公司与股票</p><h3>基本面影响</h3>{expression.fundamentals.length ? expression.fundamentals.map((impact) => <article className="ros-expression-card" key={impact.id}><strong>{impact.company_name}{impact.stock_code ? ` · ${impact.stock_code}` : ""}</strong><small>{impact.metric_name} · {impact.expected_direction === "positive" ? "预期向上" : impact.expected_direction === "negative" ? "预期向下" : "方向中性"}</small><p>{impact.rationale}</p><small>审核 {impact.reviewed_by} · {impact.source.locator ? `定位 ${JSON.stringify(impact.source.locator)}` : "原文定位未记录"}</small></article>) : <Empty text="当前没有已审核的公司/股票基本面传导关系。" />}</section>
      <section className="ros-market-chain"><p className="ros-eyebrow">股票市场</p><h3>事件窗口观测</h3>{expression.market_observations.length ? expression.market_observations.map((observation) => <article className="ros-expression-card" key={observation.id}><strong>{observation.stock_name} · {observation.window_label}</strong><small>事件 {new Date(observation.event_at).toLocaleString("zh-CN")} · 可得 {new Date(observation.available_at).toLocaleString("zh-CN")}</small><small>相对 {observation.benchmark} · 价格源 {observation.price_source}</small><b>{observation.relative_return === null ? "相对表现未记录" : `相对表现 ${(observation.relative_return * 100).toFixed(2)}%`}</b><p className="ros-note">这是市场观测，不自动表述为研报或因素造成。</p></article>) : <Empty text="当前没有已审核的市场观测。" />}</section>
      <aside className="ros-market-rail"><p className="ros-eyebrow">基金披露暴露</p><h3>历史披露，不表示实时仓位</h3>{expression.fund_exposure.length ? expression.fund_exposure.map((fund) => <article className="ros-fund-row" key={fund.fund_id}><strong>{fund.fund_name} <small>{fund.fund_code}</small></strong><span>已披露暴露 {(fund.disclosed_exposure * 100).toFixed(2)}%</span>{fund.positions.map((position) => <small key={position.stock_id}>{position.stock_name} · {(position.weight * 100).toFixed(2)}%<br />报告期 {position.report_period} · 披露 {new Date(position.published_at).toLocaleDateString("zh-CN")} · 采集 {new Date(position.acquired_at).toLocaleDateString("zh-CN")}<br />来源 {position.source} · 覆盖 {position.coverage_status} · 时效 {position.freshness_status}</small>)}</article>) : <Empty text="当前没有与已审核公司传导关系相连的历史基金披露。" />}</aside>
    </div>
  </section>;
}

function Empty({ text }: { text: string }) { return <p className="ros-empty ros-empty--compact">{text}</p>; }
