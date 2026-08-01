import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  group: "primary" | "industry" | "knowledge";
}

const NAV_ITEMS: NavItem[] = [
  { to: "/themes", label: "主题", icon: "⏚", group: "primary" },
  { to: "/workspace", label: "工作台", icon: "⌂", group: "primary" },
  { to: "/new-research", label: "新建研究", icon: "⌬", group: "primary" },
  { to: "/relationships", label: "证据图谱", icon: "⧉", group: "primary" },
  { to: "/plan", label: "研究计划", icon: "▤", group: "industry" },
  { to: "/library", label: "资料与知识", icon: "▦", group: "industry" },
  { to: "/data", label: "数据中心", icon: "⌖", group: "knowledge" },
  { to: "/review", label: "审核中心", icon: "✓", group: "knowledge" },
  { to: "/versions", label: "监测与更新", icon: "↻", group: "knowledge" },
];

const SHELL_CONTEXT: Record<string, [string, string]> = {
  themes: ["主题驱动", "主题列表"],
  workspace: ["研究工作台", "研究总览"],
  "new-research": ["主题驱动", "新建主题"],
  relationships: ["主题驱动", "证据图谱"],
  plan: ["主题驱动", "研究计划"],
  library: ["资料与知识", "来源资料"],
  data: ["数据中心", "时点数据"],
  review: ["审核中心", "关系审核"],
  versions: ["监测与更新", "版本比较"],
};

interface ActivePath {
  primary: string;
  label: string;
  module: string;
  page: string;
}

function resolveActive(pathname: string): ActivePath {
  if (pathname.startsWith("/themes")) return { primary: "/themes", label: "主题", module: "主题驱动", page: "主题列表" };
  if (pathname.startsWith("/workspace")) return { primary: "/workspace", label: "工作台", module: "研究工作台", page: "研究总览" };
  if (pathname.startsWith("/new-research")) return { primary: "/new-research", label: "新建研究", module: "主题驱动", page: "新建主题" };
  if (pathname.startsWith("/plan")) return { primary: "/plan", label: "研究计划", module: "主题驱动", page: "研究计划" };
  if (pathname.startsWith("/relationships")) return { primary: "/relationships", label: "证据图谱", module: "主题驱动", page: "证据图谱" };
  if (pathname.startsWith("/library")) return { primary: "/library", label: "资料与知识", module: "资料与知识", page: "来源资料" };
  if (pathname.startsWith("/data")) return { primary: "/data", label: "数据中心", module: "数据中心", page: "时点数据" };
  if (pathname.startsWith("/review")) return { primary: "/review", label: "审核中心", module: "审核中心", page: "关系审核" };
  if (pathname.startsWith("/versions")) return { primary: "/versions", label: "监测与更新", module: "监测与更新", page: "版本比较" };
  return { primary: "/themes", label: "主题", module: "主题驱动", page: "主题列表" };
}

export interface PrototypeShellProps {
  children?: ReactNode;
  /** Default cutoff for the historical cutoff chip when not provided in URL. */
  defaultCutoff?: string;
}

/**
 * Prototype-aligned app shell: warm-paper left nav, breadcrumbs, search, cutoff pill.
 * Designed to mirror prototype/ui/app.js shell structure while staying inside
 * the React Router layout route that hosts all prototype pages.
 */
export function PrototypeShell({ defaultCutoff = "2025-06-30" }: PrototypeShellProps) {
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  const cutoff = search.get("cutoff") ?? defaultCutoff;
  const active = resolveActive(location.pathname);

  function updateCutoff(next: string | null) {
    const sp = new URLSearchParams(search);
    if (next) sp.set("cutoff", next);
    else sp.delete("cutoff");
    setSearch(sp, { replace: true });
  }

  // Tiny helper used by the inline search box — keeps the shell self-contained.
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<string[]>([]);
  useEffect(() => {
    if (!query.trim()) {
      setHits([]);
      return;
    }
    const lowered = query.toLowerCase();
    const titles = [
      "AI 算力产业链研究",
      "NVIDIA 数据中心业务收入",
      "TSMC 月度营收同比增幅",
      "示例算力基础设施 ETF",
      "审核关系：EL-004 容量约束",
      "证据关系：EL-001 资本开支支持",
      "研究案例 RC-AIC-2025-01",
    ];
    setHits(titles.filter((t) => t.toLowerCase().includes(lowered)).slice(0, 5));
  }, [query]);

  return (
    <div className="app-shell insight-os-shell">
      <aside className="nav-rail insight-sidebar" aria-label="主导航">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◇</span>
          <div>
            <strong>洞见研究 OS</strong>
            <small>行业研究工作系统</small>
          </div>
        </div>
        <nav className="nav-list" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `nav-link${isActive || active.primary === item.to ? " is-active" : ""}${item.group !== "primary" ? " nav-link--nested" : ""}`
              }
            >
              <span className="nav-icon" aria-hidden>{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-context">
          <span>当前研究</span>
          <strong>AI 算力产业链</strong>
          <small>冻结快照 RS-2025-06-30-v3</small>
        </div>
      </aside>

      <header className="utility-header insight-topbar">
        <div className="breadcrumbs" aria-label="当前位置">
          <span>{active.module}</span>
          <span aria-hidden>/</span>
          <strong>{active.page}</strong>
        </div>
        <div className="insight-search" role="search">
          <span aria-hidden>⌕</span>
          <input
            type="search"
            aria-label="搜索研究、命题和证据"
            placeholder="搜索研究、命题、证据…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && hits.length > 0 && (
            <ul className="insight-search__hits" role="listbox">
              {hits.map((h) => (
                <li key={h} role="option">
                  <span>{h}</span>
                  <small>跳转到对应模块</small>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="utility-actions">
          <span className="utility-pill insight-history-cutoff" title="历史截点（cutoff）">
            <span aria-hidden>◷</span>
            <label className="visually-hidden" htmlFor="cutoff-input">历史截点</label>
            <input
              id="cutoff-input"
              type="date"
              value={cutoff}
              onChange={(e) => updateCutoff(e.target.value || null)}
            />
            <span className="utility-pill__caption">历史截点 {cutoff}</span>
          </span>
          <button className="insight-user-entry" type="button" aria-label="研究员账户">
            <span aria-hidden>研</span>
            <span>研究员</span>
          </button>
        </div>
      </header>

      <details className="mobile-nav">
        <summary>导航</summary>
        <nav className="mobile-nav-links" aria-label="移动端主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `nav-link${isActive || active.primary === item.to ? " is-active" : ""}`
              }
            >
              <span className="nav-icon" aria-hidden>{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
      </details>

      <div className="work-area insight-workspace">
        <Outlet />
      </div>

      <nav className="prototype-bottom-nav" aria-label="底部快捷导航">
        <Link to="/review" className="prototype-bottom-nav__tile">
          <strong>审核中心</strong>
          <small>2 项等待</small>
        </Link>
        <Link to="/versions" className="prototype-bottom-nav__tile">
          <strong>监测与更新</strong>
          <small>6 条事件</small>
        </Link>
        <Link to="/library" className="prototype-bottom-nav__tile">
          <strong>资料库</strong>
          <small>1,243 份</small>
        </Link>
        <Link to="/data" className="prototype-bottom-nav__tile">
          <strong>数据中心</strong>
          <small>32 个指标</small>
        </Link>
      </nav>

      <nav className="prototype-personal-area" aria-label="我的工作区">
        <span className="prototype-personal-area__head">我的工作区</span>
        <Link to="/themes?owner=me" className="prototype-personal-area__item">
          <span>我的项目</span>
          <small>8</small>
        </Link>
        <Link to="/themes?owner=me&status=validating" className="prototype-personal-area__item">
          <span>我的观点</span>
          <small>5</small>
        </Link>
        <Link to="/themes?owner=me&view=charts" className="prototype-personal-area__item">
          <span>我的图表</span>
          <small>12</small>
        </Link>
        <Link to="/themes?owner=me&view=data" className="prototype-personal-area__item">
          <span>我的数据</span>
          <small>9</small>
        </Link>
        <Link to="/themes?status=draft" className="prototype-personal-area__item">
          <span>回收站</span>
          <small>3</small>
        </Link>
      </nav>
    </div>
  );
}

/**
 * Shared shell context accessor — pages can read module/page labels without
 * re-importing the SHELL_CONTEXT map.
 */
export function useShellContext(): { module: string; page: string } {
  const location = useLocation();
  const active = resolveActive(location.pathname);
  return { module: active.module, page: active.page };
}