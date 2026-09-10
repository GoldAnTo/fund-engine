import type { CompanyResearchWorkspace } from "./investmentResearchApi";

export function liveResearchDraftFixture(projectId: string, preparationId: string, cutoff: string, focus: string | null): NonNullable<CompanyResearchWorkspace["research_draft"]> {
  const hash = "b".repeat(64);
  const citation = { excerpt_id: "annual.cloud", source_id: "annual", raw_hash: hash, source_url: "https://abc.xyz/investor/annual", locator: "Cloud business", quote: "Cloud customers pay for consumption of cloud services." };
  const sections = ["business_analysis", "operating_drivers", "candidate_assumptions", "counterevidence", "verification_questions", "report_sections"] as const;
  return {
    id: "90000000-0000-4000-8000-000000000001", project_id: projectId, preparation_id: preparationId,
    request_hash: hash, content_hash: hash, source_bundle_hash: hash, input_hash: hash, output_hash: hash,
    candidate_status: "machine_draft", user_focus: focus, cutoff_at: cutoff, generated_at: "2026-09-09T05:00:00Z",
    model_version: "real-provider-model", prompt_version: "company-research-grounded-draft.v1",
    markdown: "# 云业务研究初稿\n\n增长能否转化为现金流，仍取决于资本需求和客户实际使用量。",
    sections: sections.map((key) => ({ key, label: key, items: [{ title: "云业务的现金流机制", text: "客户使用量带动云业务收入，但新增基础设施投入可能延后现金回收，需要继续核对资本需求。", citations: [citation], fact_keys: [] }] })),
    sources: [{ source_id: "annual", source_url: citation.source_url, raw_hash: hash, available_at: cutoff, retrieved_at: "2026-09-09T04:59:00Z" }],
    usage: { schema_version: "llm_usage.v1", attempts: [{ outcome: "response_received", usage_state: "reported", prompt_tokens: 120, completion_tokens: 80, total_tokens: 200 }] },
  };
}
