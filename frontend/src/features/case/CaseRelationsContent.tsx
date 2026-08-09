import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ResearchNetwork } from "../../app/researchOsApi";

const relationLabels: Record<ResearchNetwork["reviewed_relations"][number]["relation_type"], string> = {
  shared_driver: "共享驱动",
  follow_up_validation: "后续验证",
  potential_conflict: "可能冲突",
  shared_material: "共享资料",
};

export function CaseRelationsContent({ caseId }: { caseId: string }) {
  const [relations, setRelations] = useState<ResearchNetwork | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { researchOsApi.caseRelations(caseId).then(setRelations).catch(() => setError("无法读取当前 Case 的关联记录；系统不会把全局网络中的其他关系带入这里。")); }, [caseId]);
  if (error) return <p className="ros-error ros-page-gap" role="alert">{error}</p>;
  if (!relations) return <div className="ros-empty ros-page-gap">正在读取当前 Case 的关联研究…</div>;
  return <section className="ros-network ros-case-relations"><header className="ros-section-heading"><div><p className="ros-eyebrow">关联研究 · 当前 Case</p><h2>只显示与这个 Case 直接相连的研究</h2><p>关联是继续核对的入口，不继承对方的证据、结论、审核状态或市场表达。</p></div><Link className="ros-button ros-button--secondary" to="/network">查看全局研究网络</Link></header><section className="ros-network-note"><strong>使用边界</strong><span>共享资料进入本 Case 前，仍要重新确认来源许可、适用范围、证据角色与人工审核。</span></section><RelationLane caseId={caseId} title="已审核关联" relations={relations.reviewed_relations} /><RelationLane caseId={caseId} title="AI 候选" relations={relations.candidate_relations} candidate /></section>;
}

function RelationLane({ caseId, title, relations, candidate = false }: { caseId: string; title: string; relations: ResearchNetwork["reviewed_relations"]; candidate?: boolean }) {
  return <section className="ros-network-lane"><header><div><p className="ros-eyebrow">{candidate ? "仅供人工核对" : "已审核关系"}</p><h3>{title} · {relations.length}</h3></div>{candidate && <span className="ros-pill ros-pill--human">AI 候选，未经人工复核</span>}</header>{relations.length ? <div className="ros-network-relations">{relations.map((relation) => { const other = relation.source_case.case_id === caseId ? relation.target_case : relation.source_case; return <article className={`ros-network-relation${candidate ? " is-candidate" : ""}`} key={relation.id}><div className="ros-network-path"><span>{relationLabels[relation.relation_type]}</span><i aria-hidden>→</i><Link to={`/events/${other.case_id}`}>{other.title}</Link></div><p>{relation.reason}</p><footer><span>{relation.created_by} · {new Date(relation.created_at).toLocaleString("zh-CN")}</span><span>{candidate ? "不得自动进入本 Case" : "已审核关联"}</span></footer></article>; })}</div> : <div className="ros-empty ros-empty--compact">当前没有{title}。</div>}</section>;
}
