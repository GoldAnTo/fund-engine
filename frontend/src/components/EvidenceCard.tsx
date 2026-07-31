import type { EvidenceRecord, EvidenceRole } from "../domain/types";
import { Chip } from "./primitives/Chip";
import { ScoreBar } from "./primitives/ScoreBar";

interface Props {
  record: EvidenceRecord;
  role?: EvidenceRole;
  focused?: boolean;
  dimmed?: boolean;
  onClick?: () => void;
}

const ROLE_TONE: Record<EvidenceRole, "moss" | "clay" | "neutral"> = {
  supports: "moss",
  contradicts: "clay",
  contextualizes: "neutral",
};

const CHIP_BY_KIND: Record<
  NonNullable<EvidenceRecord["statement_kind"]>,
  string
> = {
  disclosed_fact: "公告发布",
  management_attribution: "管理层表态",
  forecast: "预测数据",
  research_opinion: "研究观点",
};

// EvidenceCard — Prototype 2 中支持 / 反证 / 背景卡片格式：
//   [行业数据 chip] [来源] [日期]
//   证据主标题
//   可靠度条 + 数值
//   摘录预读 + 理由
export function EvidenceCard({ record, focused, dimmed, onClick }: Props) {
  const tone = ROLE_TONE[record.role];
  const chipLabel =
    record.chip_label ??
    (record.statement_kind
      ? CHIP_BY_KIND[record.statement_kind]
      : "元数据待补");
  return (
    <article
      className={`evidence-card role-${record.role}${
        focused ? " is-focused" : ""
      }${dimmed ? " is-dimmed" : ""}`}
      data-testid={`evidence-row-${record.role}`}
      data-related={focused ? "true" : undefined}
      data-role={record.role}
      data-review={record.review_state}
    >
      <button type="button" onClick={onClick} className="evidence-card__btn">
        <header className="evidence-card__head">
          <Chip tone={tone} bordered size="xs">
            {chipLabel}
          </Chip>
          <span className="evidence-card__source">
            {record.source_label ?? "元数据待补"}
          </span>
          {record.period && (
            <span className="evidence-card__date">{record.period}</span>
          )}
          <span className="evidence-card__status-dot" aria-hidden />
        </header>
        <h4 className="evidence-card__title">
          {record.statement_text ?? "尚无证据文本"}
        </h4>
        {record.preview && (
          <p className="evidence-card__preview">{record.preview}</p>
        )}
        <footer className="evidence-card__foot">
          <ScoreBar
            score={record.reliability}
            tone={tone}
            label="可靠度"
            showValue={false}
          />
          <span className="evidence-card__reliability">
            {record.reliability == null
              ? "尚无人工质量口径"
              : `${(record.reliability * 100).toFixed(0)}`}
          </span>
          {record.source_meta && (
            <span className="evidence-card__meta muted">{record.source_meta}</span>
          )}
        </footer>
      </button>
    </article>
  );
}