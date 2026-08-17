import type { EventNextActionKind, EventResearchListItem, EventWorkbench } from "./eventResearch";
import type { ResearchPreparationStatus } from "./researchPreparation";

const PREPARATION_REVIEW_ACTION_KINDS: readonly EventNextActionKind[] = [
  "review_preparation_claims",
  "review_preparation_protocol",
  "authorize_preparation_plan",
  "recover_preparation",
];

const PREPARATION_TASK_LABELS: Partial<Record<ResearchPreparationStatus, string>> = {
  awaiting_claim_review: "核验原文与候选陈述",
  awaiting_protocol_confirmation: "确认研究协议草案",
  awaiting_plan_authorization: "审核补证计划并授权启动",
  recoverable_failure: "恢复研究准备",
};

const PREPARATION_REVIEW_STATUSES: readonly ResearchPreparationStatus[] = [
  "awaiting_claim_review",
  "awaiting_protocol_confirmation",
  "awaiting_plan_authorization",
  "recoverable_failure",
];

function isPreparationReviewStatus(status: ResearchPreparationStatus): boolean {
  return PREPARATION_REVIEW_STATUSES.includes(status);
}

export function isPreparationReviewAction(
  kind: EventNextActionKind | null | undefined,
): boolean {
  return kind !== null
    && kind !== undefined
    && PREPARATION_REVIEW_ACTION_KINDS.includes(kind);
}

export function isPreparationInProgress(event: EventResearchListItem): boolean {
  return event.nextActionKind === "wait"
    && event.statusSummary === "系统正在准备研究材料";
}

export function isPreparationDeskEvent(event: EventResearchListItem): boolean {
  return isPreparationReviewAction(event.nextActionKind) || isPreparationInProgress(event);
}

export function eventDeskNeedsHumanReview(event: EventResearchListItem): boolean {
  return isPreparationReviewAction(event.nextActionKind) || Boolean(event.nextHumanAction);
}

export function hasPendingResearchPreparation(workbench: EventWorkbench): boolean {
  return Boolean(
    workbench.preparation && workbench.preparation.status !== "authorized",
  );
}

export function researchPreparationStatusLabel(workbench: EventWorkbench): string | null {
  if (!hasPendingResearchPreparation(workbench)) return null;
  return PREPARATION_TASK_LABELS[workbench.preparation!.status]
    || "系统正在准备研究材料";
}

export const EVENT_RESEARCH_STAGES = [
  { id: 1, label: "资料接入" },
  { id: 2, label: "定义研究" },
  { id: 3, label: "执行补证" },
  { id: 4, label: "审核证据" },
  { id: 5, label: "形成结论" },
  { id: 6, label: "持续跟踪" },
] as const;

export function eventResearchStage(workbench: EventWorkbench): number {
  if (workbench.lifecycle.status === "published") return 6;
  if (
    workbench.lifecycle.status === "draft_ready"
    || workbench.lifecycle.status === "exhausted"
    || workbench.nextAction.kind === "review_conclusion"
  ) return 5;
  if (
    workbench.lifecycle.status === "awaiting_key_review"
    || workbench.nextAction.kind === "review_intake"
    || workbench.nextAction.kind === "review_evidence"
  ) return 4;
  if (
    workbench.lifecycle.status === "researching"
    || workbench.lifecycle.status === "continuing"
  ) return 3;
  if (
    workbench.lifecycle.status === "awaiting_scope"
    || workbench.nextAction.kind === "edit_factors"
  ) return 2;
  return 1;
}

export interface EventActionPresentation {
  owner: "你需要做" | "现在不用做" | "本轮已完成";
  title: string;
  why: string;
  steps: string[];
  unlock: string;
  to: string;
  buttonLabel: string;
}

export function eventActionPresentation(
  workbench: EventWorkbench,
  caseId: string,
): EventActionPresentation {
  const base = `/events/${caseId}`;
  if (hasPendingResearchPreparation(workbench)) {
    const preparationTaskLabel = researchPreparationStatusLabel(workbench);
    const isHumanPreparationTask = isPreparationReviewStatus(
      workbench.preparation!.status,
    );
    return {
      owner: isHumanPreparationTask ? "你需要做" : "现在不用做",
      title: isHumanPreparationTask
          ? isPreparationReviewAction(workbench.nextAction.kind)
          ? workbench.nextAction.label
          : preparationTaskLabel || "确认研究准备"
        : "系统正在准备研究材料",
      why: isHumanPreparationTask
        ? "系统已完成草案准备，仍需由你逐项确认；未确认前不会采纳候选、启动数据 Provider 或运行正式研究。"
        : "系统正在解析冻结原文并生成协议与补证计划草案；它不会自行采纳、授权或启动正式研究。",
      steps: isHumanPreparationTask
        ? ["核对当前准备材料与冻结原文", "逐项确认候选、协议或补证计划", "明确授权后才会创建正式研究运行"]
        : ["解析冻结原文", "生成研究协议草案", "生成补证计划草案"],
      unlock: isHumanPreparationTask
        ? "确认顺序完成后，系统才会创建一条可回放的正式研究运行。"
        : "准备完成后，系统会明确提示你确认下一项材料。",
      to: `${base}/preparation`,
      buttonLabel: isHumanPreparationTask ? "进入研究准备" : "查看研究准备进度",
    };
  }
  switch (workbench.nextAction.kind) {
    case "review_intake":
      return {
        owner: "你需要做",
        title: workbench.nextAction.label,
        why: workbench.lifecycle.currentGap || "资料已经进入当前事件，但原文、定位或来源许可仍需确认。",
        steps: ["核对冻结原文和资料版本", "确认来源许可与可用时点", "决定是否允许进入证据审核"],
        unlock: "通过核验的资料会进入候选证据审核，未通过资料会保留排除原因。",
        to: `${base}/documents`,
        buttonLabel: "核验冻结原文",
      };
    case "review_evidence":
      return {
        owner: "你需要做",
        title: workbench.nextAction.label,
        why: workbench.lifecycle.currentGap || "候选材料会改变关键因素状态，系统不能自行采纳。",
        steps: ["核对冻结原文与精确位置", "判断证据支持、反驳或需要补证", "填写理由并提交审核记录"],
        unlock: "系统会根据审核结果重新计算关键因素，并决定是否继续补证或形成结论草案。",
        to: `${base}/review`,
        buttonLabel: "进入证据审核",
      };
    case "review_conclusion":
      return {
        owner: "你需要做",
        title: workbench.nextAction.label,
        why: workbench.lifecycle.currentGap || "正式结论必须由研究员确认，AI 草案不能直接发布。",
        steps: ["核对结论是否超出证据边界", "检查反证、缺口和引用", "发布、退回补证或保留草案"],
        unlock: "发布后形成不可变结论版本，并进入持续跟踪。",
        to: `${base}/review`,
        buttonLabel: "复核结论草案",
      };
    case "edit_factors":
      return {
        owner: "你需要做",
        title: workbench.nextAction.label,
        why: workbench.lifecycle.currentGap || "研究因素尚未形成可验证边界，系统不能安全开始补证。",
        steps: ["确认研究问题和关键因素", "补齐因素描述与验证边界", "冻结新的研究范围版本"],
        unlock: "范围确认后才可创建受控研究运行。",
        to: `${base}/scope`,
        buttonLabel: "调整研究范围",
      };
    case "complete_research_protocol":
      return {
        owner: "你需要做",
        title: workbench.nextAction.label,
        why: workbench.lifecycle.currentGap || "新增因素必须先完成可研究性协议，系统才能安全启动补证。",
        steps: ["为新增因素绑定结果指标与范围", "确认基线、时间窗和验证口径", "审核协议后重新启动补证"],
        unlock: "协议完成后，系统才能为当前范围创建受控研究运行。",
        to: `${base}/protocol`,
        buttonLabel: "完成研究协议",
      };
    case "view_conclusion_change":
      return {
        owner: "你需要做",
        title: workbench.nextAction.label,
        why: workbench.lifecycle.currentGap || "新证据可能改变已发布结论，需要核对版本差异。",
        steps: ["比较当前与上一结论版本", "核对新增证据和反证", "决定是否保留或更新结论"],
        unlock: "决定会保留为新的不可变结论记录。",
        to: `${base}/history`,
        buttonLabel: "查看结论版本",
      };
    case "wait":
    default:
      if (workbench.lifecycle.status === "published") {
        return {
          owner: "本轮已完成",
          title: workbench.nextAction.label || "当前没有需要处理的任务",
          why: "当前结论已经发布，没有新的关键材料或人工待办。",
          steps: ["保持已发布结论不变", "等待下一验证事件", "关键变化出现时重新打开人工任务"],
          unlock: "新的受控运行只会追加候选，不会自动覆盖已发布结论。",
          to: `${base}/monitor`,
          buttonLabel: "查看持续研究",
        };
      }
      return {
        owner: "现在不用做",
        title: workbench.nextAction.label || workbench.lifecycle.summary,
        why: "系统正在冻结范围内执行，目前没有可以安全交给人工的决定。",
        steps: ["系统按允许来源检查资料", "记录纳入、排除和暂停原因", "需要判断时生成明确的人工任务"],
        unlock: "运行完成后会明确显示无关键变化，或生成可逐条处理的审核任务。",
        to: `${base}/monitor`,
        buttonLabel: "查看系统正在做什么",
      };
  }
}

export function eventDeskRoute(event: EventResearchListItem): string {
  if (event.workflowMode === "automatic") {
    return `/events/${encodeURIComponent(event.id)}/automatic-research`;
  }
  const base = `/events/${event.id}`;
  if (isPreparationDeskEvent(event)) return `${base}/preparation`;
  if (event.nextActionKind === "complete_research_protocol") return `${base}/protocol`;
  return event.nextHumanAction ? `${base}/review` : base;
}
