import type { ReactNode } from "react";
import type { GatewayAssessmentReview as Review, GatewayEvidenceSummary, GatewayQualityFlag } from "@/gateway/researchContent";
import { EvidenceMap } from "./EvidenceMap";

const verdicts = { supported: "原 AI 判断：支持", contradicted: "原 AI 判断：反驳", insufficient_evidence: "原 AI 判断：证据不足" };
const qualityLabels: Record<GatewayQualityFlag, string> = {
  mixed_data_periods: "期间混杂", unknown_data_period: "数据期待核对", user_material_only: "仅用户材料",
  no_primary_disclosure: "缺原始披露", source_independence_unverified: "来源独立性待核验", retrieval_direction_unverified: "检索关系待核验",
};
const qualityCopy: Record<GatewayQualityFlag, string> = {
  mixed_data_periods: "数据期不一致，不能直接当作同一期数据比较。",
  unknown_data_period: "部分证据缺少数据期，需核对对应期间。",
  user_material_only: "仅有用户材料，尚未独立核验。",
  no_primary_disclosure: "所用证据中没有官方原始披露，建议补充并核对。",
  source_independence_unverified: "来源独立性未核验，多份材料不等于多个独立来源。",
  retrieval_direction_unverified: "支持、反证是检索方向，不代表已确认的证据关系。",
};
type Props = { review: Review | null; evidence: GatewayEvidenceSummary[]; selectedEvidenceId?: string; onOpen: (entry: GatewayEvidenceSummary, context?: { label: string; ids: string[] }) => void; children: ReactNode };

/** Reads existing assessments; never derives a new verdict from their prose. */
export function GatewayAssessmentReview({ review, evidence, selectedEvidenceId, onOpen, children }: Props) {
  if (!review || review.state === "unavailable") return <>
    <p className="research-assessment-unavailable" role="status">{review?.reasonCode === "evidence_list_truncated"
      ? "证据列表已截断，暂不展示不完整的结论关联。原始报告保留如下。"
      : "暂无法建立完整的结论与证据关联。以下保留原始报告，不代表质量检查已通过。"}</p>
    {children}
  </>;
  const byId = new Map(evidence.map((entry) => [entry.evidenceLinkId, entry]));
  const counts = { supported: 0, contradicted: 0, insufficient_evidence: 0 };
  for (const item of review.items) counts[item.conclusion] += 1;
  return <>
    <section className="research-assessment-summary" aria-label="结果摘要">
      <h3>结果摘要</h3>
      <p>本轮保存了 {review.items.length} 项 AI 判断：支持判断 {counts.supported} 项，反驳判断 {counts.contradicted} 项，证据不足 {counts.insufficient_evidence} 项。</p>
      {review.qualityFlags.includes("mixed_data_periods") ? <p className="research-assessment-caution">本轮材料涉及不同数据期，请先核对研究期间与比较口径。</p> : null}
      <details className="research-assessment-checks"><summary>报告级质量提示（{review.qualityFlags.length} 项）</summary><ul>{review.qualityFlags.map((flag) => <li key={flag}>{qualityCopy[flag]}</li>)}</ul></details>
    </section>
    <div className="research-assessment-items">
      {review.items.map((item, index) => {
        const itemOnlyFlags = item.qualityFlags.filter((flag) => !review.qualityFlags.includes(flag));
        const inputs = item.evidenceLinkIds.map((input) => byId.get(input)).filter((entry): entry is GatewayEvidenceSummary => !!entry);
        return <section key={item.assessmentId} className="research-assessment-item" aria-label={`分项判断：${item.thesisStatement}`}>
        <header><span className="research-assessment-number">{String(index + 1).padStart(2, "0")}</span><h3>{item.thesisStatement}</h3></header>
        <p className="research-assessment-verdict">{verdicts[item.conclusion]}</p>
        {item.qualityFlags.length ? <div className="research-quality-labels" aria-label="本项适用质量提示">{item.qualityFlags.map((flag) => <span key={flag}>{qualityLabels[flag]}</span>)}</div> : null}
        <h4>还缺什么（原 AI 记录）</h4>
        {item.gaps.length ? <><ul>{item.gaps.slice(0, 1).map((gap, n) => <li key={n}>{gap}</li>)}</ul>
          {item.gaps.length > 1 ? <details><summary>展开其余 {item.gaps.length - 1} 项原始缺口</summary><ul>{item.gaps.slice(1).map((gap, n) => <li key={n}>{gap}</li>)}</ul></details> : null}</>
          : <p className="research-evidence-total">原判断未记录缺口，不表示没有缺口。</p>}
        <EvidenceMap evidence={inputs} selectedEvidenceId={selectedEvidenceId}
          onOpen={(entry) => onOpen(entry, { label: item.thesisStatement, ids: item.evidenceLinkIds })} />
        {inputs.length !== item.evidenceLinkIds.length ? <p role="status">部分评估输入当前不可展示，关系路径不完整。</p> : null}
        {itemOnlyFlags.length ? <details className="research-assessment-checks"><summary>本项独有质量提示（{itemOnlyFlags.length} 项）</summary>
          <ul>{itemOnlyFlags.map((flag) => <li key={flag}>{qualityCopy[flag]}</li>)}</ul>
        </details> : null}
        <details className="research-assessment-rationale"><summary>查看原 AI 判断理由</summary><p>{item.rationale || "未提供判断理由。"}</p></details>
      </section>; })}
    </div>
    <details className="research-original-report"><summary>查看原始报告</summary>
      <p className="research-evidence-total">保留生成时的正文、发现和局限。上方质量检查是新增只读提示，未改写此报告。</p>
      {children}
    </details>
  </>;
}
