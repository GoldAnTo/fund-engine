import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  GraphNodeView,
  RelationshipGraphView,
} from "../../domain/prototypeTypes";
import { WikiGraph } from "./WikiGraph";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

export function RelationshipCanvasScreen() {
  const params = useParams<{ caseId?: string }>();
  const caseId = params.caseId ?? "RC-AIC-2025-01";
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<RelationshipGraphView | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  // 同案例多命题共享一个图谱；让用户选择聚焦哪一条命题的证据层
  // （无后端 thesis_id 时退化到后端默认最新命题）
  const [thesisId, setThesisId] = useState<string>("");

  const loadGraph = (id: string, t: string) => {
    setState({ kind: "loading" });
    return researchClient
      .getRelationshipGraphView(id, t || undefined)
      .then((v) => {
        setView(v);
        setSelectedId(v.selectedNodeId);
        setState({ kind: "ready" });
      })
      .catch((err: Error) =>
        setState({ kind: "error", message: err.message }),
      );
  };

  useEffect(() => {
    let cancelled = false;
    loadGraph(caseId, thesisId).then(() => {
      void cancelled; // 结果在 loadGraph 内处理
    });
    return () => {
      cancelled = true;
    };
  }, [caseId, thesisId]);

  const selected = useMemo(() => {
    if (!view) return null;
    return view.nodes.find((n) => n.id === selectedId) ?? null;
  }, [view, selectedId]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="relationship-loading">
        <p>正在加载证据图谱…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="relationship-error">
        <div className="form-error">
          证据图谱加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen relationship-screen" data-testid="relationship-screen">
      <header className="relationship-header">
        <div>
          <div className="eyebrow">行业研究 · 证据图谱</div>
          <h1>{view.case.title}</h1>
          <p className="lede">{view.case.question}</p>
          {view.theses && view.theses.length > 1 ? (
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                marginTop: 8,
                fontSize: 12,
              }}
            >
              <label htmlFor="thesis-picker">聚焦命题：</label>
              <select
                id="thesis-picker"
                value={thesisId}
                onChange={(e) => setThesisId(e.target.value)}
              >
                <option value="">— 自动选择（最新命题）—</option>
                {view.theses.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.statement.length > 50
                      ? `${t.statement.slice(0, 50)}…`
                      : t.statement}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </div>
        <div className="relationship-header__meta">
          <MetaCell label="证据截止" value={view.case.cutoff} />
          <MetaCell label="冻结快照" value={view.case.snapshotId} mono />
          <MetaCell label="正式判断" value="证据不足 · 继续验证" warn />
        </div>
      </header>

      <div className="relationship-layout">
        <section className="prototype-graph-canvas" aria-label="证据关系 canvas">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">WIKI RELATIONSHIP GRAPH</p>
              <h2>证据 → 命题 → 因果链 → 公司 → 基金</h2>
            </div>
            <span className="state-badge ai">
              {view.nodes.length} 个节点 · 已选 {selectedId}
            </span>
          </div>

          <WikiGraph view={view} selectedId={selectedId} onSelect={setSelectedId} />

          <div className="canvas-layers">
            {view.layers.map((layer) => (
              <div key={layer.key} className="canvas-layer">
                <h3>{layer.label}</h3>
                {layer.nodes.map((node) => (
                  <button
                    type="button"
                    key={node.id}
                    className={`prototype-graph-node ${node.kind}`}
                    aria-pressed={selectedId === node.id}
                    onClick={() => setSelectedId(node.id)}
                  >
                    <span className="graph-node-layer">{node.layer}</span>
                    <strong>{node.title}</strong>
                    <small>{node.meta}</small>
                    <span className="graph-node-kind">{node.kindLabel}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>

        </section>

        <aside className="prototype-inspector relationship-inspector" aria-label="固定证据容器">
          {selected ? (
            <NodeInspector node={selected} />
          ) : (
            <p style={{ color: "var(--ink-muted)", fontSize: 12 }}>
              请选择任意节点以查看详情。
            </p>
          )}

          <div className="evidence-stack">
            <section>
              <h3>事实层</h3>
              <p className="muted">
                来源陈述以事实层展示；
                {view.layers[0]?.nodes.length ?? 0} 条来源事实。
              </p>
            </section>
            <section>
              <h3>反驳 · 共识</h3>
              <p className="muted">
                红线 = 反驳，灰链 = 共识。
              </p>
            </section>
            <section>
              <h3>AI 提议 · 证据来源</h3>
              <p className="muted">
                仅显示节点层；详细出处见右上证据容器。
              </p>
            </section>
          </div>
        </aside>
      </div>
    </div>
  );
}

function MetaCell({
  label,
  value,
  mono,
  warn,
}: {
  label: string;
  value: string;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={`meta-cell${warn ? " meta-cell--warn" : ""}`}>
      <span>{label}</span>
      <strong style={mono ? { fontFamily: "ui-monospace, monospace" } : undefined}>
        {value}
      </strong>
    </div>
  );
}

function NodeInspector({ node }: { node: GraphNodeView }) {
  return (
    <div data-testid="node-inspector">
      <div className="inspector-kind">{node.layer}</div>
      <h2>{node.title}</h2>
      <p>{node.relation}</p>
      <dl>
        <div>
          <dt>种类</dt>
          <dd>{node.kindLabel}</dd>
        </div>
        <div>
          <dt>审核</dt>
          <dd>{node.review}</dd>
        </div>
        <div>
          <dt>来源</dt>
          <dd>{node.sourceName}</dd>
        </div>
        <div>
          <dt>出处</dt>
          <dd>{node.sourceSpan}</dd>
        </div>
        <div>
          <dt>所属附件</dt>
          <dd>{node.attachment}</dd>
        </div>
        <div>
          <dt>发表日期</dt>
          <dd>{node.publicationDate}</dd>
        </div>
        <div>
          <dt>截止日</dt>
          <dd>{node.asOf}</dd>
        </div>
        <div>
          <dt>范围</dt>
          <dd>{node.scope}</dd>
        </div>
        <div>
          <dt>引用</dt>
          <dd>{node.citations.join(" · ")}</dd>
        </div>
        {node.note && (
          <div>
            <dt>备注</dt>
            <dd>{node.note}</dd>
          </div>
        )}
      </dl>
      {node.sourceHref && (
        <Link className="prototype-next-action" to={node.sourceHref}>
          跳转原文 →
        </Link>
      )}
    </div>
  );
}