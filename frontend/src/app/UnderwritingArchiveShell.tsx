import { NavLink, Outlet } from "react-router-dom";

export function UnderwritingArchiveShell() {
  return (
    <div className="ura-shell">
      <header className="ura-shell__header">
        <p className="ros-eyebrow">Research archive</p>
        <nav aria-label="不可变研究档案导航">
          <NavLink to="/underwriting/research" end>
            公司／行业档案目录
          </NavLink>
        </nav>
      </header>
      <Outlet />
    </div>
  );
}
