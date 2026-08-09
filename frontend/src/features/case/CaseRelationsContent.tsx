import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ResearchNetwork } from "../../app/researchOsApi";

type Relation = ResearchNetwork["reviewed_relations"][number];
type RelationType = Relation["relation_type"];
type ReviewOutcome = "confirmed" | "modified" | "rejected" | "needs_more_evidence";

const relationLabels: Record<RelationType, string> = {
  shared_driver: "共享驱动",
  follow_up_validation: "后续验证",
  potential_conflict: "可能冲突",
  shared_material: "共享资料",
};

const reviewLabels: Record<ReviewOutcome, string> = {
  confirmed: "确认候选关系",
  modified: "修改后确认",
  rejected: "驳回候选关系",
  needs_more_evidence: "要求补充证据",
};

function reviewKey(candidateId: string): string {
  return globalThis.crypto?.randomUUID?.() ?? `case-relation-${candidateId}-${Date.now()}`;
}

export function CaseRelationsContent({ caseId }: { caseId: string }) {
  const [relations, setRelations] = useState<ResearchNetwork | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<Relation | null>(null);
  const [outcome, setOutcome] = useState<ReviewOutcome>("confirmed");
  const [relationType, setRelationType] = useState<RelationType>("potential_conflict");
  const [reason, setReason] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    return researchOsApi.caseRelations(caseId).then(setRelations).catch(() => {
      setError("无法读取当前 Case 的关联记录；系统不会把全局网络中的其他关系带入这里。");
    });
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  function beginReview(candidate: Relation) {
    setReviewing(candidate);
    setOutcome("confirmed");
    setRelationType(candidate.relation_type);
    setReason("");
    setIdempotencyKey(reviewKey(candidate.id));
    setSuccess(null);
  }

  async function submitReview(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!reviewing || !reason.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await researchOsApi.reviewCaseRelation(reviewing.id, {
        outcome,
        relation_type: relationType,
        reviewer: "human:researcher",
        reason: reason.trim(),
        idempotency_key: idempotencyKey,
      });
      await load();
      setSuccess(`已记录：${reviewLabels[outcome]}。候选原记录与审核理由均可回放。`);
      setReviewing(null);
    } catch {
      setError("审核记录未保存。请核对理由和关系类型后重试；本次操作会使用相同的幂等键。");
    } finally {
      setSaving(false);
    }
  }

  if (error && !relations) return <p className="ros-error ros-page-gap" role="alert">{error}</p>;
  if (!relations) return <div className="ros-empty ros-page-gap">正在读取当前 Case 的关联研究…</div>;
  return <section className="ros-network ros-case-relations"><header className="ros-section-heading"><div><p className="ros-eyebrow">关联研究 · 当前 Case</p><h2>只显示与这个 Case 直接相连的研究</h2><p>关联是继续核对的入口，不继承对方的证据、结论、审核状态或市场表达。</p></div><Link className="ros-button ros-button--secondary" to="/network">查看全局研究网络</Link></header><section className="ros-network-note"><strong>使用边界</strong><span>共享资料进入本 Case 前，仍要重新确认来源许可、适用范围、证据角色与人工审核。</span></section>{error && <p className="ros-error" role="alert">{error}</p>}{success && <p className="ros-success" role="status">{success}</p>}<RelationLane caseId={caseId} title="已审核关联" relations={relations.reviewed_relations} /><RelationLane caseId={caseId} title="AI 候选" relations={relations.candidate_relations} candidate onReview={beginReview} />{reviewing && <section className="ros-network-lane ros-relation-review" aria-label="审核关联候选"><header><div><p className="ros-eyebrow">人工审核 · 追加记录</p><h3>审核关联候选</h3></div><button className="ros-button ros-button--secondary" type="button" onClick={() => setReviewing(null)} disabled={saving}>取消</button></header><p>系统候选的是「{relationLabels[reviewing.relation_type]}」；不会直接修改或覆盖该候选。</p><form onSubmit={submitReview}><label>审核结果<select value={outcome} onChange={(event) => setOutcome(event.target.value as ReviewOutcome)}><option value="confirmed">确认候选关系</option><option value="modified">修改关系后确认</option><option value="rejected">驳回候选关系</option><option value="needs_more_evidence">要求补充证据</option></select></label><label>关系类型<select value={relationType} onChange={(event) => setRelationType(event.target.value as RelationType)} disabled={outcome === "confirmed"}><option value="shared_driver">共享驱动</option><option value="follow_up_validation">后续验证</option><option value="potential_conflict">可能冲突</option><option value="shared_material">共享资料</option></select></label><label>审核理由<textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明为何确认、修改、驳回，或需要补什么证据。" required /></label><div className="ros-form-actions"><button className="ros-button" type="submit" disabled={saving || !reason.trim()}>{saving ? "正在记录…" : "保存可回放的审核记录"}</button></div></form></section>}<RelationLane caseId={caseId} title="已处理候选" relations={relations.resolved_candidates ?? []} resolved /></section>;
}

function RelationLane({ caseId, title, relations, candidate = false, resolved = false, onReview }: { caseId: string; title: string; relations: Relation[]; candidate?: boolean; resolved?: boolean; onReview?: (relation: Relation) => void }) {
  return <section className="ros-network-lane"><header><div><p className="ros-eyebrow">{candidate ? "仅供人工核对" : resolved ? "保留审核历史" : "已审核关系"}</p><h3>{title} · {relations.length}</h3></div>{candidate && <span className="ros-pill ros-pill--human">AI 候选，未经人工复核</span>}</header>{relations.length ? <div className="ros-network-relations">{relations.map((relation) => { const other = relation.source_case.case_id === caseId ? relation.target_case : relation.source_case; const history = relation.review_history ?? []; const latestReview = history[history.length - 1]; return <article className={`ros-network-relation${candidate ? " is-candidate" : ""}`} key={relation.id}><div className="ros-network-path"><span>{relationLabels[relation.relation_type]}</span><i aria-hidden>→</i><Link to={`/events/${other.case_id}`}>{other.title}</Link></div><p>{relation.reason}</p>{relation.candidate_origin && <p className="ros-note">源候选：{relationLabels[relation.candidate_origin.relation_type]} · {relation.candidate_origin.reason}</p>}{latestReview && <p className="ros-note">最近审核：{reviewLabels[latestReview.outcome]} · {latestReview.reviewer} · {latestReview.reason}</p>}<footer><span>{relation.created_by} · {new Date(relation.created_at).toLocaleString("zh-CN")}</span><span>{candidate ? "不得自动进入本 Case" : resolved ? "已驳回，保留可回放记录" : "已审核关联"}</span></footer>{candidate && onReview && <button className="ros-button ros-button--secondary" type="button" onClick={() => onReview(relation)}>审核关联候选</button>}</article>; })}</div> : <div className="ros-empty ros-empty--compact">当前没有{title}。</div>}</section>;
}
