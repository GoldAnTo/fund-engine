import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { ReportResearchScope, ReportWikiFactor, ReportWikiGraph, ReportWikiNode } from "../../domain/eventResearch";
import { formatReportSourceLocator, reportNodeStatusLabel } from "../../domain/reportDisplay";

function classificationLabel(value: ReportWikiFactor["classification"]) {
  return value === "key" ? "关键因素" : value === "alternative" ? "替代解释" : "证据不足";
}

function NodeList({ nodes, empty, annotation }: { nodes: ReportWikiNode[]; empty: string; annotation?: (node: ReportWikiNode) => string | null }) {
  if (!nodes.length) return <p className="report-empty">{empty}</p>;
  return <ul className="report-node-list">{nodes.map((node) => {
    const source = formatReportSourceLocator(node.sourceLocator);
    return <li key={node.id}><div><strong>{node.label}</strong><small>{annotation?.(node) ?? reportNodeStatusLabel(node.status)}</small></div>{source.href ? <a href={source.href} target="_blank" rel="noopener noreferrer">{source.label}</a> : <span className="report-locator">{source.label}</span>}</li>;
  })}</ul>;
}

function AssetMapping({ nodes }: { nodes: ReportWikiNode[] }) {
  if (!nodes.length) return <p className="report-empty">尚未形成可审计的 A 股或中国基金映射。未上市节点仅保留传导关系，不展示股票或基金数值。</p>;
  return <ul className="report-node-list">{nodes.map((node) => {
    const mapping = node.assetMapping;
    const details = mapping?.companyKind === "unlisted_transmission"
      ? "传导节点，不计算股票或基金暴露"
      : mapping?.companyKind === "listed_a_share"
        ? mapping.aShareCodes?.length ? `A 股映射：${mapping.aShareCodes.join("、")}` : "已映射 A 股公司，但代码证据不足"
        : mapping?.fundCoverage === "complete" && mapping.computable ? "基金覆盖完整，可计算基金暴露"
        : mapping?.fundCoverage === "partial" ? "基金覆盖不完整，不可计算基金暴露"
        : mapping?.fundCoverage === "stale" ? "基金覆盖已过期，不可计算基金暴露"
        : mapping?.fundCoverage === "insufficient" ? "基金覆盖证据不足，不可计算基金暴露"
        : "当前没有可计算的资产映射";
    return <li key={node.id}><div><strong>{node.label}</strong><small>{details}</small></div></li>;
  })}</ul>;
}

function lines(value: string[]): string {
  return value.join("\n");
}

function nonEmptyLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function scopeAudit(scope: ReportResearchScope): string {
  const createdAt = new Date(scope.createdAt);
  const time = Number.isNaN(createdAt.valueOf())
    ? scope.createdAt
    : createdAt.toLocaleString("zh-CN", { hour12: false });
  return `${scope.changedBy} · ${scope.changeSummary} · ${time}`;
}

function ScopeControls({
  caseId, graph, scopes, selectedVersion, onSelect, onSaved,
}: {
  caseId: string; graph: ReportWikiGraph; scopes: ReportResearchScope[]; selectedVersion: number;
  onSelect: (version: number) => void; onSaved: (version: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [question, setQuestion] = useState("");
  const [factorSelection, setFactorSelection] = useState("");
  const [evidencePlan, setEvidencePlan] = useState("");
  const [claimIds, setClaimIds] = useState<string[]>([]);
  const [relationIds, setRelationIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const claims = graph.nodes.filter((node) => node.kind === "report_claim");
  const relations = Array.from(new Map(graph.edges.filter((edge) => edge.relationId).map((edge) => [edge.relationId!, edge])).values());
  const begin = () => {
    setQuestion(graph.scope.researchQuestion);
    setFactorSelection(lines(graph.scope.factorSelection));
    setEvidencePlan(lines(graph.scope.evidencePlan));
    setClaimIds(graph.scope.selectedClaimIds);
    setRelationIds(graph.scope.selectedRelationIds);
    setError(null);
    setEditing(true);
  };
  const toggle = (id: string, values: string[], setValues: (next: string[]) => void) => {
    setValues(values.includes(id) ? values.filter((item) => item !== id) : [...values, id]);
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) { setError("研究问题不能为空。"); return; }
    if (!claimIds.length) { setError("至少选择一条研报主张，才能创建新的研究范围。"); return; }
    setSaving(true); setError(null);
    try {
      const created = await researchClient.appendReportResearchScope!({
        caseId, documentId: graph.documentId, researchQuestion: question.trim(),
        factorSelection: nonEmptyLines(factorSelection), evidencePlan: nonEmptyLines(evidencePlan),
        selectedClaimIds: claimIds, selectedRelationIds: relationIds,
      });
      setEditing(false);
      onSaved(created.version);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法创建新的研究范围，请重试。");
    } finally { setSaving(false); }
  };
  const currentScope = scopes.find((scope) => scope.version === selectedVersion) ?? graph.scope;
  return <section className="report-section report-scope-controls" aria-labelledby="report-scope-title">
    <header><div><p className="section-kicker">可重复研究</p><h2 id="report-scope-title">研究范围</h2></div><span>历史不会被覆盖</span></header>
    <p>当前正在查看范围 v{selectedVersion}</p>
    <p className="report-scope-audit">{scopeAudit(currentScope)}</p>
    <label>查看研究范围版本<select value={selectedVersion} onChange={(event) => onSelect(Number(event.target.value))}>{scopes.map((scope) => <option key={scope.version} value={scope.version}>范围 v{scope.version} · {scope.researchQuestion}</option>)}</select></label>
    <ul className="report-scope-history">{scopes.filter((scope) => scope.version !== selectedVersion).map((scope) => <li key={scope.version}><span>历史范围 v{scope.version} · {scope.researchQuestion}</span><small>{scopeAudit(scope)}</small></li>)}</ul>
    {!editing ? <button className="prototype-button" type="button" onClick={begin}>创建新的研究范围</button> : <form className="report-scope-form" onSubmit={(event) => void save(event)}>
      <p>保存会追加范围版本，不会修改当前或历史研究记录。</p>
      <label>研究问题<textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} /></label>
      <label>因素重点（每行一项）<textarea value={factorSelection} onChange={(event) => setFactorSelection(event.target.value)} rows={3} /></label>
      <label>证据计划（每行一项）<textarea value={evidencePlan} onChange={(event) => setEvidencePlan(event.target.value)} rows={3} /></label>
      <fieldset><legend>选择研报主张</legend>{claims.map((claim) => <label key={claim.id}><input type="checkbox" checked={claimIds.includes(claim.id.replace(/^report_claim:/, "")) || claimIds.includes(claim.id)} onChange={() => toggle(claim.id.replace(/^report_claim:/, ""), claimIds, setClaimIds)} />{claim.label}</label>)}</fieldset>
      <fieldset><legend>选择关系路径</legend>{relations.length ? relations.map((relation) => <label key={relation.relationId}><input type="checkbox" checked={relationIds.includes(relation.relationId!)} onChange={() => toggle(relation.relationId!, relationIds, setRelationIds)} />{relation.kind}</label>) : <p>当前主张尚未抽取出公司关系路径。</p>}</fieldset>
      {error ? <p role="alert">{error}</p> : null}
      <div><button className="prototype-button primary" type="submit" disabled={saving}>{saving ? "正在创建范围…" : "保存为新的研究范围"}</button><button className="prototype-button" type="button" onClick={() => setEditing(false)} disabled={saving}>取消</button></div>
    </form>}
  </section>;
}

export function ReportResearchScreen() {
  const { caseId = "" } = useParams();
  const [graph, setGraph] = useState<ReportWikiGraph | null>(null);
  const [scopes, setScopes] = useState<ReportResearchScope[]>([]);
  const [selectedScopeVersion, setSelectedScopeVersion] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (scopeVersion?: number) => {
    if (!caseId) return;
    setError(null);
    try {
      const [nextGraph, scopeList] = await Promise.all([
        researchClient.getReportWikiGraph!(caseId, scopeVersion ? { scopeVersion } : undefined),
        researchClient.listReportResearchScopes!(caseId),
      ]);
      setGraph(nextGraph);
      setScopes(scopeList.items);
      setSelectedScopeVersion(nextGraph.scopeVersion);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法加载研报研究。请稍后重试。"); }
  }, [caseId]);
  useEffect(() => { void load(); }, [load]);

  if (error) return <main className="prototype-screen report-research-screen"><section className="report-state report-state--error" role="alert"><h1>无法加载研报研究</h1><p>{error}</p><button className="prototype-button primary" type="button" onClick={() => void load()}>重新加载研究</button></section></main>;
  if (!graph) return <main className="prototype-screen report-research-screen"><section className="report-skeleton" aria-label="正在加载研报研究"><span /><span /><span /></section></main>;

  const factors = graph.factors;
  const keyFactors = factors.filter((factor) => factor.classification === "key");
  const currentFactor = keyFactors[0] ?? factors[0] ?? null;
  const claims = graph.nodes.filter((node) => node.kind === "report_claim");
  const evidence = graph.nodes.filter((node) => node.kind === "evidence");
  const markets = graph.nodes.filter((node) => node.kind === "market_window");
  const assets = graph.nodes.filter((node) => node.kind === "company" || node.kind === "fund");
  const evidenceRoles = (() => {
    const roles = new Map<string, string[]>();
    for (const edge of graph.edges) {
      if (edge.kind === "reported_by") roles.set(edge.targetId, [...(roles.get(edge.targetId) ?? []), "研报来源"]);
      if (edge.kind === "independent_evidence") roles.set(edge.targetId, [...(roles.get(edge.targetId) ?? []), "支持证据"]);
      if (edge.kind.startsWith("confounder:")) roles.set(edge.targetId, [...(roles.get(edge.targetId) ?? []), edge.status === "rejected" ? "反证 / 重大替代解释" : "混杂因素"]);
    }
    return roles;
  })();
  const confounders = graph.edges.filter((edge) => edge.kind.startsWith("confounder:")).map((edge) => ({ edge, node: graph.nodes.find((node) => node.id === edge.targetId) })).filter((item): item is { edge: ReportWikiGraph["edges"][number]; node: ReportWikiNode } => Boolean(item.node));
  const gap = currentFactor?.classification === "evidence_gap" ? currentFactor.explanation : null;

  return <main className="prototype-screen report-research-screen" data-layout="report-first">
    <header className="report-page-header">
      <div><p className="section-kicker">研报研究 · 范围 v{graph.scopeVersion}</p><h1>从报告观点到可验证影响</h1><p>{graph.scope.researchQuestion}</p></div>
      <Link className="prototype-button" to={`/reports/${caseId}/wiki`}>查看关系图谱</Link>
    </header>
    <section className="report-judgment" aria-labelledby="report-judgment-title">
      <div><p className="section-kicker">研究结论优先</p><h2 id="report-judgment-title">当前判断</h2><h3>{currentFactor ? classificationLabel(currentFactor.classification) : "暂不能下结论"}</h3><p>{currentFactor?.statement ?? "当前范围内尚未抽取到可核验的研报主张。"}</p></div>
      <div className="report-judgment__next"><strong>下一步</strong><p>核对报告主张的独立经营证据、市场窗口与混杂因素，再决定是否可以归为关键因素。</p></div>
    </section>
    {gap ? <section className="report-gap" aria-label="关键缺口"><strong>关键缺口</strong><span>{gap}</span></section> : null}
    <ScopeControls caseId={caseId} graph={graph} scopes={scopes.length ? scopes : [graph.scope]} selectedVersion={selectedScopeVersion ?? graph.scopeVersion} onSelect={(version) => void load(version)} onSaved={(version) => void load(version)} />
    <div className="report-workbench-grid">
      <section className="report-section report-section--claims" aria-labelledby="report-claims-title"><header><div><p className="section-kicker">来源到主张</p><h2 id="report-claims-title">研报主张</h2></div><span>{claims.length} 条</span></header><NodeList nodes={claims} empty="尚未解析出研报主张。原文已保留，等待解析或补充文本。" /></section>
      <aside className="report-section report-section--factor" aria-labelledby="report-factor-title"><p className="section-kicker">因素门槛</p><h2 id="report-factor-title">关键因素并非自动成立</h2>{factors.length ? <ul className="report-factor-list">{factors.map((factor) => <li key={`${factor.claimId}-${factor.relationId ?? "none"}`}><strong>{factor.statement}</strong><span className={`report-factor-label report-factor-label--${factor.classification}`}>{classificationLabel(factor.classification)}</span><p>{factor.explanation}</p></li>)}</ul> : <p className="report-empty">尚无可分类因素，不能生成归因。</p>}</aside>
    </div>
    <section className="report-section report-section--evidence" aria-labelledby="report-evidence-title"><header><div><p className="section-kicker">可核验材料</p><h2 id="report-evidence-title">证据与反证</h2></div><span>只显示当前范围内可定位材料</span></header><NodeList nodes={evidence} annotation={(node) => evidenceRoles.get(node.id)?.join(" · ") ?? reportNodeStatusLabel(node.status)} empty="证据不足：当前没有独立证据或反证进入该研究范围。" /></section>
    <section className="report-section report-section--market" aria-labelledby="report-market-title"><header><div><p className="section-kicker">发布后验证</p><h2 id="report-market-title">1D / 5D 市场窗口与混杂因素</h2></div><span>缺数据不补造</span></header><NodeList nodes={markets} empty="证据不足：未取得满足时点要求的 1D / 5D 市场观察。" /><div className="report-confounder"><strong>混杂因素</strong>{confounders.length ? <ul>{confounders.map(({ edge, node }) => <li key={edge.id}><span>{edge.status === "verified" ? "已排除" : edge.status === "rejected" ? "存在重大替代解释" : "仍待评估"}</span><b>{node.label}</b></li>)}</ul> : <p>仍待评估：当前范围未收集到可定位的混杂因素，不能将价格变化归因于本报告。</p>}</div></section>
    <section className="report-section report-section--assets" aria-labelledby="report-assets-title"><header><div><p className="section-kicker">资产映射</p><h2 id="report-assets-title">A 股与中国基金</h2></div><span>仅展示可审计映射</span></header><AssetMapping nodes={assets} /></section>
  </main>;
}
