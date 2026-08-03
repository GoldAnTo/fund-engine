export type StatusBadgeVariant =
  | "ai"
  | "reviewed"
  | "support"
  | "contradict"
  | "warning"
  | "monitoring"
  | "validating"
  | "frozen"
  | "draft"
  | "default";

export interface StatusBadgeProps {
  variant?: StatusBadgeVariant;
  children: React.ReactNode;
}

/**
 * Consistent status pill used across the prototype shell — same vocabulary
 * as theme pills / claim sentiment pills / hypothesis badges.
 */
export function StatusBadge({ variant = "default", children }: StatusBadgeProps) {
  return <span className={`status-pill status-pill--${variant}`}>{children}</span>;
}