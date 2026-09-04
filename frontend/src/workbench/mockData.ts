export type WorkbenchThread = {
  id: string;
  title: string;
  status: string;
  updatedAt: string;
  active?: boolean;
};

export type FeedKind = "goal" | "agent" | "reviewer";

export type FeedItem = {
  id: string;
  kind: FeedKind;
  title: string;
  status: string;
  summary: string;
  references: number;
  files?: Array<{ name: string; type: string }>;
};

export type Evidence = {
  id: string;
  title: string;
  date: string;
  source: string;
  summary: string;
  label: string;
  tone: "support" | "related" | "counter";
};

export const workbenchThreads: WorkbenchThread[] = [
  { id: "semiconductor", title: "科创板半导体设备国产化机会研究", status: "进行中", updatedAt: "更新于 10:24", active: true },
  { id: "overseas", title: "海外半导体设备龙头跟踪", status: "进行中", updatedAt: "更新于 09:47" },
  { id: "storage", title: "存储芯片周期与供需研究", status: "进行中", updatedAt: "更新于昨天" },
  { id: "advanced", title: "先进封装产业链梳理", status: "待启动", updatedAt: "创建于昨天" },
  { id: "compute", title: "AI 算力产业链投资机会", status: "已归档", updatedAt: "更新于 05-12" },
];

export const workbenchFeed: FeedItem[] = [
  {
    id: "goal", kind: "goal", title: "研究目标", status: "", references: 0,
    summary: "请评估科创板半导体设备公司在关键工艺环节的国产化进展、竞争格局与投资机会。",
  },
  {
    id: "industry", kind: "agent", title: "分析师-产业", status: "进行中", references: 4,
    summary: "聚焦刻蚀、薄膜沉积、清洗、量测四类设备，梳理国产化率、主要玩家与突破进展。",
    files: [{ name: "产业链地图.pdf", type: "PDF" }, { name: "国产化进展总览.xlsx", type: "XLSX" }, { name: "设备环节定义.md", type: "MD" }],
  },
  {
    id: "finance", kind: "agent", title: "分析师-财务", status: "进行中", references: 5,
    summary: "拆分公司收入结构、毛利率、研发投入与订单能见度，评估业绩弹性与估值合理性。",
    files: [{ name: "财务对比表.xlsx", type: "XLSX" }, { name: "盈利预测模型.xlsx", type: "XLSX" }, { name: "订单与在手情况.pdf", type: "PDF" }],
  },
  {
    id: "strategy", kind: "agent", title: "分析师-策略", status: "进行中", references: 6,
    summary: "结合国产化趋势与政策导向，评估行业空间、竞争格局演变与资本市场预期。",
    files: [{ name: "策略视角框架.md", type: "MD" }, { name: "可比公司估值.xlsx", type: "XLSX" }, { name: "政策梳理.pdf", type: "PDF" }],
  },
  {
    id: "reviewer", kind: "reviewer", title: "质控审核员", status: "待审核", references: 3,
    summary: "检查关键假设、数据来源与逻辑链路，识别潜在偏差与遗漏，形成审核意见。",
  },
];

export const workbenchEvidence: Evidence[] = [
  { id: "amec", tone: "support", label: "高度相关", title: "中微公司：刻蚀设备在客户端验证进展", date: "2025-05-10", source: "公司公告", summary: "公司披露多项刻蚀设备已在国内多家客户的生产线上完成验证，部分产品已实现批量销售。" },
  { id: "semi", tone: "support", label: "高度相关", title: "SEMI：2024 中国半导体设备国产化率提升至 28%", date: "2025-04-29", source: "行业报告", summary: "SEMI 报告显示，中国大陆半导体设备市场规模超过 496 亿美元，其中本土设备厂商占比显著提升。" },
  { id: "naura", tone: "related", label: "中度相关", title: "北方华创：2025Q1 业绩说明会纪要", date: "2025-04-28", source: "公司纪要", summary: "公司表示刻蚀和薄膜沉积设备在逻辑与存储客户的导入进展顺利，订单能见度良好。" },
  { id: "asml", tone: "counter", label: "反证据", title: "ASML：先进制程设备交付对国产替代形成压力", date: "2025-04-15", source: "海外公司财报", summary: "ASML 指出，中国大陆客户在先进制程扩产加速，短期对高端光刻设备需求强劲。" },
];
