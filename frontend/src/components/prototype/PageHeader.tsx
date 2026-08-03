import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: string;
  eyebrow?: string;
  lede?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
  breadcrumbs?: Array<{ label: string; to?: string }>;
}

/**
 * Standardized page header used across prototype screens. Mirrors the design
 * document's evidence-view header: title + eyebrow + lede on the left, optional
 * metadata grid or actions on the right.
 */
export function PageHeader({
  title,
  eyebrow,
  lede,
  actions,
  meta,
  breadcrumbs,
}: PageHeaderProps) {
  return (
    <header className="prototype-page-header">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav className="prototype-page-header__breadcrumbs" aria-label="面包屑导航">
          {breadcrumbs.map((b, idx) => (
            <span key={`${b.label}-${idx}`} className="prototype-page-header__crumb">
              {b.to ? <a href={b.to}>{b.label}</a> : <span>{b.label}</span>}
              {idx < breadcrumbs.length - 1 && <span className="prototype-page-header__sep">›</span>}
            </span>
          ))}
        </nav>
      )}
      <div className="prototype-page-header__main">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {(actions || meta) && (
        <div className="prototype-page-header__side">
          {meta}
          {actions && <div className="prototype-page-header__actions">{actions}</div>}
        </div>
      )}
    </header>
  );
}