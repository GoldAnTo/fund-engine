import { NavLink, Outlet } from "react-router-dom";

export function UnderwritingArchiveShell() {
  return (
    <div className="ura-shell ir-shell">
      <a className="skip-link" href="#archive-content" onClick={() => document.getElementById("archive-content")?.focus()}>跳到研究内容</a>
      <header className="ura-shell__header">
        <p className="ros-eyebrow">Research archive</p>
        <nav aria-label="不可变研究档案导航">
          <a href="/research">返回公司研究</a>{" · "}
          <NavLink to="/underwriting/research" end>
            公司／行业档案目录
          </NavLink>
        </nav>
      </header>
      <div id="archive-content" tabIndex={-1}><Outlet /></div>
    </div>
  );
}
