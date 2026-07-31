import type { CitationEntry, EvidenceRecord, ObjectStatus } from "../domain/types";
import { locatorText } from "../domain/locator";
import { StatusMark } from "./StatusMark";
import { Chip } from "./primitives/Chip";
import { CitationList } from "./CitationList";
import { Breadcrumb } from "./primitives/Breadcrumb";
import { Button } from "./primitives/Button";

interface Props {
  record: EvidenceRecord | null;
  citations?: CitationEntry[];
  onClose?: () => void;
}

const STATUS_BY_REVIEW: Record<string, ObjectStatus> = {
  machine_generated: "ai_pending_review",
  reviewed: "human_confirmed",
  rejected: "human_rejected",
};

// Synthetic fallback citations when the caller does not pass any.
function defaultCitations(record: EvidenceRecord): CitationEntry[] {
  return [
    {
      id: `${record.link_id}-c1`,
      theme: "需求扩张 + 渗透率提升",
      date: record.available_at.slice(0, 10),
      description: (record.statement_text ?? "").slice(0, 28),
    },
    {
      id: `${record.link_id}-c2`,
      theme: "规模效应释放",
      date: record.available_at.slice(0, 10),
      description: "价格数据 → 毛利上移",
    },
    {
      id: `${record.link_id}-c3`,
      theme: "盈利中枢上移",
      date: record.available_at.slice(0, 10),
      description: "盈利中枢上移",
    },
  ];
}

const ROLE_LABEL: Record<EvidenceRecord["role"], string> = {
  supports: "支持",
  contradicts: "反证",
  contextualizes: "背景",
};

const ROLE_TONE: Record<EvidenceRecord["role"], "moss" | "clay" | "neutral"> = {
  supports: "moss",
  contradicts: "clay",
  contextualizes: "neutral",
};

export function SourceInspector({ record, citations, onClose }: Props) {
  if (!record) {
    return (
      <aside className="source-inspector" data-empty>
        <p className="muted">
          选择证据或关系边以查看冻结原文、定位、时间、范围与审核记录。
        </p>
      </aside>
    );
  }

  return (
    <aside
      className="source-inspector"
      aria-label="证据检查器"
      data-testid="source-inspector"
    >
      <header className="source-inspector__header">
        <h3>证据详情</h3>
        {onClose && (
          <Button variant="bare" size="xs" type="button" onClick={onClose} aria-label="关闭检查器">
            ×
          </Button>
        )}
      </header>

      <Breadcrumb
        items={[
          { label: "来源", href: "#" },
          { label: record.source_label ?? "元数据待补", href: "#" },
          { label: record.period ?? "信息", href: "#" },
          { label: "原始快照" },
        ]}
      />
      <a className="source-inspector__open-original" href="#open-original">
        ⤴ 打开原文
      </a>

      <h4 className="source-inspector__heading">{record.statement_text}</h4>

      {record.verbatim_text && (
        <section
          className="source-inspector__preview"
          aria-label="原文摘录（已定位）"
        >
          {record.verbatim_text}
        </section>
      )}

      <p className="source-inspector__byline muted">
        — {record.source_label} · {record.period ?? "—"}
      </p>

      <dl className="source-inspector__meta">
        <div>
          <dt>章节</dt>
          <dd>三、需求</dd>
        </div>
        <div>
          <dt>段落</dt>
          <dd>第 2 段</dd>
        </div>
        <div>
          <dt>位置</dt>
          <dd>{locatorText(record.locator)}</dd>
        </div>
        <div>
          <dt>证据 ID</dt>
          <dd>{record.link_id}</dd>
        </div>
        <div>
          <dt>证据类型</dt>
          <dd>
            <Chip tone={ROLE_TONE[record.role]} bordered size="xs">
              {ROLE_LABEL[record.role]}
            </Chip>
          </dd>
        </div>
        <div>
          <dt>相关性</dt>
          <dd>
            <Chip tone="moss" bordered size="xs">
              {record.reliability == null
                ? "尚无人工质量口径"
                : `${(record.reliability * 100).toFixed(0)}`}
            </Chip>
          </dd>
        </div>
        <div>
          <dt>加入时间</dt>
          <dd>{record.available_at.slice(0, 10)}</dd>
        </div>
        <div>
          <dt>范围</dt>
          <dd>
            <code>{JSON.stringify(record.scope)}</code>
          </dd>
        </div>
        <div>
          <dt>状态</dt>
          <dd>
            {record.review_state in STATUS_BY_REVIEW ? (
              <StatusMark status={STATUS_BY_REVIEW[record.review_state as keyof typeof STATUS_BY_REVIEW]} />
            ) : (
              <span>—</span>
            )}
          </dd>
        </div>
      </dl>

      <section className="source-inspector__history" aria-label="审核历史">
        <h4>复核状态</h4>
        <ol>
          <li>
            <span>{record.available_at}</span>{" "}
            <span>
              AI 提议证据 · 可靠度{" "}
              {record.reliability == null
                ? "尚无人工质量口径"
                : `${(record.reliability * 100).toFixed(0)}`}
            </span>
          </li>
          {record.review_state === "machine_generated" && (
            <li className="muted">等待人工复核</li>
          )}
        </ol>
        <div className="source-inspector__actions">
          <Button variant="primary" size="sm" type="button">发起复核</Button>
          <Button variant="chip" size="sm" type="button">加入证据链</Button>
        </div>
      </section>

      <CitationList citations={citations ?? defaultCitations(record)} />
    </aside>
  );
}