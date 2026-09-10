import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  CaseSummaryItem,
  PlaybackEvent,
  SnapshotPoint,
  VersionColumnContent,
  VersionsView,
} from "../../domain/prototypeTypes";

interface PlaybackStep {
  id: string;
  index: number;
  cutoff: string;
  linkCount: number;
  event: PlaybackEvent;
}

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

/** Render a horizontal snapshot timeline. Node radius scales with link_count;
 *  orange = current compareCutoff, blue = current baseCutoff, white = others.
 *  Clicking a node sets compareCutoff. Shift-click sets baseCutoff. */
function SnapshotTimeline({
  points,
  baseCutoff,
  compareCutoff,
  onPickCompare,
  onPickBase,
}: {
  points: SnapshotPoint[];
  baseCutoff: string;
  compareCutoff: string;
  onPickCompare: (cutoff: string) => void;
  onPickBase: (cutoff: string) => void;
}) {
  if (points.length === 0) {
    return <p>（该案例暂无快照记录）</p>;
  }
  const maxLinks = Math.max(1, ...points.map((p) => p.linkCount));
  const minSize = 14;
  const maxSize = 32;

  return (
    <div
      data-testid="snapshot-timeline"
      style={{
        width: "100%",
        overflowX: "auto",
        background: "var(--surface-subtle, #fafafa)",
        padding: 12,
        borderRadius: 6,
      }}
    >
      <div
        style={{ display: "flex", alignItems: "flex-end", gap: 12, minWidth: 0 }}
      >
        {points.map((p) => {
          const size =
            minSize + (p.linkCount / maxLinks) * (maxSize - minSize);
          const isBase = p.cutoff === baseCutoff;
          const isCompare = p.cutoff === compareCutoff;
          const bg = isCompare
            ? "var(--accent, #f59e0b)"
            : isBase
              ? "var(--info, #3b82f6)"
              : "var(--surface, #ffffff)";
          const border = isCompare
            ? "var(--accent-strong, #b45309)"
            : isBase
              ? "var(--info-strong, #1d4ed8)"
              : "var(--ink-muted, #999)";
          const fg =
            isCompare || isBase
              ? "var(--surface, #fff)"
              : "var(--ink, #222)";
          return (
            <button
              key={p.cutoff}
              type="button"
              data-testid="snapshot-timeline-node"
              onClick={(e) => {
                if (e.shiftKey) onPickBase(p.cutoff);
                else onPickCompare(p.cutoff);
              }}
              title={`${p.cutoff} · ${p.linkCount} 关系 · 短 ID ${p.id}\n点击 = 设为 compare；Shift+点击 = 设为 base`}
              style={{
                background: bg,
                color: fg,
                border: `2px solid ${border}`,
                borderRadius: 999,
                width: size * 2.2,
                height: size * 2.2,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                cursor: "pointer",
                padding: 0,
                fontSize: 11,
                fontWeight: 600,
                flex: "0 0 auto",
              }}
            >
              <span>{p.linkCount}</span>
              <span style={{ fontSize: 9, opacity: 0.85 }}>
                {p.cutoff.slice(5, 16).replace("T", " ")}
              </span>
            </button>
          );
        })}
      </div>
      <p
        style={{
          fontSize: 11,
          color: "var(--ink-muted)",
          marginTop: 8,
          marginBottom: 0,
        }}
      >
        节点大小 ∝ 关系数（已审核 + AI 提议合计）。橙色 = compareCutoff，
        蓝色 = baseCutoff，白色 = 其他。点击节点切换 compare，Shift+点击
        切换 base。
      </p>
    </div>
  );
}

const CONCLUSION_LABELS: Record<string, string> = {
  supported: "支持",
  insufficient_evidence: "证据不足",
  refuted: "反驳",
  contested: "争议",
  pending: "待评估",
};
function conclusionLabel(c: string): string {
  return CONCLUSION_LABELS[c] ?? c;
}
function badgeFor(c: string): string {
  if (c === "supported") return "reviewed";
  if (c === "insufficient_evidence") return "monitoring";
  if (c === "refuted") return "rejected";
  if (c === "contested") return "pending";
  return "pending";
}

function renderColumn(label: string, content: VersionColumnContent, accent: string) {
  return (
    <article className="version-column" data-snapshot={accent}>
      <p className="section-kicker">{label}</p>
      <h3>正式判断 · {content.formalConclusion.state}</h3>
      <p style={{ fontSize: 12 }}>{content.formalConclusion.text}</p>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>输入</h4>
      <ul className="prototype-version-record-list">
        {content.inputs.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>
              {row.label} · {row.version ?? "—"}
            </span>
          </li>
        ))}
      </ul>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>关系</h4>
      <ul className="prototype-version-record-list compact">
        {content.relationships.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>{row.label}</span>
            <em>{row.role}</em>
          </li>
        ))}
      </ul>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>因素</h4>
      <ul className="prototype-version-record-list">
        {content.factors.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>
              {row.label} · {row.role}
            </span>
          </li>
        ))}
      </ul>

      <h4 style={{ marginTop: 12, fontSize: 12 }}>缺口</h4>
      <ul className="prototype-version-record-list">
        {content.gaps.map((row) => (
          <li key={row.id}>
            <strong>{row.id}</strong>
            <span>
              {row.label} · {row.state}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function VersionsScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<VersionsView | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [rerunNotice, setRerunNotice] = useState<string | null>(null);
  const [rerunSucceeded, setRerunSucceeded] = useState(false);
  const [cases, setCases] = useState<CaseSummaryItem[]>([]);
  const [caseId, setCaseId] = useState<string>("");
  const [baseCutoff, setBaseCutoff] = useState<string>("");
  const [compareCutoff, setCompareCutoff] = useState<string>("");

  const loadView = useCallback(
    (id?: string, opts?: { base?: string; compare?: string }) =>
      researchClient.getVersionsView(id, opts).then((v) => {
        setView(v);
        if (!opts?.base) setBaseCutoff(v.beforeSnapshot.cutoff);
        if (!opts?.compare) setCompareCutoff(v.afterSnapshot.cutoff);
        return v;
      }),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      researchClient.listCaseSummaries().catch(() => []),
      loadView(caseId || undefined),
    ])
      .then(([list]) => {
        if (!cancelled) {
          setCases(list);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [loadView, caseId]);

  const runRerun = () => {
    if (!view?.focusThesisId || rerunning) return;
    setRerunning(true);
    setRerunNotice(null);
    setRerunSucceeded(false);
    researchClient
      .rerunThesis(view.focusThesisId)
      .then((result) => {
        setRerunNotice(
          `已冻结新快照 ${result.snapshotId.slice(0, 8)}，结论：${result.conclusion}（临时评估，未经人工复核）`,
        );
        setRerunSucceeded(true);
        return loadView();
      })
      .catch((err: Error) => {
        // 422 = 合规拒绝：AI 文本被拦截，这是状态而不是故障。
        setRerunNotice(`AI RERUN 未完成：${err.message || "未知错误"}`);
      })
      .finally(() => setRerunning(false));
  };

  // Derive the playback list. We reuse snapshotPoints so the UI is a single
  // source of truth; filter out the first cutoff (eventSummary=null) and
  // attach a stable id so React keys are stable across re-renders.
  const playbackSteps: PlaybackStep[] = useMemo(() => {
    if (!view) return [];
    return view.snapshotPoints
      .filter((p) => p.eventSummary !== null)
      .map((p, i) => ({
        id: p.id + ":" + p.cutoff,
        index: i,
        cutoff: p.cutoff,
        linkCount: p.linkCount,
        event: p.eventSummary as PlaybackEvent,
      }));
  }, [view]);

  const [playbackIndex, setPlaybackIndex] = useState(0);
  // Re-sync the cursor when the playback list changes (e.g. user switched
  // case or modified the cutoff window).
  useEffect(() => {
    if (playbackSteps.length > 0) {
      setPlaybackIndex((idx) => Math.min(idx, playbackSteps.length - 1));
    } else {
      setPlaybackIndex(0);
    }
  }, [playbackSteps.length]);

  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState<0.5 | 1 | 2>(1);
  // Auto-advance one step every (1500 / speed) ms while playing.
  useEffect(() => {
    if (!isPlaying) return;
    if (playbackSteps.length === 0) return;
    const id = window.setInterval(() => {
      setPlaybackIndex((i) => {
        if (i >= playbackSteps.length - 1) {
          setIsPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, Math.round(1500 / playbackSpeed));
    return () => window.clearInterval(id);
  }, [isPlaying, playbackSpeed, playbackSteps.length]);

  // Sync the timeline cursor to the current playback step. We treat the
  // active step's cutoff as the "current" compare cutoff so the rest of the
  // page (主对比区 / 多命题概览表) reflects the playhead.
  useEffect(() => {
    if (playbackSteps.length === 0) return;
    const cur = playbackSteps[Math.min(playbackIndex, playbackSteps.length - 1)];
    if (cur && cur.cutoff !== compareCutoff) {
      setCompareCutoff(cur.cutoff);
      loadView(caseId || undefined, { base: baseCutoff, compare: cur.cutoff });
    }
  }, [playbackIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  const onTogglePlay = useCallback(() => {
    setIsPlaying((p) => {
      if (!p && playbackSteps.length - 1 <= playbackIndex) {
        // Re-start from beginning when play is hit at the tail.
        setPlaybackIndex(0);
      }
      return !p;
    });
  }, [playbackSteps.length, playbackIndex]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="versions-loading">
        <p>正在加载版本比较…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="versions-error">
        <div className="form-error">
          版本比较加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen" data-testid="versions-screen">
      <header>
        <div className="eyebrow">监测与更新 · Versions</div>
        <h1>快照版本比较</h1>
        <p className="lede">
          案例{" "}
          <Link to={`/cases/${view.case.id}`}>
            <code>{view.case.id}</code>
          </Link>{" "}
          · {view.case.title}
        </p>
        {cases.length > 1 ? (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              marginTop: 8,
              fontSize: 12,
            }}
          >
            <label htmlFor="versions-case">切换案例：</label>
            <select
              id="versions-case"
              value={caseId}
              onChange={(e) => setCaseId(e.target.value)}
            >
              {cases.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title} · {c.topic}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {view.availableCutoffs.length > 0 ? (
          <div
            data-testid="version-snapshot-pickers"
            style={{
              display: "flex",
              gap: 16,
              alignItems: "center",
              marginTop: 8,
              fontSize: 12,
              flexWrap: "wrap",
            }}
          >
            <label>
              基准快照：&nbsp;
              <select
                value={baseCutoff}
                onChange={(e) => {
                  const newBase = e.target.value;
                  setBaseCutoff(newBase);
                  loadView(caseId || undefined, {
                    base: newBase,
                    compare: compareCutoff,
                  });
                }}
              >
                <option value="1970-01-01T00:00:00Z">（最早 / 起点）</option>
                {view.availableCutoffs.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <span>→</span>
            <label>
              当前快照：&nbsp;
              <select
                value={compareCutoff}
                onChange={(e) => {
                  const newCompare = e.target.value;
                  setCompareCutoff(newCompare);
                  loadView(caseId || undefined, {
                    base: baseCutoff,
                    compare: newCompare,
                  });
                }}
              >
                {view.availableCutoffs.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <span style={{ color: "var(--ink-muted)" }}>
              （共 {view.availableCutoffs.length} 个快照点可对比）
            </span>
          </div>
        ) : null}
      </header>

      {view.snapshotPoints.length > 0 ? (
        <section data-testid="snapshot-timeline" style={{ marginBottom: 24 }}>
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">快照时间轴</p>
              <h2>本案例的全部证据关系增长轨迹</h2>
              <p style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                节点大小 ∝ 该快照点处的证据关系数（已审核 + AI 提议合计）。
                点击节点可切换「当前快照」；橙色填充 = 当前 compareCutoff；
                蓝色填充 = 当前 baseCutoff；白色填充 = 其他快照。
              </p>
            </div>
          </div>
          <SnapshotTimeline
            points={view.snapshotPoints}
            baseCutoff={baseCutoff}
            compareCutoff={compareCutoff}
            onPickCompare={(c) => {
              setCompareCutoff(c);
              loadView(caseId || undefined, { base: baseCutoff, compare: c });
            }}
            onPickBase={(c) => {
              setBaseCutoff(c);
              loadView(caseId || undefined, { base: c, compare: compareCutoff });
            }}
          />
        </section>
      ) : null}

      {playbackSteps.length > 0 ? (
        <section
          data-testid="playback-mode"
          style={{ marginBottom: 24, padding: 12, background: "var(--surface-subtle, #fafafa)", borderRadius: 6 }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 16,
              marginBottom: 8,
              flexWrap: "wrap",
            }}
          >
            <strong>回放模式</strong>
            <button
              type="button"
              data-testid="playback-toggle"
              onClick={onTogglePlay}
              className="prototype-button primary"
              disabled={playbackSteps.length === 0}
            >
              {isPlaying ? "⏸ 暂停" : "▶ 播放"}
            </button>
            <label style={{ fontSize: 12 }}>
              速度：
              <select
                data-testid="playback-speed"
                value={playbackSpeed}
                onChange={(e) =>
                  setPlaybackSpeed(parseFloat(e.target.value) as 0.5 | 1 | 2)
                }
              >
                <option value={0.5}>0.5x</option>
                <option value={1}>1x</option>
                <option value={2}>2x</option>
              </select>
            </label>
            <button
              type="button"
              data-testid="playback-prev"
              onClick={() =>
                setPlaybackIndex((i) => Math.max(0, i - 1))
              }
              disabled={playbackIndex === 0}
            >
              ◀ 上一帧
            </button>
            <button
              type="button"
              data-testid="playback-next"
              onClick={() =>
                setPlaybackIndex((i) =>
                  Math.min(playbackSteps.length - 1, i + 1),
                )
              }
              disabled={playbackIndex >= playbackSteps.length - 1}
            >
              下一帧 ▶
            </button>
            <span style={{ fontSize: 12, color: "var(--ink-muted)" }}>
              第 {playbackIndex + 1} / {playbackSteps.length} 步
            </span>
          </div>
          <div
            style={{
              height: 6,
              background: "var(--surface, #eee)",
              borderRadius: 3,
              overflow: "hidden",
              marginBottom: 12,
            }}
          >
            <div
              data-testid="playback-progress"
              style={{
                width: `${
                  playbackSteps.length === 0
                    ? 0
                    : ((playbackIndex + 1) / playbackSteps.length) * 100
                }%`,
                height: "100%",
                background: "var(--accent, #f59e0b)",
                transition: "width 200ms ease",
              }}
            />
          </div>
          {(() => {
            const cur = playbackSteps[Math.min(playbackIndex, playbackSteps.length - 1)];
            if (!cur) return null;
            return (
              <div
                data-testid="playback-event-card"
                style={{
                  background: "var(--surface, #fff)",
                  border: "1px solid var(--ink-muted, #ccc)",
                  borderRadius: 4,
                  padding: 12,
                }}
              >
                <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                  {cur.cutoff.replace("T", " ")} · 总关系 {cur.linkCount}
                </div>
                <div style={{ marginTop: 4, fontSize: 13 }}>
                  增量{" "}
                  <strong style={{ color: "var(--positive)" }}>
                    +{cur.event.linkDelta}
                  </strong>{" "}
                  关系
                  {cur.event.removedLinkDelta > 0 ? (
                    <span>
                      {" / "}移除{" "}
                      <strong style={{ color: "var(--negative)" }}>
                        −{cur.event.removedLinkDelta}
                      </strong>
                    </span>
                  ) : null}
                  {cur.event.reviewedDelta > 0 ? (
                    <span>
                      {" / "}复核{" "}
                      <strong style={{ color: "var(--info, #3b82f6)" }}>
                        +{cur.event.reviewedDelta}
                      </strong>
                    </span>
                  ) : null}
                </div>
                {cur.event.conclusionFlips.length > 0 ? (
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12 }}>
                    {cur.event.conclusionFlips.map((f, i) => (
                      <li key={i} data-testid="playback-flip">
                        <span className={`state-badge ${badgeFor(f.from)}`}>
                          {conclusionLabel(f.from)}
                        </span>{" "}
                        →{" "}
                        <span className={`state-badge ${badgeFor(f.to)}`}>
                          {conclusionLabel(f.to)}
                        </span>
                        <span style={{ marginLeft: 6, color: "var(--ink-muted)" }}>
                          {f.statement.slice(0, 40)}
                          {f.statement.length > 40 ? "…" : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {Object.keys(cur.event.gapsDelta).length > 0 ? (
                  <div style={{ marginTop: 4, fontSize: 12 }}>
                    缺口变化：
                    {Object.entries(cur.event.gapsDelta).map(([tid, d]) => (
                      <span
                        key={tid}
                        style={{
                          marginLeft: 8,
                          color: d < 0 ? "var(--positive)" : "var(--negative)",
                        }}
                      >
                        {tid.slice(0, 8)} {d > 0 ? `+${d}` : d}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })()}
        </section>
      ) : null}

      <section>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">快照</p>
            <h2>
              {view.beforeSnapshot.id} ({view.beforeSnapshot.cutoff}) →{" "}
              {view.afterSnapshot.id} ({view.afterSnapshot.cutoff})
            </h2>
          </div>
          <span className="state-badge reviewed">
            冻结于 {view.afterSnapshot.freezeTime}
          </span>
        </div>

        <div className="prototype-version-compare">
          {renderColumn(
            `上一季度 · ${view.beforeSnapshot.id}`,
            view.before,
            "before",
          )}
          <aside className="change-rail" aria-label="变更说明">
            <p className="section-kicker">变更记录</p>
            <h3>从 {view.beforeSnapshot.cutoff} 到 {view.afterSnapshot.cutoff}</h3>
            <ul className="prototype-version-record-list compact">
              <li>
                <strong>输入</strong>
                <span>{view.changeRail.inputSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>关系</strong>
                <span>{view.changeRail.relationshipSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>因素</strong>
                <span>{view.changeRail.factorSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>结论</strong>
                <span>{view.changeRail.conclusionSummary}</span>
                <em></em>
              </li>
              <li>
                <strong>缺口</strong>
                <span>{view.changeRail.gapSummary}</span>
                <em></em>
              </li>
            </ul>
            <p style={{ fontSize: 12, marginTop: 8 }}>
              {view.changeRail.rationale}
            </p>
            <p style={{ fontSize: 11, color: "var(--ink-muted)" }}>
              审核人：{view.changeRail.reviewedBy} · {view.changeRail.reviewedAt}
            </p>
            <div className="prototype-version-ai-proposal">
              <strong>AI 提议（未经人工复核）</strong>
              <p style={{ fontSize: 12 }}>{view.aiProposal.text}</p>
              <small>
                Run {view.aiProposal.runId} · {view.aiProposal.observedAt}
              </small>
              <p style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                边界：{view.aiProposal.boundary}
              </p>
              <button
                type="button"
                className="prototype-button primary"
                disabled={rerunning || !view.focusThesisId}
                onClick={runRerun}
                data-testid="ai-rerun-button"
              >
                {rerunning ? "RERUN 执行中…" : "AI RERUN（冻结新快照）"}
              </button>
              {rerunNotice ? (
                <p style={{ fontSize: 11, marginTop: 6 }} role="status">
                  {rerunNotice}
                  {rerunSucceeded ? (
                    <>
                      {" "}
                      <Link to={`/cases/${view.case.id}`}>
                        回案例工作台查看 →
                      </Link>
                    </>
                  ) : null}
                </p>
              ) : null}
            </div>
          </aside>
          {renderColumn(
            `当前快照 · ${view.afterSnapshot.id}`,
            view.after,
            "after",
          )}
        </div>
      </section>

      <section style={{ marginTop: 32 }}>
        <div className="prototype-section-header">
          <div>
            <p className="section-kicker">多命题概览</p>
            <h2>本轮快照内全部命题的 before → after 变化</h2>
          </div>
        </div>
        {view.perThesisChanges.length === 0 ? (
          <p>（本案例当前没有可对比的命题）</p>
        ) : (
          <table
            className="prototype-table"
            data-testid="version-thesis-changes"
            style={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: 6 }}>命题</th>
                <th style={{ textAlign: "left", padding: 6 }}>结论 before</th>
                <th style={{ textAlign: "left", padding: 6 }}>结论 after</th>
                <th style={{ textAlign: "right", padding: 6 }}>缺口 before</th>
                <th style={{ textAlign: "right", padding: 6 }}>缺口 after</th>
                <th style={{ textAlign: "right", padding: 6 }}>新增关系</th>
                <th style={{ textAlign: "right", padding: 6 }}>移除关系</th>
              </tr>
            </thead>
            <tbody>
              {view.perThesisChanges.map((t) => (
                <tr key={t.thesisId} data-testid="version-thesis-row">
                  <td style={{ padding: 6, maxWidth: 320 }}>
                    <Link
                      to={`/cases/${view.case.id}?thesis_id=${t.thesisId}`}
                      style={{ fontSize: 12, color: "var(--accent)" }}
                    >
                      {t.statement}
                    </Link>
                  </td>
                  <td style={{ padding: 6 }}>
                    {t.conclusionBefore ? (
                      <span className={`state-badge ${badgeFor(t.conclusionBefore)}`}>
                        {conclusionLabel(t.conclusionBefore)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--ink-muted)", fontSize: 12 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: 6 }}>
                    {t.conclusionAfter ? (
                      <span className={`state-badge ${badgeFor(t.conclusionAfter)}`}>
                        {conclusionLabel(t.conclusionAfter)}
                      </span>
                    ) : (
                      <span style={{ color: "var(--ink-muted)", fontSize: 12 }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: 6, textAlign: "right" }}>{t.gapsBeforeCount}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>{t.gapsAfterCount}</td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    <strong style={{ color: "var(--positive)" }}>+{t.addedLinks}</strong>
                  </td>
                  <td style={{ padding: 6, textAlign: "right" }}>
                    {t.removedLinks > 0 ? (
                      <strong style={{ color: "var(--negative)" }}>−{t.removedLinks}</strong>
                    ) : (
                      <span style={{ color: "var(--ink-muted)" }}>0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}