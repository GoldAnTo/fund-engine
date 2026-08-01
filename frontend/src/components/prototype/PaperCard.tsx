import type { ReactNode } from "react";

export interface PaperCardProps {
  title?: ReactNode;
  kicker?: string;
  actions?: ReactNode;
  children: ReactNode;
  variant?: "default" | "warning" | "contradict" | "ai";
  padding?: "default" | "compact";
}

/**
 * Paper card surface used across prototype screens. Provides a consistent
 * padding/border treatment and an optional header row with kicker + title +
 * actions.
 */
export function PaperCard({
  title,
  kicker,
  actions,
  children,
  variant = "default",
  padding = "default",
}: PaperCardProps) {
  return (
    <article
      className={`prototype-paper prototype-paper-card prototype-paper-card--${variant} prototype-paper-card--${padding}`}
    >
      {(title || actions) && (
        <header className="prototype-section-header">
          {title && (
            <div>
              {kicker && <p className="section-kicker">{kicker}</p>}
              <h2>{title}</h2>
            </div>
          )}
          {actions && (
            <div className="prototype-paper-card__actions">{actions}</div>
          )}
        </header>
      )}
      <div className="prototype-paper-card__body">{children}</div>
    </article>
  );
}