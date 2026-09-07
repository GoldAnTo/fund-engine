/** Display vocabulary only; API state values remain unchanged for transitions. */
const labels: Record<string, string> = {
  queued: "已排队", preparing: "准备中", running: "执行中", researching: "研究中",
  completed: "已完成", succeeded: "已完成", failed: "失败", cancelled: "已取消",
  retrying: "重试中", stale: "已过期", locked: "尚未开放", pending: "待处理",
  awaiting_key_review: "等待关键因素审核", awaiting_claim_review: "等待陈述审核",
  awaiting_protocol_confirmation: "等待协议确认", awaiting_plan_authorization: "等待计划授权",
  awaiting_review: "等待审核", awaiting_scope: "等待研究范围确认", waiting_for_review: "等待人工审核",
  waiting_for_sources: "等待来源", recoverable_failure: "失败，可重试", authorized: "已授权",
  confirmed: "已确认", reviewed: "已审核", rejected: "已拒绝", modified: "已修改",
  draft: "草案", draft_ready: "草案待审", ai_draft: "AI 草案，待人工审核",
  system_generated: "系统生成，待人工审核", machine_generated: "机器生成，待人工审核",
  unreviewed: "未经人工审核", published: "已发布", cannot_conclude: "暂不能下结论",
  low: "低", medium: "中", high: "高", normal: "普通", unknown: "未知",
  support: "支持", supports: "支持", contradiction: "反证", contradicts: "反驳",
  related: "相关", neutral: "中性", context: "背景", uncertain: "不确定",
  acquire: "采集材料", acquisition: "采集材料", retrieval: "检索证据", parse: "解析材料",
  admit: "核验来源", analyze: "分析证据", conclude: "形成结论", review: "审核",
  planning: "规划", assessment: "评估", verification: "核验", synthesis: "综合判断",
  official: "官方来源", company_disclosure: "公司披露", licensed_provider: "授权数据源",
  public_url: "公开网页", pasted_snapshot: "粘贴原文", uploaded_file: "上传文件",
};
export const researchLabel = (value: string): string => labels[value] ?? value;
export function researchTime(value: string, timeZone?: string): string {
  // Backend timestamps use UTC; SQLite can omit the offset when serializing.
  const normalized = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?$/.test(value) ? `${value}Z` : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", ...(timeZone ? { timeZone } : {}) }).format(date);
}
