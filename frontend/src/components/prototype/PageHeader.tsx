import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: string;
  eyebrow?: string;
  lede?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
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
}: PageHeaderProps) {
  return (
    <header className="prototype-page-header">
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