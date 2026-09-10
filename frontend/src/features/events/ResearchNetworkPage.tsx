import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ResearchNetwork } from "../../app/researchOsApi";

const relationLabels: Record<ResearchNetwork["reviewed_relations"][number]["relation_type"], string> = {
  shared_driver: "共享驱动",
  follow_up_validation: "后续验证",
  potential_conflict: "可能冲突",
  shared_material: "共享资料",
};

export function ResearchNetworkPage() {
  const [network, setNetwork] = useState<ResearchNetwork | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { researchOsApi.network().then(setNetwork).catch(() => setError("无法读取跨 Case 关联；系统不会以推测关系替代真实记录。")); }, []);
  return <main className="ros-page ros-network"><header className="ros-page-head"><div><p className="ros-eyebrow">研究资产 · Research Network</p><h1>跨 Case 研究网络</h1><p>只用于发现可继续下钻的相关研究。关联不继承证据、结论或审核状态。</p></div><Link className="ros-button ros-button--primary" to="/events/new">＋ 从事件开始</Link></header>{error && <p className="ros-error" role="alert">{error}</p>}{!network ? !error && <NetworkSkeleton /> : <><section className="ros-network-note"><strong>关联边界</strong><span>共享资料进入目标 Case 后，仍须重新确认来源权限、研究范围、证据角色与人工审核。</span></section><RelationLane title="已审核关联" relations={network.reviewed_relations} reviewed /><RelationLane title="AI 候选" relations={network.candidate_relations} /><RelationLane title="已处理候选" relations={network.resolved_candidates ?? []} resolved /></>}</main>;
}

function NetworkSkeleton() {
  return <section className="ros-network-skeleton" aria-label="跨 Case 关联加载中" aria-busy="true">
    {[0, 1].map((item) => <article data-testid="network-relation-skeleton" key={item}><header><i /><b /></header><span /><em /><footer><small /><small /></footer></article>)}
  </section>;
}

function RelationLane({ title, relations, reviewed = false, resolved = false }: { title: string; relations: ResearchNetwork["reviewed_relations"]; reviewed?: boolean; resolved?: boolean }) { return <section className="ros-network-lane"><header><div><p className="ros-eyebrow">{reviewed ? "正式发现入口" : resolved ? "保留审核历史" : "仅供人工核对"}</p><h2>{title} · {relations.length}</h2></div>{!reviewed && !resolved && <span className="ros-pill ros-pill--human">AI 候选，未经人工复核</span>}</header>{relations.length === 0 ? <div className="ros-empty ros-empty--compact">当前没有{title}。</div> : <div className="ros-network-relations">{relations.map((relation) => <article className={`ros-network-relation${reviewed || resolved ? "" : " is-candidate"}`} key={relation.id}><div className="ros-network-path"><Link to={`/events/${relation.source_case.case_id}`}>{relation.source_case.title}</Link><span>{relationLabels[relation.relation_type]}</span><i aria-hidden>→</i><Link to={`/events/${relation.target_case.case_id}`}>{relation.target_case.title}</Link></div><p>{relation.reason}</p><footer><span>{relation.created_by} · {new Date(relation.created_at).toLocaleString("zh-CN")}</span><span>{reviewed ? "已审核关联" : resolved ? "已处理候选，可回放审核历史" : "AI 候选，未经人工复核"}</span></footer>{!reviewed && !resolved && <Link className="ros-button ros-button--secondary" to={`/events/${relation.source_case.case_id}/relations`}>审核此关联候选</Link>}</article>)}</div>}</section>; }
