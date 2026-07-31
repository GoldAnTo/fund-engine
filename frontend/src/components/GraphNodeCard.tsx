import type { GraphNode } from "../domain/types";
import { Chip } from "./primitives/Chip";
import { ScoreBar } from "./primitives/ScoreBar";

interface Props {
  node: GraphNode;
  selected?: boolean;
  onClick?: () => void;
}

type Tone = "moss" | "ochre" | "clay" | "mineral" | "iris";

const GROUP_TONE: Record<NonNullable<GraphNode["group"]>, Tone> = {
  evidence: "moss",
  proposition: "ochre",
  causal: "ochre",
  company: "mineral",
  fund: "iris",
};

// GraphNodeCard — Prototype 3 中证据 / 命题 / 因果 / 公司 / 基金列的卡片格式。
// 每种 group 用对应低饱和语义色 + 顶部 chip / meta / 关联度 / 关联条。
export function GraphNodeCard({ node, selected, onClick }: Props) {
  const group = node.group ?? "evidence";
  const tone = GROUP_TONE[group];
  return (
    <article
      className={`graph-card group-${group}${selected ? " is-selected" : ""}`}
    >
      <button
        type="button"
        className="graph-card__btn"
        onClick={onClick}
        aria-pressed={selected ?? false}
      >
        <header className="graph-card__head">
          {node.chip && (
            <Chip tone={tone} bordered size="xs">
              {node.chip}
            </Chip>
          )}
          {node.sequence != null && (
            <span className="graph-card__seq">
              {String(node.sequence).padStart(2, "0")}
            </span>
          )}
          {node.publisher && (
            <span className="graph-card__source">{node.publisher}</span>
          )}
          {node.publish_date && (
            <span className="graph-card__date">{node.publish_date}</span>
          )}
          <span className="graph-card__status-dot" aria-hidden />
        </header>

        <h4 className="graph-card__title">{node.label}</h4>

        {(group === "proposition" || group === "causal") && node.description && (
          <p className="graph-card__desc muted">{node.description}</p>
        )}
        {group === "causal" && node.chapter && (
          <p className="graph-card__chapter muted">{node.chapter}</p>
        )}

        {group === "evidence" && node.reliability_bar != null && (
          <footer className="graph-card__foot">
            <ScoreBar
              score={node.reliability_bar}
              tone={tone}
              label="可靠度"
              showValue={false}
            />
            <span className="graph-card__score">
              {node.reliability_bar.toFixed(2)}
            </span>
          </footer>
        )}

        {group === "proposition" && node.reliability_bar != null && (
          <footer className="graph-card__foot">
            <ScoreBar
              score={node.reliability_bar}
              tone={tone}
              label="可靠度"
              showValue={false}
            />
            <span className="graph-card__score">
              {node.reliability_bar.toFixed(2)}
            </span>
          </footer>
        )}

        {group === "company" && (
          <footer className="graph-card__foot graph-card__foot--two">
            {node.code && (
              <span className="graph-card__code">{node.code}</span>
            )}
            {node.sector && (
              <Chip tone="neutral" size="xs">
                {node.sector}
              </Chip>
            )}
            {node.relevance != null && (
              <span className="graph-card__score">
                关联度 {node.relevance.toFixed(2)}
              </span>
            )}
          </footer>
        )}

        {group === "fund" && (
          <footer className="graph-card__foot graph-card__foot--two">
            {node.code && (
              <span className="graph-card__code">{node.code}</span>
            )}
            {node.weight && (
              <span className="graph-card__weight">{node.weight}</span>
            )}
            {node.relevance_score != null && (
              <span className="graph-card__score">
                关联度 {node.relevance_score.toFixed(2)}
              </span>
            )}
            {node.report_period && (
              <span className="graph-card__report muted">
                报告期 {node.report_period}
              </span>
            )}
          </footer>
        )}
      </button>
    </article>
  );
}