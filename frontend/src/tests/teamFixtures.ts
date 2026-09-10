export const teamId = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
export const teamConversationId = teamId(1), teamRunId = teamId(2), teamEvidenceId = teamId(3);
export const teamAt = "2026-09-07T04:00:00Z";
export function teamWire() {
  const roles = ["industry", "finance", "strategy", "quality"];
  return { conversation_id: teamConversationId, run_spec_id: teamRunId, status: "active", revision: 1, event_sequence: 8,
    tasks: roles.map((role, index) => ({ id: teamId(10 + index), role, revision: 1, status: "succeeded", reason_code: null,
      attempt: 1, instruction: "核验本轮实际材料", dependency_ids: index < 2 ? [] : roles.slice(0, index).map((_, n) => teamId(10 + n)),
      created_at: teamAt, updated_at: teamAt, output_state: "available", output: { id: teamId(20 + index),
        content: { summary: `${role} 已保存摘要`, findings: [{ statement: "实际证据中的观察。", basis: "supported", citations: [{ evidence_link_id: teamEvidenceId, quote: "冻结引用" }] }],
          gaps: ["仍待核验来源独立性"], checks: index === 3 ? [{ kind: "periods", status: "warning", detail: "期间需要核对", task_ids: [teamId(11)] }] : [], limitations: ["未经人工复核"] },
        evidence_ids: [teamEvidenceId], dependency_output_ids: index < 2 ? [] : roles.slice(0, index).map((_, n) => teamId(20 + n)), created_at: teamAt },
      attempts: [{ call_id: teamId(30 + index), attempt: 1, operation: `professional_${role}`, provider: "test-provider", requested_model: "test-model", model: null,
        provider_request_id: null, finish_reason: null, prompt_tokens: null, completion_tokens: null, total_tokens: null,
        usage_status: "unknown", latency_ms: 1200, outcome: "succeeded", retryable: false, retry_delay_seconds: null }],
    })), reviews: [] as unknown[] };
}
export function teamReceipt(revision = 2) {
  return { request_id: teamId(40), conversation_id: teamConversationId, run_spec_id: teamRunId, revision, task_ids: [teamId(10)] };
}
