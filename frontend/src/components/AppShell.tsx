import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { researchClient } from "../data/researchClient";
import type { SearchHit } from "../domain/types";

const NAV_PRIMARY: { to: string; label: string; implemented: boolean }[] = [
  { to: "/", label: "研究总览", implemented: true },
  { to: "/cases", label: "行业研究", implemented: true },
  { to: "/companies", label: "公司研究", implemented: true },
  { to: "/topics", label: "主题研究", implemented: true },
  { to: "/macro", label: "宏观与政策", implemented: true },
  { to: "/relationships", label: "关系模式", implemented: true },
  { to: "/documents", label: "证据库", implemented: true },
  { to: "/review", label: "审核队列", implemented: true },
];

const NAV_LATER: { label: string }[] = [
  { label: "数据中心" },
  { label: "监测中心" },
  { label: "图表库" },
  { label: "方法论" },
  { label: "知识库" },
];

const NAV_PERSONAL: { label: string }[] = [
  { label: "我的项目" },
  { label: "我的观点" },
  { label: "我的图表" },
  { label: "我的数据" },
  { label: "回收站" },
];

interface Props {
  children?: never;
}

export function AppShell(_: Props = {}) {
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!query.trim()) {
      setHits([]);
      return;
    }
    researchClient.search(query).then((h) => {
      if (!cancelled) setHits(h);
    });
    return () => {
      cancelled = true;
    };
  }, [query]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
        const el = document.getElementById("global-search-input");
        el?.focus();
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app-shell" data-current-path={location.pathname}>
      <aside className="app-shell__nav" aria-label="产品导航">
        <div className="brand">
          <span className="brand__mark">EF</span>
          <span className="brand__title">洞见研究 OS</span>
          <span className="brand__sub">Evidence · Thesis · Fund</span>
        </div>

        <nav className="nav nav--primary" aria-label="主导航">
          {NAV_PRIMARY.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `nav__item${isActive ? " is-active" : ""}`
              }
            >
              <span>{item.label}</span>
              {!item.implemented && (
                <span className="nav__hint" aria-hidden>
                  后续
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <p className="nav__section">后续入口</p>
        <nav className="nav nav--later" aria-label="后续入口">
          {NAV_LATER.map((item) => (
            <span
              key={item.label}
              className="nav__item nav__item--disabled"
              aria-disabled
            >
              <span>{item.label}</span>
              <span className="nav__hint" aria-hidden>
                后续
              </span>
            </span>
          ))}
        </nav>

        <p className="nav__section">我的工作区</p>
        <nav className="nav nav--personal" aria-label="个人工作区">
          {NAV_PERSONAL.map((item) => (
            <span
              key={item.label}
              className="nav__item nav__item--disabled"
              aria-disabled
            >
              <span>{item.label}</span>
              <span className="nav__hint" aria-hidden>
                后续
              </span>
            </span>
          ))}
        </nav>

        <div className="nav__footer">
          <Link to="/settings" className="nav__item nav__item--disabled" aria-disabled>
            系统设置
          </Link>
        </div>
      </aside>

      <div className="app-shell__body">
        <header className="app-shell__topbar">
          <button
            type="button"
            className="search-trigger"
            onClick={() => setOpen(true)}
            aria-label="全局搜索"
          >
            <span className="search-trigger__placeholder">
              搜索行业、公司、图表、观点、研报 …
            </span>
            <kbd>⌘K</kbd>
          </button>
          <div className="topbar__right">
            <span className="cutoff-chip" aria-label="当前时间上下文">
              2024-05-31
            </span>
            <button className="icon-btn" aria-label="通知" type="button">
              ◔
            </button>
            <button className="user-chip" type="button" aria-label="用户菜单">
              <span className="user-chip__avatar" aria-hidden>
                陈
              </span>
              <span className="user-chip__name">陈思远</span>
            </button>
          </div>
        </header>

        <main className="app-shell__main" data-testid="app-main">
          <Outlet />
        </main>
      </div>

      {open && (
        <div
          className="search-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="全局搜索"
          onClick={() => setOpen(false)}
        >
          <div
            className="search-panel"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              id="global-search-input"
              autoFocus
              placeholder="搜索案例、命题、证据、公司、股票、基金"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="搜索"
            />
            <div className="search-panel__hint">
              ⌘K 打开 · ESC 关闭 · 选择结果跳转到对应页面
            </div>
            <ul className="search-panel__hits">
              {hits.map((h) => (
                <li key={`${h.group}-${h.id}`}>
                  <Link
                    to={h.navigate_to}
                    onClick={() => setOpen(false)}
                    className={`search-hit search-hit--${h.group}`}
                  >
                    <span className="search-hit__group">{h.group}</span>
                    <span className="search-hit__title">{h.title}</span>
                    <span className="search-hit__hint">{h.hint}</span>
                  </Link>
                </li>
              ))}
              {query && hits.length === 0 && (
                <li className="search-panel__empty">
                  无匹配结果：可能是资料不存在，或当前 cutoff / 权限下不可见。
                </li>
              )}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}