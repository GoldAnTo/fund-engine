import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import { PageHeader } from "../../components/prototype/PageHeader";
import { PaperCard } from "../../components/prototype/PaperCard";
import { StatusBadge } from "../../components/prototype/StatusBadge";
import type {
  ThemeIndexEntry,
  ThemeIndexView,
  ThemeStatus,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const STATUS_LABEL: Record<ThemeStatus, string> = {
  monitoring: "监测中",
  validating: "持续验证",
  frozen: "已冻结",
  draft: "草稿",
};

export function ThemeIndexScreen() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<ThemeIndexView | null>(null);
  const [industry, setIndustry] = useState<string>("all");
  const [status, setStatus] = useState<ThemeStatus | "all">("all");
  const [query, setQuery] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getThemeIndexView()
      .then((v) => {
        if (!cancelled) {
          setView(v);
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

  const filtered = useMemo<ThemeIndexEntry[]>(() => {
    if (!view) return [];
    return view.themes.filter((t) => {
      if (industry !== "all" && t.industry !== industry) return false;
      if (status !== "all" && t.status !== status) return false;
      if (
        query &&
        !`${t.name}${t.industry}${t.hypothesis}`
          .toLowerCase()
          .includes(query.toLowerCase())
      ) {
        return false;
      }
      return true;
    });
  }, [view, industry, status, query]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="theme-index-loading">
        <p>正在加载主题列表…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="theme-index-error">
        <div className="form-error">
          主题列表加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen theme-index" data-testid="theme-index-screen">
      <PageHeader
        title="主题"
        eyebrow="主题驱动 · Theme-driven"
        lede="从这里开始：先选一个你相信的事情，所有证据（Claim）和穿透（股票 / 基金）都挂在主题下。"
        actions={
          <Link to="/new-research" className="prototype-button primary">
            ＋ 新建主题
          </Link>
        }
      />

      <section className="theme-totals">
        <div className="theme-totals__cell">
          <span>主题</span>
          <strong>{view.totals.themes}</strong>
        </div>
        <div className="theme-totals__cell">
          <span>持续验证</span>
          <strong>{view.totals.validating}</strong>
        </div>
        <div className="theme-totals__cell">
          <span>已冻结</span>
          <strong>{view.totals.frozen}</strong>
        </div>
        <div className="theme-totals__cell theme-totals__cell--warn">
          <span>矛盾对</span>
          <strong>{view.totals.conflictPairs}</strong>
        </div>
      </section>

      <section className="theme-filters" aria-label="主题筛选">
        <label>
          <span>行业</span>
          <select
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            aria-label="按行业筛选"
          >
            <option value="all">全部</option>
            {view.filters.industries.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>状态</span>
          <select
            value={status}
            onChange={(e) =>
              setStatus(e.target.value as ThemeStatus | "all")
            }
            aria-label="按状态筛选"
          >
            <option value="all">全部</option>
            <option value="monitoring">监测中</option>
            <option value="validating">持续验证</option>
            <option value="frozen">已冻结</option>
            <option value="draft">草稿</option>
          </select>
        </label>
        <label className="theme-filters__search">
          <span>搜索</span>
          <input
            type="search"
            placeholder="主题名 / 行业 / 假设"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <Link to="/new-research" className="prototype-button primary">
          ＋ 新建主题
        </Link>
      </section>

      <ul className="theme-list" role="list">
        {filtered.map((theme) => (
          <li key={theme.id}>
            <Link to={`/themes/${theme.id}`} className="theme-card">
              <header>
                <StatusBadge variant={theme.status}>
                  {theme.statusLabel}
                </StatusBadge>
                <span className="theme-card__industry">{theme.industry}</span>
              </header>
              <h3>{theme.name}</h3>
              <p className="theme-card__hypothesis">{theme.hypothesis}</p>
              <footer>
                <span>
                  <strong>{theme.claimCount}</strong> 条证据
                </span>
                <span className={theme.conflictCount > 0 ? "muted-warn" : ""}>
                  {theme.conflictCount} 矛盾
                </span>
                <span>更新 {theme.lastUpdatedAt}</span>
              </footer>
            </Link>
          </li>
        ))}
        {filtered.length === 0 && (
          <li className="theme-list__empty">没有匹配的主题。</li>
        )}
      </ul>
    </div>
  );
}