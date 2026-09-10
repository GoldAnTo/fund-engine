export interface ReportSourceDisplay {
  href: string | null;
  label: string;
}

const SNAPSHOT_LOCATOR = /^(valuation_snapshot|industry_index_snapshot|report_market_observation|holding_disclosure|report_fund_exposure):/;

/** Converts a ledger locator into safe, human-readable display text. */
export function formatReportSourceLocator(value: string | null | undefined): ReportSourceDisplay {
  if (!value) return { href: null, label: "当前状态已记录，暂无可公开定位" };
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "http:") return { href: url.toString(), label: "查看安全来源" };
  } catch {
    // A ledger locator can be JSON, a private snapshot key, or an unknown value.
  }
  if (SNAPSHOT_LOCATOR.test(value)) return { href: null, label: "已记录的市场/行业数据快照" };
  try {
    const locator = JSON.parse(value) as Record<string, unknown>;
    if (locator && typeof locator === "object" && !Array.isArray(locator)) {
      const parts: string[] = [];
      if (typeof locator.page === "number" || typeof locator.page === "string") parts.push(`第 ${locator.page} 页`);
      if (typeof locator.paragraph === "number" || typeof locator.paragraph === "string") parts.push(`第 ${locator.paragraph} 段`);
      const table = locator.table ?? locator.table_id;
      if (typeof table === "number" || typeof table === "string") parts.push(`表格 ${table}`);
      if (parts.length) return { href: null, label: `原文定位：${parts.join(" · ")}` };
    }
  } catch {
    // Safe fallback below; never display opaque locator content.
  }
  return { href: null, label: "当前状态已记录，暂无可公开定位" };
}

export function reportNodeStatusLabel(status: string): string {
  if (status === "verified") return "已验证";
  if (status === "candidate") return "候选，不能计入结论";
  if (status === "rejected") return "反证或重大替代解释";
  if (status === "market_observation") return "市场窗口观察";
  return "研报原始主张";
}
