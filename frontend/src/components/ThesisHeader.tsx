import type { ThesisAssessment } from "../domain/types";
import { Chip } from "./primitives/Chip";
import { BulletList } from "./primitives/BulletList";
import { Button } from "./primitives/Button";

interface Props {
  thesis: ThesisAssessment;
}

const CONCLUSION_LABEL: Record<ThesisAssessment["conclusion"], string> = {
  supported: "已支持",
  contradicted: "反证",
  insufficient_evidence: "证据不足",
};

const CONCLUSION_TONE: Record<ThesisAssessment["conclusion"], "moss" | "clay" | "ochre"> = {
  supported: "moss",
  contradicted: "clay",
  insufficient_evidence: "ochre",
};

// ThesisHeader — Prototype 3 中"核心命题"标题卡 + 长段结论 + 元数据行
// （当前状态 / 核心论点 chips / 供应链级 / 更新时间 / 置信度 / 验证关注）。
export function ThesisHeader({ thesis }: Props) {
  return (
    <header className="thesis-header" data-testid="thesis-header">
      <div className="thesis-header__head">
        <h2>核心命题</h2>
        <Chip tone={CONCLUSION_TONE[thesis.conclusion]} bordered size="sm">
          {CONCLUSION_LABEL[thesis.conclusion]}
        </Chip>
        <Button variant="bare" size="xs" type="button" aria-label="更多">
          ⋯
        </Button>
        <Button variant="primary" size="sm" type="button">
          编辑命题
        </Button>
      </div>
      <p className="thesis-header__rationale">{thesis.rationale}</p>

      <dl className="thesis-header__meta">
        <div>
          <dt>当前状态</dt>
          <dd>
            <Chip tone="amber" bordered size="xs">
              {thesis.status_label}
            </Chip>
          </dd>
        </div>
        <div>
          <dt>核心论点</dt>
          <dd className="thesis-header__chips">
            {thesis.focus_axes.map((axis) => (
              <Chip key={axis} tone="neutral" size="xs">
                {axis}
              </Chip>
            ))}
          </dd>
        </div>
        <div>
          <dt>供应链级</dt>
          <dd>{thesis.supply_chain_level}</dd>
        </div>
        <div>
          <dt>更新时间</dt>
          <dd>{thesis.updated_at}</dd>
        </div>
        <div>
          <dt>置信度</dt>
          <dd>
            <Chip tone="moss" size="xs">
              {thesis.confidence_label}
            </Chip>
          </dd>
        </div>
        <div>
          <dt>验证关注</dt>
          <dd>{thesis.major_gap ?? "—"}</dd>
        </div>
      </dl>

      {thesis.bullets.length > 0 && (
        <section className="thesis-header__bullets">
          <h3>关键支撑</h3>
          <BulletList items={thesis.bullets} />
        </section>
      )}
    </header>
  );
}