import type { CompanyResearchWorkspace } from "../../data/investmentResearchApi";

type ResearchDraft = NonNullable<CompanyResearchWorkspace["research_draft"]>;
const PAGE_SECTIONS: Record<string, readonly string[]> = {
  overview: ["report_sections"], business: ["business_analysis"],
  forecast: ["operating_drivers", "candidate_assumptions"],
  valuation: ["counterevidence"], evidence: ["verification_questions"],
  versions: ["report_sections"],
};

export function CompanyResearchDraftPanel({ draft, page, saved }: { draft: ResearchDraft; page: string; saved: boolean }) {
  const sections = draft.sections.filter((section) => PAGE_SECTIONS[page]?.includes(section.key));
  const attempts = draft.usage?.attempts ?? [];
  const reportedTokens = attempts.reduce((total, attempt) => total + (attempt.total_tokens ?? 0), 0);
  const allReported = attempts.length > 0 && attempts.every((attempt) => attempt.usage_state === "reported");
  return <section className="ir-live-draft" aria-label="模型研究初稿">
    <header><p className="ir-eyebrow">{saved ? "模型生成的原始分析" : "模型生成，待确认"}</p><h3>{saved ? "已保存研究的分析依据" : "可阅读的研究初稿"}</h3>
      <p>分析依据截至 {new Date(draft.cutoff_at).toLocaleDateString("zh-CN")} 的资料。下方判断可先阅读；正式预测与估值仍取决于证据审核和模型所需数据。</p></header>
    {sections.map((section) => <div key={section.key}><h3>{section.label}</h3>{section.items.map((item, index) => <article className="ir-live-finding" key={`${section.key}-${index}`}>
      <h4>{item.title}</h4><p>{item.text}</p><details><summary>查看依据</summary>{item.citations.map((citation, citationIndex) => <figure key={`${citation.excerpt_id}-${citationIndex}`}><blockquote>{citation.quote}</blockquote><figcaption><a href={citation.source_url} rel="noreferrer" target="_blank">原始资料 · {citation.locator}</a></figcaption></figure>)}</details>
    </article>)}</div>)}
    <details className="ir-live-receipt"><summary>资料与模型调用记录</summary><dl><div><dt>模型</dt><dd>{draft.model_version}</dd></div><div><dt>生成时间</dt><dd>{new Date(draft.generated_at).toLocaleString("zh-CN")}</dd></div><div><dt>模型报告的用量</dt><dd>{reportedTokens.toLocaleString("zh-CN")} tokens{allReported ? "" : "（用量记录可能不完整）"}</dd></div></dl>
      {draft.sources.map((source) => <p key={source.source_id}><a href={source.source_url} rel="noreferrer" target="_blank">{source.source_id}</a> · 获取于 {new Date(source.retrieved_at).toLocaleString("zh-CN")}</p>)}
      <dl className="ir-live-hashes"><div><dt>输入校验值</dt><dd>{draft.input_hash}</dd></div><div><dt>输出校验值</dt><dd>{draft.output_hash}</dd></div><div><dt>资料校验值</dt><dd>{draft.source_bundle_hash}</dd></div></dl>
    </details>
  </section>;
}
