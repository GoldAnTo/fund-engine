import type { WorkbenchResponse } from "../types";

interface Props {
  data: WorkbenchResponse;
  selectedNodeId: string | null;
}

export function ExposurePanel({ data, selectedNodeId }: Props) {
  const valuations = data.stock_valuation_snapshots.filter(
    (s) => !selectedNodeId || s.stock_id === selectedNodeId
  );
  const disclosures = data.fund_holding_disclosures.filter(
    (d) =>
      !selectedNodeId ||
      d.stock_id === selectedNodeId ||
      d.fund_id === selectedNodeId
  );

  return (
    <section className="exposure-panel">
      <h3>估值与持仓</h3>
      {valuations.length > 0 && (
        <div className="valuations">
          <h4>估值快照</h4>
          <ul>
            {valuations.map((v, i) => (
              <li key={i}>
                {v.stock_name}（{v.stock_code}）{v.metric_name}={v.metric_value} @
                {v.as_of_date}
                <span className="def"> 口径：{v.definition}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {disclosures.length > 0 && (
        <div className="disclosures">
          <h4>基金持仓披露</h4>
          <ul>
            {disclosures.map((d) => (
              <li key={d.disclosure_id}>
                {d.fund_name} 持有 {d.stock_name}（{d.stock_code}）权重 {d.weight}
                <span className="dates">
                  报告期 {d.report_period}｜披露日 {d.published_at}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="disclaimer">
        披露持仓存在滞后；主题暴露不等于实时持仓；命题被支持不等于推荐买入。
      </p>
    </section>
  );
}
