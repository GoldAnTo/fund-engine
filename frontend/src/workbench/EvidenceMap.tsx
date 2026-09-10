import { useId, useState } from "react";
import type { GatewayEvidenceSummary } from "@/gateway/researchContent";
import "./EvidenceMap.css";

type Direction = "support" | "contradict" | "alternative" | "unverified";
const directions: { key: Direction; label: string; filter: string; mark: string }[] = [
  { key: "support", label: "支持方向检索", filter: "支持方向", mark: "○" },
  { key: "contradict", label: "反证方向检索", filter: "反证方向", mark: "◇" },
  { key: "alternative", label: "替代解释方向检索", filter: "替代解释", mark: "□" },
  { key: "unverified", label: "关系待核验", filter: "待核验", mark: "?" },
];
function directionOf(value: string): Direction {
  if (["support", "supports"].includes(value)) return "support";
  if (["contradict", "contradicts", "counter_evidence"].includes(value)) return "contradict";
  if (["alternative", "alternative_explanation"].includes(value)) return "alternative";
  return "unverified";
}
type Props = {
  evidence: GatewayEvidenceSummary[];
  selectedEvidenceId?: string;
  onOpen: (entry: GatewayEvidenceSummary) => void;
};

/** Visual paths describe actual assessment inputs, never verified claim edges. */
export function EvidenceMap({ evidence, selectedEvidenceId, onOpen }: Props) {
  const id = useId();
  const inputKey = JSON.stringify(evidence.map((entry) => [entry.evidenceLinkId, entry.relationship]));
  const [selection, setSelection] = useState<{ inputKey: string; direction: Direction | "all" } | null>(null);
  const filter = selection?.inputKey === inputKey ? selection.direction : "all";
  const groups = directions.map((direction) => ({ ...direction, entries: evidence.filter((entry) => directionOf(entry.relationship) === direction.key) }))
    .filter((group) => group.entries.length > 0);
  const shown = groups.filter((group) => filter === "all" || filter === group.key);
  const shownCount = shown.reduce((count, group) => count + group.entries.length, 0);

  return <section className="evidence-map" aria-label="判断与证据关系" aria-describedby={`${id}-description`}>
    <header className="evidence-map-heading"><h4>本项依据（{evidence.length} 条）</h4></header>
    <p id={`${id}-description`} className="evidence-map-description">路径连接实际评估输入，检索方向不是已验证关系。</p>
    {!evidence.length ? <p>当前没有可展示的评估输入。</p> : <>
      {groups.length > 1 ? <div className="evidence-map-filters" role="group" aria-label="筛选检索方向">
        <button type="button" aria-pressed={filter === "all"} onClick={() => setSelection({ inputKey, direction: "all" })}>全部方向（{evidence.length}）</button>
        {groups.map((group) => <button key={group.key} type="button" aria-pressed={filter === group.key}
          onClick={() => setSelection({ inputKey, direction: group.key })}>仅看{group.filter}（{group.entries.length}）</button>)}
      </div> : null}
      <div className="evidence-map-paths">
        <p className="evidence-map-root"><strong>本项判断</strong><span>实际冻结输入</span></p>
        <div className="evidence-map-branches">
          {shown.map((group) => <div className={`evidence-map-branch evidence-map-${group.key}`} key={group.key} role="group" aria-labelledby={`${id}-${group.key}`}>
            <svg className="evidence-map-connector" viewBox="0 0 32 32" aria-hidden="true" focusable="false"><path d="M 0 1 V 16 Q 0 24 8 24 H 30" /><path d="m 25 20 5 4 -5 4" /></svg>
            <div className="evidence-map-branch-content">
              <p className="evidence-map-direction"><span aria-hidden="true">{group.mark}</span><strong id={`${id}-${group.key}`}>{group.label}</strong><span className="evidence-map-direction-count">{group.entries.length} 条</span></p>
              <ul className="evidence-map-nodes">
                {group.entries.map((entry) => <li key={entry.evidenceLinkId}>
                  <button className="evidence-map-node" type="button" aria-label={`查看引用原文：${entry.title || "未命名材料"}`}
                    aria-pressed={selectedEvidenceId === entry.evidenceLinkId} onClick={() => onOpen(entry)}>
                    <span className="evidence-map-node-title">{entry.title || "未命名材料"}</span>
                    <span className="evidence-map-node-meta">数据期：{entry.observedPeriod ?? "未提供"}</span>
                    <span className="evidence-map-node-action" aria-hidden="true">{selectedEvidenceId === entry.evidenceLinkId ? "正在阅读原文" : "查看原文 →"}</span>
                  </button>
                </li>)}
              </ul>
            </div>
          </div>)}
        </div>
      </div>
      {filter !== "all" ? <p className="evidence-map-description" role="status">当前显示 {shownCount} / {evidence.length} 条实际输入；其他检索方向已折叠。</p> : null}
    </>}
  </section>;
}
