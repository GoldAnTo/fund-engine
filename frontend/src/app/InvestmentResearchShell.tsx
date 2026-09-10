import { NavLink, Outlet } from "react-router-dom";

export function InvestmentResearchShell() {
  return (
    <div className="ir-shell">
      <header className="ir-shell__header">
        <NavLink className="ir-shell__brand" to="/research" end>
          <span aria-hidden="true">IR</span>
          <strong>独立投资研究</strong>
        </NavLink>
        <nav aria-label="投资研究导航">
          <NavLink to="/research" end>研究目录</NavLink>
          <NavLink to="/research/new">建立研究</NavLink>
        </nav>
      </header>
      <Outlet />
    </div>
  );
}
