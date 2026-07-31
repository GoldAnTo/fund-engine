import { Link } from "react-router-dom";
import type { WorkspaceOverview as Overview } from "../domain/types";
import { StatusMark } from "./StatusMark";
import { Chip } from "./primitives/Chip";
import { BulletList } from "./primitives/BulletList";
import { Avatar } from "./primitives/Avatar";
import { Button } from "./primitives/Button";

interface Props {
  data: Overview;
}

export function WorkspaceOverview({ data }: Props) {
  return (
    <div className="workspace-overview" data-testid="workspace-overview">
      <header className="workspace-overview__title">
        <h1>研究总览</h1>
        <p className="muted">全局视角跟踪研究进展与关键变化</p>
      </header>

      <div className="workspace-overview__layout">
        <article className="workspace-overview__main">
          <section className="case-summary">
            <header>
              <h2>
                <Link to={`/cases/${data.case_id}`}>{data.case_title}</Link>
              </h2>
              <span className="case-summary__chips">
                {data.case_topic_tags.map((t) => (
                  <Chip key={t} tone="neutral" size="xs">
                    {t}
                  </Chip>
                ))}
                <Chip tone="amber" bordered size="xs">
                  AI 提议
                </Chip>
              </span>
              <span className="muted">
                最后更新 {data.last_updated_at}
              </span>
              <div className="case-summary__actions">
                <Button variant="ghost" size="sm" type="button">↗ 分享</Button>
                <Button variant="bare" size="xs" type="button" aria-label="更多">···</Button>
                <Button variant="bare" size="xs" type="button" aria-label="收藏">☆</Button>
              </div>
            </header>

            <nav className="case-tabs" aria-label="案例内导航">
              <a href="#summary" className="case-tab is-active">研究摘要</a>
              <a href="#charts" className="case-tab">关键图表 32</a>
              <a href="#views" className="case-tab">核心观点 18</a>
              <a href="#risks" className="case-tab">风险与假设 12</a>
              <a href="#companies" className="case-tab">相关公司 48</a>
              <a href="#log" className="case-tab">研究日志</a>
            </nav>

            <section className="summary-block">
              <h3>核心结论</h3>
              <BulletList items={data.bullets} />
            </section>

            <section
              className="summary-block"
              aria-labelledby="key-changes"
            >
              <header className="row">
                <h3 id="key-changes">关键变化（近 7 天）</h3>
                <a href="#changes-all" className="muted">
                  展开全部 +
                </a>
              </header>
              <ul className="key-changes">
                {data.key_changes.map((c) => (
                  <li
                    key={c.id}
                    data-tag={c.tag}
                    data-review={c.review_state ?? undefined}
                  >
                    <span className="key-change__tag" data-tag={c.tag}>
                      {c.tag}
                    </span>
                    <div>
                      <div className="key-change__text">{c.text}</div>
                      <div className="key-change__detail muted">{c.detail}</div>
                      <div className="key-change__meta">
                        <span className="key-change__source">{c.source_label}</span>
                        <span className="dot">·</span>
                        <span>{c.occurred_at}</span>
                        {c.review_state && (
                          <span
                            className="key-change__review"
                            data-review={c.review_state}
                          >
                            {c.review_state === "reviewed"
                              ? "已人工复核"
                              : c.review_state === "machine_generated"
                                ? "AI 提议"
                                : "已拒绝"}
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section className="summary-block">
              <header className="row">
                <h3>研究框架</h3>
                <span className="muted">已更新 3 项证据</span>
              </header>
              <ol className="framework">
                {data.framework.map((node) => (
                  <li key={node.id}>
                    <details open>
                      <summary>
                        <span className="framework__seq">{node.sequence}</span>
                        <span>{node.title}</span>
                      </summary>
                      <ol className="framework__children">
                        {node.children.map((c) => (
                          <li key={c.id}>
                            <span className="framework__seq">{c.sequence}</span>
                            <span>{c.title}</span>
                          </li>
                        ))}
                      </ol>
                    </details>
                  </li>
                ))}
              </ol>
            </section>

            <footer className="case-summary__totals">
              <span className="case-summary__total">
                <span className="case-summary__total__label">证据总数</span>
                <strong>{data.totals.evidence_total.toLocaleString()}</strong>
              </span>
              <span className="case-summary__total">
                <span className="case-summary__total__label">
                  {data.totals.reliable_pct === null
                    ? "尚无人工质量口径"
                    : "可靠证据"}
                </span>
                <strong>
                  {data.totals.reliable_pct === null
                    ? "-"
                    : `${data.totals.reliable_pct}%`}
                </strong>
              </span>
              <span className="case-summary__total">
                <span className="case-summary__total__label">待评级</span>
                <strong>{data.totals.pending_review}</strong>
              </span>
              <span
                className={
                  data.totals.major_blockers > 0
                    ? "case-summary__total case-summary__total--alert"
                    : "case-summary__total"
                }
              >
                <span className="case-summary__total__label">主要阻塞</span>
                <strong>
                  <span className="heart" aria-hidden>❤</span>{" "}
                  {data.totals.major_blockers}
                </strong>
              </span>
            </footer>
          </section>
        </article>

        <aside className="workspace-overview__rail" aria-label="调度栏">
          <section className="rail rail--tasks" aria-labelledby="tasks">
            <header className="rail__header">
              <h3 id="tasks">任务队列</h3>
              <Button variant="chip" size="xs" type="button">+ 新建任务</Button>
            </header>
            {groupByCategory(data.task_queue).map((group) => (
              <div
                key={group.category}
                className={
                  group.category === "主要阻塞"
                    ? "rail__group rail__group--alert"
                    : "rail__group"
                }
              >
                <h4>
                  {group.category}
                  {group.category === "主要阻塞" && (
                    <span className="heart" aria-hidden>❤</span>
                  )}
                  <span className="count">{group.items.length}</span>
                </h4>
                <ul>
                  {group.items.map((t) => (
                    <li key={t.id} className="task-item">
                      <div className="task-item__body">
                        <div className="task-item__title">{t.title}</div>
                        <div className="task-item__meta muted">
                          来源：{t.source} · {t.updated_at}
                        </div>
                      </div>
                      <span className="task-item__avatar">
                        <Avatar name={t.assignee} size={20} />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <a className="rail__more" href="#tasks-all">
              查看全部任务
            </a>
          </section>

          <section className="rail rail--changes" aria-labelledby="changes">
            <header className="rail__header">
              <h3 id="changes">证据变化</h3>
              <Button variant="ghost" size="xs" type="button">全部 ⌄</Button>
            </header>
            {groupByLabel(data.evidence_changes, (i) => bucketize(i.updated_at)).map(
              (group) => (
                <div key={group.label} className="rail__group">
                  <h4>{group.label}</h4>
                  <ul>
                    {group.items.map((c) => (
                      <li key={c.id} className="change-item">
                        <span
                          className={`change-item__dot change-item__dot--${c.kind}`}
                          aria-hidden
                        />
                        <div className="change-item__body">
                          <div className="change-item__title">{c.description}</div>
                          <div className="change-item__meta muted">
                            <span className={`change-pill change-pill--${c.kind}`}>
                              {c.kind === "update" ? "更新" : "新增"}
                            </span>
                            <span>{c.source}</span>
                          </div>
                        </div>
                        <span className="change-item__time muted">{c.updated_at}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            )}
            <a className="rail__more" href="#changes-all">
              查看全部变化
            </a>
          </section>

          <section className="rail rail--activity" aria-labelledby="activity">
            <header className="rail__header">
              <h3 id="activity">活动</h3>
              <Button variant="ghost" size="xs" type="button">全部 ⌄</Button>
            </header>
            {groupActivity(data.activity).map((group) => (
              <div key={group.label} className="rail__group">
                <h4>{group.label}</h4>
                <ul>
                  {group.items.map((a) => (
                    <li key={a.id} className="activity-item">
                      <span className="activity-item__head">
                        <Avatar name={a.actor} size={20} />
                        <span>
                          <strong>{a.actor}</strong> {a.verb} · {a.target}
                        </span>
                      </span>
                      <span className="activity-item__meta muted">
                        {a.occurred_at}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <a className="rail__more" href="#activity-all">
              查看全部活动
            </a>
          </section>
        </aside>
      </div>

      {data.totals.pending_review > 0 && (
        <p className="workspace-overview__notice">
          <StatusMark status="ai_pending_review" />
          AI 临时判断未经人工复核，需要在{" "}
          <Link to="/review">审核队列</Link> 中处理。
        </p>
      )}
    </div>
  );
}

function groupByCategory<T extends { category: string }>(
  items: T[]
): { category: string; items: T[] }[] {
  const map = new Map<string, T[]>();
  for (const it of items) {
    if (!map.has(it.category)) map.set(it.category, []);
    map.get(it.category)!.push(it);
  }
  return Array.from(map.entries()).map(([category, list]) => ({
    category,
    items: list,
  }));
}

function groupByLabel<T extends object, K extends string>(
  items: T[],
  key: (i: T) => K
): { label: K; items: T[] }[] {
  const map = new Map<K, T[]>();
  for (const it of items) {
    const k = key(it);
    if (!map.has(k)) map.set(k, []);
    map.get(k)!.push(it);
  }
  return Array.from(map.entries()).map(([label, list]) => ({ label, items: list }));
}

function groupActivity<T extends { group: string; occurred_at: string }>(
  items: T[]
): { label: string; items: T[] }[] {
  return groupByLabel(items, (i) => i.group).sort(
    (a, b) => sortIndex(b.label) - sortIndex(a.label)
  );
}

function sortIndex(label: string): number {
  if (label === "今天") return 3;
  if (label === "昨天") return 2;
  return 1;
}

function bucketize(at: string): string {
  if (at === "10:15" || at === "09:42" || at === "09:30" || at === "10:24" || at === "09:56") {
    return "今天";
  }
  if (at === "昨天" || at === "17:15" || at === "16:40" || at === "15:33") {
    return "昨天";
  }
  return "更早";
}