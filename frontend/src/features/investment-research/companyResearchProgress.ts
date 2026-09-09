import type { CompanyResearchWorkspace } from "../../data/investmentResearchApi";

export type CompanyResearchProgress = NonNullable<CompanyResearchWorkspace["product_progress"]>;

export const COMPANY_RESEARCH_STATUS_LABELS: Record<CompanyResearchProgress["status"], string> = {
  queued: "等待开始",
  collecting_sources: "收集资料",
  analyzing_company: "分析公司",
  building_forecast: "构建预测",
  generating_report: "生成研究报告",
  completed: "研究已完成",
  needs_input: "需要确认",
  failed: "研究遇到问题",
};
