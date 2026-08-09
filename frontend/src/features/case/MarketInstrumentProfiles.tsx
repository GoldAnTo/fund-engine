import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type MarketExpression } from "../../app/researchOsApi";

const freshnessLabels: Record<string, string> = {
  historical_disclosure: "报告期仍在有效期",
  stale_disclosure: "披露已过期",
  coverage_incomplete: "覆盖不足",
  source_unlinked: "来源受限",
};

function useMarketExpression(caseId: string) {
  const [expression, setExpression] = useState<MarketExpression | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setExpression(null);
    setError(null);
    researchOsApi
      .marketExpression(caseId)
      .then((next) => active && setExpression(next))
      .catch(
        () =>
          active &&
          setError(
            "无法读取当前 Case 的市场表达；不会以其他 Case 或行情数据替代。",
          ),
      );
    return () => {
      active = false;
    };
  }, [caseId]);

  return { expression, error };
}

export function MarketStockProfile({
  caseId,
  stockId,
}: {
  caseId: string;
  stockId: string;
}) {
  const { expression, error } = useMarketExpression(caseId);
  if (error) return <p className="ros-error">{error}</p>;
  if (!expression)
    return <div className="ros-empty">正在读取当前 Case 的股票研究档案…</div>;

  const impacts = expression.fundamentals.filter(
    (item) => item.stock_id === stockId,
  );
  const observations = expression.market_observations.filter(
    (item) => item.stock_id === stockId,
  );
  const disclosures = expression.fund_exposure.flatMap((fund) =>
    fund.positions
      .filter((position) => position.stock_id === stockId)
      .map((position) => ({ fund, position })),
  );
  const identity = impacts[0] ?? observations[0] ?? disclosures[0]?.position;

  if (!identity) {
    return <InstrumentMissing caseId={caseId} label="这只股票" />;
  }

  const stockName =
    "stock_name" in identity ? identity.stock_name : "未命名股票";
  const stockCode = "stock_code" in identity ? identity.stock_code : "未记录";
  return (
    <section className="ros-instrument-profile">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">当前 Case · 单只股票</p>
          <h2>{stockName} · 股票研究档案</h2>
          <p>
            只汇总当前 Case
            已审核的基本面传导、市场观测和基金历史披露；不构成交易建议，也不会把相关性写成因果。
          </p>
        </div>
        <Link
          className="ros-button ros-button--secondary"
          to={`/events/${caseId}/market`}
        >
          返回市场与表达
        </Link>
      </header>
      <div className="ros-instrument-profile__identity">
        <strong>{stockCode}</strong>
        <span>证据截止 {expression.cutoff}</span>
        <span>观察日期 {expression.as_of}</span>
      </div>
      <section className="ros-instrument-profile__section">
        <h3>基本面传导</h3>
        {impacts.length ? (
          impacts.map((impact) => (
            <article className="ros-expression-card" key={impact.id}>
              <strong>
                {impact.company_name} · {impact.metric_name}
              </strong>
              <p>{impact.rationale}</p>
              <small>
                审核 {impact.reviewed_by} · {impact.review_reason}
              </small>
              <SourceLink
                caseId={caseId}
                documentId={impact.source.document_version_id}
              />
            </article>
          ))
        ) : (
          <Empty text="当前 Case 尚未为这只股票登记已审核基本面传导。" />
        )}
      </section>
      <section className="ros-instrument-profile__section">
        <h3>市场观测</h3>
        {observations.length ? (
          observations.map((observation) => (
            <article className="ros-expression-card" key={observation.id}>
              <strong>
                {observation.window_label} · 相对 {observation.benchmark}
              </strong>
              <p>
                事件时间{" "}
                {new Date(observation.event_at).toLocaleString("zh-CN")} ·
                可得时间{" "}
                {new Date(observation.available_at).toLocaleString("zh-CN")}
              </p>
              <small>
                价格来源 {observation.price_source} · 盘后处理{" "}
                {observation.after_hours_treatment} · 相对表现{" "}
                {observation.relative_return === null
                  ? "未记录"
                  : `${(observation.relative_return * 100).toFixed(2)}%`}
              </small>
              <p className="ros-note">
                这是市场观测，不自动表述为研报、事件或因素导致。
              </p>
            </article>
          ))
        ) : (
          <Empty text="当前 Case 尚未为这只股票登记市场观测。" />
        )}
      </section>
      <section className="ros-instrument-profile__section">
        <h3>基金历史披露</h3>
        {disclosures.length ? (
          disclosures.map(({ fund, position }) => (
            <article
              className="ros-expression-card"
              key={`${fund.fund_id}-${position.stock_id}`}
            >
              <strong>
                <Link to={`/events/${caseId}/funds/${fund.fund_id}`}>
                  {fund.fund_name}
                </Link>{" "}
                · {fund.fund_code}
              </strong>
              <p>
                披露仓位 {(position.weight * 100).toFixed(2)}% · 报告期{" "}
                {position.report_period}
              </p>
              <small>
                披露{" "}
                {new Date(position.published_at).toLocaleDateString("zh-CN")} ·
                采集{" "}
                {new Date(position.acquired_at).toLocaleDateString("zh-CN")} ·{" "}
                {freshnessLabels[position.freshness_status] ?? "状态未记录"}
              </small>
              <SourceLink
                caseId={caseId}
                documentId={
                  position.source_visible_in_case
                    ? position.source_document_version_id
                    : null
                }
              />
            </article>
          ))
        ) : (
          <Empty text="没有可用的当前 Case 基金历史披露；这不表示实时持仓为零。" />
        )}
      </section>
    </section>
  );
}

export function MarketFundProfile({
  caseId,
  fundId,
}: {
  caseId: string;
  fundId: string;
}) {
  const { expression, error } = useMarketExpression(caseId);
  if (error) return <p className="ros-error">{error}</p>;
  if (!expression)
    return <div className="ros-empty">正在读取当前 Case 的基金披露档案…</div>;

  const fund = expression.fund_exposure.find((item) => item.fund_id === fundId);
  if (!fund) return <InstrumentMissing caseId={caseId} label="这只基金" />;

  return (
    <section className="ros-instrument-profile">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">当前 Case · 单只基金</p>
          <h2>{fund.fund_name} · 基金披露档案</h2>
          <p>
            展示的是命中当前 Case
            已审核股票传导的历史披露，不代表实时仓位、基金推荐或完整组合。
          </p>
        </div>
        <Link
          className="ros-button ros-button--secondary"
          to={`/events/${caseId}/market`}
        >
          返回市场与表达
        </Link>
      </header>
      <div className="ros-instrument-profile__identity">
        <strong>{fund.fund_code}</strong>
        <span>
          {fund.disclosed_exposure === null
            ? "当前口径不能汇总精确暴露"
            : `相关已披露仓位 ${(fund.disclosed_exposure * 100).toFixed(2)}%`}
        </span>
      </div>
      <section className="ros-instrument-profile__section">
        <h3>命中股票与披露来源</h3>
        {fund.positions.map((position) => (
          <article className="ros-expression-card" key={position.stock_id}>
            <strong>
              <Link to={`/events/${caseId}/stocks/${position.stock_id}`}>
                {position.stock_name}
              </Link>{" "}
              · {position.stock_code}
            </strong>
            <p>
              披露仓位 {(position.weight * 100).toFixed(2)}% · 报告期{" "}
              {position.report_period}
            </p>
            <small>
              披露 {new Date(position.published_at).toLocaleDateString("zh-CN")}{" "}
              · 采集{" "}
              {new Date(position.acquired_at).toLocaleDateString("zh-CN")} ·
              覆盖 {position.coverage_status} ·{" "}
              {freshnessLabels[position.freshness_status] ?? "状态未记录"}
            </small>
            <SourceLink
              caseId={caseId}
              documentId={
                position.source_visible_in_case
                  ? position.source_document_version_id
                  : null
              }
            />
          </article>
        ))}
      </section>
    </section>
  );
}

function SourceLink({
  caseId,
  documentId,
}: {
  caseId: string;
  documentId: string | null;
}) {
  return documentId ? (
    <p>
      <Link
        className="ros-source-link"
        to={`/events/${caseId}/documents?document=${documentId}`}
      >
        定位到冻结来源
      </Link>
    </p>
  ) : (
    <p className="ros-note">
      冻结来源未关联当前 Case 或无展示许可，不能提供原文跳转。
    </p>
  );
}

function InstrumentMissing({
  caseId,
  label,
}: {
  caseId: string;
  label: string;
}) {
  return (
    <div className="ros-empty">
      {label}未进入当前 Case 的已审核市场表达，不能用其他 Case 的关系替代。
      <p>
        <Link to={`/events/${caseId}/market`}>返回市场与表达</Link>
      </p>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="ros-empty ros-empty--compact">{text}</div>;
}
