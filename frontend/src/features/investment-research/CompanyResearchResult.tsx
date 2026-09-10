import { useState } from "react";

import type {
  CompanyResearchFrozenRevision,
  CompanyResearchMemoNarrative,
  CompanyResearchRun,
  CompanyResearchWorkspace,
} from "../../data/investmentResearchApi";
import { answerabilityView, artifactByKind } from "./companyResearchView";

type EvidenceFact = Extract<CompanyResearchWorkspace["artifacts"][number], { kind: "evidence_index" }>["payload"]["facts"][number];
type WorkspaceArtifact = CompanyResearchWorkspace["artifacts"][number];
type WorkspaceArtifactKind = WorkspaceArtifact["kind"];
type ResearchGap = Extract<WorkspaceArtifact, { kind: "research_gaps" }>["payload"]["gaps"][number];

const ANSWERABILITY_COPY = {
  preparing: "研究判断正在形成，已完成的内容会持续保留。",
  not_answerable: "当前证据不足，不能形成方向、置信度或伪精确价值结论。",
  partially_answerable: "当前判断为暂定结论，关键缺口关闭后再确认。",
  answerable: "当前证据支持形成研究判断，仍需持续核验最强反证。",
} as const;

function observationText(observation: { value: string; unit: string; currency: string; period: string }): string {
  return `${observation.value} ${observation.unit}${observation.currency === "N/A" ? "" : ` ${observation.currency}`} · ${observation.period}`;
}

function SourceDisclosure({ fact }: { fact: EvidenceFact }) {
  const [expanded, setExpanded] = useState(false);
  return <li>
    <div className="ir-source-row">
      <div><strong>{fact.fact_key}</strong><span>{observationText(fact.observation)}</span></div>
      <button aria-controls={`result-source-${fact.fact_key}`} aria-expanded={expanded} className="ir-button" onClick={() => setExpanded((value) => !value)} type="button">查看来源 {fact.fact_key}</button>
    </div>
    {expanded ? <div className="ir-source-detail" id={`result-source-${fact.fact_key}`}>
      <p>{fact.source_role} · {fact.source_locator}</p>
      <p>可得时间：<time dateTime={fact.available_at}>{fact.available_at}</time></p>
      <a href={fact.source_url} rel="noreferrer" target="_blank">打开授权来源</a>
    </div> : null}
  </li>;
}

function gapIdentity(gap: ResearchGap): string {
  return "gap_key" in gap ? gap.gap_key : gap.code;
}

function gapDescription(gap: ResearchGap): string {
  return "gap_key" in gap ? gap.reason : `${gap.severity} · ${gap.message}`;
}

function ClaimSources({ claim, label, onOpenArtifact, workspace }: {
  claim: CompanyResearchMemoNarrative["summary"];
  label: string;
  onOpenArtifact: (kind: WorkspaceArtifactKind) => void;
  workspace: CompanyResearchWorkspace;
}) {
  if (claim.citations.length === 0) return null;
  const evidence = artifactByKind(workspace, "evidence_index");
  const gaps = artifactByKind(workspace, "research_gaps")?.payload.gaps ?? [];
  return <details aria-label={`${label}来源`} className="ir-claim-sources">
    <summary>查看{label}来源（{claim.citations.length}）</summary>
    <ul>{claim.citations.map((citation) => {
      const artifactMatch = /^artifact:([a-z_]+):([0-9a-f]{64})$/.exec(citation);
      if (artifactMatch) {
        const artifact = workspace.artifacts.find((item) => item.kind === artifactMatch[1] && item.content_hash === artifactMatch[2]);
        return <li key={citation}>{artifact
          ? <button className="ir-button" onClick={() => onOpenArtifact(artifact.kind)} type="button">打开研究依据 {artifact.kind} v{artifact.version}</button>
          : <span>引用不可用：{citation}</span>}</li>;
      }
      const fact = evidence?.payload.facts.find((item) => item.fact_key === citation);
      if (fact) return <li key={citation}>
        <a href={fact.source_url} rel="noreferrer" target="_blank">{fact.fact_key} · {fact.source_locator}</a>
      </li>;
      const gap = gaps.find((item) => gapIdentity(item) === citation);
      return <li key={citation}>{gap
        ? <span>{gapIdentity(gap)} · {gapDescription(gap)}</span>
        : <span>引用不可用：{citation}</span>}</li>;
    })}</ul>
  </details>;
}

export default function CompanyResearchResult({ onOpenArtifact, run, verifiedRevision }: {
  onOpenArtifact: (kind: WorkspaceArtifactKind) => void;
  run: CompanyResearchRun;
  verifiedRevision: CompanyResearchFrozenRevision | null;
}) {
  const workspace = run.workspace;
  const memo = workspace.artifacts.find((artifact) => artifact.kind === "memo");
  const narrative = memo?.payload.narrative;
  const answerability = answerabilityView(workspace);
  const business = artifactByKind(workspace, "business_map");
  const allDrivers = artifactByKind(workspace, "driver_map")?.payload.drivers ?? [];
  const drivers = narrative
    ? narrative.driver_explanations.flatMap((claim) => {
      const driver = allDrivers.find((item) => item.driver_key === claim.driver_key);
      return driver ? [driver] : [];
    })
    : allDrivers.slice(0, 3);
  const valuation = artifactByKind(workspace, "valuation_set");
  const scenarios = artifactByKind(workspace, "scenario_set");
  const judgment = artifactByKind(workspace, "judgment_context");
  const gaps = artifactByKind(workspace, "research_gaps");
  const evidence = artifactByKind(workspace, "evidence_index");
  const scenarioValues = new Map(valuation?.payload.scenario_dcf_values.map((item) => [item.scenario_id, item.enterprise_value]));
  const narrativeDrivers = new Map(narrative?.driver_explanations.map((item) => [item.driver_key, item]));
  const narrativeCounterevidence = narrative?.counterevidence ?? [];
  const narrativeGaps = narrative?.gaps ?? [];
  const narrativeNextChecks = narrative?.next_checks ?? [];

  return <section className="ir-result" aria-labelledby="company-research-result-title">
    <header className="ir-result__lead">
      <div><p className="ir-eyebrow">{verifiedRevision ? "Verified frozen revision" : "Authenticated draft"}</p><h2 id="company-research-result-title">公司研究结果</h2></div>
      <span>{verifiedRevision ? `冻结版本 ${verifiedRevision.sequence}` : memo?.payload.candidate_status === "human_confirmed" ? "人工确认备忘录" : "AI 初稿，未经人工复核"}</span>
    </header>
    <section className="ir-result__judgment">
      <p className="ir-eyebrow">Answerability</p><h3>当前判断</h3>
      <strong>可回答性：{answerability.label}</strong>
      <p>{narrative?.summary.text ?? ANSWERABILITY_COPY[answerability.status]}</p>
      {narrative ? <ClaimSources claim={narrative.summary} label="当前判断" onOpenArtifact={onOpenArtifact} workspace={workspace} /> : null}
    </section>
    <section className="ir-result__business">
      <h3>公司如何赚钱</h3>
      {narrative?.business_explanation.text ? <><p>{narrative.business_explanation.text}</p><ClaimSources claim={narrative.business_explanation} label="公司经营解释" onOpenArtifact={onOpenArtifact} workspace={workspace} /></> : business && business.payload.modules.length > 0
        ? <ul>{business.payload.modules.map((module) => <li key={module.module_key}><strong>{module.module_key}</strong><span>{module.revenue_sources.join("、") || "收入来源待补充"}</span><small>成本：{module.cost_structure.join("、") || "待补充"}；资本需求：{module.capital_needs.join("、") || "待补充"}</small></li>)}</ul>
        : <p>经营结构仍在分析，当前不推断未建立的收入来源。</p>}
    </section>
    <section className="ir-result__drivers">
      <h3>三个最重要的经营驱动</h3>
      {drivers.length > 0 ? <ol>{drivers.map((driver, index) => <li key={driver.driver_key}>
        <span>{index + 1}</span><div><strong>{driver.driver_key}</strong><p>{narrativeDrivers.get(driver.driver_key)?.text ?? driver.assumption_rationale ?? driver.equation}</p>
          {narrativeDrivers.get(driver.driver_key) ? <ClaimSources claim={narrativeDrivers.get(driver.driver_key)!} label={`${driver.driver_key} 驱动`} onOpenArtifact={onOpenArtifact} workspace={workspace} /> : null}
          <small>影响 {driver.output_metric}</small></div>
      </li>)}</ol> : <p>关键经营驱动尚未建立。</p>}
    </section>
    <section className="ir-result__scenarios">
      <h3>情景与价值范围</h3>
      <div className="ir-scenario-strip">
        {(["base", "bull", "bear"] as const).map((scenarioId) => {
          const scenario = scenarios?.payload.scenarios.find((item) => item.scenario_id === scenarioId);
          const value = scenarioValues.get(scenarioId);
          return <article key={scenarioId} data-scenario={scenarioId}><strong>{scenarioId === "base" ? "Base" : scenarioId === "bull" ? "Bull" : "Bear"}</strong><p>{scenario?.mechanism_id ?? "情景机制待建立"}</p>
            {scenario && scenario.driver_overrides.length > 0 ? <ul className="ir-scenario-assumptions">{scenario.driver_overrides.map((override) => <li key={override.driver_key}><b>{override.driver_key}</b><span>{observationText(override.observation)}</span><small>{override.rationale ?? override.equation}</small></li>)}</ul> : <small>情景驱动变化尚未建立</small>}
            <small>DCF：{value ? observationText(value) : "价值尚不可回答"}</small></article>;
        })}
      </div>
      {valuation && valuation.payload.security_value_ranges.length > 0 ? <ul className="ir-value-ranges">{valuation.payload.security_value_ranges.map((range) => <li key={range.security_external_key}><strong>{range.security_external_key}</strong><span>{observationText(range.value_per_share.minimum)} 至 {observationText(range.value_per_share.maximum)} {range.value_currency}</span></li>)}</ul> : <p>价值区间未建立；不会用默认值填补未知输入。</p>}
    </section>
    <section className="ir-result__counterevidence"><h3>最强反证</h3>{narrativeCounterevidence.length > 0 ? <ul>{narrativeCounterevidence.map((claim) => <li key={claim.text}><p>{claim.text}</p><ClaimSources claim={claim} label="最强反证" onOpenArtifact={onOpenArtifact} workspace={workspace} /></li>)}</ul> : judgment && judgment.payload.strongest_counterevidence.length > 0 ? <ul>{judgment.payload.strongest_counterevidence.map((item) => <li key={item.fact_key}><a href={item.source_url} rel="noreferrer" target="_blank">{item.fact_key} · {item.source_locator}</a></li>)}</ul> : <p>最强反证尚未建立。</p>}</section>
    <section className="ir-result__gaps"><h3>关键缺口</h3>{narrativeGaps.length > 0 ? <ul>{narrativeGaps.map((claim) => <li key={claim.text}><p>{claim.text}</p><ClaimSources claim={claim} label="关键缺口" onOpenArtifact={onOpenArtifact} workspace={workspace} /></li>)}</ul> : gaps && gaps.payload.gaps.length > 0 ? <ul>{gaps.payload.gaps.map((item) => <li key={gapIdentity(item)}>{gapIdentity(item)}：{gapDescription(item)}</li>)}</ul> : <p>当前没有已认证的关键缺口。</p>}</section>
    <section className="ir-result__next"><h3>下一验证事件</h3>{narrativeNextChecks.length > 0 ? <ul>{narrativeNextChecks.map((claim) => <li key={claim.text}><p>{claim.text}</p><ClaimSources claim={claim} label="下一验证事件" onOpenArtifact={onOpenArtifact} workspace={workspace} /></li>)}</ul> : judgment && judgment.payload.next_verification_events.length > 0 ? <ul>{judgment.payload.next_verification_events.map((item) => <li key={item}>{item}</li>)}</ul> : <p>下一验证事件尚未建立。</p>}</section>
    <section className="ir-result__sources"><h3>来源</h3>{evidence && evidence.payload.facts.length > 0 ? <ul>{evidence.payload.facts.map((fact) => <SourceDisclosure fact={fact} key={fact.fact_key} />)}</ul> : <p>来源索引仍在建立。</p>}</section>
    <section className="ir-result__versions"><h3>版本</h3><dl><div><dt>草稿</dt><dd>v{workspace.draft.lock_version}</dd></div><div><dt>冻结版本</dt><dd>{verifiedRevision?.id ?? "尚未冻结"}</dd></div><div><dt>制品</dt><dd>{workspace.artifacts.length} 项</dd></div></dl><details><summary>查看制品版本</summary><ul>{workspace.artifacts.map((artifact) => <li key={artifact.kind}>{artifact.kind} v{artifact.version}</li>)}</ul></details></section>
  </section>;
}
