import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { ReportResearchIntake } from "../../domain/eventResearch";

function stateLabel(state: ReportResearchIntake["state"]): string {
  if (state === "needs_supplement") return "等待补充正文";
  if (state === "artifact_extraction_unavailable") return "已保存恢复快照";
  return "等待候选陈述抽取";
}

export function ReportResearchIntakeScreen() {
  const { caseId } = useParams();
  const [intake, setIntake] = useState<ReportResearchIntake | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [snapshotVisible, setSnapshotVisible] = useState(false);
  const [reloadAttempt, setReloadAttempt] = useState(0);

  useEffect(() => {
    if (!caseId) return;
    let active = true;
    setIntake(null);
    setError(null);
    setSnapshotVisible(false);
    void researchClient.getReportResearchIntake!(caseId)
      .then((value) => { if (active) setIntake(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取资料接入状态。"); });
    return () => { active = false; };
  }, [caseId, reloadAttempt]);

  if (error) return <main className="prototype-screen report-intake-screen"><section className="report-state report-state--error" role="alert"><h1>无法读取资料接入状态</h1><p>{error}</p><div className="report-state__actions"><button type="button" className="prototype-button primary" onClick={() => setReloadAttempt((value) => value + 1)}>重新读取</button><Link className="prototype-button" to="/reports/new">接入新资料</Link></div></section></main>;
  if (!intake) return <main className="prototype-screen report-intake-screen"><section className="report-state" aria-busy="true"><h1>正在读取冻结资料</h1><p>正在核对 Case、原件和可执行的下一步。</p></section></main>;

  const { nextAction } = intake;
  const recoveryUrl = `/reports/new?${new URLSearchParams({ resume_case_id: intake.caseId, resume_document_id: intake.primaryDocument.id, resume_input_kind: intake.primaryDocument.inputKind, resume_reason: "needs_supplement" }).toString()}`;
  const primaryAction = nextAction.kind === "supplement_text"
    ? <Link className="prototype-button primary" to={recoveryUrl}>{nextAction.label}</Link>
    : nextAction.kind === "view_saved_snapshot"
      ? <button type="button" className="prototype-button primary" onClick={() => setSnapshotVisible((value) => !value)} aria-expanded={snapshotVisible}>{nextAction.label}</button>
      : <button type="button" className="prototype-button primary" disabled aria-describedby="candidate-adapter-note">{nextAction.label}</button>;

  return <main className="prototype-screen report-intake-screen">
    <header className="report-page-header"><div><p className="section-kicker">资料收件箱 · 下一步</p><h1>{intake.caseTitle}</h1><p>{intake.primaryDocument.title} 已被冻结为研究资料。此处只展示接入状态，不会自动进入 Wiki、正式 Case 工作台或市场影响研究。</p></div></header>
    <section className="report-intake-action" aria-labelledby="intake-action-heading">
      <div className="report-intake-action__status"><span>当前状态</span><strong>{stateLabel(intake.state)}</strong></div>
      <div className="report-intake-action__body"><p className="section-kicker">唯一下一步</p><h2 id="intake-action-heading">{nextAction.label}</h2><p>{intake.blockingReason ?? "资料已冻结，尚未进入可发布的正式研究阶段。"}</p><div className="report-intake-action__unlock"><strong>完成后解锁</strong><span>{nextAction.unlockMessage}</span></div>{primaryAction}
        {nextAction.kind === "extract_candidates" ? <p id="candidate-adapter-note" className="report-form-help">候选抽取适配器尚未接入，因此此操作暂不可执行。系统不会用虚构结果替代抽取或自动创建正式陈述。</p> : null}
      </div>
    </section>
    {snapshotVisible ? <section className="report-intake-snapshots" aria-live="polite"><h2>已保存的 Case 内快照</h2>{intake.supplementArtifacts.length ? <ul>{intake.supplementArtifacts.map((artifact) => <li key={artifact.id}><strong>恢复快照</strong><span>{artifact.claimedPageReference ? `研究员标注：${artifact.claimedPageReference}` : "未标注原件位置"}</span></li>)}</ul> : <p>此状态没有可展示的恢复快照元数据。</p>}</section> : null}
    <nav className="report-intake-secondary" aria-label="资料接入次要入口"><Link to="/library">查看来源库</Link></nav>
  </main>;
}
