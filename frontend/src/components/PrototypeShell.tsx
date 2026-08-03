import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { researchClient } from "../data/researchClient";
import type { SearchHit } from "../domain/types";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  group: "primary" | "industry" | "knowledge";
}

const NAV_ITEMS: NavItem[] = [
  { to: "/themes", label: "主题", icon: "⏚", group: "primary" },
  { to: "/topics", label: "主题研究", icon: "✦", group: "primary" },
  { to: "/workspace", label: "工作台", icon: "⌂", group: "primary" },
  { to: "/new-research", label: "新建研究", icon: "⌬", group: "primary" },
  { to: "/relationships", label: "证据图谱", icon: "⧉", group: "primary" },
  { to: "/companies", label: "公司研究", icon: "◉", group: "primary" },
  { to: "/plan", label: "研究计划", icon: "▤", group: "industry" },
  { to: "/library", label: "资料与知识", icon: "▦", group: "industry" },
  { to: "/data", label: "数据中心", icon: "⌖", group: "knowledge" },
  { to: "/review", label: "审核中心", icon: "✓", group: "knowledge" },
  { to: "/versions", label: "监测与更新", icon: "↻", group: "knowledge" },
];

// 左侧导航分组标题（与设计原型 1/10/11 视觉一致）
const NAV_GROUP_LABELS: Record<"primary" | "industry" | "knowledge", string> = {
  primary: "行业研究",
  industry: "资料与知识",
  knowledge: "数据中心",
};

const SHELL_CONTEXT: Record<string, [string, string]> = {
  themes: ["主题驱动", "主题列表"],
  topics: ["主题研究", "横切主题"],
  workspace: ["研究工作台", "研究总览"],
  "new-research": ["主题驱动", "新建主题"],
  relationships: ["主题驱动", "证据图谱"],
  companies: ["公司研究", "公司深度"],
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
  if (pathname.startsWith("/topics")) return { primary: "/topics", label: "主题研究", module: "主题研究", page: "横切主题" };
  if (pathname.startsWith("/workspace")) return { primary: "/workspace", label: "工作台", module: "研究工作台", page: "研究总览" };
  if (pathname.startsWith("/new-research")) return { primary: "/new-research", label: "新建研究", module: "主题驱动", page: "新建主题" };
  if (pathname.startsWith("/plan")) return { primary: "/plan", label: "研究计划", module: "主题驱动", page: "研究计划" };
  if (pathname.startsWith("/relationships")) return { primary: "/relationships", label: "证据图谱", module: "主题驱动", page: "证据图谱" };
  if (pathname.startsWith("/companies")) return { primary: "/companies", label: "公司研究", module: "公司研究", page: "公司深度" };
  if (pathname.startsWith("/library")) return { primary: "/library", label: "资料与知识", module: "资料与知识", page: "来源资料" };
  if (pathname.startsWith("/data")) return { primary: "/data", label: "数据中心", module: "数据中心", page: "时点数据" };
  if (pathname.startsWith("/review")) return { primary: "/review", label: "审核中心", module: "审核中心", page: "关系审核" };
  if (pathname.startsWith("/versions")) return { primary: "/versions", label: "监测与更新", module: "监测与更新", page: "版本比较" };
  return { primary: "/themes", label: "主题", module: "主题驱动", page: "主题列表" };
}

export interface PrototypeShellProps {
  children?: ReactNode;
}

/**
 * Prototype-aligned app shell: warm-paper left nav, breadcrumbs, live search.
 * Designed to mirror prototype/ui/app.js shell structure while staying inside
 * the React Router layout route that hosts all prototype pages.
 *
 * Non-functional decorations (cutoff pill, personal-area filters, hardcoded
 * tile counts) are intentionally omitted until the backend capabilities
 * exist to back them.
 */
export function PrototypeShell(_props: PrototypeShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const active = resolveActive(location.pathname);

  // Global search: debounced live query against /search; hits navigate to
  // the corresponding frontend route (deep links are already rewritten by
  // the adapter; unmappable types fall back to the library).
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setHits([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      researchClient
        .search(q)
        .then((h) => setHits(h.slice(0, 8)))
        .catch(() => setHits([]))
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  function hitRoute(hit: SearchHit): string | null {
    const to = hit.navigate_to;
    if (
      to.startsWith("/cases") ||
      to.startsWith("/relationships") ||
      to.startsWith("/themes")
    ) {
      return to;
    }
    if (to.startsWith("/documents")) return "/library";
    return null;
  }

  function openHit(hit: SearchHit) {
    const to = hitRoute(hit);
    if (!to) return;
    setQuery("");
    setHits([]);
    navigate(to);
  }

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
          {(["primary", "industry", "knowledge"] as const).map((group) => (
            <div key={group} className="nav-group" data-group={group}>
              <div className="nav-group__title">{NAV_GROUP_LABELS[group]}</div>
              {NAV_ITEMS.filter((item) => item.group === group).map((item) => (
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
            </div>
          ))}
        </nav>
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
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setQuery("");
                setHits([]);
              }
            }}
          />
          {query.trim().length >= 2 && (
            <ul className="insight-search__hits" role="listbox">
              {searching && hits.length === 0 ? (
                <li role="option" aria-disabled>
                  <span>搜索中…</span>
                </li>
              ) : hits.length === 0 ? (
                <li role="option" aria-disabled>
                  <span>无匹配结果</span>
                </li>
              ) : (
                hits.map((h) => {
                  const to = hitRoute(h);
                  return (
                    <li
                      key={`${h.group}-${h.id}`}
                      role="option"
                      onClick={() => openHit(h)}
                      style={to ? { cursor: "pointer" } : undefined}
                    >
                      <span>
                        {h.group} · {h.title}
                      </span>
                      <small>{to ? h.hint || "跳转到对应模块" : h.hint}</small>
                    </li>
                  );
                })
              )}
            </ul>
          )}
        </div>
        <div className="utility-actions">
          <button className="insight-user-entry" type="button" aria-label="研究员账户">
            <span aria-hidden>研</span>
            <span>研究员</span>
          </button>
        </div>
      </header>

      <details className="mobile-nav">
        <summary>导航</summary>
        <nav className="mobile-nav-links" aria-label="移动端主导航">
          {(["primary", "industry", "knowledge"] as const).map((group) => (
            <div key={group} className="nav-group">
              <div className="nav-group__title">{NAV_GROUP_LABELS[group]}</div>
              {NAV_ITEMS.filter((item) => item.group === group).map((item) => (
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
            </div>
          ))}
        </nav>
      </details>

      <div className="work-area insight-workspace">
        <Outlet />
      </div>

      <nav className="prototype-bottom-nav" aria-label="底部快捷导航">
        <Link to="/review" className="prototype-bottom-nav__tile">
          <strong>审核中心</strong>
        </Link>
        <Link to="/versions" className="prototype-bottom-nav__tile">
          <strong>监测与更新</strong>
        </Link>
        <Link to="/library" className="prototype-bottom-nav__tile">
          <strong>资料库</strong>
        </Link>
        <Link to="/data" className="prototype-bottom-nav__tile">
          <strong>数据中心</strong>
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