import { useEffect, useState } from "react";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import type {
  OverviewEvidenceChange,
  OverviewFrameworkNode,
  OverviewKeyChange,
  OverviewTab,
  OverviewTask,
  OverviewActivity,
  WorkspaceOverviewScreen,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const TAG_VARIANT: Record<OverviewKeyChange["tag"], string> = {
  新增: "tag tag-add",
  更新: "tag tag-update",
  风险: "tag tag-risk",
};

const CATEGORY_LABEL: Record<OverviewTask["category"], string> = {
  待审核: "待审核",
  进行中: "进行中",
  等待中: "等待",
  主要阻塞: "主要阻塞",
};

function formatDate(value: string): string {
  if (!value) return value;
  // Keep the prototype date strings; they're already short.
  return value;
}

export function OverviewScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<WorkspaceOverviewScreen | null>(null);
  const [activeTab, setActiveTab] = useState<string>("summary");
  const [frameworkExpanded, setFrameworkExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getWorkspaceOverviewScreen()
      .then((v: WorkspaceOverviewScreen) => {
        if (!cancelled) {
          setView(v);
          const expanded: Record<string, boolean> = {};
          v.framework.forEach((n) => {
            expanded[n.id] = n.expanded;
          });
          setFrameworkExpanded(expanded);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="overview-loading">
        <p>正在加载研究总览…</p>
      </div>
    );
  }

  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="overview-error">
        <div className="form-error">研究总览加载失败：{state.message ?? "未知错误"}</div>
      </div>
    );
  }

  const tabs: OverviewTab[] = view.tabs.map((t) => ({
    ...t,
    active: t.id === activeTab,
  }));

  // Group tasks by category for the queue column.
  const tasksByCategory = view.taskQueue.reduce<Record<string, OverviewTask[]>>(
    (acc, task) => {
      (acc[task.category] ??= []).push(task);
      return acc;
    },
    {},
  );

  // Group evidence changes by recency bucket.
  const evidenceBuckets = view.evidenceChanges.reduce<Record<string, OverviewEvidenceChange[]>>(
    (acc, item) => {
      const bucket = item.updatedAt === "昨天" || item.updatedAt.startsWith("05-")
        ? item.updatedAt
        : "今天";
      (acc[bucket] ??= []).push(item);
      return acc;
    },
    {},
  );

  const activityGroups = view.activity.reduce<Record<string, OverviewActivity[]>>(
    (acc, item) => {
      (acc[item.group] ??= []).push(item);
      return acc;
    },
    {},
  );

  return (
    <div className="prototype-screen workspace-overview" data-testid="overview-screen">
      <PageHeader
        title={view.caseTitle}
        eyebrow="研究总览 · Workspace Overview"
        lede={
          <>
            全局视角地理解研究进展与关键变化。
            <span className="updated-at">
              最后更新 {formatDate(view.lastUpdatedAt)}
            </span>
          </>
        }
        actions={
          <>
            <button type="button" className="prototype-button">↗ 分享</button>
            <button type="button" className="prototype-button quiet">···</button>
            <button type="button" className="prototype-button quiet">☆</button>
          </>
        }
      />

      <nav className="workspace-overview__tabs" aria-label="研究总览分类">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`workspace-tab${tab.active ? " is-active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
            aria-pressed={tab.active ?? false}
          >
            <span className="workspace-tab__label">{tab.label}</span>
            <span className="workspace-tab__count">{tab.count}</span>
          </button>
        ))}
      </nav>

      <section className="workspace-overview__body">
        <div className="workspace-column workspace-column--main">
          <article className="prototype-paper workspace-block">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">核心结论</p>
                <h2>关键判断</h2>
              </div>
              <button type="button" className="link-button">
                展开全部 ▾
              </button>
            </div>
            <ul className="workspace-bullets">
              {view.bullets.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </article>

          <article className="prototype-paper workspace-block">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">关键变化（近 7 天）</p>
                <h2>变化总览</h2>
              </div>
            </div>
            <ul className="workspace-key-changes">
              {view.keyChanges.map((kc) => (
                <li key={kc.id} className="workspace-key-change">
                  <span className={TAG_VARIANT[kc.tag]}>{kc.tag}</span>
                  <div>
                    <p className="workspace-key-change__text">{kc.text}</p>
                    <p className="workspace-key-change__detail">{kc.detail}</p>
                    <small>
                      {kc.occurredAt} · 来源：{kc.sourceLabel}
                    </small>
                  </div>
                </li>
              ))}
            </ul>
          </article>

          <article className="prototype-paper workspace-block">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">研究框架</p>
                <h2>研究主线</h2>
              </div>
            </div>
            <ul className="workspace-framework">
              {view.framework.map((node) => (
                <FrameworkItem
                  key={node.id}
                  node={node}
                  expanded={frameworkExpanded[node.id] ?? false}
                  onToggle={() =>
                    setFrameworkExpanded((prev) => ({
                      ...prev,
                      [node.id]: !prev[node.id],
                    }))
                  }
                />
              ))}
            </ul>
          </article>
        </div>

        <div className="workspace-column workspace-column--right">
          <article className="prototype-paper workspace-block workspace-block--queue">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">任务队列（示例 · 非目标范围）</p>
                <h2>边研究 · 边处理</h2>
              </div>
              <button type="button" className="link-button">＋ 新建任务</button>
            </div>
            {(["待审核", "进行中", "等待中", "主要阻塞"] as const).map(
              (cat) =>
                tasksByCategory[cat] ? (
                  <section
                    key={cat}
                    className={`workspace-task-group task-group--${cat}`}
                  >
                    <header>
                      <span className="task-group-label">{cat}</span>
                      <span className="task-group-count">
                        {tasksByCategory[cat].length}
                      </span>
                    </header>
                    <ul>
                      {tasksByCategory[cat].map((t) => (
                        <li key={t.id} className="workspace-task">
                          <strong>{t.title}</strong>
                          <small>
                            来源：{t.source} · {t.updatedAt}
                          </small>
                          <span className="workspace-task__assignee" aria-hidden>
                            {t.assignee.slice(0, 1)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null,
            )}
            <button type="button" className="link-button">
              查看全部任务 →
            </button>
          </article>

          <article className="prototype-paper workspace-block">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">证据变化（示例 · 非目标范围）</p>
                <h2>{view.evidenceChanges.length} 条</h2>
              </div>
              <button type="button" className="link-button">全部 ▾</button>
            </div>
            {Object.entries(evidenceBuckets).map(([bucket, items]) => (
              <section key={bucket} className="workspace-evidence-bucket">
                <header>
                  <span>{bucket}</span>
                </header>
                <ul>
                  {items.map((item) => (
                    <li key={item.id}>
                      <span className={`kind kind--${item.kind}`}>●</span>
                      <div>
                        <p>
                          <strong>{item.description}</strong>
                        </p>
                        <small>
                          {item.caseTitle} · {item.source}
                        </small>
                      </div>
                      <span className="updated-at small">{item.updatedAt}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <button type="button" className="link-button">
              查看全部变化 →
            </button>
          </article>

          <article className="prototype-paper workspace-block">
            <div className="prototype-section-header">
              <div>
                <p className="section-kicker">活动（示例 · 非目标范围）</p>
                <h2>研究日志</h2>
              </div>
              <button type="button" className="link-button">全部 ▾</button>
            </div>
            {Object.entries(activityGroups).map(([group, items]) => (
              <section key={group} className="workspace-activity-group">
                <header>
                  <span>{group}</span>
                </header>
                <ul>
                  {items.map((act) => (
                    <li key={act.id}>
                      <span className="workspace-activity__actor" aria-hidden>
                        {act.actor.slice(0, 1)}
                      </span>
                      <div>
                        <p>
                          <strong>{act.actor}</strong>{" "}
                          <span className="muted">{act.verb}</span>{" "}
                          <span>{act.target}</span>
                        </p>
                        <small>{act.occurredAt}</small>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            <button type="button" className="link-button">
              查看全部活动 →
            </button>
          </article>
        </div>
      </section>

      <footer className="workspace-overview__totals">
        <span>
          证据总数 <strong>{view.totals.evidenceTotal.toLocaleString()}</strong>
        </span>
        <span>
          可靠证据{" "}
          <strong>
            {view.totals.reliablePct === null
              ? "—"
              : `${Math.round(
                  (view.totals.reliablePct / 100) * view.totals.evidenceTotal,
                ).toLocaleString()} (${view.totals.reliablePct}%)`}
          </strong>
        </span>
        <span>
          待审核 <strong>{view.totals.pendingReview}</strong>
        </span>
        <span>
          主要阻塞 <strong>{view.totals.majorBlockers}</strong>
          <span className="blocker-dot" aria-hidden>●</span>
        </span>
      </footer>
    </div>
  );
}

function FrameworkItem({
  node,
  expanded,
  onToggle,
}: {
  node: OverviewFrameworkNode;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <li className="workspace-framework-item">
      <button
        type="button"
        className="workspace-framework-item__head"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className={`caret${expanded ? " is-open" : ""}`} aria-hidden>
          ▸
        </span>
        <span className="sequence">{node.sequence}</span>
        <strong>{node.title}</strong>
        <small>{node.description}</small>
      </button>
      {expanded && node.children.length > 0 && (
        <ul className="workspace-framework-item__children">
          {node.children.map((c) => (
            <li key={c.id}>
              <span className="sequence">{c.sequence}</span>
              {c.title}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}