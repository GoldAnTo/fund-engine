import { NavLink, Outlet } from "react-router-dom";

export function InvestmentResearchShell() {
  return (
    <div className="ir-shell">
      <a className="skip-link" href="#company-content" onClick={() => document.getElementById("company-content")?.focus()}>跳到研究内容</a>
      <header className="ir-shell__header">
        <NavLink className="ir-shell__brand" to="/research" end>
          <span aria-hidden="true">研</span>
          <strong>公司研究工作台</strong>
        </NavLink>
        <nav aria-label="投资研究导航">
          <a href="/">事件研究</a>
          <a href="/underwriting/research">研究档案</a>
          <NavLink to="/research" end>研究库</NavLink>
          <NavLink to="/research/new">开始研究</NavLink>
        </nav>
      </header>
      <div id="company-content" tabIndex={-1}><Outlet /></div>
    </div>
  );
}
