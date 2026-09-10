import type { HTMLAttributes, ReactNode } from "react";

export interface PaperCardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
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
 * actions. 透传剩余 HTML 属性（含 data-testid / aria-*），便于 e2e 与可访问
 * 性测试在不破坏视觉一致性的前提下锚定卡片。
 */
export function PaperCard({
  title,
  kicker,
  actions,
  children,
  variant = "default",
  padding = "default",
  ...rest
}: PaperCardProps) {
  return (
    <article
      {...rest}
      className={`prototype-paper prototype-paper-card prototype-paper-card--${variant} prototype-paper-card--${padding}${rest.className ? " " + rest.className : ""}`}
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